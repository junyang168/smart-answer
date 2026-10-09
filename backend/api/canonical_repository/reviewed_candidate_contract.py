"""Integrity contract for consensus-derived claim packages.

The consensus stage changes an already authenticated cross-section package.
Its output therefore needs its own seal and a mechanically checkable account
of the final review state.  This module is deliberately dependency-light so
both pipeline runners and the canonical store can enforce the same boundary.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


CONSENSUS_APPLICATION_VERSION = "wang_ai_consensus_application_v2"
AI_REVIEW_PROVENANCE_VERSION = "wang_claim_ai_review_provenance_v1"
SOURCE_SCOPED = "source_scoped"
RESEARCH_BATCH_AGGREGATE = "research_batch_aggregate"


class ConsensusApplicationError(ValueError):
    """Raised when a reviewed candidate cannot authenticate its final state."""


def package_requires_reviewed_candidate_contract(package: Mapping[str, Any]) -> bool:
    """Whether this package claims extraction/review authority over Claims."""

    if package.get("consensus_application") is not None:
        return True
    if package.get("extraction") is not None or package.get(
        "cross_section_relations"
    ) is not None:
        return True
    return any(
        str(claim.get("review_status") or "").startswith("ai_")
        or claim.get("review_status") in {
            "human_review_required",
            "superseded",
        }
        for claim in package.get("claims") or []
        if isinstance(claim, Mapping)
    )


def validate_store_package_authorization(package: Mapping[str, Any]) -> None:
    """Fail closed before DB access for packages carrying claim-layer authority."""

    if package.get("source_repair_application") is not None:
        validate_source_repair_application(package)
        return
    if (any(r.get('approval_basis') == 'owner_scoped_independent_terminal_review'
            for r in package.get('viewpoint_identity_decisions', []))
            or any(r.get('review_status') == 'independent_terminal_reviewed'
                   for r in package.get('claims', []))):
        raise ConsensusApplicationError('scoped Opus provenance requires exact source repair authority')
    if not package_requires_reviewed_candidate_contract(package):
        return
    if package.get("consensus_application") is None:
        raise ConsensusApplicationError(
            "claim-layer/extraction packages require a sealed final reviewed candidate"
        )
    validate_reviewed_candidate_artifact(package)
    # This import stays local so the dependency-light seal contract remains
    # usable by the merge module itself.  Store authorization is stronger than
    # a seal: a correctly resealed package with dangling or one-way graph edges
    # is still not admissible.
    from backend.pipeline.knowledge_package_merge import (
        KnowledgePackageMergeError,
        validate_merged_package,
    )

    try:
        validate_merged_package(dict(package))
    except KnowledgePackageMergeError as exc:
        raise ConsensusApplicationError(
            f"reviewed candidate graph is invalid: {exc}"
        ) from exc


def validate_source_repair_application(package: Mapping[str, Any]) -> None:
    """A bounded repair is not a complete extraction or its historical review.

    Dispatch only an exact versioned authority. Historical adapters retain
    their original dual-review boundaries; the generic source reconciliation
    contract uses one independent Opus terminal review and bounded CVR writes.
    Every adapter must prove its own exact records, never borrow old approval.
    """
    app = package.get('source_repair_application') or {}
    if app.get('schema_version') == 'wang_cvp_owner_sql_link_retirement_v1':
        from backend.pipeline.cvp_link_retirement import validate_package
        validate_package(dict(package))
        return
    if app.get('schema_version') == 'wang_cvp_sql_link_retarget_v1':
        from backend.pipeline.cvp_link_retarget import validate_package
        validate_package(dict(package))
        return
    if app.get('schema_version') == 'wang_owner_sql_link_correction_v2':
        from backend.pipeline.cvp_sql_actions import validate_owner_link_package
        validate_owner_link_package(dict(package))
        return
    if app.get('schema_version') == 'wang_source_sql_reconciliation_v2':
        from backend.pipeline.cvp_sql_actions import validate_source_package
        validate_source_package(dict(package))
        return
    if app.get('schema_version') == 'wang_owner_approved_link_correction_v1':
        from backend.pipeline.owner_approved_link_correction import validate_reviewed_package
        validate_reviewed_package(dict(package))
        return
    if app.get('schema_version') == 'wang_owner_claim_statement_revision_v1':
        from backend.pipeline.claim_statement_revision import validate_package
        validate_package(dict(package))
        return
    if app.get('schema_version') == 'wang_source_reconciliation_v1':
        from backend.pipeline.source_reconciliation_runner import validate_package
        validate_package(dict(package))
        return
    if app.get('schema_version') == 'wang_s201129_owner_cl005_supports_v1':
        from backend.pipeline.viewpoint_s201129_link_support_apply import validate_package
        validate_package(package)
        return
    if app.get('schema_version') == 'wang_s201129_owner_opus_source_reconciliation_v1':
        from backend.pipeline.viewpoint_s201129_source_repair import validate_package
        validate_package(package)
        return
    if app.get('schema_version') == 'wang_g041_owner_qualified_transcript_reconciliation_v2':
        from backend.pipeline.viewpoint_g041_qualified_transcript_repair import validate_package
        validate_package(package)
        return
    if app.get('schema_version') == 'wang_g037_claim_statement_repair_v2':
        from backend.pipeline.viewpoint_g037_claim_condition_repair import validate_package
        validate_package(package)
        return
    if app.get('schema_version') == 'wang_g037_claim_statement_repair_v1':
        from backend.pipeline.viewpoint_g037_claim_statement_repair import validate_package
        validate_package(package)
        return
    if app.get('schema_version') == 'wang_owner_evidence_annotation_v1':
        from backend.pipeline.viewpoint_owner_evidence_annotation import validate_package
        validate_package(package)
        return
    if app.get('schema_version') == 'wang_claim_qualifier_repair_v2':
        validate_claim_qualifier_repair(package)
        return
    if app.get('schema_version') != 'wang_authorized_source_repair_v1':
        raise ConsensusApplicationError('unsupported source repair application')
    if set(package) - {'package_id','claims','evidence_steps','source_fragments','viewpoint_claim_links','source_repair_application'}:
        raise ConsensusApplicationError('source repair contains unauthorized collections')
    body = {k:v for k,v in package.items() if k != 'source_repair_application'}
    packet = app.get('review_packet') or {}
    if not app.get('user_authorization') or app.get('approval_status') != 'not_human_approved':
        raise ConsensusApplicationError('source repair lacks bounded authorization')
    if packet.get('package_sha256') != _canonical_sha256(body):
        raise ConsensusApplicationError('source repair package changed after review')
    items = packet.get('items') or []
    if [i.get('after') for i in items] != list(body.get('claims') or []):
        raise ConsensusApplicationError('review packet does not match repaired Claims')
    if [i.get('new_evidence_step') for i in items] != list(body.get('evidence_steps') or []) or [i.get('new_fragment') for i in items] != list(body.get('source_fragments') or []):
        raise ConsensusApplicationError('review packet does not match repaired evidence')
    if [l for i in items for l in i.get('updated_links',[])] != list(body.get('viewpoint_claim_links') or []):
        raise ConsensusApplicationError('review packet does not match repaired links')
    ids = [c.get('claim_id') for c in body.get('claims') or []]
    if not ids or not all(ids) or len(ids) != len(set(ids)):
        raise ConsensusApplicationError('source repair repeats or omits Claim identities')
    for item in items:
        old, new = item['before'], item['after']
        if old.get('claim_id') != new.get('claim_id') or new.get('revision') != old.get('revision',0)+1:
            raise ConsensusApplicationError('source repair is not a next Claim revision')
        mutable = {'revision','statement','evidence_step_ids','occurrences','review_note','reviewed_at','reviewed_by'}
        if {k:v for k,v in old.items() if k not in mutable} != {k:v for k,v in new.items() if k not in mutable}:
            raise ConsensusApplicationError('source repair changes unrelated Claim fields')
        if not set(old.get('evidence_step_ids',[])) <= set(new.get('evidence_step_ids',[])):
            raise ConsensusApplicationError('source repair removes existing Claim evidence')
        step, frag = item['new_evidence_step'], item['new_fragment']
        if step.get('produced_claim_ids') != [new['claim_id']] or step.get('evidence_step_id') not in new.get('evidence_step_ids',[]) or step.get('source_fragment_ids') != [frag.get('fragment_id')]:
            raise ConsensusApplicationError('source repair evidence is not reciprocal and owned')
        quote = frag.get('verbatim_excerpt')
        if not quote or quote not in item.get('source_paragraph','') or frag.get('source_sha256') != packet.get('source_body_sha256'):
            raise ConsensusApplicationError('source repair quote is not bound to its paragraph')
        if frag.get('verbatim_excerpt_sha256') != hashlib.sha256(quote.encode()).hexdigest() or frag.get('paragraph_text_sha256') != hashlib.sha256(item['source_paragraph'].encode()).hexdigest():
            raise ConsensusApplicationError('source repair physical quote SHA mismatch')
        old_occ, new_occ = old.get('occurrences',[]), new.get('occurrences',[])
        if len(old_occ) != 1 or len(new_occ) != 1 or {k:v for k,v in old_occ[0].items() if k!='anchors'} != {k:v for k,v in new_occ[0].items() if k!='anchors'}:
            raise ConsensusApplicationError('source repair changes occurrence identity')
        if frag.get('source_id') != new_occ[0].get('source_id') or new_occ[0].get('anchors',[])[:-1] != old_occ[0].get('anchors',[]) or new_occ[0]['anchors'][-1].get('evidence_id') != step.get('evidence_step_id'):
            raise ConsensusApplicationError('source repair changes existing anchors or crosses source')
    artifacts = app.get('review_artifacts') or []
    if len(artifacts) != 2 or {r.get('backend') for r in artifacts} != {'codex_subscription','claude_subscription'}:
        raise ConsensusApplicationError('source repair requires two independent subscription reviews')
    for artifact in artifacts:
        raw = {k:v for k,v in artifact.items() if k != 'artifact_sha256'}
        response = artifact.get('response') or {}
        if artifact.get('artifact_sha256') != _canonical_sha256(raw) or artifact.get('request_payload_sha256') != _canonical_sha256(packet) or artifact.get('response_sha256') != _canonical_sha256(response):
            raise ConsensusApplicationError('source repair review SHA mismatch')
        verdicts = response.get('verdicts') or []
        if len(verdicts) != len(ids) or {v.get('claim_id') for v in verdicts} != set(ids) or not all(v.get('approved') is True and v.get('problems') == [] for v in verdicts):
            raise ConsensusApplicationError('source repair lacks unanimous complete approvals')


def validate_claim_qualifier_repair(package: Mapping[str, Any]) -> None:
    """Add a reviewed local qualifier; never remove evidence or change identity.

    This separate contract does not relax the original evidence-addition repair.
    Existing identity decisions remain historical; fresh exact-postimage reviews
    in this ChangeSet authorize revalidation of the same component membership.
    """
    app = package['source_repair_application']
    if set(package) != {'package_id', 'claims', 'viewpoint_claim_links', 'source_repair_application'}:
        raise ConsensusApplicationError('qualifier repair has unauthorized collections')
    body = {k: v for k, v in package.items() if k != 'source_repair_application'}
    packet = app.get('review_packet') or {}
    if not app.get('user_authorization') or app.get('approval_status') != 'not_human_approved':
        raise ConsensusApplicationError('qualifier repair lacks authorization')
    if packet.get('package_sha256') != _canonical_sha256(body) or packet.get('proposed_package') != body:
        raise ConsensusApplicationError('qualifier repair postimage changed')
    if len(body['claims']) != 1 or len(body['viewpoint_claim_links']) != 1:
        raise ConsensusApplicationError('qualifier repair must have one Claim and one link')
    old, new = packet['before_claim'], body['claims'][0]
    prefix = packet.get('qualifier_prefix')
    if not prefix or new.get('statement') != prefix + old.get('statement', ''):
        raise ConsensusApplicationError('qualifier repair must preserve the complete old statement')
    mutable = {'revision', 'statement', 'review_note', 'reviewed_at', 'reviewed_by'}
    if new.get('revision') != old.get('revision', 0) + 1 or {k:v for k,v in old.items() if k not in mutable} != {k:v for k,v in new.items() if k not in mutable}:
        raise ConsensusApplicationError('qualifier repair changed evidence, occurrence, or unrelated Claim fields')
    previous, link = packet['before_link'], body['viewpoint_claim_links'][0]
    mutable_link = {'revision', 'pinned_claim_revision', 'component_locator'}
    if {k:v for k,v in previous.items() if k not in mutable_link} != {k:v for k,v in link.items() if k not in mutable_link}:
        raise ConsensusApplicationError('qualifier repair changed identity or evidence bindings')
    if link['revision'] != previous['revision'] + 1 or link['pinned_claim_revision'] != new['revision'] or link['claim_id'] != new['claim_id'] or link['link_type'] != 'equivalent_component':
        raise ConsensusApplicationError('qualifier repair link revision or type mismatch')
    locator = {'claim_sha256': _canonical_sha256(new), 'statement_component': new['statement'],
               'canonical_spans': [{'start_char': 0, 'end_char': len(new['statement']), 'exact_text': new['statement']}]}
    # semantic_record_sha includes the normalized Claim fields; packet supplies
    # that independently computed digest, which store graph validation rechecks.
    locator['claim_sha256'] = packet.get('after_claim_semantic_sha256')
    if not locator['claim_sha256'] or link['component_locator'] != locator:
        raise ConsensusApplicationError('qualifier repair locator mismatch')
    artifacts = app.get('review_artifacts') or []
    if len(artifacts) != 2 or {r.get('backend') for r in artifacts} != {'codex_subscription', 'claude_subscription'}:
        raise ConsensusApplicationError('qualifier repair needs independent subscription reviews')
    for artifact in artifacts:
        raw = {k:v for k,v in artifact.items() if k != 'artifact_sha256'}
        response = artifact.get('response') or {}
        verdicts = response.get('verdicts') or []
        if artifact.get('artifact_sha256') != _canonical_sha256(raw) or artifact.get('request_payload_sha256') != _canonical_sha256(packet) or artifact.get('response_sha256') != _canonical_sha256(response):
            raise ConsensusApplicationError('qualifier repair review SHA mismatch')
        if len(verdicts) != 1 or verdicts[0].get('claim_id') != new['claim_id'] or verdicts[0].get('approved') is not True or verdicts[0].get('problems') != []:
            raise ConsensusApplicationError('qualifier repair lacks exact unanimous approval')


def reviewed_candidate_artifact_sha256(package: Mapping[str, Any]) -> str:
    """Hash the whole derived candidate except the seal field itself."""

    candidate = json.loads(json.dumps(package, ensure_ascii=False))
    application = candidate.get("consensus_application")
    if isinstance(application, dict):
        application.pop("artifact_sha256", None)
    return hashlib.sha256(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def reseal_after_relation_id_migration(
    original: Mapping[str, Any],
    migrated: dict[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Authenticate first, then bind a deterministic ID-only migration.

    A legacy relation-ID migration necessarily invalidates the consensus
    stage's seal.  The effective package receives a new seal only after the
    manifest proves the exact input and pre-lineage output.  The old seal stays
    in the lineage so this is an auditable transformation, not silent repair.
    """

    validate_reviewed_candidate_artifact(original)
    if manifest.get("input_canonical_sha256") != _canonical_sha256(original):
        raise ConsensusApplicationError(
            "relation-id migration manifest does not bind the reviewed candidate"
        )
    if manifest.get("output_canonical_sha256") != _canonical_sha256(migrated):
        raise ConsensusApplicationError(
            "relation-id migration manifest does not bind its effective output"
        )
    if manifest.get("status") == "not_required":
        validate_reviewed_candidate_artifact(migrated)
        return migrated
    if manifest.get("status") != "applied" or manifest.get("semantic_change") != (
        "none_relation_identifiers_only"
    ) or manifest.get("round_trip_verified") is not True:
        raise ConsensusApplicationError(
            "reviewed candidate relation-id migration is not proven ID-only"
        )
    application = migrated.get("consensus_application")
    if not isinstance(application, dict):
        raise ConsensusApplicationError(
            "migrated reviewed candidate lost its consensus application"
        )
    application["relation_id_namespace_migration"] = {
        **dict(manifest),
        "upstream_reviewed_candidate_artifact_sha256": (
            (original.get("consensus_application") or {}).get("artifact_sha256")
        ),
    }
    application["artifact_sha256"] = reviewed_candidate_artifact_sha256(migrated)
    validate_reviewed_candidate_artifact(migrated)
    return migrated


