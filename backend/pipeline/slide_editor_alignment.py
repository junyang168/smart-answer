"""Independent visual fidelity audit, importance filtering, and transcript paragraph alignment."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.pipeline.agy_subscription_client import AgySubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.slide_change_pilot import clock, file_sha, object_sha, read_json, save_json
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.slide_visual_evidence import (
    BOOL,
    NULLABLE_STRING,
    STRING,
    check_schema,
    obj,
    render_svg,
    validate_content,
)
from backend.pipeline.source_projection import visual_source_blocks
from backend.pipeline.transcript_audio_pilot import saved_call

import hashlib

FIDELITY_PROMPT_VERSION = "slide-fidelity-audit.v1"
ALIGNMENT_PROMPT_VERSION = "slide-editor-alignment.v1"

IMPORTANT_CATEGORIES = (
    "professor_referenced_diagram",
    "structural_layout",
    "exegetical_evidence_text",
    "independent_build_step",
)
EXCLUDED_CATEGORIES = (
    "pure_scripture_only",
    "cover_or_transition",
    "redundant_build",
    "other_not_important",
)
ALL_CATEGORIES = IMPORTANT_CATEGORIES + EXCLUDED_CATEGORIES

FIDELITY_PROMPT = """Audit whether the extracted slide text and white-background SVG summary are faithful to each original slide image.
Do not use audio, transcript, or biblical knowledge. Compare ONLY the original image against the extracted payload.
Check for: missing lines, truncated text, wrong Greek/Hebrew letters or diacritics, wrong verse references/numbers,
missing/wrong diagram connectors, or lost meaningful color distinctions on the white-background SVG.
Return one result per asset_id in exact order:
- passed: true if faithful with no material omissions or distortions; false otherwise.
- severity: "none" when passed, "minor" for punctuation/formatting differences, "major" for missing/wrong text, Greek/Hebrew errors, or lost diagram/color meaning.
- issues: concise list of concrete discrepancies (empty when none).
- summary: one concise Traditional Chinese sentence."""

ALIGNMENT_PROMPT = """Evaluate each lecture slide occurrence against its candidate spoken transcript paragraphs.
Define "important" as: the slide helps a reader understand the scripture, structure, or reasoning Prof. Wang is explaining.
Categories:
- professor_referenced_diagram: lecturer explicitly points to diagram lines/arrows/groups.
- structural_layout: arrangement, color contrast, or arrows showing passage structure (e.g. chiasm A-B-B'-A').
- exegetical_evidence_text: text carrying exegetical evidence (Greek/Hebrew, translation comparison, verse nuances), especially when speech only mentions part.
- independent_build_step: progressive reveal step where this step itself is independently explained.
- pure_scripture_only: plain scripture quotation with no professor annotations, structure, or word-study notes (MUST NOT be marked important or source).
- cover_or_transition: title/cover, break screen, video UI, or transition frame.
- redundant_build: progressive reveal of the same content where only the final complete slide is needed.
- other_not_important: decorative or non-explanatory slide.

