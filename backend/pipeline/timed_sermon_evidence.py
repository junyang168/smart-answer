"""Attach verified excerpts from one timed sermon segment to existing claims."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def build_timed_sermon_evidence(
    knowledge: dict[str, Any],
    transcript_path: Path,
    source: dict[str, Any],
    bindings: list[dict[str, Any]],
) -> dict[str, Any]:
    result = copy.deepcopy(knowledge)
    raw = transcript_path.read_bytes()
    parsed = json.loads(raw)
    segments = parsed.get("script", []) if isinstance(parsed, dict) else parsed
    source_sha = hashlib.sha256(raw).hexdigest()
    declared_sha = str(source.get("source_sha256") or "")
    if declared_sha and declared_sha != source_sha:
        raise ValueError("transcript source hash mismatch")

    source_id = str(source["source_id"])
    transcript_id = str(source["transcript_id"])
    document = {
        **source,
        "source_type": "sermon_transcript",
        "source_path": str(transcript_path.resolve()),
        "source_sha256": source_sha,
        "review_status": source.get("review_status") or "candidate",
    }
    documents = {str(row.get("source_id")): row for row in result.get("source_documents", [])}
    documents[source_id] = document
    result["source_documents"] = list(documents.values())

    claims = {str(row.get("claim_id")): row for row in result.get("claims", [])}
    fragments = {
        str(row.get("fragment_id")): row for row in result.get("source_fragments", [])
    }
    evidence = {
        str(row.get("evidence_step_id")): row for row in result.get("evidence_steps", [])
    }
    for binding in bindings:
        claim_id = str(binding["claim_id"])
        if claim_id not in claims:
            raise ValueError(f"unknown claim_id: {claim_id}")
        source_index = str(binding["source_index"])
        matches = [
            (ordinal, segment)
            for ordinal, segment in enumerate(segments)
            if str(segment.get("index")) == source_index
        ]
        if len(matches) != 1:
            raise ValueError(f"source index must resolve exactly once: {source_index}")
        ordinal, segment = matches[0]
        excerpt = str(binding["verbatim_excerpt"])
        text = str(segment.get("text") or "")
        if not excerpt or excerpt not in text:
            raise ValueError(f"excerpt is not verbatim: {claim_id}")
        start = segment.get("start_time")
        end = segment.get("end_time")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
            raise ValueError(f"segment has no valid media range: {source_index}")

        evidence_id = _stable_id("EV-MEDIA", transcript_id, claim_id, source_index, excerpt)
        fragment_id = _stable_id("FR-MEDIA", transcript_id, claim_id, source_index, excerpt)
        fragments[fragment_id] = {
                "fragment_id": fragment_id,
                "source_id": source_id,
                "verbatim_excerpt": excerpt,
                "paragraph_key": f"S{ordinal + 1:04d}",
                "source_segment_index": segment.get("index"),
                "media_time": start,
                "media_end_time": end,
                "source_sha256": source_sha,
                "paragraph_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "verbatim_excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "anchor_state": "source_version_bound",
                "review_status": "candidate",
            }
        evidence[evidence_id] = {
                "evidence_step_id": evidence_id,
                "statement": binding.get("statement") or excerpt,
                "step_type": binding.get("step_type") or "reasoning",
                "speaker": "professor",
                "stance": "asserted",
                "discourse_role": "verified_timed_sermon_support",
                "support_eligibility": "eligible_candidate",
                "scripture_refs": binding.get("scripture_refs") or [],
                "produced_claim_ids": [claim_id],
                "source_fragment_ids": [fragment_id],
                "review_status": "candidate",
            }
        claim = claims[claim_id]
        claim["evidence_step_ids"] = list(
            dict.fromkeys([*(claim.get("evidence_step_ids") or []), evidence_id])
        )
        occurrences = claim.setdefault("occurrences", [])
        occurrence = next(
            (row for row in occurrences if row.get("transcript_id") == transcript_id),
            None,
        )
        if occurrence is None:
            occurrence = {
                "source_id": source_id,
                "transcript_id": transcript_id,
                "lecture": document.get("title"),
                "anchors": [],
            }
            occurrences.append(occurrence)
        anchor = {
                "paragraph_key": f"S{ordinal + 1:04d}",
                "media_time": start,
                "evidence_id": evidence_id,
                "evidence_type": binding.get("step_type") or "reasoning",
                "speaker": "professor",
                "stance": "asserted",
                "discourse_role": "verified_timed_sermon_support",
                "assertive": True,
                "proposed_highlight": {"text": excerpt, "status": "verified"},
            }
        anchors = occurrence.setdefault("anchors", [])
        anchors[:] = [row for row in anchors if row.get("evidence_id") != evidence_id]
        anchors.append(anchor)

    result["claims"] = list(claims.values())
    result["source_fragments"] = list(fragments.values())
    result["evidence_steps"] = list(evidence.values())
    result["media_projection"] = {
        "transcript_id": transcript_id,
        "binding_count": len(bindings),
        "method": "unique_source_index_and_verbatim_excerpt",
        "requires_model": False,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", required=True, type=Path)
    parser.add_argument("--transcript", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    result = build_timed_sermon_evidence(
        json.loads(args.knowledge.read_text(encoding="utf-8")),
        args.transcript,
        config["source"],
        config["bindings"],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"output": str(args.output), **result["media_projection"]},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
