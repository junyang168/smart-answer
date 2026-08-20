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

from backend.pipeline.knowledge_source import load_knowledge_source_document


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


def _anchored_evidence_ids(claims: list[dict[str, Any]]) -> set[str]:
    """Every evidence id the live claims still hold an anchor for."""
    return {
        str(anchor.get("evidence_id"))
        for claim in claims
        if not claim.get("superseded_by")
        for occurrence in claim.get("occurrences", [])
        for anchor in occurrence.get("anchors", [])
        if anchor.get("evidence_id")
    }


def _merge_into_survivor(
    *, loser: dict[str, Any], survivor: dict[str, Any], relations: list[dict[str, Any]],
) -> None:
    """Move what the retired claim carried onto the one that stays.

    The loser keeps its own record and is marked superseded rather than
    removed: it is the evidence that a merge happened.  What must not stay
    behind is its grip on the source -- an anchor only the retired claim held
    would drop out of coverage the moment a reader filters superseded claims.
    """

    held = {
        str(anchor.get("evidence_id"))
        for occurrence in survivor.get("occurrences", [])
        for anchor in occurrence.get("anchors", [])
        if anchor.get("evidence_id")
    }
    for occurrence in loser.get("occurrences", []):
        transcript_id = str(occurrence.get("transcript_id") or "")
        target = next(
            (
                item for item in survivor.setdefault("occurrences", [])
                if str(item.get("transcript_id") or "") == transcript_id
            ),
            None,
        )
        if target is None:
            target = {
                "transcript_id": transcript_id,
                "source_id": occurrence.get("source_id"),
                "lecture": occurrence.get("lecture"),
                "anchors": [],
            }
            survivor["occurrences"].append(target)
        for anchor in occurrence.get("anchors", []):
            evidence_id = str(anchor.get("evidence_id") or "")
            if evidence_id and evidence_id in held:
                continue
            if evidence_id:
                held.add(evidence_id)
            target.setdefault("anchors", []).append(deepcopy(anchor))
    survivor_evidence = list(survivor.get("evidence_step_ids") or [])
    for evidence_id in loser.get("evidence_step_ids") or []:
        if evidence_id not in survivor_evidence:
            survivor_evidence.append(evidence_id)
    survivor["evidence_step_ids"] = survivor_evidence
    for name in ("scripture_refs", "topic_terms", "opposed_position_ids"):
        merged = list(survivor.get(name) or [])
        for value in loser.get(name) or []:
            if value not in merged:
                merged.append(value)
        if merged:
            survivor[name] = merged
    survivor_id = survivor["claim_id"]
    loser_id = loser["claim_id"]
    for relation in relations:
        for endpoint in ("from_id", "to_id", "source_id", "target_id"):
            if str(relation.get(endpoint) or "") == loser_id:
                relation[endpoint] = survivor_id


def apply_consensus_overrides(
    package: dict[str, Any], overrides: dict[str, Any], transcripts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    result = deepcopy(package)
    adjudication_fingerprint = overrides.get("adjudication_fingerprint")
    if isinstance(adjudication_fingerprint, dict):
        adjudication_fingerprint = adjudication_fingerprint.get("fingerprint_sha256")
    elif adjudication_fingerprint is not None:
        adjudication_fingerprint = str(adjudication_fingerprint)
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

    anchored_before = _anchored_evidence_ids(result.get("claims", []))
    merges = {
        claim_id: str(patch.get("superseded_by") or "")
        for claim_id, patch in (overrides.get("claims") or {}).items()
        if patch.get("status") == "ai_consensus_applied" and patch.get("superseded_by")
    }
    for claim_id, survivor_id in merges.items():
        survivor = claims.get(survivor_id)
        if survivor is None:
            raise ConsensusApplicationError(f"merge target does not exist: {claim_id} -> {survivor_id}")
        if survivor_id in merges:
            raise ConsensusApplicationError(
                f"merge target is itself merged away: {claim_id} -> {survivor_id}"
            )
        _merge_into_survivor(
            loser=claims[claim_id],
            survivor=survivor,
            relations=result.get("claim_relations", []),
        )
        claims[claim_id]["superseded_by"] = survivor_id
        claims[claim_id]["review_status"] = "superseded"
    if merges:
        # Retargeting can turn an edge between the two merged claims into a
        # self-loop, and can make two edges identical.
        seen: set[tuple[str, str, str]] = set()
        deduped = []
        for relation in result.get("claim_relations", []):
            source = str(relation.get("from_id") or relation.get("source_id") or "")
            target = str(relation.get("to_id") or relation.get("target_id") or "")
            signature = (source, target, str(relation.get("relation_type") or ""))
            if source == target or signature in seen:
                continue
            seen.add(signature)
            deduped.append(relation)
        result["claim_relations"] = deduped
        lost = anchored_before - _anchored_evidence_ids(result.get("claims", []))
        if lost:
            raise ConsensusApplicationError(
                "merge dropped source coverage for evidence: " + ", ".join(sorted(lost))
            )

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
        "adjudication_fingerprint": adjudication_fingerprint,
        "applied_claim_ids": sorted((overrides.get("claims") or {}).keys()),
        "removed_claim_relation_ids": sorted(relations_to_remove),
        "merged_claim_ids": {claim_id: merges[claim_id] for claim_id in sorted(merges)},
        "approval_status": "not_human_approved",
    }
    superseded = [claim for claim in result.get("claims", []) if claim.get("superseded_by")]
    result["summary"] = {
        **result.get("summary", {}),
        "source_fragments_count": len(result.get("source_fragments", [])),
        "evidence_steps_count": len(result.get("evidence_steps", [])),
        "claim_relations_count": len(result.get("claim_relations", [])),
        # `claim_count` still counts every row in the file; a reader asking how
        # many distinct claims survived needs the live number, not the ledger.
        "active_claim_count": len(result.get("claims", [])) - len(superseded),
        "superseded_claim_count": len(superseded),
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
    transcript_dirs = [args.transcript_dir]
    for source in package.get("source_documents", []):
        transcript_id = str(source.get("transcript_id") or "")
        transcript, _, _ = load_knowledge_source_document(source, transcript_dirs)
        transcripts[transcript_id] = transcript
    result = apply_consensus_overrides(package, overrides, transcripts)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["consensus_application"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
