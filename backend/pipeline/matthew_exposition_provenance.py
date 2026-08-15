"""Normalize author-agent provenance into audit-compatible hidden JSON comments."""

from __future__ import annotations

import json
import re


KEY_VALUE_COMMENT = re.compile(r"^[ \t]*<!--\s*provenance:\s*(.*?)\s*-->[ \t]*$", re.MULTILINE)


def _list_value(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _parse_key_value(payload: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for part in payload.split(";"):
        if "=" not in part:
            continue
        key, value = part.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _normalize_metadata(values: dict[str, str], visible_text: str) -> dict[str, object]:
    raw_attribution = values.get("attribution", "")
    claim_ids = _list_value(values.get("claims", ""))
    scripture_refs = _list_value(values.get("scripture", ""))
    if raw_attribution == "professor,editor":
        attribution = "editorial_synthesis"
    elif raw_attribution == "editor" and "資料說明" not in visible_text:
        # A neutral passage introduction is grounded directly in the stated
        # Scripture range; it is not a visible editorial boundary notice.
        attribution = "scripture" if scripture_refs else "editor"
    else:
        attribution = raw_attribution
    metadata: dict[str, object] = {"attribution": attribution}
    if claim_ids:
        metadata["claim_ids"] = claim_ids
    if scripture_refs:
        metadata["scripture_refs"] = scripture_refs
    if attribution == "editorial_synthesis":
        metadata["synthesis_note"] = values.get(
            "editorial_note", "跨來源材料的歸屬、張力或神學收束"
        )
    evidence_steps = _list_value(values.get("steps", values.get("evidence_steps", "")))
    if evidence_steps:
        metadata["evidence_step_ids"] = evidence_steps
    source_ids = _list_value(values.get("sources", values.get("source", "")))
    if source_ids:
        metadata["source_ids"] = source_ids
    if values.get("decision"):
        metadata["decision_id"] = values["decision"]
    return metadata


def normalize_provenance(markdown: str) -> str:
    chunks = re.split(r"\n{2,}", markdown.strip())
    normalized: list[str] = []
    for chunk in chunks:
        matches = list(KEY_VALUE_COMMENT.finditer(chunk))
        if not matches:
            normalized.append(chunk.strip())
            continue
        if len(matches) != 1:
            raise ValueError("each Markdown block must contain at most one provenance comment")
        match = matches[0]
        visible = (chunk[: match.start()] + chunk[match.end() :]).strip()
        if not visible:
            raise ValueError("provenance comment has no visible block")
        values = _parse_key_value(match.group(1))
        metadata = _normalize_metadata(values, visible)
        comment = "<!-- provenance: " + json.dumps(metadata, ensure_ascii=False, separators=(",", ":")) + " -->"
        normalized.append(comment + "\n" + visible)
    return "\n\n".join(normalized) + "\n"