def validate_reviewed_candidate_artifact(
    package: Mapping[str, Any], *, require_review_completion: bool = True
) -> None:
    """Reject an incomplete, modified, or internally inconsistent candidate."""

    application = package.get("consensus_application")
    if not isinstance(application, Mapping):
        raise ConsensusApplicationError(
            "ingest requires a consensus-derived reviewed candidate"
        )
    if application.get("schema_version") != CONSENSUS_APPLICATION_VERSION:
        raise ConsensusApplicationError(
            "reviewed candidate predates the self-authenticating consensus contract"
        )
    if application.get("artifact_sha256") != reviewed_candidate_artifact_sha256(
        package
    ):
        raise ConsensusApplicationError(
            "reviewed candidate artifact is incomplete or was modified"
        )
    if not require_review_completion:
        return
    if application.get("review_completion") != "complete":
        raise ConsensusApplicationError(
            "reviewed candidate does not carry a complete review outcome"
        )
    scope_kind = str(application.get("scope_kind") or "")
    source_documents = list(package.get("source_documents") or [])
    member_lineage = application.get("member_artifact_lineage")
    if scope_kind == SOURCE_SCOPED:
        if len(source_documents) != 1:
            raise ConsensusApplicationError(
                "source-scoped reviewed candidate must have exactly one source"
            )
        if package.get("complete") is not True:
            raise ConsensusApplicationError(
                "source-scoped reviewed candidate is not a complete extraction"
            )
        extraction = package.get("extraction")
        if not isinstance(extraction, Mapping) or not str(
            extraction.get("fingerprint_sha256")
            or extraction.get("generation_fingerprint_sha256")
            or ""
        ).strip():
            raise ConsensusApplicationError(
                "source-scoped reviewed candidate lacks extraction identity"
            )
        if package.get("batch") is not None or member_lineage is not None:
            raise ConsensusApplicationError(
                "source-scoped reviewed candidate claims aggregate lineage"
            )
    elif scope_kind == RESEARCH_BATCH_AGGREGATE:
        if not source_documents:
            raise ConsensusApplicationError(
                "merged reviewed candidate has no source documents"
            )
        batch = package.get("batch")
        if not isinstance(batch, Mapping) or not str(
            batch.get("batch_id") or ""
        ).strip():
            raise ConsensusApplicationError(
                "merged reviewed candidate lacks its batch identity"
            )
        source_transcript_rows = [
            str(row.get("transcript_id") or "")
            for row in source_documents
            if isinstance(row, Mapping)
        ]
        if (
            len(source_transcript_rows) != len(source_documents)
            or not all(source_transcript_rows)
            or len(source_transcript_rows) != len(set(source_transcript_rows))
        ):
            raise ConsensusApplicationError(
                "merged reviewed candidate has missing or duplicate source transcript identities"
            )
    else:
        raise ConsensusApplicationError(
            "reviewed candidate has an unknown or missing scope kind"
        )
    for field in (
        "review_artifact_sha256",
        "review_fingerprint",
        "adjudication_artifact_sha256",
        "adjudication_fingerprint",
        "overrides_artifact_sha256",
    ):
        if not str(application.get(field) or "").strip():
            raise ConsensusApplicationError(f"reviewed candidate lacks {field}")
    if application.get("approval_status") != "not_human_approved":
        raise ConsensusApplicationError(
            "AI-reviewed candidate must not imply human approval"
        )

    claims = list(package.get("claims") or [])
    if any("ai_review_provenance" in claim for claim in claims):
        raise ConsensusApplicationError(
            "claim-level AI review provenance is forbidden; use package resolutions"
        )
    claim_ids = [str(row.get("claim_id") or "") for row in claims]
    if not all(claim_ids) or len(claim_ids) != len(set(claim_ids)):
        raise ConsensusApplicationError(
            "reviewed candidate has missing or duplicate claim ids"
        )
    resolutions = application.get("review_resolutions")
    if not isinstance(resolutions, list) or not all(
        isinstance(row, Mapping) for row in resolutions
    ):
        raise ConsensusApplicationError(
            "reviewed candidate lacks valid package-level review resolutions"
        )
    resolution_ids = [str(row.get("claim_id") or "") for row in resolutions]
    if not all(resolution_ids) or len(resolution_ids) != len(set(resolution_ids)):
        raise ConsensusApplicationError(
            "reviewed candidate has missing or duplicate review resolutions"
        )
    if set(resolution_ids) != set(claim_ids):
        raise ConsensusApplicationError(
            "review resolutions must cover every package claim exactly once"
        )

    resolution_by_id = {
        str(row["claim_id"]): row for row in resolutions
    }
    allowed_decisions = {"pass", "changes_suggested", "human_review_required"}
    outcome_targets = {
        "not_required": "ai_consensus_reviewed",
        "human_spot_check": "human_review_required",
        "auto_applied": "ai_consensus_reviewed",
        "withdrawn": "ai_consensus_reviewed",
        "human_confirmation_required": "human_review_required",
        "human_disagreement_required": "human_review_required",
    }
    actual_counts: dict[str, int] = {}
    auto_applied_ids: set[str] = set()
    merged_claim_ids: dict[str, str] = {}
    for claim in claims:
        claim_id = str(claim["claim_id"])
        resolution = resolution_by_id[claim_id]
        if resolution.get("schema_version") != AI_REVIEW_PROVENANCE_VERSION:
            raise ConsensusApplicationError(
                f"claim has unsupported AI review provenance: {claim_id}"
            )
        decision = str(resolution.get("independent_review_decision") or "")
        outcome = str(resolution.get("adjudication_status") or "")
        if decision not in allowed_decisions or outcome not in outcome_targets:
            raise ConsensusApplicationError(
                f"claim has invalid review decision/outcome: {claim_id}"
            )
        if decision == "pass" and outcome not in {"not_required", "human_spot_check"}:
            raise ConsensusApplicationError(
                f"pass claim has an impossible adjudication outcome: {claim_id}"
            )
        if decision != "pass" and outcome in {"not_required", "human_spot_check"}:
            raise ConsensusApplicationError(
                f"actionable claim lacks an adjudication outcome: {claim_id}"
            )
        if outcome == "auto_applied" and decision != "changes_suggested":
            raise ConsensusApplicationError(
                f"auto-applied claim did not originate as changes_suggested: {claim_id}"
            )
        if (
            outcome == "human_confirmation_required"
            and decision != "human_review_required"
        ):
            raise ConsensusApplicationError(
                f"human confirmation does not originate in source ambiguity: {claim_id}"
            )
        target = str(resolution.get("target_review_status") or "")
        expected_target = outcome_targets[outcome]
        superseded_by = str(claim.get("superseded_by") or "")
        if superseded_by:
            if outcome != "auto_applied" or target != "superseded":
                raise ConsensusApplicationError(
                    f"superseded claim has inconsistent review resolution: {claim_id}"
                )
            merged_claim_ids[claim_id] = superseded_by
        elif target != expected_target:
            raise ConsensusApplicationError(
                f"claim has inconsistent final review target: {claim_id}"
            )
        if claim.get("review_status") != target:
            raise ConsensusApplicationError(
                f"claim review status disagrees with resolution: {claim_id}"
            )
        reviewer_id = str(resolution.get("reviewer_id") or "").strip()
        reason = str(resolution.get("reason") or "").strip()
        if not reviewer_id or not reason:
            raise ConsensusApplicationError(
                f"claim AI review resolution lacks reviewer or reason: {claim_id}"
            )
        if resolution.get("approval_status") != "not_human_approved":
            raise ConsensusApplicationError(
                f"claim AI review resolution implies human approval: {claim_id}"
            )
        if not superseded_by:
            if str(claim.get("reviewed_by") or "") != reviewer_id:
                raise ConsensusApplicationError(
                    f"claim reviewer disagrees with resolution: {claim_id}"
                )
            if str(claim.get("review_note") or "") != reason:
                raise ConsensusApplicationError(
                    f"claim review note disagrees with resolution: {claim_id}"
                )
            if not str(claim.get("reviewed_at") or "").strip():
                raise ConsensusApplicationError(
                    f"claim lacks deterministic review time: {claim_id}"
                )
        actual_counts[target] = actual_counts.get(target, 0) + 1
        if outcome == "auto_applied":
            auto_applied_ids.add(claim_id)

    if application.get("final_review_status_counts") != dict(sorted(actual_counts.items())):
        raise ConsensusApplicationError(
            "final review status counts disagree with package claims"
        )
    applied_claim_ids = application.get("applied_claim_ids")
    if (
        not isinstance(applied_claim_ids, list)
        or applied_claim_ids != sorted(set(str(value) for value in applied_claim_ids))
        or set(applied_claim_ids) != auto_applied_ids
    ):
        raise ConsensusApplicationError(
            "consensus-applied claims do not exactly match auto-applied adjudications"
        )
    if application.get("merged_claim_ids") != dict(sorted(merged_claim_ids.items())):
        raise ConsensusApplicationError(
            "merged claim manifest disagrees with superseded claims"
        )

    if scope_kind == RESEARCH_BATCH_AGGREGATE:
        if not isinstance(member_lineage, list) or not member_lineage:
            raise ConsensusApplicationError(
                "merged reviewed candidate lacks member artifact lineage"
            )
        required_member_fields = {
            "transcript_id",
            "reviewed_candidate_artifact_sha256",
            "review_artifact_sha256",
            "review_fingerprint",
            "adjudication_artifact_sha256",
            "adjudication_fingerprint",
            "overrides_artifact_sha256",
            "review_resolution_count",
        }
        if any(
            not isinstance(row, Mapping)
            or any(row.get(field) in {None, ""} for field in required_member_fields)
            for row in member_lineage
        ):
            raise ConsensusApplicationError(
                "merged reviewed candidate has incomplete member artifact lineage"
            )
        transcript_ids = [str(row["transcript_id"]) for row in member_lineage]
        if len(transcript_ids) != len(set(transcript_ids)):
            raise ConsensusApplicationError(
                "merged reviewed candidate repeats a member transcript"
            )
        member_by_transcript = {
            str(row["transcript_id"]): row for row in member_lineage
        }
        top_lineage = package.get("lineage")
        if not isinstance(top_lineage, list):
            raise ConsensusApplicationError(
                "merged reviewed candidate lacks merge-stage lineage"
            )
        top_transcript_ids = [
            str(row.get("transcript_id") or "")
            for row in top_lineage
            if isinstance(row, Mapping)
        ]
        if (
            len(top_transcript_ids) != len(top_lineage)
            or not all(top_transcript_ids)
            or len(top_transcript_ids) != len(set(top_transcript_ids))
            or len(top_lineage) != len(member_lineage)
        ):
            raise ConsensusApplicationError(
                "merged reviewed candidate has duplicate or invalid merge-stage lineage"
            )
        top_by_transcript = {
            str(row.get("transcript_id") or ""): row
            for row in top_lineage
            if isinstance(row, Mapping)
        }
        if set(top_by_transcript) != set(member_by_transcript) or any(
            member.get(field) != top_by_transcript[transcript_id].get(field)
            for transcript_id, member in member_by_transcript.items()
            for field in required_member_fields
        ):
            raise ConsensusApplicationError(
                "aggregate member artifacts disagree with merge-stage lineage"
            )
        source_transcript_ids = set(source_transcript_rows)
        if set(transcript_ids) != source_transcript_ids:
            raise ConsensusApplicationError(
                "merged member lineage does not cover the source documents exactly"
            )
        aggregate_fields = {
            "review_artifact_sha256": "review_artifact_sha256",
            "review_fingerprint": "review_fingerprint",
            "adjudication_artifact_sha256": "adjudication_artifact_sha256",
            "adjudication_fingerprint": "adjudication_fingerprint",
            "overrides_artifact_sha256": "overrides_artifact_sha256",
        }
        for application_field, member_field in aggregate_fields.items():
            expected = _canonical_sha256(
                [
                    (row["transcript_id"], row[member_field])
                    for row in member_lineage
                ]
            )
            if application.get(application_field) != expected:
                raise ConsensusApplicationError(
                    f"merged reviewed candidate has invalid aggregate {application_field}"
                )
        resolution_counts: dict[str, int] = {}
        source_transcript_by_id = {
            str(row.get("source_id") or ""): str(
                row.get("transcript_id") or row.get("source_id") or ""
            )
            for row in package.get("source_documents") or []
        }
        fragment_transcript_by_id = {
            str(row.get("fragment_id") or ""): source_transcript_by_id.get(
                str(row.get("source_id") or ""), ""
            )
            for row in package.get("source_fragments") or []
        }
        evidence_transcripts: dict[str, set[str]] = {}
        for evidence in package.get("evidence_steps") or []:
            fragment_ids = {
                str(value)
                for value in evidence.get("source_fragment_ids") or []
            }
            if evidence.get("source_fragment_id"):
                fragment_ids.add(str(evidence["source_fragment_id"]))
            evidence_transcripts[str(evidence.get("evidence_step_id") or "")] = {
                fragment_transcript_by_id.get(fragment_id, "")
                for fragment_id in fragment_ids
                if fragment_transcript_by_id.get(fragment_id, "")
            }
        claim_transcripts = {
            str(claim.get("claim_id") or ""): {
                transcript_id
                for evidence_id in claim.get("evidence_step_ids") or []
                for transcript_id in evidence_transcripts.get(
                    str(evidence_id), set()
                )
            }
            for claim in claims
        }
        source_resolution_fields = {
            "source_reviewed_candidate_artifact_sha256": (
                "reviewed_candidate_artifact_sha256"
            ),
            "source_review_artifact_sha256": "review_artifact_sha256",
            "source_review_fingerprint": "review_fingerprint",
            "source_adjudication_artifact_sha256": (
                "adjudication_artifact_sha256"
            ),
            "source_adjudication_fingerprint": "adjudication_fingerprint",
            "source_overrides_artifact_sha256": "overrides_artifact_sha256",
        }
        for resolution in resolutions:
            transcript_id = str(resolution.get("source_transcript_id") or "")
            member = member_by_transcript.get(transcript_id)
            claim_id = str(resolution.get("claim_id") or "")
            if claim_transcripts.get(claim_id, set()) != {transcript_id}:
                raise ConsensusApplicationError(
                    "merged resolution source does not match claim evidence: "
                    f"{claim_id}"
                )
            if member is None or any(
                resolution.get(resolution_field) != member.get(member_field)
                for resolution_field, member_field in source_resolution_fields.items()
            ):
                raise ConsensusApplicationError(
                    "merged resolution is not bound to its member artifacts: "
                    f"{resolution.get('claim_id')}"
                )
            resolution_counts[transcript_id] = (
                resolution_counts.get(transcript_id, 0) + 1
            )
        if any(
            int(row["review_resolution_count"])
            != resolution_counts.get(str(row["transcript_id"]), 0)
            for row in member_lineage
        ):
            raise ConsensusApplicationError(
                "merged member resolution counts do not match the aggregate"
            )
