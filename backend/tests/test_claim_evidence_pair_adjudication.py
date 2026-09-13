from __future__ import annotations

from backend.pipeline.claim_evidence_pair_adjudication import (
    CONSENSUS_SCHEMA_VERSION,
    FINAL_DECISIONS_SCHEMA_VERSION,
    PACKET_SCHEMA_VERSION,
    REVIEW_SCHEMA_VERSION,
    aggregate_relation_consensus,
    build_relation_packets,
    build_pair_repair_plan,
    compile_pair_repair_authorization,
    compile_relation_consensus,
    merge_reconsidered_consensus,
    review_relation_packets,
    split_relation_packets,
)
from backend.api.canonical_repository.postgres_store import (
    build_claim_evidence_active_snapshot,
    build_review_event_ledger_snapshot,
    build_source_lineage_identity_snapshot,
    record_content_sha,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    AUDIT_INPUT_SCHEMA_VERSION,
    AUDIT_SCHEMA_VERSION,
    ClaimEvidenceReciprocityRepairError,
    seal_artifact,
)


def _record(collection: str, object_id: str, payload: dict, *, revision: int = 1) -> dict:
    stored_payload = {**payload, "revision": revision}
    return {
        "collection": collection,
        "object_id": object_id,
        "revision": revision,
        "content_sha256": record_content_sha(stored_payload),
        "payload": stored_payload,
    }


def _inputs() -> tuple[dict, dict, list[dict]]:
    claim = _record(
        "claims",
        "CL-1",
        {"claim_id": "CL-1", "statement": "主张", "evidence_step_ids": ["E-OLD"]},
    )
    old = _record(
        "evidence_steps",
        "E-OLD",
        {"evidence_step_id": "E-OLD", "statement": "旧证据"},
    )
    evidence = _record(
        "evidence_steps",
        "E-NEW",
        {
            "evidence_step_id": "E-NEW",
            "statement": "候选证据",
            "produced_claim_ids": ["CL-1"],
            "source_fragment_ids": ["FR-1"],
        },
    )
    records = [claim, old, evidence]
    active_snapshot = build_claim_evidence_active_snapshot(
        [
            (
                row["collection"],
                row["object_id"],
                row["revision"],
                row["content_sha256"],
                row["payload"],
            )
            for row in records
        ]
    )
    frozen = seal_artifact(
        {
            "schema_version": AUDIT_INPUT_SCHEMA_VERSION,
            "active_snapshot": active_snapshot,
            "active_records": records,
        }
    )
    endpoint = {
        "revision": 1,
        "content_sha256": evidence["content_sha256"],
        "object_version": {"payload": evidence["payload"]},
        "source_lineage": {"source_documents": [{"object_id": "SRC-1"}]},
    }
    source_snapshot = build_source_lineage_identity_snapshot(
        [
            {
                "collection": "source_fragments",
                "object_id": "FR-1",
                "revision": 1,
                "content_sha256": "a" * 64,
                "retired": False,
            },
            {
                "collection": "source_documents",
                "object_id": "SRC-1",
                "revision": 1,
                "content_sha256": "a" * 64,
                "retired": False,
            },
        ]
    )
    audit = seal_artifact(
        {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "active_snapshot_sha256": active_snapshot["snapshot_sha256"],
            "store_snapshot": active_snapshot,
            "review_event_ledger_snapshot": build_review_event_ledger_snapshot(()),
            "source_lineage_identity_snapshot": source_snapshot,
            "pairs": [
                {
                    "pair_id": "PAIR-1",
                    "claim_id": "CL-1",
                    "evidence_step_id": "E-NEW",
                    "mismatch_type": "evidence_only",
                    "claim_endpoint": {"revision": 1, "content_sha256": claim["content_sha256"], "object_version": {"payload": claim["payload"]}},
                    "evidence_endpoint": endpoint,
                },
                {
                    "pair_id": "PAIR-OLD",
                    "claim_id": "CL-1",
                    "evidence_step_id": "E-OLD",
                    "mismatch_type": "claim_only",
                    "claim_endpoint": {
                        "revision": 1,
                        "content_sha256": claim["content_sha256"],
                        "object_version": {"payload": claim["payload"]},
                    },
                    "evidence_endpoint": {
                        "revision": 1,
                        "content_sha256": old["content_sha256"],
                        "object_version": {"payload": old["payload"]},
                    },
                }
            ],
        }
    )
    sources = [
        {"collection": "source_fragments", "object_id": "FR-1", "revision": 1, "content_sha256": "a" * 64, "retired": False, "payload": {"source_id": "SRC-1", "verbatim_excerpt": "逐字原文"}},
        {"collection": "source_documents", "object_id": "SRC-1", "revision": 1, "content_sha256": "a" * 64, "retired": False, "payload": {"source_type": "sermon_transcript", "transcript_id": "讲道一"}},
    ]
    return audit, frozen, sources