For every slide:
- important: true ONLY for the first four categories; false for the last four.
- aligned_paragraph_id: the single paragraph ID where Prof. Wang actually explains this slide (may differ from raw slide timestamp if he switches early/late), or null if none.
- helps_reader_understand: one Traditional Chinese sentence answering "它幫讀者看懂哪一段的什麼？" (empty string if not important).
- cross_channel_notes: any factual mismatch between slide and speech (e.g. ASR typo on Greek term vs slide), without altering either; empty string if none."""

FIDELITY_SCHEMA = obj({
    "results": {
        "type": "array",
        "items": obj({
            "asset_id": STRING,
            "passed": BOOL,
            "severity": {"type": "string", "enum": ["none", "minor", "major"]},
            "issues": {"type": "array", "items": STRING},
            "summary": STRING,
        }),
    }
})

ALIGNMENT_SCHEMA = obj({
    "results": {
        "type": "array",
        "items": obj({
            "event_id": STRING,
            "category": {"type": "string", "enum": list(ALL_CATEGORIES)},
            "important": BOOL,
            "aligned_paragraph_id": NULLABLE_STRING,
            "helps_reader_understand": STRING,
            "cross_channel_notes": STRING,
        }),
    }
})


def load_transcript_paragraphs(transcript_path: Path) -> list[dict[str, Any]]:
    """Load spoken paragraphs from either script_published/*.json or illustrated_transcript.json."""
    data = read_json(transcript_path)
    if isinstance(data, dict) and isinstance(data.get("paragraphs"), list):
        rows = data["paragraphs"]
        result = []
        for idx, p in enumerate(rows):
            text = str(p.get("text") or "").strip()
            if not text:
                continue
            start_ms = p.get("start_ms")
            end_ms = p.get("end_ms")
            result.append({
                "paragraph_id": str(p.get("id") or f"p-{idx}"),
                "start_ms": int(start_ms) if isinstance(start_ms, (int, float)) else None,
                "end_ms": int(end_ms) if isinstance(end_ms, (int, float)) else None,
                "text": text,
            })
        return result
    items = data.get("script", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Unsupported transcript JSON format")
    result = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") == "subtitle":
            continue
        text = str(item.get("text") or "").strip()
        if not text or (text.startswith("##") and "start_time" not in item):
            continue
        start_s, end_s = item.get("start_time"), item.get("end_time")
        result.append({
            "paragraph_id": str(item.get("index") if item.get("index") is not None else f"p-{idx}"),
            "start_ms": round(float(start_s) * 1000) if isinstance(start_s, (int, float)) else None,
            "end_ms": round(float(end_s) * 1000) if isinstance(end_s, (int, float)) else None,
            "text": text,
        })
    return result


def candidate_paragraphs_for_time(
    paragraphs: list[dict[str, Any]],
    time_ms: int,
    *,
    window_padding_ms: int = 45000,
    max_candidates: int = 6,
) -> list[dict[str, Any]]:
    timed = [p for p in paragraphs if p["start_ms"] is not None and p["end_ms"] is not None]
    if not timed:
        return paragraphs[:max_candidates]
    in_window = [
        p for p in timed
        if p["end_ms"] >= time_ms - window_padding_ms and p["start_ms"] <= time_ms + window_padding_ms
    ]
    if not in_window:
        nearest = sorted(timed, key=lambda p: min(abs(p["start_ms"] - time_ms), abs(p["end_ms"] - time_ms)))
        in_window = nearest[:max_candidates]
    return in_window[:max_candidates]


def _prepare_adapter(provider: str, prompt: str, schema: dict) -> VisionReviewer:
    adapter = VisionReviewer(provider, prompt, schema)
    adapter.config["max_output_tokens"] = 8192
    if provider == "gemini":
        adapter.config.pop("response_json_schema", None)
        adapter.prompt = prompt + "\nReturn JSON matching this exact schema:\n" + json.dumps(schema, ensure_ascii=False)
    return adapter


def _append_context_part(adapter: VisionReviewer, contents: Any, context_text: str) -> Any:
    if adapter.provider == "openai":
        contents[0]["content"].append({"type": "input_text", "text": context_text})
        return contents
    from google.genai import types
    return list(contents) + [types.Part.from_text(text=context_text)]


FIDELITY_REPAIR_PROMPT = """Fix ONLY the text discrepancies listed in `issues` by comparing against the original slide image.
Preserve all text_region IDs, bounding boxes, colors, and unaffected lines verbatim.
Return the corrected text_regions array for each asset_id."""

FIDELITY_REPAIR_SCHEMA = obj({
    "results": {
        "type": "array",
        "items": obj({
            "asset_id": STRING,
            "text_regions": {
                "type": "array",
                "items": obj({
                    "id": STRING,
                    "text": STRING,
                }),
            },
        }),
    }
})


def _apply_fidelity_repair(
    evidence_dir: Path,
    asset: dict[str, Any],
    repaired_regions: list[dict[str, str]],
    audit_row: dict[str, Any],
) -> None:
    before_text = asset["visible_text"]
    by_id = {r["id"]: r["text"] for r in repaired_regions}
    for region in asset["content"]["text_regions"]:
        if region["id"] in by_id:
            region["text"] = by_id[region["id"]]
    validate_content(asset["content"])
    new_text = "\n".join(r["text"] for r in asset["content"]["text_regions"])
    asset["visible_text"] = new_text
    asset["text_sha256"] = hashlib.sha256(new_text.encode()).hexdigest()
    asset["content_sha256"] = object_sha(asset["content"])
    if asset.get("svg"):
        white_bg = asset["svg"].get("background_mode") == "white"
        svg = render_svg(
            asset["content"],
            asset["image_width"],
            asset["image_height"],
            asset["image_sha256"],
            allow_text_only=True,
            white_background=white_bg,
        )
        svg_path = evidence_dir / asset["svg"]["path"]
        svg_path.write_text(svg, encoding="utf-8")
        block = visual_source_blocks(svg, segment_index="S0001")[0]
        asset["svg"].update({
            "sha256": file_sha(svg_path),
            "literal_facts": list(block.facts),
            "canonical_sha256": block.canonical_sha256,
        })
    text_changed = new_text != before_text
    asset["fidelity_review"] = {
        "status": "repaired" if text_changed else "needs_review",
        "repaired": text_changed,
        "issues": audit_row["issues"],
        "summary": audit_row["summary"],
        **({"before_text": before_text} if text_changed else {}),
    }
    save_json(evidence_dir / "assets" / asset["asset_id"] / "evidence.json", asset)


def verify_visual_fidelity(
    evidence_dir: Path,
    *,
    provider: str = "gemini",
    model: str | None = None,
    batch_size: int = 2,
    max_calls: int = 16,
    budget_usd: float = 2.0,
    auto_repair: bool = True,
) -> dict[str, Any]:
    """Independently check extracted text and white-background SVG against original images."""
    evidence_path = evidence_dir / "slide_evidence.json"
    collection = read_json(evidence_path)
    assets = collection["assets"]
    if not assets:
        raise ValueError("No slide assets to audit")
    if math.ceil(len(assets) / batch_size) > max_calls:
        raise ValueError("Too many fidelity batches; no paid calls made")

    if provider == "codex_subscription":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    codex_client = CodexSubscriptionClient(model=model or "gpt-6.1-sol") if provider == "codex_subscription" else None
    adapter = None if codex_client is not None else _prepare_adapter(provider, FIDELITY_PROMPT, FIDELITY_SCHEMA)
    model_name = codex_client.model if codex_client is not None else adapter.model
    results, calls = [], []
    assets_by_id = {a["asset_id"]: a for a in assets}
    for start in range(0, len(assets), batch_size):
        batch = assets[start:start + batch_size]
        ids = [a["asset_id"] for a in batch]
        paths = [evidence_dir / a["original_image"] for a in batch]
        payload = [
            {
                "asset_id": a["asset_id"],
                "visible_text": a["visible_text"],
                "text_region_colors": [r["color"] for r in a["content"]["text_regions"]],
                "has_diagram": a["content"]["has_diagram"],
                "shape_count": len(a["content"]["shapes"]),
                "edge_count": len(a["content"]["edges"]),
                "svg_background_mode": a["svg"]["background_mode"] if a.get("svg") else None,
            }
            for a in batch
        ]
        user_payload = "Extracted slide payloads to audit (images attached in asset_id order):\n" + json.dumps(payload, ensure_ascii=False)
        request = {
            "stage": "slide_fidelity_audit",
            "provider": provider,
            "slide_evidence_sha256": file_sha(evidence_path),
            "prompt_version": FIDELITY_PROMPT_VERSION,
            "model": model_name,
            "asset_ids": ids,
            "payload": payload,
        }
        if codex_client is not None:
            parsed = saved_call(
                evidence_dir / "fidelity_calls",
                request,
                0.0,
                lambda: codex_client.generate_json(FIDELITY_PROMPT, user_payload, FIDELITY_SCHEMA, image_paths=paths),
                max_calls=max_calls,
                budget_usd=budget_usd,
            )
            usage = {"estimated_usd_upper": 0.0}
        else:
            contents = _append_context_part(adapter, adapter.contents(ids, paths), user_payload)
            cached = (evidence_dir / "fidelity_calls" / "calls" / object_sha(request) / "state.json").exists()
            count = 0 if cached else adapter.count(contents)
            reserved = (count * adapter.input_rate * 1.2 + 8192 * adapter.output_rate) / 1_000_000
            raw = saved_call(
                evidence_dir / "fidelity_calls",
                request,
                reserved,
                lambda: adapter.generate(contents),
                max_calls=max_calls,
                budget_usd=budget_usd,
            )
            parsed = json.loads(adapter.text(raw))
            usage = adapter.usage(raw)
        check_schema(parsed, FIDELITY_SCHEMA)
        if [r["asset_id"] for r in parsed["results"]] != ids:
            raise ValueError("Fidelity audit asset IDs drifted")
        results.extend(parsed["results"])
        calls.append({"request_sha256": object_sha(request), **usage})

    repaired_count = 0
    failed_items = [(assets_by_id[r["asset_id"]], r) for r in results if not r["passed"]]
    for asset, audit_row in failed_items:
        if not auto_repair:
            asset["fidelity_review"] = {
                "status": "needs_review",
                "repaired": False,
                "issues": audit_row["issues"],
                "summary": audit_row["summary"],
            }
            continue
        repair_payload = [{
            "asset_id": asset["asset_id"],
            "issues": audit_row["issues"],
            "text_regions": [{"id": r["id"], "text": r["text"]} for r in asset["content"]["text_regions"]],
        }]
        repair_user_payload = "Fidelity issues to fix:\n" + json.dumps(repair_payload, ensure_ascii=False)
        repair_req = {
            "stage": "slide_fidelity_repair",
            "provider": provider,
            "slide_evidence_sha256": file_sha(evidence_path),
            "asset_id": asset["asset_id"],
            "issues": audit_row["issues"],
            "model": model_name,
        }
        img_path = evidence_dir / asset["original_image"]
        if codex_client is not None:
            repaired_parsed = saved_call(
                evidence_dir / "fidelity_calls",
                repair_req,
                0.0,
                lambda: codex_client.generate_json(FIDELITY_REPAIR_PROMPT, repair_user_payload, FIDELITY_REPAIR_SCHEMA, image_paths=[img_path]),
                max_calls=max_calls,
                budget_usd=budget_usd,
            )
            repair_usage = {"estimated_usd_upper": 0.0}
        else:
            repair_adapter = _prepare_adapter(provider, FIDELITY_REPAIR_PROMPT, FIDELITY_REPAIR_SCHEMA)
            repair_contents = _append_context_part(
                repair_adapter,
                repair_adapter.contents([asset["asset_id"]], [img_path]),
                repair_user_payload,
            )
            cached = (evidence_dir / "fidelity_calls" / "calls" / object_sha(repair_req) / "state.json").exists()
            count = 0 if cached else repair_adapter.count(repair_contents)
            reserved = (count * repair_adapter.input_rate * 1.2 + 8192 * repair_adapter.output_rate) / 1_000_000
            repair_raw = saved_call(
                evidence_dir / "fidelity_calls",
                repair_req,
                reserved,
                lambda: repair_adapter.generate(repair_contents),
                max_calls=max_calls,
                budget_usd=budget_usd,
            )
            repaired_parsed = json.loads(repair_adapter.text(repair_raw))
            repair_usage = repair_adapter.usage(repair_raw)
        check_schema(repaired_parsed, FIDELITY_REPAIR_SCHEMA)
        _apply_fidelity_repair(evidence_dir, asset, repaired_parsed["results"][0]["text_regions"], audit_row)
        calls.append({"request_sha256": object_sha(repair_req), **repair_usage})
        repaired_count += 1

    for asset, audit_row in [(assets_by_id[r["asset_id"]], r) for r in results if r["passed"]]:
        asset["fidelity_review"] = {
            "status": "passed",
            "repaired": False,
            "issues": [],
            "summary": audit_row["summary"],
        }
    save_json(evidence_path, collection)

    audit = {
        "schema_version": "slide-fidelity-audit.v1",
        "slide_evidence_sha256": file_sha(evidence_path),
        "provider": provider,
        "model": model_name,
        "prompt_version": FIDELITY_PROMPT_VERSION,
        "asset_count": len(results),
        "passed_count": sum(1 for r in results if r["passed"]),
        "repaired_count": repaired_count,
        "auto_corrections": repaired_count,
        "estimated_usd_upper": sum(c["estimated_usd_upper"] for c in calls),
        "results": results,
    }
    save_json(evidence_dir / "fidelity_audit.json", audit)
    return audit


def align_and_filter_important_slides(
    evidence_dir: Path,
    transcript_path: Path,
    out_dir: Path,
    *,
    provider: str = "gemini",
    model: str | None = None,
    batch_size: int = 2,
    window_padding_ms: int = 45000,
    max_calls: int = 20,
    budget_usd: float = 2.0,
) -> dict[str, Any]:
    """Filter important slides (excluding pure scripture) and align each to its explained paragraph."""
    evidence_path = evidence_dir / "slide_evidence.json"
    collection = read_json(evidence_path)
    assets_by_id = {a["asset_id"]: a for a in collection["assets"]}
    paragraphs = load_transcript_paragraphs(transcript_path)
    occurrences = collection["occurrences"]
    if not occurrences:
        raise ValueError("No slide occurrences to align")
    if math.ceil(len(occurrences) / batch_size) > max_calls:
        raise ValueError("Too many alignment batches; no paid calls made")

    if provider == "codex_subscription":
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    subscription_client = (
        AgySubscriptionClient(**({"model": model} if model else {}))
        if provider == "agy_subscription"
        else CodexSubscriptionClient(model=model or "gpt-6.1-sol")
        if provider == "codex_subscription"
        else None
    )
    adapter = None if subscription_client is not None else _prepare_adapter(provider, ALIGNMENT_PROMPT, ALIGNMENT_SCHEMA)
    model_name = subscription_client.model if subscription_client is not None else adapter.model
    decisions, calls = [], []
    for start in range(0, len(occurrences), batch_size):
        batch = occurrences[start:start + batch_size]
        ids = [o["event_id"] for o in batch]
        paths = [evidence_dir / assets_by_id[o["asset_id"]]["original_image"] for o in batch]
        context_items = []
        for occ in batch:
            asset = assets_by_id[occ["asset_id"]]
            cands = candidate_paragraphs_for_time(paragraphs, occ["time_ms"], window_padding_ms=window_padding_ms)
            svg_rel = asset["svg"]["path"] if asset.get("svg") else None
            svg_file = (evidence_dir / svg_rel) if svg_rel else None
            svg_xml = svg_file.read_text(encoding="utf-8") if svg_file and svg_file.exists() else None
            context_items.append({
                "event_id": occ["event_id"],
                "time": clock(occ["time_ms"]),
                "kind": occ["kind"],
                "visible_text": asset["visible_text"],
                "svg_xml": svg_xml,
                "text_region_colors": [r["color"] for r in asset["content"]["text_regions"]],
                "has_diagram": asset["content"]["has_diagram"],
                "shape_count": len(asset["content"]["shapes"]),
                "edge_count": len(asset["content"]["edges"]),
                "candidate_paragraphs": cands,
            })
        user_payload = "Slide occurrences and candidate transcript paragraphs:\n" + json.dumps(context_items, ensure_ascii=False)
        request = {
            "stage": "slide_editor_alignment",
            "provider": provider,
            "slide_evidence_sha256": file_sha(evidence_path),
            "transcript_sha256": file_sha(transcript_path),
            "prompt_version": ALIGNMENT_PROMPT_VERSION,
            "model": model_name,
            "event_ids": ids,
            "context_items": context_items,
        }
        if subscription_client is not None:
            raw = saved_call(
                out_dir,
                request,
                0.0,
                lambda: subscription_client.generate_json(ALIGNMENT_PROMPT, user_payload, ALIGNMENT_SCHEMA),
                max_calls=max_calls,
                budget_usd=budget_usd,
            )
            parsed = raw
            usage = {"estimated_usd_upper": 0.0}
        else:
            contents = _append_context_part(adapter, adapter.contents(ids, paths), user_payload)
            cached = (out_dir / "calls" / object_sha(request) / "state.json").exists()
            count = 0 if cached else adapter.count(contents)
            reserved = (count * adapter.input_rate * 1.2 + 8192 * adapter.output_rate) / 1_000_000
            raw = saved_call(
                out_dir,
                request,
                reserved,
                lambda: adapter.generate(contents),
                max_calls=max_calls,
                budget_usd=budget_usd,
            )
            parsed = json.loads(adapter.text(raw))
            usage = adapter.usage(raw)
        check_schema(parsed, ALIGNMENT_SCHEMA)
        if [r["event_id"] for r in parsed["results"]] != ids:
            raise ValueError("Alignment event IDs drifted")
        for occ, row in zip(batch, parsed["results"]):
            is_important = row["category"] in IMPORTANT_CATEGORIES and bool(row["important"])
            source_role = (
                "visual_source_candidate" if is_important
                else "scripture_reference_only" if row["category"] == "pure_scripture_only"
                else "excluded"
            )
            asset = assets_by_id[occ["asset_id"]]
            decisions.append({
                **row,
                "important": is_important,
                "source_role": source_role,
                "time_ms": occ["time_ms"],
                "asset_id": occ["asset_id"],
                "svg_path": asset["svg"]["path"] if asset.get("svg") else None,
                "original_image": asset["original_image"],
            })
        calls.append({"request_sha256": object_sha(request), **usage})

    fidelity_path = evidence_dir / "fidelity_audit.json"
    result = {
        "schema_version": "slide-editor-bindings.v1",
        "slide_evidence_sha256": file_sha(evidence_path),
        "transcript_sha256": file_sha(transcript_path),
        "fidelity_audit_sha256": file_sha(fidelity_path) if fidelity_path.exists() else None,
        "provider": provider,
        "model": model_name,
        "prompt_version": ALIGNMENT_PROMPT_VERSION,
        "occurrence_count": len(decisions),
        "important_count": sum(1 for d in decisions if d["important"]),
        "pure_scripture_excluded_count": sum(1 for d in decisions if d["category"] == "pure_scripture_only"),
        "estimated_usd_upper": sum(c["estimated_usd_upper"] for c in calls),
        "bindings": decisions,
    }
    save_json(out_dir / "editor_slide_bindings.json", result)
    return result


def import_bindings_to_review(
    evidence_dir: Path,
    bindings_path: Path,
    review_dir: Path,
) -> dict[str, Any]:
    """Import aligned white-background SVG slides and fidelity annotations into transcript_review context."""
    import shutil

    evidence = read_json(evidence_dir / "slide_evidence.json")
    bindings_doc = read_json(bindings_path)
    context_path = review_dir / "context.json"
    context = read_json(context_path)

    assets_out = review_dir / "assets"
    assets_out.mkdir(parents=True, exist_ok=True)
    visual_assets: dict[str, Any] = {}
    for asset in evidence["assets"]:
        aid = asset["asset_id"]
        target_dir = assets_out / aid
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(evidence_dir / asset["original_image"], target_dir / "original.png")
        svg_rel = None
        svg_sha = None
        if asset.get("svg"):
            shutil.copyfile(evidence_dir / asset["svg"]["path"], target_dir / "reconstruction.svg")
            svg_rel = f"transcript_review/{review_dir.name}/assets/{aid}/reconstruction.svg"
            svg_sha = file_sha(target_dir / "reconstruction.svg")
        visual_assets[aid] = {
            "visible_text": asset["visible_text"],
            "image": f"transcript_review/{review_dir.name}/assets/{aid}/original.png",
            "image_sha256": asset["image_sha256"],
            "content": asset["content"],
            "svg_review_status": "repaired_model_candidate" if asset.get("fidelity_review", {}).get("repaired") else "unreviewed_model_candidate",
            **({"svg_path": svg_rel, "svg_sha256": svg_sha} if svg_rel else {}),
            **({"fidelity_review": asset["fidelity_review"]} if asset.get("fidelity_review") else {}),
        }

    occurrences: dict[str, Any] = {}
    groups: list[dict[str, Any]] = []
    valid_paragraphs = set(context.get("paragraph_bindings", {}))
    for b in bindings_doc["bindings"]:
        sid = b["event_id"]
        aid = b["asset_id"]
        occurrences[sid] = {
            "asset_id": aid,
            "time_ms": b["time_ms"],
            "capture_ms": b["time_ms"],
        }
        para = b.get("aligned_paragraph_id")
        if para not in valid_paragraphs:
            para = None
        repaired = bool(visual_assets.get(aid, {}).get("fidelity_review", {}).get("repaired"))
        groups.append({
            "id": f"g-{sid}",
            "member_ids": [sid],
            "slide_id": sid,
            "before_paragraph_id": para,
            "hidden": not bool(b["important"]) or para is None,
            "reason": b.get("helps_reader_understand") or b.get("category", ""),
            "needs_review": repaired,
        })

    context["visual_assets"] = visual_assets
    context["occurrences"] = occurrences
    context["groups"] = groups
    save_json(context_path, context)
    return {
        "review_dir": str(review_dir),
        "visual_assets": len(visual_assets),
        "occurrences": len(occurrences),
        "visible_groups": sum(1 for g in groups if not g["hidden"]),
        "needs_review_groups": sum(1 for g in groups if g["needs_review"] and not g["hidden"]),
    }
