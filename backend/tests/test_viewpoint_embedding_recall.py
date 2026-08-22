from __future__ import annotations

import pytest

import backend.api.canonical_repository.viewpoint_embedding_recall as recall_module

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_embedding_recall import (
    ViewpointEmbeddingRecallArtifact,
    build_viewpoint_embedding_recall,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.semantic_index.embeddings import (
    EmbeddingProviderDescriptor,
    build_embedding_generation_plan,
    build_embedding_index_artifact,
)
from backend.api.semantic_index.projections import build_claim_embedding_projection


class FakeProvider:
    descriptor = EmbeddingProviderDescriptor(
        provider="fake",
        model="fixture-v1",
        dimensions=3,
        provider_contract_version="fixture_exact_v1",
    )

    def prepare_document(self, projection, use_case):
        return f"{use_case}:{projection.text}"


def _claim(claim_id: str, statement: str) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        statement=statement,
        claim_type="interpretive_judgment",
        attribution="professor",
        scripture_refs=["Matt.16.18"],
    )


def _manifest(claims: list[ClaimRecord]) -> dict:
    payload = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "coverage_snapshot_id": "VCS-1",
        "claims": [
            {
                "claim_id": claim.claim_id,
                "pinned_claim_revision": claim.revision,
                "claim_revision_sha256": semantic_record_sha(claim),
                "source_id": f"S-{claim.claim_id}",
            }
            for claim in sorted(claims, key=lambda item: item.claim_id)
        ],
    }
    payload["manifest_sha256"] = sha256_json(payload)
    return payload


def _index(claims: list[ClaimRecord], vectors: list[list[float]]):
    projections = [build_claim_embedding_projection(claim) for claim in claims]
    plan = build_embedding_generation_plan(
        projections=projections,
        provider=FakeProvider(),
        use_case="candidate_recall",
    )
    return build_embedding_index_artifact(
        plan=plan,
        projections=projections,
        vectors_by_object_id={
            claim.claim_id: vector
            for claim, vector in zip(claims, vectors, strict=True)
        },
    )


def test_embedding_recall_is_bounded_stable_and_recall_only():
    claims = [
        _claim("C1", "彼得本人是磐石"),
        _claim("C2", "彼得就是磐石"),
        _claim("C3", "磐石不是彼得本人，而是基督"),
        _claim("C4", "五饼二鱼显明基督的供应"),
    ]
    index = _index(
        claims,
        [
            [1.0, 0.0, 0.0],
            [0.99, 0.01, 0.0],
            [0.95, 0.05, 0.0],
            [0.0, 1.0, 0.0],
        ],
    )

    artifact = build_viewpoint_embedding_recall(
        claim_manifest=_manifest(claims),
        embedding_index=index,
        top_k=2,
    )

    by_id = {item.focal_claim_id: item for item in artifact.neighborhoods}
    assert [item.claim_id for item in by_id["C1"].neighbors] == ["C2", "C3"]
    assert all(not item.identity_evidence for item in by_id["C1"].neighbors)
    assert artifact.recall_only is True
    assert artifact.statistics["directed_neighbor_count"] == 8
    ViewpointEmbeddingRecallArtifact.model_validate(artifact.model_dump(mode="json"))


def test_embedding_recall_threshold_can_leave_claim_uncovered():
    claims = [_claim("C1", "彼得本人是磐石"), _claim("C2", "五饼二鱼")]
    artifact = build_viewpoint_embedding_recall(
        claim_manifest=_manifest(claims),
        embedding_index=_index(claims, [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]),
        top_k=1,
        minimum_similarity=0.5,
    )

    assert artifact.uncovered_claim_ids == ["C1", "C2"]


def test_embedding_recall_requires_exact_manifest_coverage():
    claims = [_claim("C1", "彼得本人是磐石"), _claim("C2", "彼得就是磐石")]
    index = _index(claims[:1], [[1.0, 0.0, 0.0]])

    with pytest.raises(ValueError, match="exactly cover"):
        build_viewpoint_embedding_recall(
            claim_manifest=_manifest(claims),
            embedding_index=index,
        )

    artifact = build_viewpoint_embedding_recall(
        claim_manifest=_manifest(claims),
        embedding_index=index,
        source_ineligible_claim_ids=["C2"],
    )
    assert artifact.source_ineligible_claim_ids == ["C2"]
    assert artifact.statistics["input_claim_count"] == 2
    assert artifact.statistics["eligible_claim_count"] == 1


def test_exact_embedding_recall_refuses_unbounded_corpus(monkeypatch):
    claims = [_claim("C1", "彼得本人是磐石"), _claim("C2", "彼得就是磐石")]
    index = _index(claims, [[1.0, 0.0, 0.0], [0.99, 0.01, 0.0]])
    monkeypatch.setattr(recall_module, "MAX_EXACT_RECALL_RECORDS", 1)

    with pytest.raises(ValueError, match="bounded ANN index"):
        build_viewpoint_embedding_recall(
            claim_manifest=_manifest(claims),
            embedding_index=index,
        )
