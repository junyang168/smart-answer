"""Apply AI-consensus fidelity corrections to a candidate knowledge package.

The extraction and review artifacts remain immutable audit records.  This
module writes a new candidate package; it never grants human approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.pipeline.corpus_survey_runner import _load


class ConsensusApplicationError(ValueError):
    pass


def _matches_signature(anchor: dict[str, Any], signature: dict[str, Any], transcript_id: str) -> bool:
    highlight = anchor.get("proposed_highlight") or {}
    return (
        str(signature.get("transcript_id") or "") == transcript_id
        and str(signature.get("paragraph_key") or "") == str(anchor.get("paragraph_key") or "")
        and (not signature.get("evidence_id") or signature.get("evidence_id") == anchor.get("evidence_id"))
        and (not signature.get("verbatim_excerpt") or signature.get("verbatim_excerpt") == highlight.get("text"))
    )


def apply_consensus_overrides(
    package: dict[str, Any], overrides: dict[str, Any], transcripts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result = deepcopy(package)
    claims = {row["claim_id"]: row for row in result.get("claims", [])}
    evidence = {row["evidence_step_id"]: row for row in result.get("evidence_steps", [])}
    fragments = result.setdefault("source_fragments", [])
    relations_to_remove: set[str] = set()

    for claim_id, patch in (overrides.get("claims") or {}).items():
        if patch.get("status") != "ai_consensus_applied":
            continue
        claim = claims.get(claim_id)
        if claim is None:
            raise ConsensusApplicationError(f"override references unknown claim: {claim_id}")
        if patch.get("title"):
            claim["title"] = patch["title"]
        if patch.get("claim_type"):
            claim["claim_type"] = patch["claim_type"]
        if patch.get("scripture_refs"):
            claim["scripture_refs"] = patch["scripture_refs"]
        if patch.get("route_type"):
            claim["ai_route_override"] = patch["route_type"]

        excluded = patch.get("excluded_anchors") or []
        removed_evidence_ids: set[str] = set()
        for occurrence in claim.get("occurrences", []):
            transcript_id = str(occurrence.get("transcript_id") or "")
            retained = []
            for anchor in occurrence.get("anchors", []):
                if any(_matches_signature(anchor, signature, transcript_id) for signature in excluded):
                    if anchor.get("evidence_id"):
                        removed_evidence_ids.add(str(anchor["evidence_id"]))
                else:
                    retained.append(anchor)
            occurrence["anchors"] = retained
        still_anchored = {
            str(anchor.get("evidence_id"))
            for occurrence in claim.get("occurrences", [])
            for anchor in occurrence.get("anchors", [])
            if anchor.get("evidence_id")
        }
        claim["evidence_step_ids"] = [
            value for value in claim.get("evidence_step_ids", [])
            if value not in removed_evidence_ids or value in still_anchored
        ]

        for position, addition in enumerate(patch.get("anchor_additions") or [], start=1):
            transcript_id = str(addition.get("transcript_id") or "")
            transcript = transcripts.get(transcript_id)
            if transcript is None:
                raise ConsensusApplicationError(f"missing transcript for anchor addition: {transcript_id}")
            source_index = str(addition.get("source_index") or "")
            matching = [
                (ordinal, segment)
                for ordinal, segment in enumerate(transcript.get("script", []))
                if str(segment.get("index")) == source_index
            ]
            if len(matching) != 1:
                raise ConsensusApplicationError(
                    f"anchor addition source index must resolve exactly once: {transcript_id}:{source_index}"
                )
            ordinal, segment = matching[0]
            excerpt = str(addition.get("verbatim_excerpt") or "")
            paragraph_text = str(segment.get("text") or "")
            if not excerpt or excerpt not in paragraph_text:
                raise ConsensusApplicationError(f"anchor addition is not verbatim: {claim_id}:{source_index}")
            evidence_id = f"AI-ADJ-{claim_id}-{position:02d}"
            fragment_id = f"FR-{evidence_id}"
            source = next(
                item for item in result.get("source_documents", [])
                if item.get("transcript_id") == transcript_id
            )
            fragments.append({
                "fragment_id": fragment_id,
                "source_id": source["source_id"],
                "verbatim_excerpt": excerpt,
                "paragraph_key": f"S{ordinal + 1:04d}",
                "source_segment_index": segment.get("index"),
                "media_time": segment.get("start_time"),
                "media_end_time": segment.get("end_time"),
                "source_sha256": source.get("source_sha256"),
                "paragraph_text_sha256": hashlib.sha256(paragraph_text.encode("utf-8")).hexdigest(),
                "verbatim_excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                "anchor_state": "source_version_bound",
                "review_status": "ai_consensus_candidate",
            })
            evidence_row = {
                "evidence_step_id": evidence_id,
                "statement": excerpt,
                "step_type": addition.get("evidence_type") or "reasoning",
                "speaker": "professor",
                "stance": "asserted",
                "discourse_role": "ai_consensus_source_addition",
                "support_eligibility": "eligible_candidate",
                "scripture_refs": [],
                "produced_claim_ids": [claim_id],
                "source_fragment_ids": [fragment_id],
                "review_status": "ai_consensus_candidate",
            }
            result.setdefault("evidence_steps", []).append(evidence_row)
            evidence[evidence_id] = evidence_row
            claim.setdefault("evidence_step_ids", []).append(evidence_id)
            occurrence = next(
                (item for item in claim.get("occurrences", []) if item.get("transcript_id") == transcript_id),
                None,
            )
            if occurrence is None:
                occurrence = {"transcript_id": transcript_id, "lecture": source.get("title"), "anchors": []}
                claim.setdefault("occurrences", []).append(occurrence)
            occurrence.setdefault("anchors", []).append({
                "paragraph_key": f"S{ordinal + 1:04d}",
                "media_time": segment.get("start_time"),
                "evidence_id": evidence_id,
                "evidence_type": evidence_row["step_type"],
                "speaker": "professor",
                "stance": "asserted",
                "discourse_role": "ai_consensus_source_addition",
                "assertive": True,
                "proposed_highlight": {"text": excerpt, "status": "ai_consensus_candidate"},
            })

        relations_to_remove.update(patch.get("excluded_claim_relation_ids") or [])
        claim["ai_adjudication"] = {
            "status": patch.get("status"),
            "approval_status": patch.get("approval_status"),
            "fingerprint": patch.get("adjudication_fingerprint"),
            "structural_notes": patch.get("structural_notes") or [],
        }

    known_relations = {
        str(row.get("claim_relation_id") or row.get("relation_id") or "")
        for row in result.get("claim_relations", [])
    }
    unknown = relations_to_remove - known_relations
    if unknown:
        raise ConsensusApplicationError("unknown relations in overrides: " + ", ".join(sorted(unknown)))
    result["claim_relations"] = [
        row for row in result.get("claim_relations", [])
        if str(row.get("claim_relation_id") or row.get("relation_id") or "") not in relations_to_remove
    ]

    for claim in result.get("claims", []):
        if not claim.get("evidence_step_ids"):
            raise ConsensusApplicationError(f"consensus application left claim without evidence: {claim['claim_id']}")
        missing = set(claim["evidence_step_ids"]) - set(evidence)
        if missing:
            raise ConsensusApplicationError(f"claim references missing evidence: {claim['claim_id']}:{sorted(missing)}")
    result["consensus_application"] = {
        "schema_version": "wang_ai_consensus_application_v1",
        "adjudication_fingerprint": (overrides.get("adjudication_fingerprint") or {}).get("fingerprint_sha256"),
        "applied_claim_ids": sorted((overrides.get("claims") or {}).keys()),
        "removed_claim_relation_ids": sorted(relations_to_remove),
        "approval_status": "not_human_approved",
    }
    result["summary"] = {
        **result.get("summary", {}),
        "source_fragment_count": len(result.get("source_fragments", [])),
        "evidence_step_count": len(result.get("evidence_steps", [])),
        "claim_relation_count": len(result.get("claim_relations", [])),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--transcript-dir", type=Path,
        default=Path("/opt/homebrew/var/www/church/web/data/script_published"),
    )
    args = parser.parse_args()
    package = json.loads(args.package.read_text(encoding="utf-8"))
    overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
    transcripts = {}
    for source in package.get("source_documents", []):
        transcript_id = str(source.get("transcript_id") or "")
        transcript, _ = _load(args.transcript_dir / f"{transcript_id}.json")
        transcripts[transcript_id] = transcript
    result = apply_consensus_overrides(package, overrides, transcripts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["consensus_application"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