def test_builds_targeted_packet_with_existing_evidence_and_source() -> None:
    audit, frozen, sources = _inputs()
    result = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    assert result["schema_version"] == PACKET_SCHEMA_VERSION
    assert result["pair_ids"] == ["PAIR-1"]
    packet = result["packets"][0]
    assert packet["claim_existing_evidence"][0]["evidence_step_id"] == "E-OLD"
    assert packet["source_fragments"][0]["verbatim_excerpt"] == "逐字原文"


def test_rejects_source_row_drift_after_freeze() -> None:
    audit, frozen, sources = _inputs()
    sources[0]["revision"] = 2
    try:
        build_relation_packets(
            audit_artifact=audit, frozen_input=frozen, source_records=sources
        )
    except ClaimEvidenceReciprocityRepairError as exc:
        assert "drifted after freeze" in str(exc)
    else:
        raise AssertionError("source drift must fail closed")


class _Client:
    model = "test-model"
    backend = "test-backend"

    def __init__(self, decisions: list[dict]):
        self.decisions = decisions

    def generate_json(self, *_args, **_kwargs):
        return {"decisions": self.decisions}


def _decision(value: str) -> dict:
    return {
        "pair_id": "PAIR-1",
        "decision": value,
        "reason_code": "direct_support" if value == "include" else "context_only",
        "confidence": "high",
        "explanation": "机械测试",
    }


def test_review_requires_exact_pair_coverage() -> None:
    audit, frozen, sources = _inputs()
    packets = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    try:
        review_relation_packets(
            packet_artifact=packets, client=_Client([]), reviewer_role="test"
        )
    except ClaimEvidenceReciprocityRepairError as exc:
        assert "every packet exactly once" in str(exc)
    else:
        raise AssertionError("incomplete review must fail closed")


def test_consensus_keeps_agreement_and_routes_disagreement_to_human() -> None:
    audit, frozen, sources = _inputs()
    packets = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    include = review_relation_packets(
        packet_artifact=packets,
        client=_Client([_decision("include")]),
        reviewer_role="first",
    )
    same = review_relation_packets(
        packet_artifact=packets,
        client=_Client([_decision("include")]),
        reviewer_role="second",
    )
    agreed = compile_relation_consensus(
        packet_artifact=packets, first_review=include, second_review=same
    )
    assert agreed["schema_version"] == CONSENSUS_SCHEMA_VERSION
    assert agreed["counts"] == {"include": 1, "exclude": 0, "needs_human": 0}

    excluded = review_relation_packets(
        packet_artifact=packets,
        client=_Client([_decision("exclude")]),
        reviewer_role="second",
    )
    disputed = compile_relation_consensus(
        packet_artifact=packets, first_review=include, second_review=excluded
    )
    assert disputed["counts"]["needs_human"] == 1
    assert include["schema_version"] == REVIEW_SCHEMA_VERSION


def test_split_and_aggregate_remain_bound_to_full_packet_root() -> None:
    audit, frozen, sources = _inputs()
    packets = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    batches = split_relation_packets(packets, batch_size=1)
    first = review_relation_packets(
        packet_artifact=batches[0],
        client=_Client([_decision("include")]),
        reviewer_role="first",
    )
    second = review_relation_packets(
        packet_artifact=batches[0],
        client=_Client([_decision("include")]),
        reviewer_role="second",
    )
    consensus = compile_relation_consensus(
        packet_artifact=batches[0], first_review=first, second_review=second
    )
    combined = aggregate_relation_consensus(
        packet_artifact=packets,
        packet_batches=batches,
        consensus_batches=[consensus],
    )
    assert combined["counts"]["include"] == 1
    assert combined["packet_artifact_sha256"] == packets["artifact_sha256"]


