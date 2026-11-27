"""Decoupled sermon source API contract (``argument-source-package.v1``) for the argument layer."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.sc_api.transcript_review import ReviewError, ReviewStore, file_hash
from backend.pipeline.source_projection import (
    SourceProjection,
    VisualSourceBlock,
    visual_source_blocks,
)

SCHEMA_VERSION = "argument-source-package.v1"


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_argument_source_package(
    root: Path,
    item: str,
    *,
    require_confirmed_visuals: bool = True,
) -> dict[str, Any]:
    """Compile spoken paragraphs and attested white-background SVG slides into a storage-independent package."""
    store = ReviewStore(root)
    rows, context = store.load(item)
    bindings = context.get("paragraph_bindings") or {}
    timing_states = (context.get("timing_quality") or {}).get("paragraph_states") or {}

    paragraphs: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") == "subtitle":
            continue
        text = str(row.get("text") or "").strip()
        if not text or (text.startswith("##") and row.get("index") not in bindings):
            continue
        pid = str(row.get("index"))
        tb = bindings.get(pid)
        paragraphs.append({
            "paragraph_id": pid,
            "start_ms": int(tb["start_ms"]) if tb and isinstance(tb.get("start_ms"), (int, float)) else None,
            "end_ms": int(tb["end_ms"]) if tb and isinstance(tb.get("end_ms"), (int, float)) else None,
            "text": text,
            "timing_status": (timing_states.get(pid) or {}).get("status", "qualified" if tb else "unresolved"),
        })

    occurrences = context.get("occurrences") or {}
    visual_assets = context.get("visual_assets") or {}
    reviews = context.get("reviews") or {}
    visual_blocks: list[dict[str, Any]] = []
    pending_visual_blocks: list[dict[str, Any]] = []
    attestations: dict[str, str] = {}
    excluded_hidden_count = 0

    for ordinal, group in enumerate(context.get("groups") or []):
        if group.get("hidden"):
            excluded_hidden_count += 1
            continue
        para_id = group.get("before_paragraph_id")
        if not para_id:
            continue
        sid = group.get("slide_id")
        occ = occurrences.get(sid)
        if not occ:
            continue
        aid = occ.get("asset_id")
        asset = visual_assets.get(aid)
        if not asset:
            continue

        img_rel = asset.get("image")
        if img_rel:
            img_path = Path(root) / img_rel
            if not img_path.exists() or file_hash(img_path) != asset.get("image_sha256"):
                raise ReviewError(409, f"原圖雜湊驗證失敗：{aid}")

        svg_rel = asset.get("svg_path")
        if not svg_rel:
            continue
        svg_path = Path(root) / svg_rel
        if not svg_path.exists():
            raise ReviewError(404, f"找不到白底 SVG：{aid}")
        actual_svg_sha = file_hash(svg_path)
        if asset.get("svg_sha256") and actual_svg_sha != asset["svg_sha256"]:
            raise ReviewError(409, f"白底 SVG 雜湊驗證失敗：{aid}")

        svg_xml = svg_path.read_text(encoding="utf-8")
        locator = f"visual:{group['id']}:{aid}"
        parsed = visual_source_blocks(svg_xml, segment_index=str(para_id))
        if not parsed:
            raise ReviewError(422, f"SVG 無法解析（{aid}）：找不到 <svg> 區塊")
        block = parsed[0]
        if not block.readable:
            raise ReviewError(422, f"SVG 無法解析（{aid}）：{block.parse_error}")

        review = reviews.get(f"g:{group['id']}") or {}
        is_confirmed = review.get("decision") == "confirmed"
        entry = {
            "locator": locator,
            "group_id": group["id"],
            "asset_id": aid,
            "before_paragraph_id": str(para_id),
            "ordinal": ordinal,
            "time_ms": occ.get("time_ms"),
            "source_modality": "visual",
            "svg_path": svg_rel,
            "svg_sha256": actual_svg_sha,
            "svg_xml": svg_xml,
            "visible_text": asset.get("visible_text") or "",
            "visual_facts": list(block.facts),
            "canonical_sha256": block.canonical_sha256,
            "reason": group.get("reason") or "",
            "confirmed": is_confirmed,
            "needs_review": bool(group.get("needs_review")),
        }
        if is_confirmed or not require_confirmed_visuals:
            visual_blocks.append(entry)
            attestations[locator] = actual_svg_sha
        else:
            pending_visual_blocks.append(entry)

    body_sha = _sha256_json(paragraphs)
    visual_sha = _sha256_json([
        {"locator": b["locator"], "svg_sha256": b["svg_sha256"], "before_paragraph_id": b["before_paragraph_id"]}
        for b in visual_blocks
    ]) if visual_blocks else None

    return {
        "schema_version": SCHEMA_VERSION,
        "item": item,
        "source_body_sha256": body_sha,
        "visual_content_sha256": visual_sha,
        "paragraphs": paragraphs,
        "visual_blocks": visual_blocks,
        "pending_visual_blocks": pending_visual_blocks,
        "visual_source_attestations": attestations,
        "stats": {
            "paragraph_count": len(paragraphs),
            "admitted_visual_count": len(visual_blocks),
            "pending_visual_count": len(pending_visual_blocks),
            "excluded_hidden_count": excluded_hidden_count,
        },
    }


def project_argument_source(package: dict[str, Any]) -> SourceProjection:
    """Adapt an ``argument-source-package.v1`` payload into a ``SourceProjection`` for the argument layer."""
    if package.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"Unsupported argument source schema: {package.get('schema_version')}")
    body_rows = tuple(
        {
            "index": p["paragraph_id"],
            "text": p["text"],
            "start_time": (p["start_ms"] / 1000.0) if p["start_ms"] is not None else None,
            "end_time": (p["end_ms"] / 1000.0) if p["end_ms"] is not None else None,
        }
        for p in package["paragraphs"]
    )
    visuals = tuple(
        VisualSourceBlock(
            locator=b["locator"],
            segment_index=b["before_paragraph_id"],
            source_segment_index=b["before_paragraph_id"],
            ordinal=b["ordinal"],
            char_start=0,
            char_end=0,
            raw_svg=b["svg_xml"],
            raw_sha256=b["svg_sha256"],
            canonical_sha256=b["canonical_sha256"],
            facts=tuple(b["visual_facts"]),
            source_path=b["svg_path"],
            source_file_sha256=b["svg_sha256"],
            binding_kind="sidecar_svg",
        )
        for b in package["visual_blocks"]
    )
    spoken_text_sha = hashlib.sha256("\n".join(r["text"] for r in body_rows).encode("utf-8")).hexdigest()
    return SourceProjection(
        body_rows=body_rows,
        spoken_rows=body_rows,
        headings=(),
        visual_blocks=visuals,
        spoken_text_sha256=spoken_text_sha,
        body_sha256=package["source_body_sha256"],
        visual_content_sha256=package["visual_content_sha256"],
        editorial_structure_sha256=_sha256_json([]),
        editorial_topology_sha256=_sha256_json([]),
    )
