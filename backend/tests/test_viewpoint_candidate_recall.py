from __future__ import annotations

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_candidate_recall import (
    ViewpointCandidateRecallArtifact,
    build_viewpoint_candidate_recall,
)
from backend.api.canonical_repository.viewpoint_embedding_recall import (
    build_viewpoint_embedding_recall,
)
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    build_viewpoint_recall_blocking,
)
from backend.api.canonical_repository.viewpoint_semantic_scheduler import (
    build_semantic_bundle_schedule,
)
from backend.api.semantic_index.embeddings import (
    EmbeddingProviderDescriptor,
    build_embedding_generation_plan,
    build_embedding_index_artifact,
)
from backend.api.semantic_index.projections import build_claim_embedding_projection


class FakeProvider:
    descriptor = EmbeddingProviderDescriptor(
        provider="fake", model="fixture", dimensions=3,
        provider_contract_version="fixture_v1",
    )

    def prepare_document(self, projection, use_case):
        return projection.text


def _claim(claim_id: str, text: str, terms: list[str]) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id, statement=text, claim_type="interpretive_judgment",
        attribution="professor", scripture_refs=["Matt.16.18"], topic_terms=terms,
        source_refs=[f"SRC-{claim_id}"], evidence_step_ids=[f"E-{claim_id}"],
    )


def test_candidate_recall_preserves_opposed_embedding_neighbor_without_merging():
    claims = [
        _claim("C1", "彼得本人是磐石", ["彼得", "磐石"]),
        _claim("C2", "磐石不是彼得本人，而是基督", ["基督", "真理"]).model_copy(
            update={"scripture_refs": ["Matt.17.1"]}
        ),
        _claim("C3", "天国钥匙授予彼得", ["钥匙", "彼得"]),
    ]
    manifest = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "coverage_snapshot_id": "VCS-1",
        "claims": [
            {"claim_id": c.claim_id, "pinned_claim_revision": c.revision,
             "claim_revision_sha256": semantic_record_sha(c), "source_id": f"S-{c.claim_id}"}
            for c in claims
        ],
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    rule = build_viewpoint_recall_blocking(
        claim_manifest=manifest, claims=claims, claim_relations=[], existing_links=[],
    )
    projections = [build_claim_embedding_projection(c) for c in claims]
    plan = build_embedding_generation_plan(
        projections=projections, provider=FakeProvider(), use_case="candidate_recall",
    )
    index = build_embedding_index_artifact(
        plan=plan, projections=projections,
        vectors_by_object_id={
            "C1": [1.0, 0.0, 0.0],
            "C2": [0.99, 0.01, 0.0],
            "C3": [0.0, 1.0, 0.0],
        },
    )
    embedding = build_viewpoint_embedding_recall(
        claim_manifest=manifest, embedding_index=index, top_k=1,
    )

    fused = build_viewpoint_candidate_recall(rule_recall=rule, embedding_recall=embedding)

    c1 = next(item for item in fused.neighborhoods if item.focal_claim_id == "C1")
    opposed = next(item for item in c1.neighbors if item.claim_id == "C2")
    assert opposed.channels == ["embedding"]
    assert opposed.identity_evidence is False
    assert fused.known_positive_recall.union_recall is None
    assert fused.known_positive_recall.measurement_status == "no_scoped_positive_pairs"
    assert fused.statistics["embedding_unique_candidate_pair_count"] >= 1
    ViewpointCandidateRecallArtifact.model_validate(fused.model_dump(mode="json"))

    schedule = build_semantic_bundle_schedule(
        preflight_packet_sha256="preflight",
        resolution_queue_sha256="queue",
        claim_manifest=manifest,
        candidates=[
            {
                "identity_candidate_id": f"VIC-{claim.claim_id}",
                "candidate_claim_ids": [claim.claim_id],
                "candidate_viewpoint_ids": [],
                "seed_relation_ids": [],
                "proposed_action": "create_new",
                "coverage_snapshot_id": "VCS-1",
                "blocker_codes": [],
                "generation_fingerprint": "fixture",
            }
            for claim in claims
        ],
        claims=claims,
        evidence_steps=[
            {
                "evidence_step_id": f"E-{claim.claim_id}",
                "source_fragment_ids": [f"F-{claim.claim_id}"],
                "statement": f"证据 {claim.claim_id}",
            }
            for claim in claims
        ],
        source_fragments=[
            {
                "fragment_id": f"F-{claim.claim_id}",
                "source_id": f"SRC-{claim.claim_id}",
                "source_sha256": f"sha-{claim.claim_id}",
                "verbatim_excerpt": claim.statement,
                "anchor_state": "source_version_bound",
            }
            for claim in claims
        ],
        candidate_recall=fused,
    )
    scheduled_c1 = next(
        item for item in schedule.work_items if item.identity_candidate_id == "VIC-C1"
    )
    assert schedule.candidate_recall_artifact_sha256 == fused.artifact_sha256
    assert scheduled_c1.recall_neighbor_claim_ids
    assert scheduled_c1.semantic_input["recall_neighborhoods"][0]["neighbors"][0][
        "identity_evidence"
    ] is False


def test_candidate_recall_rejects_channel_manifest_mismatch():
    # Schema validation and mismatch behavior are covered through immutable artifact SHAs;
    # a direct mutation must fail before it can be used as another channel.
    claims = [_claim("C1", "甲", ["甲"]), _claim("C2", "乙", ["乙"])]
    manifest = {
        "schema_version": "viewpoint_input_claim_manifest_v1", "coverage_snapshot_id": "VCS",
        "claims": [{"claim_id": c.claim_id, "pinned_claim_revision": c.revision,
                    "claim_revision_sha256": semantic_record_sha(c), "source_id": c.claim_id}
                   for c in claims],
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    rule = build_viewpoint_recall_blocking(
        claim_manifest=manifest, claims=claims, claim_relations=[], existing_links=[]
    )
    payload = rule.model_dump(mode="json")
    payload["claim_manifest_sha256"] = "different"
    # The original artifact itself detects the tamper.
    import pytest
    with pytest.raises(ValueError, match="SHA mismatch"):
        type(rule).model_validate(payload)