def test_reconsidered_consensus_replaces_only_the_disputed_decision() -> None:
    audit, frozen, sources = _inputs()
    packets = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    include = review_relation_packets(
        packet_artifact=packets,
        client=_Client([_decision("include")]),
        reviewer_role="first",
    )
    exclude = review_relation_packets(
        packet_artifact=packets,
        client=_Client([_decision("exclude")]),
        reviewer_role="second",
    )
    initial = compile_relation_consensus(
        packet_artifact=packets, first_review=include, second_review=exclude
    )
    disputes = seal_artifact(
        {
            "schema_version": PACKET_SCHEMA_VERSION,
            "parent_packet_artifact_sha256": packets["artifact_sha256"],
            "prior_consensus_artifact_sha256": initial["artifact_sha256"],
            "pair_ids": ["PAIR-1"],
            "packets": packets["packets"],
        }
    )
    reconsidered = seal_artifact(
        {
            "schema_version": CONSENSUS_SCHEMA_VERSION,
            "packet_artifact_sha256": disputes["artifact_sha256"],
            "counts": {"include": 0, "exclude": 1, "needs_human": 0},
            "decisions": [
                {
                    "pair_id": "PAIR-1",
                    "decision": "exclude",
                    "reason_code": "independent_model_consensus",
                }
            ],
        }
    )

    final = merge_reconsidered_consensus(
        packet_artifact=packets,
        initial_consensus=initial,
        dispute_packets=disputes,
        dispute_consensus=reconsidered,
    )

    assert final["schema_version"] == FINAL_DECISIONS_SCHEMA_VERSION
    assert final["counts"] == {"include": 0, "exclude": 1, "needs_human": 0}
    assert final["decisions"][0]["initial_disagreement"]["decision"] == "needs_human"


def _final_decisions(packets: dict, decision: str) -> dict:
    return seal_artifact(
        {
            "schema_version": FINAL_DECISIONS_SCHEMA_VERSION,
            "packet_artifact_sha256": packets["artifact_sha256"],
            "counts": {
                "include": int(decision == "include"),
                "exclude": int(decision == "exclude"),
                "needs_human": int(decision == "needs_human"),
            },
            "decisions": [
                {
                    "pair_id": "PAIR-1",
                    "decision": decision,
                    "reason_code": "test",
                }
            ],
        }
    )


def test_builds_one_clean_pair_repair_preview() -> None:
    audit, frozen, sources = _inputs()
    packets = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    final = _final_decisions(packets, "include")

    authorization = compile_pair_repair_authorization(
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packets,
        final_decisions=final,
    )
    preview = build_pair_repair_plan(
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packets,
        final_decisions=final,
    )

    assert authorization["action_counts"] == {
        "add_claim_forward": 1,
        "add_evidence_reverse": 1,
        "remove_evidence_reverse": 0,
    }
    assert preview["changed_object_counts"] == {
        "claims": 1,
        "evidence_steps": 1,
        "total": 2,
    }
    assert preview["expected_final_counts"]["claim_only_pairs"] == 0
    assert preview["expected_final_counts"]["evidence_only_pairs"] == 0


def test_persistent_disagreement_preserves_the_claim_projection() -> None:
    audit, frozen, sources = _inputs()
    packets = build_relation_packets(
        audit_artifact=audit, frozen_input=frozen, source_records=sources
    )
    authorization = compile_pair_repair_authorization(
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packets,
        final_decisions=_final_decisions(packets, "needs_human"),
    )

    disputed = next(
        row for row in authorization["actions"] if row["pair_id"] == "PAIR-1"
    )
    assert disputed["effective_decision"] == "exclude"
    assert disputed["decision_basis"] == (
        "preserve_claim_projection_on_persistent_model_disagreement"
    )
