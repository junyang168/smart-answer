"""Read-only, fail-closed inventory for legacy Claim review reconciliation.

Old ``reviewed-candidate`` files predate the sealed review contract.  This
module deliberately does not turn their presence into an approval: it checks
the original review input, the current source bytes, and the exact current
Claim payload before reporting a possible migration candidate.  It never
updates PostgreSQL or writes into the staging artifact tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    ChangeOperation,
    ChangeSetPlan,
    PlannedReviewEvent,
    _normalize_records,
    _substantive_payload,
    operation_fingerprint_rows,
    record_content_sha,
    review_event_fingerprint_rows,
    sha256_json,
    validate_change_set_plan_integrity,
)
from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.knowledge_source import markdown_source_document
from backend.pipeline.corpus_ai_review import validate_review_response
from backend.pipeline.corpus_ai_review_runner import (
    _normalize_claim_layer,
    _review_artifact_sha256,
)
from backend.pipeline.corpus_ai_adjudication import (
    actionable_reviews,
    compile_outcome,
    validate_openai_adjudication,
    validate_claude_reconsideration,
)
from backend.pipeline.corpus_ai_adjudication_runner import (
    _adjudication_artifact_sha256,
    _transcript_segments,
)
from backend.pipeline.corpus_ai_review import apply_risk_routing
from backend.pipeline.corpus_survey_runner import _transcript_for_prompt


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _related(path: Path, suffix: str) -> Path:
    name = path.name.removesuffix(".reviewed-candidate.json") + suffix
    direct = path.with_name(name)
    if direct.is_file():
        return direct
    # Research batches keep reviewed, extraction and review files in sibling
    # directories.  Match the exact stem within this batch only.
    matches = sorted(candidate for candidate in path.parent.parent.glob(f"*/{name}") if candidate.is_file())
    return matches[0] if len(matches) == 1 else direct


def review_source_binding_mode(
    reviewed_hash: str, source_bytes: bytes, payload: Mapping[str, Any]
) -> str | None:
    """Accept both historical raw-file and current rendered-input fingerprints.

    Review runner 1091f4b (2026-09-10) changed ``transcript_sha256`` from
    physical bytes to the exact prompt projection. Comparing it only to a
    SourceDocument's file SHA falsely reports source drift for new reviews.
    """

    if reviewed_hash == hashlib.sha256(source_bytes).hexdigest():
        return "file_bytes"
    rendered = _transcript_for_prompt(
        dict(payload), visual_source_attested=True
    ).encode("utf-8")
    if reviewed_hash == hashlib.sha256(rendered).hexdigest():
        return "rendered_review_input"
    return None


def inspect_legacy_bundle(path: Path) -> dict[str, Any]:
    """Return evidence for one bundle; failures never become positive evidence."""

    reviewed = _read(path)
    normalized, _ = _normalize_records(reviewed)
    claims = normalized.get("claims", {})
    sources = normalized.get("source_documents", {})
    result: dict[str, Any] = {
        "reviewed_path": str(path),
        "reviewed_sha256": _sha(path),
        "claims": claims,
        "sources": sources,
        "reason": None,
    }
    review_path = _related(path, ".independent-review.json")
    if not review_path.is_file():
        result["reason"] = "missing_upstream_artifact"
        return result
    try:
        review = _read(review_path)
        stated_package_sha = str((review.get("source") or {}).get("package_sha256") or "")
        stem = path.name.removesuffix(".reviewed-candidate.json")
        candidates = {
            path.parent / f"{stem}.detailed-knowledge.json",
            path.parent.parent / "detailed-extractions" / f"{stem}.detailed-knowledge.json",
            path.parent.parent / "cross-section" / f"{stem}.cross-section.json",
        }
        named = str((review.get("source") or {}).get("package_path") or "")
        if named and Path(named).is_absolute():
            candidates.add(Path(named))
        matching = sorted(p for p in candidates if p.is_file() and _sha(p) == stated_package_sha)
        if len(matching) != 1:
            result["reason"] = "review_package_missing_or_ambiguous"
            return result
        package_path = matching[0]
        package = _read(package_path)
        package_sha = _sha(package_path)
        if str((review.get("source") or {}).get("package_sha256") or "") != package_sha:
            result["reason"] = "review_package_sha_mismatch"
            return result
        survey = _normalize_claim_layer(package)
        if review.get("reviewed_claims") != survey.get("candidate_claims"):
            result["reason"] = "review_claim_snapshot_mismatch"
            return result
        validate_review_response(review, survey)
        reviewer = review.get("reviewer") or {}
        reviewer_identity = {
            key: value for key, value in reviewer.items()
            if key not in {"fingerprint_sha256", "generated_at", "artifact_sha256", "provider"}
        }
        review_fingerprint = str(reviewer.get("fingerprint_sha256") or "")
        if review_fingerprint != sha256_json(reviewer_identity):
            result["reason"] = "review_fingerprint_invalid"
            return result
        review_seal = reviewer.get("artifact_sha256")
        if review_seal is not None and review_seal != _review_artifact_sha256(review):
            result["reason"] = "review_artifact_seal_invalid"
            return result
        routed = apply_risk_routing(
            {"claim_reviews": review["claim_reviews"]},
            reviewer_fingerprint_sha256=review_fingerprint,
            spot_check_percent=int(review["spot_check_percent"]),
        )
        if (review.get("claim_reviews") != routed["claim_reviews"]
                or review.get("routing_summary") != routed["routing_summary"]):
            result["reason"] = "review_routing_invalid"
            return result
        review_rows = review.get("claim_reviews") or []
        ids = [str(row.get("claim_id") or "") for row in review_rows]
        if not ids or len(ids) != len(set(ids)) or "" in ids:
            result["reason"] = "review_claim_ids_invalid"
            return result
        result["review_rows"] = {str(row["claim_id"]): row for row in review_rows}
        result["review_path"] = str(review_path)
        result["review_sha256"] = _sha(review_path)
        result["review_transcript_sha256"] = dict(
            (review.get("source") or {}).get("transcript_sha256") or {}
        )
        result["review_transcript_paths"] = dict(
            (review.get("source") or {}).get("transcript_paths") or {}
        )
        result["review_fingerprint"] = str(
            (review.get("reviewer") or {}).get("fingerprint_sha256") or ""
        )
        result["reviewer_id"] = str(
            (review.get("reviewer") or {}).get("review_model_id")
            or (review.get("reviewer") or {}).get("model_id")
            or "independent_ai_review"
        )
        if not result["review_fingerprint"]:
            result["reason"] = "review_fingerprint_missing"
            return result
        result["package_path"] = str(package_path)
        result["package_sha256"] = package_sha
        package_claims, _ = _normalize_records(package)
        result["original_claims"] = package_claims.get("claims", {})
        adjudication_paths = [
            _related(path, ".ai-adjudication.json"),
            _related(path, ".adjudication.json"),
        ]
        adjudication_paths = list(dict.fromkeys(p for p in adjudication_paths if p.is_file()))
        if len(adjudication_paths) != 1:
            result["reason"] = "adjudication_missing_or_ambiguous"
            return result
        adjudication_path = adjudication_paths[0]
        adjudication = _read(adjudication_path)
        adjudicator = adjudication.get("adjudicator") or {}
        adjudication_fingerprint = str(adjudicator.get("fingerprint_sha256") or "")
        adjudicator_identity = {
            key: value for key, value in adjudicator.items()
            if key not in {"fingerprint_sha256", "generated_at", "artifact_sha256"}
        }
        if (
            not adjudication_fingerprint
            or adjudication_fingerprint != sha256_json(adjudicator_identity)
            or adjudicator.get("review_fingerprint") != review_fingerprint
            or (reviewed.get("consensus_application") or {}).get("adjudication_fingerprint")
            != adjudication_fingerprint
        ):
            result["reason"] = "adjudication_fingerprint_invalid"
            return result
        adjudication_seal = adjudicator.get("artifact_sha256")
        if adjudication_seal is not None and adjudication_seal != _adjudication_artifact_sha256(adjudication):
            result["reason"] = "adjudication_artifact_seal_invalid"
            return result
        expected_outcome = compile_outcome(
            adjudication["openai_adjudication"],
            adjudication.get("claude_reconsideration"),
            reviews=actionable_reviews(review),
        )
        if any(adjudication.get(key) != value for key, value in expected_outcome.items()):
            result["reason"] = "adjudication_outcome_invalid"
            return result
        source_paths = (review.get("source") or {}).get("transcript_paths") or {}
        reviewed_source_hashes = (review.get("source") or {}).get("transcript_sha256") or {}
        transcript_segments = {}
        current_source_matches = True
        source_binding_modes: dict[str, str] = {}
        for source in package.get("source_documents") or []:
            source_id = str(source.get("source_id") or "")
            transcript_id = str(source.get("transcript_id") or source_id)
            source_path = source_paths.get(source_id)
            if not source_path or not Path(source_path).is_file():
                current_source_matches = False
                break
            path_obj = Path(source_path)
            if source.get("source_type") == "notes_manuscript":
                payload, _, _ = markdown_source_document(source)
            else:
                parsed = json.loads(path_obj.read_text(encoding="utf-8"))
                payload = {"script": parsed} if isinstance(parsed, list) else parsed
                if not isinstance(payload, dict):
                    raise ValueError("source JSON is neither object nor array")
            reviewed_hash = str(reviewed_source_hashes.get(source_id) or "")
            binding_mode = review_source_binding_mode(
                reviewed_hash, path_obj.read_bytes(), payload
            )
            if binding_mode is None:
                current_source_matches = False
                break
            source_binding_modes[source_id] = binding_mode
            transcript_segments[transcript_id] = _transcript_segments(payload)
        if current_source_matches:
            claim_snapshots = {
                str(row["claim_id"]): row for row in survey["candidate_claims"]
            }
            actionable = actionable_reviews(review)
            validate_openai_adjudication(
                adjudication["openai_adjudication"], reviews=actionable,
                claims_by_id=claim_snapshots,
                transcript_segments=transcript_segments,
            )
            reconsideration = adjudication.get("claude_reconsideration")
            rejected_ids = {
                str(row["claim_id"])
                for row in adjudication["openai_adjudication"]["adjudications"]
                if row.get("decision") == "reject"
            }
            if rejected_ids:
                validate_claude_reconsideration(
                    reconsideration, rejected_claim_ids=rejected_ids,
                    claims_by_id=claim_snapshots,
                )
            elif reconsideration is not None:
                result["reason"] = "unexpected_claude_reconsideration"
                return result
        result["adjudication_structurally_validated"] = current_source_matches
        result["review_source_binding_modes"] = source_binding_modes
        result["adjudication_path"] = str(adjudication_path)
        result["adjudication_sha256"] = _sha(adjudication_path)
        result["adjudicated_at"] = str(adjudicator.get("generated_at") or "")
        result["adjudicator_id"] = str(adjudicator.get("openai_model") or "")
        result["adjudication_results"] = {
            str(row["claim_id"]): row for row in adjudication.get("results") or []
        }
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["reason"] = f"review_validation_failed:{type(exc).__name__}"
        result["detail"] = str(exc)[:300]
    return result


def classify_candidate(
    claim: Mapping[str, Any],
    bundles: list[Mapping[str, Any]],
    source_rows: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    claim_id = str(claim["object_id"])
    current = claim["payload"]
    common = {
        "claim_id": claim_id,
        "revision": int(claim["revision"]),
        "content_sha256": str(claim["content_sha256"]),
    }
    if not bundles:
        return {**common, "reason": "missing_reviewed_candidate"}
    viable: list[dict[str, Any]] = []
    failures: list[str] = []
    failure_details: list[str] = []
    for bundle in bundles:
        if bundle.get("reason"):
            failures.append(str(bundle["reason"]))
            if bundle.get("detail"):
                failure_details.append(str(bundle["detail"]))
            continue
        prior = bundle["claims"][claim_id]
        if _substantive_payload(prior) != _substantive_payload(current):
            failures.append("claim_payload_changed")
            continue
        sources = bundle["sources"]
        if len(sources) != 1:
            failures.append("source_binding_not_unique")
            continue
        source_id, old_source = next(iter(sources.items()))
        live_source = source_rows.get(source_id)
        if live_source is None:
            failures.append("source_missing_or_retired")
            continue
        old_sha = str(old_source.get("source_sha256") or "")
        current_source = live_source["payload"]
        # Legacy source_sha256 named either raw file or canonical body bytes.
        if not old_sha or old_sha not in {
            str(current_source.get("source_file_sha256") or ""),
            str(current_source.get("source_body_sha256") or ""),
        }:
            failures.append("source_generation_changed")
            continue
        reviewed_transcripts = bundle["review_transcript_sha256"]
        if (
            len(reviewed_transcripts) != 1
            or bundle["review_source_binding_modes"].get(source_id)
            not in {"file_bytes", "rendered_review_input"}
        ):
            failures.append("reviewed_source_input_changed")
            continue
        if not bundle["adjudication_structurally_validated"]:
            failures.append("adjudication_validation_unavailable")
            continue
        source_file_paths = bundle["review_transcript_paths"]
        if (
            len(source_file_paths) != 1
            or not Path(next(iter(source_file_paths.values()))).is_file()
            or _sha(Path(next(iter(source_file_paths.values()))))
            != current_source.get("source_file_sha256")
        ):
            failures.append("source_file_missing_or_changed")
            continue
        review_row = bundle["review_rows"].get(claim_id)
        if review_row is None:
            failures.append("review_claim_missing")
            continue
        original = bundle["original_claims"].get(claim_id)
        original_matches = original is not None and _substantive_payload(original) == _substantive_payload(prior)
        decision = str(review_row.get("decision") or "")
        spot_check = bool(review_row.get("spot_check_selected"))
        adjudication = bundle["adjudication_results"].get(claim_id)
        if decision == "pass" and not spot_check and original_matches:
            reason = "pass_ready_for_legacy_migration"
            target_status = "ai_consensus_reviewed"
            adjudication_status = "not_required"
        elif decision == "pass" and spot_check and original_matches:
            reason = "human_spot_check_required"
            target_status = "human_review_required"
            adjudication_status = "human_spot_check"
        elif original_matches and adjudication and adjudication.get("status") in {
            "human_confirmation_required", "human_disagreement_required"
        }:
            reason = str(adjudication["status"])
            target_status = "human_review_required"
            adjudication_status = reason
        else:
            reason = "legacy_adjudicated_claim_needs_patch_replay"
            target_status = None
            adjudication_status = str((adjudication or {}).get("status") or "")
        viable.append({
            **common,
            "reason": reason,
            "target_review_status": target_status,
            "adjudication_status": adjudication_status,
            "source_id": source_id,
            "source_revision": int(live_source["revision"]),
            "source_content_sha256": str(live_source["content_sha256"]),
            "source_file_path": str(next(iter(source_file_paths.values()))),
            "source_file_sha256": str(current_source["source_file_sha256"]),
            "review_decision": decision,
            "spot_check_selected": spot_check,
            "package_sha256": bundle["package_sha256"],
            "review_sha256": bundle["review_sha256"],
            "review_fingerprint": bundle["review_fingerprint"],
            "review_source_binding_mode": bundle["review_source_binding_modes"][source_id],
            "reviewer_id": bundle["reviewer_id"],
            "adjudicator_id": bundle["adjudicator_id"],
            "adjudicated_at": bundle["adjudicated_at"],
            "adjudication_sha256": bundle["adjudication_sha256"],
            "reviewed_candidate_sha256": bundle["reviewed_sha256"],
            "reviewed_candidate_path": bundle["reviewed_path"],
        })
    if not viable:
        # Give a stable high-priority reason without hiding secondary failures.
        priority = [
            "claim_payload_changed", "source_binding_not_unique",
            "source_generation_changed", "reviewed_source_input_changed",
        ]
        reason = next((item for item in priority if item in failures), sorted(failures)[0])
        return {
            **common, "reason": reason, "all_reasons": sorted(set(failures)),
            **({"details": sorted(set(failure_details))} if failure_details else {}),
        }
    identities = {
        (row["source_id"], row["review_decision"], row["spot_check_selected"],
         row["review_sha256"], row["adjudication_sha256"],
         row["reviewed_candidate_sha256"])
        for row in viable
    }
    if len(identities) != 1:
        return {**common, "reason": "conflicting_matching_review_bundles"}
    return viable[0]


def inventory(root: Path, store: PostgresKnowledgeStore) -> dict[str, Any]:
    bundles_by_claim: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bundles_seen = 0
    for path in sorted(root.rglob("*.reviewed-candidate.json")):
        try:
            bundle = inspect_legacy_bundle(path)
        except (OSError, ValueError, KeyError, TypeError):
            continue  # No trustworthy claim index can be read from this file.
        bundles_seen += 1
        for claim_id in bundle["claims"]:
            bundles_by_claim[claim_id].append(bundle)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT object_id,revision,content_sha256,payload
               FROM wang_knowledge.objects
               WHERE collection='claims' AND review_status='candidate'
                 AND retired_at IS NULL ORDER BY object_id"""
        )
        claims = [
            {"object_id": row[0], "revision": row[1],
             "content_sha256": row[2], "payload": row[3]}
            for row in cursor.fetchall()
        ]
        cursor.execute(
            """SELECT object_id,revision,content_sha256,payload
               FROM wang_knowledge.objects
               WHERE collection='source_documents' AND retired_at IS NULL"""
        )
        sources = {
            str(row[0]): {"revision": row[1], "content_sha256": row[2], "payload": row[3]}
            for row in cursor.fetchall()
        }
    rows = [
        classify_candidate(claim, bundles_by_claim.get(str(claim["object_id"]), []), sources)
        for claim in claims
    ]
    missing_ids = {row["claim_id"] for row in rows if row["reason"] == "missing_reviewed_candidate"}
    if missing_ids:
        missing_claims = {str(row["object_id"]): row["payload"] for row in claims if row["object_id"] in missing_ids}
        evidence_ids = sorted({
            str(evidence_id) for claim in missing_claims.values()
            for evidence_id in claim.get("evidence_step_ids") or []
        })
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT object_id,payload FROM wang_knowledge.objects
                   WHERE collection='evidence_steps' AND object_id = ANY(%s)
                     AND retired_at IS NULL""",
                (evidence_ids,),
            )
            evidence = {str(item[0]): item[1] for item in cursor.fetchall()}
            fragment_ids = sorted({
                str(fragment_id) for step in evidence.values()
                for fragment_id in (
                    list(step.get("source_fragment_ids") or [])
                    + ([step["source_fragment_id"]] if step.get("source_fragment_id") else [])
                )
            })
            cursor.execute(
                """SELECT object_id,payload FROM wang_knowledge.objects
                   WHERE collection='source_fragments' AND object_id = ANY(%s)
                     AND retired_at IS NULL""",
                (fragment_ids,),
            )
            fragments = {str(item[0]): item[1] for item in cursor.fetchall()}
        for row in rows:
            if row["claim_id"] not in missing_ids:
                continue
            source_ids = {
                str(fragments[fragment_id]["source_id"])
                for evidence_id in missing_claims[row["claim_id"]].get("evidence_step_ids") or []
                for fragment_id in (
                    list(evidence.get(evidence_id, {}).get("source_fragment_ids") or [])
                    + ([evidence[evidence_id]["source_fragment_id"]]
                       if evidence.get(evidence_id, {}).get("source_fragment_id") else [])
                )
                if fragment_id in fragments
            }
            if len(source_ids) != 1:
                row["reason"] = "source_binding_not_unique"
                row["bound_source_ids"] = sorted(source_ids)
    counts = dict(sorted(Counter(row["reason"] for row in rows).items()))
    return {
        "schema_version": "wang_legacy_candidate_reconciliation_preflight_v1",
        "candidate_count": len(rows),
        "bundle_count": bundles_seen,
        "counts": counts,
        "rows": rows,
        "snapshot_sha256": sha256_json(rows),
        "note": "Read-only inventory. Migration-ready rows still require backup, CAS apply and readback.",
    }


def plan_review_migration(
    rows: list[Mapping[str, Any]],
    current_claims: Mapping[str, Mapping[str, Any]],
    *,
    freeze_sha256: str,
    source_kind: str = "legacy_candidate_review_reconciliation_v1",
) -> ChangeSetPlan:
    """Build a status-only ChangeSet for one source's verified decisions."""

    if not rows or len({str(row["source_id"]) for row in rows}) != 1:
        raise ValueError("one nonempty source unit is required")
    source_id = str(rows[0]["source_id"])
    operations: list[ChangeOperation] = []
    events: list[PlannedReviewEvent] = []
    for row in sorted(rows, key=lambda item: str(item["claim_id"])):
        if row.get("reason") not in {
            "pass_ready_for_legacy_migration", "human_spot_check_required",
            "human_confirmation_required", "human_disagreement_required",
            "withdrawn_unchanged_graph_verified",
        }:
            raise ValueError(f"not a verified status decision: {row['claim_id']}")
        if row.get("reason") == "withdrawn_unchanged_graph_verified":
            if (
                source_kind != "legacy_adjudicated_withdrawn_review_reconciliation_v1"
                or row.get("adjudication_status") != "withdrawn"
                or row.get("review_decision") != "changes_suggested"
                or row.get("target_review_status") != "ai_consensus_reviewed"
                or not row.get("graph_guard_sha256")
            ):
                raise ValueError(f"withdrawn decision lacks graph proof: {row['claim_id']}")
        elif source_kind != "legacy_candidate_review_reconciliation_v1":
            raise ValueError(f"invalid legacy migration source kind: {source_kind}")
        claim_id = str(row["claim_id"])
        current = current_claims.get(claim_id)
        if current is None:
            raise ValueError(f"candidate missing from current store: {claim_id}")
        if (
            current["revision"] != row["revision"]
            or current["content_sha256"] != row["content_sha256"]
            or (current["payload"] or {}).get("review_status") != "candidate"
        ):
            raise ValueError(f"candidate changed since preflight: {claim_id}")
        before = dict(current["payload"])
        after = dict(before)
        decision = str(row["review_decision"])
        adjudication_status = str(row["adjudication_status"])
        target = str(row["target_review_status"])
        if target not in {"ai_consensus_reviewed", "human_review_required"}:
            raise ValueError(f"invalid target review status: {claim_id}")
        reason = f"独立 AI 复审：{decision}；仲裁：{adjudication_status}（旧产物严格绑定迁移）"
        reviewer_id = str(row["reviewer_id"])
        if decision != "pass" and row.get("adjudicator_id"):
            reviewer_id += "+" + str(row["adjudicator_id"])
        reviewed_at = str(row["adjudicated_at"])
        if not reviewer_id or not reviewed_at:
            raise ValueError(f"review provenance incomplete: {claim_id}")
        after.update({
            "review_status": target,
            "reviewed_by": reviewer_id,
            "reviewed_at": reviewed_at,
            "review_note": reason,
        })
        if _substantive_payload(before) != _substantive_payload(after):
            raise ValueError(f"migration changes substantive content: {claim_id}")
        revision = int(row["revision"]) + 1
        after_sha = record_content_sha({**after, "revision": revision})
        operation = ChangeOperation(
            operation="update", collection="claims", object_id=claim_id,
            before_sha256=str(row["content_sha256"]), after_sha256=after_sha,
            before_revision=int(row["revision"]), after_revision=revision,
            payload=after,
        )
        operations.append(operation)
        resolution = {
            "schema_version": "wang_legacy_ai_review_reconciliation_v1",
            "claim_id": claim_id,
            "independent_review_decision": decision,
            "adjudication_status": adjudication_status,
            "target_review_status": target,
            "reviewer_id": reviewer_id,
            "reason": reason,
            "approval_status": "not_human_approved",
        }
        artifact = {
            "legacy_unsealed": True,
            "compatibility_verification": "package_sha+review_snapshot+routing+adjudication+current_claim+current_source",
            "freeze_sha256": freeze_sha256,
            "source_id": source_id,
            "source_revision": row["source_revision"],
            "source_content_sha256": row["source_content_sha256"],
            "package_sha256": row["package_sha256"],
            "review_artifact_sha256": row["review_sha256"],
            "review_fingerprint": row["review_fingerprint"],
            "adjudication_artifact_sha256": row["adjudication_sha256"],
            "reviewed_candidate_sha256": row["reviewed_candidate_sha256"],
            "resolution": resolution,
        }
        if row.get("graph_guard_sha256"):
            artifact["graph_guard_sha256"] = row["graph_guard_sha256"]
        event_id = "REV-AI-" + sha256_json({
            "collection": "claims", "object_id": claim_id,
            "object_revision": revision, "after_sha256": after_sha,
            "artifact": artifact,
        })[:32]
        events.append(PlannedReviewEvent(
            review_event_id=event_id, collection="claims", object_id=claim_id,
            object_revision=revision, reviewer_kind="ai",
            reviewer_id=reviewer_id, decision=target,
            reason=reason, artifact=artifact,
        ))
    source_sha = sha256_json({
        "freeze_sha256": freeze_sha256,
        "source_id": source_id,
        "claim_ids": [row.object_id for row in operations],
    })
    fingerprint = sha256_json({
        "planner_schema": "wang_postgres_changeset_v2",
        "source_kind": source_kind,
        "source_sha256": source_sha,
        "package_id": f"LEGACY-REVIEW-{source_id}",
        "operations": operation_fingerprint_rows(operations),
        "review_events": review_event_fingerprint_rows(events),
    })
    return ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}", fingerprint_sha256=fingerprint,
        package_id=f"LEGACY-REVIEW-{source_id}",
        source_kind=source_kind,
        source_sha256=source_sha, operations=tuple(operations), unchanged=0,
        ignored_keys=(), review_events=tuple(events),
    )


