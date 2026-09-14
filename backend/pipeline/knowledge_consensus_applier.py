"""Apply AI-consensus fidelity corrections to a candidate knowledge package.

The extraction and review artifacts remain immutable audit records.  This
module writes a new candidate package; it never grants human approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    validate_merged_package,
)
from backend.pipeline.knowledge_source import load_knowledge_source_document
from backend.pipeline.run_ledger import run_record
from backend.pipeline.sentence_ledger_runner import coverage_for_package
from backend.pipeline.source_keys import package_row_key
from backend.pipeline.source_projection import (
    assert_locator_space_compatible,
    excerpt_overlaps_inline_markup,
    project_script,
)
from backend.pipeline.corpus_ai_review_runner import _validate_claim_layer_package
from backend.pipeline.corpus_ai_adjudication import actionable_reviews
from backend.pipeline.corpus_ai_adjudication_runner import (
    _adjudication_artifact_sha256,
    _overrides_artifact_sha256,
    _valid_adjudication_artifact,
    _valid_overrides_artifact,
    _validated_review_context,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    AI_REVIEW_PROVENANCE_VERSION,
    CONSENSUS_APPLICATION_VERSION,
    SOURCE_SCOPED,
    ConsensusApplicationError,
    reviewed_candidate_artifact_sha256,
    validate_reviewed_candidate_artifact,
)


def apply_final_ai_review_outcomes(
    package: dict[str, Any],
    *,
    review: dict[str, Any],
    review_artifact_sha256: str,
    adjudication: dict[str, Any],
    adjudication_artifact_sha256: str,
    overrides_artifact_sha256: str,
) -> None:
    """Put the post-adjudication verdict on every claim, with its audit chain."""

    review_rows = list(review.get("claim_reviews") or [])
    result_rows = list(adjudication.get("results") or [])
    reviews = {
        str(row.get("claim_id") or ""): row
        for row in review_rows
    }
    results = {
        str(row.get("claim_id") or ""): row
        for row in result_rows
    }
    claim_id_rows = [
        str(row.get("claim_id") or "") for row in package.get("claims") or []
    ]
    claim_ids = set(claim_id_rows)
    if (
        not all(claim_id_rows)
        or len(claim_id_rows) != len(claim_ids)
        or len(reviews) != len(review_rows)
        or "" in reviews
        or set(reviews) != claim_ids
    ):
        raise ConsensusApplicationError(
            "final review must cover every package claim exactly once"
        )
    actionable_ids = {
        claim_id
        for claim_id, row in reviews.items()
        if row.get("decision") != "pass"
    }
    if (
        len(results) != len(result_rows)
        or "" in results
        or set(results) != actionable_ids
    ):
        raise ConsensusApplicationError(
            "adjudication outcomes must cover every non-pass review exactly once"
        )
    allowed_decisions = {"pass", "changes_suggested", "human_review_required"}
    if any(
        str(row.get("decision") or "") not in allowed_decisions
        for row in reviews.values()
    ):
        raise ConsensusApplicationError("review contains an unknown decision")
    allowed_outcomes = {
        "auto_applied",
        "withdrawn",
        "human_confirmation_required",
        "human_disagreement_required",
    }
    if any(str(row.get("status") or "") not in allowed_outcomes for row in results.values()):
        raise ConsensusApplicationError("adjudication contains an unknown final outcome")

    reviewer = review.get("reviewer") or {}
    adjudicator = adjudication.get("adjudicator") or {}
    review_fingerprint = str(reviewer.get("fingerprint_sha256") or "")
    adjudication_fingerprint = str(adjudicator.get("fingerprint_sha256") or "")
    application = package.get("consensus_application") or {}
    if (
        not review_fingerprint
        or not adjudication_fingerprint
        or str(adjudicator.get("review_fingerprint") or "")
        != review_fingerprint
        or str(application.get("adjudication_fingerprint") or "")
        != adjudication_fingerprint
    ):
        raise ConsensusApplicationError(
            "final review is not bound to the applied adjudication chain"
        )
    auto_applied_ids = {
        claim_id
        for claim_id, row in results.items()
        if row.get("status") == "auto_applied"
    }
    if set(application.get("applied_claim_ids") or []) != auto_applied_ids:
        raise ConsensusApplicationError(
            "applied overrides do not exactly match auto-applied adjudication outcomes"
        )
    reviewed_at = str(adjudicator.get("generated_at") or "")
    if not reviewed_at:
        raise ConsensusApplicationError("adjudication lacks deterministic review time")
    batch_models = {
        str(((row.get("reviewer") or {}).get("review_model_id") or "")).strip()
        for row in ((review.get("review_strategy") or {}).get("reviewer_batches") or [])
        if isinstance(row, dict)
    }
    batch_models.discard("")
    reviewer_id = "+".join(sorted(batch_models)) or str(
        reviewer.get("model")
        or reviewer.get("model_id")
        or reviewer.get("provider")
        or "independent_ai_review"
    )
    adjudicator_id = str(adjudicator.get("openai_model") or "")

    status_counts: dict[str, int] = {}
    resolutions: list[dict[str, Any]] = []
    for claim in package.get("claims") or []:
        claim_id = str(claim["claim_id"])
        row = reviews[claim_id]
        decision = str(row.get("decision") or "")
        if decision == "pass":
            outcome = "human_spot_check" if row.get("spot_check_selected") else "not_required"
            target = (
                "human_review_required"
                if row.get("spot_check_selected")
                else "ai_consensus_reviewed"
            )
        else:
            outcome = str(results[claim_id]["status"])
            target = (
                "ai_consensus_reviewed"
                if outcome in {"auto_applied", "withdrawn"}
                else "human_review_required"
            )
        if claim.get("superseded_by"):
            target = "superseded"
        status_counts[target] = status_counts.get(target, 0) + 1
        reason = f"独立 AI 复审：{decision}；仲裁：{outcome}"
        resolution_reviewer_id = (
            f"{reviewer_id}+{adjudicator_id}"
            if decision != "pass" and adjudicator_id
            else reviewer_id
        )
        resolutions.append({
            "schema_version": AI_REVIEW_PROVENANCE_VERSION,
            "claim_id": claim_id,
            "independent_review_decision": decision,
            "adjudication_status": outcome,
            "target_review_status": target,
            "reviewer_id": resolution_reviewer_id,
            "reason": reason,
            "approval_status": "not_human_approved",
        })
        if not claim.get("superseded_by"):
            claim["review_status"] = target
            claim["reviewed_by"] = resolution_reviewer_id
            claim["reviewed_at"] = reviewed_at
            claim["review_note"] = reason

    application.update(
        {
            "review_completion": "complete",
            "review_artifact_sha256": review_artifact_sha256,
            "review_fingerprint": review_fingerprint,
            "adjudication_artifact_sha256": adjudication_artifact_sha256,
            "overrides_artifact_sha256": overrides_artifact_sha256,
            "final_review_status_counts": dict(sorted(status_counts.items())),
            "review_resolutions": resolutions,
        }
    )
    application["artifact_sha256"] = reviewed_candidate_artifact_sha256(package)
    validate_reviewed_candidate_artifact(package)


def _validate_overrides_artifact(
    overrides: dict[str, Any], *, package_sha256: str
) -> None:
    if overrides.get("artifact_sha256") != _overrides_artifact_sha256(overrides):
        raise ConsensusApplicationError(
            "consensus overrides artifact is incomplete or was modified"
        )
    fingerprint = overrides.get("adjudication_fingerprint")
    if not isinstance(fingerprint, dict):
        raise ConsensusApplicationError(
            "consensus overrides lack a package-bound adjudication fingerprint"
        )
    if fingerprint.get("source_package_sha256") != package_sha256:
        raise ConsensusApplicationError(
            "consensus overrides were adjudicated against a different package"
        )


def _serialized(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _atomic_write(path: Path, content: bytes) -> None:
    """Install one complete derived artifact, never a partially written JSON file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _archive_previous(path: Path) -> None:
    if not path.is_file():
        return
    content = path.read_bytes()
    fingerprint = hashlib.sha256(content).hexdigest()[:16]
    archive = path.parent / "consensus-generations" / f"{path.stem}.{fingerprint}.json"
    if not archive.exists():
        _atomic_write(archive, content)


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
    *,
    loser: dict[str, Any],
    survivor: dict[str, Any],
    evidence: dict[str, dict[str, Any]],
    relations: list[dict[str, Any]],
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
        evidence_step = evidence.get(str(evidence_id))
        if evidence_step is None:
            raise ConsensusApplicationError(
                f"merge references missing evidence: {loser['claim_id']}:{evidence_id}"
            )
        produced = list(evidence_step.get("produced_claim_ids") or [])
        if survivor["claim_id"] not in produced:
            produced.append(survivor["claim_id"])
        evidence_step["produced_claim_ids"] = produced
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
        for signature in excluded:
            matches = [
                anchor
                for occurrence in claim.get("occurrences", [])
                for anchor in occurrence.get("anchors", [])
                if _matches_signature(
                    anchor,
                    signature,
                    str(occurrence.get("transcript_id") or ""),
                )
            ]
            if len(matches) != 1:
                raise ConsensusApplicationError(
                    "excluded anchor must resolve exactly once: "
                    f"{claim_id}:{signature!r}"
                )
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
        previous_evidence_ids = list(claim.get("evidence_step_ids", []))
        claim["evidence_step_ids"] = [
            value for value in claim.get("evidence_step_ids", [])
            if value not in removed_evidence_ids or value in still_anchored
        ]
        removed_links = set(previous_evidence_ids) - set(claim["evidence_step_ids"])
        for evidence_id in removed_links:
            evidence_step = evidence.get(str(evidence_id))
            if evidence_step is None:
                raise ConsensusApplicationError(
                    f"claim references missing evidence: {claim_id}:{evidence_id}"
                )
            evidence_step["produced_claim_ids"] = [
                value
                for value in evidence_step.get("produced_claim_ids") or []
                if str(value) != claim_id
            ]

        for position, addition in enumerate(patch.get("anchor_additions") or [], start=1):
            transcript_id = str(addition.get("transcript_id") or "")
            transcript = transcripts.get(transcript_id)
            if transcript is None:
                raise ConsensusApplicationError(f"missing transcript for anchor addition: {transcript_id}")
            source = next(
                item for item in result.get("source_documents", [])
                if item.get("transcript_id") == transcript_id
            )
            try:
                assert_locator_space_compatible(source, transcript.get("script", []))
            except ValueError as exc:
                raise ConsensusApplicationError(
                    f"ambiguous source locators for {transcript_id}: {exc}"
                ) from exc
            source_index = str(addition.get("source_index") or "")
            source_rows = project_script(transcript.get("script", [])).body_rows
            matching = [
                (ordinal, segment)
                for ordinal, segment in enumerate(source_rows)
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
            if excerpt_overlaps_inline_markup(paragraph_text, excerpt):
                raise ConsensusApplicationError(
                    f"anchor addition overlaps non-spoken inline structure: "
                    f"{claim_id}:{source_index}; visual evidence must come from extraction"
                )
            evidence_id = f"AI-ADJ-{claim_id}-{position:02d}"
            fragment_id = f"FR-{evidence_id}"
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
        # A package can arrive already merged.  Retiring into a claim that an
        # earlier round retired takes both out of the live set at once, and the
        # coverage guard below would then report it as lost source coverage --
        # true, but it names the symptom instead of the override that caused it.
        if survivor.get("superseded_by"):
            raise ConsensusApplicationError(
                f"merge target was already superseded by "
                f"{survivor['superseded_by']}: {claim_id} -> {survivor_id}"
            )
        _merge_into_survivor(
            loser=claims[claim_id],
            survivor=survivor,
            evidence=evidence,
            relations=result.get("claim_relations", []),
        )
        claims[claim_id]["superseded_by"] = survivor_id
        claims[claim_id]["review_status"] = "superseded"
    # Relations the merge itself removed.  An override may name one of these in
    # `excluded_claim_relation_ids` -- Claude flags a wrong edge between two
    # claims and their duplication in the same review -- and "already gone" has
    # to satisfy "remove this", not fail the whole application as unknown.
    dissolved_relation_ids: set[str] = set()
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
                dissolved_relation_ids.add(
                    str(relation.get("claim_relation_id") or relation.get("relation_id") or "")
                )
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
    } | dissolved_relation_ids
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
        "schema_version": CONSENSUS_APPLICATION_VERSION,
        "scope_kind": SOURCE_SCOPED,
        "adjudication_fingerprint": adjudication_fingerprint,
        "applied_claim_ids": sorted((overrides.get("claims") or {}).keys()),
        "removed_claim_relation_ids": sorted(relations_to_remove),
        # Edges the merge itself dissolved -- a self-loop or a now-identical
        # twin.  Recorded separately so a relation that vanished without an
        # override asking for it is still answerable from the artifact.
        "dissolved_claim_relation_ids": sorted(item for item in dissolved_relation_ids if item),
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
    source_documents = result.get("source_documents") or []
    if "coverage" in result:
        if len(source_documents) != 1:
            raise ConsensusApplicationError(
                "a package-level coverage report requires exactly one source document"
            )
        source_document = source_documents[0]
        transcript_id = str(source_document.get("transcript_id") or "")
        transcript = transcripts.get(transcript_id)
        if transcript is None:
            raise ConsensusApplicationError(
                f"missing transcript for final coverage: {transcript_id}"
            )
        try:
            result["coverage"] = {
                "available": True,
                **coverage_for_package(
                    source_document=source_document,
                    script=list(transcript.get("script") or []),
                    package=result,
                    source_file_sha256=str(
                        source_document.get("source_file_sha256")
                        or source_document.get("source_sha256")
                        or ""
                    ),
                    reconciled_against=(
                        f"consensus:{adjudication_fingerprint or 'unknown'}"
                    ),
                ),
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise ConsensusApplicationError(
                f"could not recompute final package coverage: {exc}"
            ) from exc
    # Consensus can add evidence/fragments and retarget or remove relations.
    # Revalidate the whole graph before it becomes a current artifact: local
    # Claim checks cannot detect a generated ID colliding with another
    # collection or a dangling endpoint introduced by a future override type.
    try:
        validate_merged_package(result)
    except KnowledgePackageMergeError as exc:
        raise ConsensusApplicationError(
            f"consensus result violates package integrity: {exc}"
        ) from exc
    result["consensus_application"]["artifact_sha256"] = (
        reviewed_candidate_artifact_sha256(result)
    )
    validate_reviewed_candidate_artifact(result, require_review_completion=False)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review", type=Path, required=True)
    parser.add_argument("--adjudication", type=Path, required=True)
    parser.add_argument(
        "--transcript-dir", type=Path,
        default=Path("/opt/homebrew/var/www/church/web/data/script_published"),
    )
    args = parser.parse_args()
    package_bytes = args.package.read_bytes()
    package = json.loads(package_bytes)
    _validate_claim_layer_package(package)
    review = None
    review_bytes = b""
    adjudication = None
    adjudication_bytes = b""
    loaded_transcripts: list[tuple[str, dict[str, Any]]] = []
    if args.review and args.adjudication:
        (
            _survey,
            claims_by_id,
            loaded_transcripts,
            transcript_segments,
            review,
            review_bytes,
        ) = _validated_review_context(args.package, args.review, [args.transcript_dir])
        adjudication_bytes = args.adjudication.read_bytes()
        adjudication = json.loads(adjudication_bytes.decode("utf-8"))
        adjudicator = adjudication.get("adjudicator") or {}
        actionable = actionable_reviews(review)
        if (
            adjudicator.get("source_package_sha256")
            != hashlib.sha256(package_bytes).hexdigest()
            or adjudicator.get("review_artifact_sha256")
            != hashlib.sha256(review_bytes).hexdigest()
            or not _valid_adjudication_artifact(
                adjudication,
                expected_fingerprint=str(adjudicator.get("fingerprint_sha256") or ""),
                reviews=actionable,
                claims_by_id=claims_by_id,
                transcript_segments=transcript_segments,
            )
        ):
            raise ConsensusApplicationError(
                "adjudication artifact is incomplete, modified, or bound to another review"
            )
    overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
    _validate_overrides_artifact(
        overrides, package_sha256=hashlib.sha256(package_bytes).hexdigest()
    )
    if adjudication is not None and not _valid_overrides_artifact(
        overrides,
        outcome=adjudication,
        claims_by_id=claims_by_id,
        fingerprint=adjudication["adjudicator"],
    ):
        raise ConsensusApplicationError(
            "consensus overrides do not exactly match this adjudication"
        )
    transcripts = {
        transcript_id: transcript
        for transcript_id, transcript in loaded_transcripts
    }
    transcript_dirs = [args.transcript_dir]
    for source in package.get("source_documents", []):
        transcript_id = str(source.get("transcript_id") or "")
        if transcript_id not in transcripts:
            transcript, _, _ = load_knowledge_source_document(source, transcript_dirs)
            transcripts[transcript_id] = transcript
    subject = package_row_key(package) or args.package.name
    result = apply_consensus_overrides(package, overrides, transcripts)
    if review is not None and adjudication is not None:
        apply_final_ai_review_outcomes(
            result,
            review=review,
            review_artifact_sha256=hashlib.sha256(review_bytes).hexdigest(),
            adjudication=adjudication,
            adjudication_artifact_sha256=hashlib.sha256(adjudication_bytes).hexdigest(),
            overrides_artifact_sha256=hashlib.sha256(
                args.overrides.read_bytes()
            ).hexdigest(),
        )
    encoded = _serialized(result)
    # The output bytes are the complete deterministic function of package,
    # overrides, and the SHA-validated transcripts above. Check before opening
    # a run record: replaying the exact stage must write neither the artifact
    # nor a second ledger row.
    if args.output.is_file() and args.output.read_bytes() == encoded:
        print(json.dumps({
            "status": "skipped",
            "reason": "matching consensus application",
            "adjudication_fingerprint": (
                result.get("consensus_application") or {}
            ).get("adjudication_fingerprint"),
        }, ensure_ascii=False))
        return 0
    # No model call here, so the row costs nothing and says the one thing the
    # overview could not: whether the adjudicator's overrides were ever applied.
    # One source reached the store from its raw extraction package because
    # nobody could see that this stage had not run for it.
    # `merge` and not `apply`: this artifact is what the overview's 合併 column
    # has always meant -- `run_ledger_backfill` maps both `.reviewed-candidate`
    # and `.consensus-applied` to it. Filing a stage name the dashboard has no
    # column for meant the work was done, the row was written, and the cell
    # still read ✗. A stage name is part of the user-facing contract, not an
    # internal label.
    with run_record(subject=subject, stage="merge") as record:
        record.input_artifacts(args.package, args.review, args.adjudication, args.overrides)
        record.inputs({
            "package_sha256": hashlib.sha256(args.package.read_bytes()).hexdigest(),
            "review_sha256": hashlib.sha256(review_bytes).hexdigest(),
            "adjudication_sha256": hashlib.sha256(adjudication_bytes).hexdigest(),
            "overrides_sha256": hashlib.sha256(args.overrides.read_bytes()).hexdigest(),
        })
        _archive_previous(args.output)
        _atomic_write(args.output, encoded)
        record.quality(result["consensus_application"])
        record.outputs(args.output)
    print(json.dumps(result["consensus_application"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
