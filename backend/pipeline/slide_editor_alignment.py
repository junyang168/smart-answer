"""Independent visual fidelity audit, importance filtering, and transcript paragraph alignment."""
from __future__ import annotations

import json
import math
import re
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


def _resolve_editor_paths(
    transcript_path: Path,
    context_path: Path | None = None,
) -> tuple[Path, Path | None]:
    """Resolve script JSON path and optional formal Editor context.json path."""
    if context_path is not None:
        return transcript_path, context_path
    if transcript_path.name == "context.json" and transcript_path.parent.parent.name == "transcript_review":
        item = transcript_path.parent.name
        script_path = transcript_path.parent.parent.parent / "script_review" / f"{item}.json"
        if not script_path.exists():
            raise FileNotFoundError(f"Editor script_review file not found for {item}: {script_path}")
        return script_path, transcript_path
    if transcript_path.parent.name == "script_review":
        candidate = transcript_path.parent.parent / "transcript_review" / transcript_path.stem / "context.json"
        if candidate.exists():
            return transcript_path, candidate
    return transcript_path, None


def load_transcript_paragraphs(
    transcript_path: Path,
    *,
    context_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Load spoken paragraphs from formal Editor (script_review + context.json), argument_source, or script_published."""
    script_path, resolved_ctx = _resolve_editor_paths(transcript_path, context_path)
    data = read_json(script_path)
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
                "paragraph_id": str(p.get("paragraph_id") or p.get("id") or f"p-{idx}"),
                "start_ms": int(start_ms) if isinstance(start_ms, (int, float)) else None,
                "end_ms": int(end_ms) if isinstance(end_ms, (int, float)) else None,
                "text": text,
            })
        return result
    bindings: dict[str, Any] = {}
    if resolved_ctx is not None:
        ctx_data = read_json(resolved_ctx)
        bindings = ctx_data.get("paragraph_bindings") or {}
    items = data.get("script", data) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Unsupported transcript JSON format")
    result = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or item.get("type") == "subtitle":
            continue
        pid = str(item.get("index") if item.get("index") is not None else f"p-{idx}")
        tb = bindings.get(pid)
        text = str(item.get("text") or "").strip()
        if not text or (text.startswith("##") and tb is None and "start_time" not in item):
            continue
        start_s, end_s = item.get("start_time"), item.get("end_time")
        start_ms = (
            int(tb["start_ms"])
            if tb and isinstance(tb.get("start_ms"), (int, float))
            else (round(float(start_s) * 1000) if isinstance(start_s, (int, float)) else None)
        )
        end_ms = (
            int(tb["end_ms"])
            if tb and isinstance(tb.get("end_ms"), (int, float))
            else (round(float(end_s) * 1000) if isinstance(end_s, (int, float)) else None)
        )
        result.append({
            "paragraph_id": pid,
            "start_ms": start_ms,
            "end_ms": end_ms,
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
        raise ValueError(
            "Transcript paragraphs have no start_ms/end_ms timings; "
            "provide formal Editor context.json paragraph_bindings or a timed transcript"
        )
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


def parse_fidelity_result(text, expected_ids):
    """Recover only the outer envelope after every expected row has decoded."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        prefix = re.match(r'\s*\{\s*"results"\s*:\s*\[', text)
        if not prefix:
            raise
        rest = text[prefix.end():].lstrip()
        rows = []
        decoder = json.JSONDecoder()
        for index, aid in enumerate(expected_ids):
            if index:
                if not rest.startswith(","):
                    raise
                rest = rest[1:].lstrip()
            row, end = decoder.raw_decode(rest)
            if not isinstance(row, dict) or row.get("asset_id") != aid:
                raise
            rows.append(row)
            rest = rest[end:].lstrip()
        # Extra members, arrays or syntactically incomplete rows remain errors.
        if not re.fullmatch(r'\]?\s*[\w\s。]*"?\s*\]?\s*\}', rest):
            raise
        return {"results": rows}


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
            parsed = parse_fidelity_result(adapter.text(raw), ids)
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
    context_path: Path | None = None,
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
    script_path, resolved_ctx = _resolve_editor_paths(transcript_path, context_path)
    paragraphs = load_transcript_paragraphs(script_path, context_path=resolved_ctx)
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
            "transcript_sha256": file_sha(script_path),
            **({"context_sha256": file_sha(resolved_ctx)} if resolved_ctx is not None else {}),
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
                "capture_ms": occ.get("capture_ms", occ["time_ms"]),
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


def _image_dimensions(image_path: Path, asset: dict[str, Any]) -> tuple[int, int]:
    """Return (width, height) from asset metadata or by reading the image file."""
    w, h = asset.get("width"), asset.get("height")
    if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
        return w, h
    import cv2

    img = cv2.imread(str(image_path))
    if img is None:
        raise ValueError(f"Cannot read image dimensions: {image_path}")
    height, width = img.shape[:2]
    return int(width), int(height)


def import_bindings_to_review(
    evidence_dir: Path,
    bindings_path: Path,
    review_dir: Path,
    *,
    actor: str = "slide_editor_alignment",
    operation_id: str | None = None,
    expected_version: str | None = None,
) -> dict[str, Any]:
    """Attach aligned white-background SVG slides to transcript_review via ReviewStore versioned commit."""
    import copy
    import shutil
    from backend.api.sc_api.transcript_review import ReviewStore, digest

    evidence_path = evidence_dir / "slide_evidence.json"
    evidence = read_json(evidence_path)
    bindings_doc = read_json(bindings_path)
    root = review_dir.parent.parent
    item = review_dir.name
    store = ReviewStore(root)

    op_id = operation_id or f"import-slides-{file_sha(bindings_path)[:16]}-{file_sha(evidence_path)[:16]}"
    evidence_occ_by_id = {o["event_id"]: o for o in evidence.get("occurrences", [])}

    with store.lock(item):
        rows, context = store.load(item)
        request = {
            "version": expected_version if expected_version is not None else digest(store.document_value(rows, context)),
            "operation_id": op_id,
            "operations": [
                {"kind": "attach_visual_slides", "id": f"g-{b['event_id']}"}
                for b in bindings_doc["bindings"]
            ],
        }
        if expected_version is not None:
            request["bindings_sha256"] = file_sha(bindings_path)
            request["evidence_sha256"] = file_sha(evidence_path)
        prior = store.receipt(item, op_id, actor, request)
        if prior:
            _, current_context = store.load(item)
            current_groups = current_context.get("groups") or []
            return {
                "review_dir": str(review_dir),
                "revision": prior["revision"],
                "visual_assets": len(current_context.get("visual_assets") or {}),
                "occurrences": len(current_context.get("occurrences") or {}),
                "visible_groups": sum(1 for g in current_groups if not g["hidden"]),
                "needs_review_groups": sum(1 for g in current_groups if g["needs_review"] and not g["hidden"]),
            }

        if expected_version is not None:
            store.version(rows, context, expected_version)

        if expected_version is not None:
            existing_group_ids = {g["id"] for g in context.get("groups", [])}
            for b in bindings_doc["bindings"]:
                if (b["event_id"] in context.get("occurrences", {})
                        or f"g-{b['event_id']}" in existing_group_ids):
                    raise ValueError(f"Attachment already occupies {b['event_id']}")
            for a in evidence["assets"]:
                aid = a["asset_id"]
                target = review_dir / "assets" / aid
                files = [("original.png", a["original_image"])]
                if a.get("svg"):
                    files.append(("reconstruction.svg", a["svg"]["path"]))
                for name, source in files:
                    dest = target / name
                    if dest.exists() and file_sha(dest) != file_sha(evidence_dir / source):
                        raise ValueError(f"Conflicting attachment file {aid}/{name}")
                if aid in context.get("visual_assets", {}):
                    raise ValueError(f"Existing Editor asset {aid} is already in use")

        assets_out = review_dir / "assets"
        assets_out.mkdir(parents=True, exist_ok=True)
        imported_assets: dict[str, Any] = {}
        for asset in evidence["assets"]:
            aid = asset["asset_id"]
            target_dir = assets_out / aid
            target_dir.mkdir(parents=True, exist_ok=True)
            orig_target = target_dir / "original.png"
            shutil.copyfile(evidence_dir / asset["original_image"], orig_target)
            width, height = _image_dimensions(orig_target, asset)
            svg_rel = None
            svg_sha = None
            if asset.get("svg"):
                shutil.copyfile(evidence_dir / asset["svg"]["path"], target_dir / "reconstruction.svg")
                svg_rel = f"transcript_review/{item}/assets/{aid}/reconstruction.svg"
                svg_sha = file_sha(target_dir / "reconstruction.svg")
            imported_assets[aid] = {
                "visible_text": asset["visible_text"],
                "image": f"transcript_review/{item}/assets/{aid}/original.png",
                "image_sha256": asset["image_sha256"],
                "width": width,
                "height": height,
                "content": asset["content"],
                "svg_review_status": "repaired_model_candidate" if asset.get("fidelity_review", {}).get("repaired") else "unreviewed_model_candidate",
                **({"svg_path": svg_rel, "svg_sha256": svg_sha} if svg_rel else {}),
                **({"fidelity_review": asset["fidelity_review"]} if asset.get("fidelity_review") else {}),
            }

        next_assets = {**(context.get("visual_assets") or {}), **imported_assets}
        next_occurrences = dict(context.get("occurrences") or {})
        imported_groups: list[dict[str, Any]] = []
        valid_paragraphs = set(context.get("paragraph_bindings", {}))

        for b in bindings_doc["bindings"]:
            sid = b["event_id"]
            aid = b["asset_id"]
            ev_occ = evidence_occ_by_id.get(sid) or {}
            capture_ms = b.get("capture_ms") if b.get("capture_ms") is not None else ev_occ.get("capture_ms", b["time_ms"])
            next_occurrences[sid] = {
                "asset_id": aid,
                "time_ms": b["time_ms"],
                "capture_ms": capture_ms,
            }
            para = b.get("aligned_paragraph_id")
            if para not in valid_paragraphs:
                para = None
            repaired = bool(next_assets.get(aid, {}).get("fidelity_review", {}).get("repaired"))
            imported_groups.append({
                "id": f"g-{sid}",
                "member_ids": [sid],
                "slide_id": sid,
                "before_paragraph_id": para,
                "hidden": not bool(b["important"]) or para is None,
                "reason": b.get("helps_reader_understand") or b.get("category", ""),
                "needs_review": repaired,
            })

        imported_group_ids = {g["id"] for g in imported_groups}
        preserved_groups = [
            g for g in (context.get("groups") or [])
            if g.get("id") not in imported_group_ids
        ]
        next_groups = preserved_groups + imported_groups

        next_context = copy.deepcopy(context)
        next_context["visual_assets"] = next_assets
        next_context["occurrences"] = next_occurrences
        next_context["groups"] = next_groups

        receipt = store.commit(
            item,
            actor,
            op_id,
            request,
            rows,
            next_context,
            "import_slide_bindings",
        )

    return {
        "review_dir": str(review_dir),
        "revision": receipt["revision"],
        "visual_assets": len(next_assets),
        "occurrences": len(next_occurrences),
        "visible_groups": sum(1 for g in next_groups if not g["hidden"]),
        "needs_review_groups": sum(1 for g in next_groups if g["needs_review"] and not g["hidden"]),
    }