def _current_claims(store: PostgresKnowledgeStore, ids: list[str]) -> dict[str, dict[str, Any]]:
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT object_id,revision,content_sha256,payload
               FROM wang_knowledge.objects
               WHERE collection='claims' AND object_id = ANY(%s)
                 AND retired_at IS NULL""",
            (ids,),
        )
        return {
            str(row[0]): {"revision": row[1], "content_sha256": row[2], "payload": row[3]}
            for row in cursor.fetchall()
        }


def _encode_report(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _decode_plan(data: Mapping[str, Any]) -> ChangeSetPlan:
    plan = ChangeSetPlan(
        change_set_id=str(data["change_set_id"]),
        fingerprint_sha256=str(data["fingerprint_sha256"]),
        package_id=str(data["package_id"]),
        source_kind=str(data["source_kind"]),
        source_sha256=str(data["source_sha256"]),
        operations=tuple(ChangeOperation(**row) for row in data["operations"]),
        unchanged=int(data["unchanged"]),
        ignored_keys=tuple(data["ignored_keys"]),
        review_events=tuple(PlannedReviewEvent(**row) for row in data["review_events"]),
    )
    validate_change_set_plan_integrity(plan)
    expected = sha256_json({
        "planner_schema": "wang_postgres_changeset_v2",
        "source_kind": plan.source_kind,
        "source_sha256": plan.source_sha256,
        "package_id": plan.package_id,
        "operations": operation_fingerprint_rows(plan.operations),
        "review_events": review_event_fingerprint_rows(plan.review_events),
    })
    if (
        plan.source_kind not in {
            "legacy_candidate_review_reconciliation_v1",
            "legacy_adjudicated_withdrawn_review_reconciliation_v1",
        }
        or expected != plan.fingerprint_sha256
        or plan.change_set_id != f"KCS-{expected[:20]}"
    ):
        raise ValueError("migration plan fingerprint is invalid")
    return plan


def _readback(store: PostgresKnowledgeStore, plan: ChangeSetPlan) -> None:
    ids = [item.object_id for item in plan.operations]
    rows = _current_claims(store, ids)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            """SELECT review_event_id,object_id,object_revision,decision
               FROM wang_knowledge.review_events
               WHERE review_event_id = ANY(%s)""",
            ([event.review_event_id for event in plan.review_events],),
        )
        events = {str(row[0]): row for row in cursor.fetchall()}
    if len(events) != len(plan.review_events):
        raise RuntimeError("migration review-event readback count mismatch")
    for op, event in zip(plan.operations, plan.review_events, strict=True):
        row = rows.get(op.object_id)
        if (
            row is None
            or row["revision"] != op.after_revision
            or row["content_sha256"] != op.after_sha256
            or row["payload"].get("review_status") != event.decision
            or event.review_event_id not in events
            or events[event.review_event_id][2] != op.after_revision
            or events[event.review_event_id][3] != event.decision
        ):
            raise RuntimeError(f"migration readback differs: {op.object_id}")


def dry_run(root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise ValueError("dry-run output root must be empty")
    report = inventory(root, store)
    ready: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in report["rows"]:
        if row["reason"] in {
            "pass_ready_for_legacy_migration", "human_spot_check_required",
            "human_confirmation_required", "human_disagreement_required",
        }:
            ready[row["source_id"]].append(row)
    ids = [row["claim_id"] for group in ready.values() for row in group]
    current = _current_claims(store, ids)
    plans = [
        plan_review_migration(rows, current, freeze_sha256=report["snapshot_sha256"])
        for _, rows in sorted(ready.items())
    ]
    for plan in plans:
        validate_change_set_plan_integrity(plan)
    plan_document = {
        "schema_version": "wang_legacy_candidate_migration_plans_v1",
        "freeze_sha256": report["snapshot_sha256"],
        "plans": [asdict(plan) for plan in plans],
    }
    _encode_report(output_root / "preflight.json", report)
    _encode_report(output_root / "migration-plans.json", plan_document)
    return {
        "candidate_count": report["candidate_count"],
        "counts": report["counts"],
        "snapshot_sha256": report["snapshot_sha256"],
        "source_units": len(plans),
        "planned_claims": sum(len(plan.operations) for plan in plans),
        "output_root": str(output_root),
    }


def apply_frozen(
    output_root: Path, backup_path: Path, store: PostgresKnowledgeStore,
    *, max_source_units: int | None = None,
) -> dict[str, Any]:
    if not backup_path.is_file() or backup_path.stat().st_size < 1_000_000:
        raise ValueError("a nonempty independent PostgreSQL backup is required")
    report = _read(output_root / "preflight.json")
    document = _read(output_root / "migration-plans.json")
    if (
        report.get("snapshot_sha256") != sha256_json(report.get("rows"))
        or document.get("freeze_sha256") != report.get("snapshot_sha256")
    ):
        raise ValueError("frozen preflight or plan binding is invalid")
    rows_by_id = {str(row["claim_id"]): row for row in report["rows"]}
    plans = [_decode_plan(data) for data in document["plans"]]
    graph_guards = document.get("claim_related_graph_guards") or {}
    backup_sha = _sha(backup_path)
    applied = already = 0
    selected = plans[:max_source_units] if max_source_units is not None else plans
    for plan in selected:
        source_rows = [rows_by_id[op.object_id] for op in plan.operations]
        if any(row["target_review_status"] not in {
            "ai_consensus_reviewed", "human_review_required"
        } for row in source_rows):
            raise ValueError("plan includes an unverified target status")
        expected_sources = {
            str(row["source_id"]): (
                int(row["source_revision"]), str(row["source_content_sha256"])
            ) for row in source_rows
        }
        if len(expected_sources) != 1:
            raise ValueError("migration plan crosses source units")
        expected_graph = None
        if plan.source_kind == "legacy_adjudicated_withdrawn_review_reconciliation_v1":
            expected_graph = {}
            for row in source_rows:
                guard = graph_guards.get(row["claim_id"])
                if (
                    not isinstance(guard, list)
                    or sha256_json(guard) != row.get("graph_guard_sha256")
                ):
                    raise ValueError(f"frozen graph guard differs: {row['claim_id']}")
                expected_graph[row["claim_id"]] = [
                    (str(item["collection"]), str(item["object_id"]),
                     int(item["revision"]), str(item["content_sha256"]))
                    for item in guard
                ]
        elif graph_guards:
            raise ValueError("graph guards supplied for ordinary legacy migration")
        verified_bundles: dict[str, dict[str, Any]] = {}
        for row in source_rows:
            for field, path_field in (
                ("source_file_sha256", "source_file_path"),
                ("reviewed_candidate_sha256", "reviewed_candidate_path"),
            ):
                if _sha(Path(row[path_field])) != row[field]:
                    raise ValueError(f"source artifact changed: {row[path_field]}")
            reviewed_path = str(row["reviewed_candidate_path"])
            if reviewed_path not in verified_bundles:
                verified_bundles[reviewed_path] = inspect_legacy_bundle(Path(reviewed_path))
            bundle = verified_bundles[reviewed_path]
            if (
                bundle.get("reason") is not None
                or bundle.get("package_sha256") != row["package_sha256"]
                or bundle.get("review_sha256") != row["review_sha256"]
                or bundle.get("adjudication_sha256") != row["adjudication_sha256"]
                or bundle.get("reviewed_sha256") != row["reviewed_candidate_sha256"]
            ):
                raise ValueError(f"legacy review chain changed: {reviewed_path}")
        result = store.apply_plan(
            plan,
            metadata={
                "ticket": 397,
                "freeze_sha256": report["snapshot_sha256"],
                "backup_path": str(backup_path),
                "backup_sha256": backup_sha,
                "legacy_unsealed": True,
            },
            expected_current_source_records=expected_sources,
            expected_current_claim_related_records=expected_graph,
        )
        _readback(store, plan)
        if result["status"] == "already_applied":
            already += 1
        else:
            applied += 1
    return {"applied_source_units": applied, "already_applied_source_units": already,
            "selected_source_units": len(selected), "planned_source_units": len(plans),
            "backup_sha256": backup_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--artifact-root", type=Path,
        default=wang_platform_paths().claim_layer_staging,
    )
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--max-source-units", type=int)
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.apply:
        if args.output_root is None or args.backup_path is None:
            parser.error("--apply requires --output-root and --backup-path")
        result = apply_frozen(
            args.output_root, args.backup_path, store,
            max_source_units=args.max_source_units,
        )
    elif args.output_root is not None:
        result = dry_run(args.artifact_root, args.output_root, store)
    else:
        report = inventory(args.artifact_root, store)
        result = {key: value for key, value in report.items() if key != "rows"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
