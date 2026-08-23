from __future__ import annotations

from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord,
    ClaimRelationRecord,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_recall_blocking import (
    ViewpointRecallBlockingArtifact,
    build_viewpoint_recall_blocking,
    normalize_recall_term,
)


def _claim(
    claim_id: str,
    *,
    term: str,
    scripture: str = "Matt.16.18",
    claim_type: str = "interpretive_judgment",
    review_status: str = "candidate",
) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        statement=f"{claim_id} statement",
        claim_type=claim_type,
        attribution="professor",
        review_status=review_status,
        scripture_refs=[scripture] if scripture else [],
        topic_terms=[term] if term else [],
    )


def _manifest(claims: list[ClaimRecord], sources: dict[str, str]) -> dict:
    payload = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "coverage_snapshot_id": "VCS-1",
        "claims": [
            {
                "claim_id": claim.claim_id,
                "pinned_claim_revision": claim.revision,
                "claim_revision_sha256": semantic_record_sha(claim),
                "source_id": sources[claim.claim_id],
            }
            for claim in sorted(claims, key=lambda item: item.claim_id)
        ],
    }
    payload["manifest_sha256"] = sha256_json(payload)
    return payload


def test_recall_blocking_normalizes_terms_and_keeps_identity_unresolved():
    claims = [_claim("C1", term="圣灵"), _claim("C2", term="聖靈")]
    artifact = build_viewpoint_recall_blocking(
        claim_manifest=_manifest(claims, {"C1": "S1", "C2": "S2"}),
        claims=claims,
    )

    assert normalize_recall_term(" 圣灵 ") == "聖靈"
    assert artifact.statistics["unique_candidate_pair_count"] == 1
    assert artifact.neighborhoods[0].neighbors[0].signals == [
        "compatible_claim_role",
        "cross_source",
        "shared_scripture_chapter",
        "shared_topic_term",
    ]
    assert artifact.neighborhoods[0].neighbors[0].candidate_viewpoint_ids == []
    ViewpointRecallBlockingArtifact.model_validate(artifact.model_dump(mode="json"))


def test_recall_blocking_uses_scripture_and_role_without_text_similarity():
    claims = [
        _claim("C1", term="彼得", scripture="馬太福音16:18"),
        _claim("C2", term="磐石", scripture="Matt.16.13-Matt.16.20"),
        _claim("C3", term="教會", scripture="Matt.16.18", claim_type="application"),
    ]
    artifact = build_viewpoint_recall_blocking(
        claim_manifest=_manifest(
            claims, {"C1": "S1", "C2": "S2", "C3": "S3"}
        ),
        claims=claims,
    )

    by_id = {item.focal_claim_id: item for item in artifact.neighborhoods}
    assert [item.claim_id for item in by_id["C1"].neighbors] == ["C2"]
    assert by_id["C3"].neighbors == []
    assert artifact.uncovered_claim_ids == ["C3"]


def test_recall_blocking_does_not_pair_one_same_source_term_without_more_evidence():
    claims = [
        _claim("C1", term="彼得", scripture=""),
        _claim("C2", term="彼得", scripture=""),
    ]
    artifact = build_viewpoint_recall_blocking(
        claim_manifest=_manifest(claims, {"C1": "S1", "C2": "S1"}),
        claims=claims,
    )

    assert artifact.statistics["unique_candidate_pair_count"] == 0
    assert artifact.uncovered_claim_ids == ["C1", "C2"]


def test_recall_blocking_records_suppressed_blocks_and_keeps_reviewed_gold():
    claims = [_claim(f"C{i}", term="彼得") for i in range(1, 5)]
    relation = ClaimRelationRecord(
        claim_relation_id="CR-1",
        from_id="C1",
        to_id="C4",
        relation_type="duplicate",
        review_status="ai_consensus",
    )
    artifact = build_viewpoint_recall_blocking(
        claim_manifest=_manifest(
            claims, {claim.claim_id: f"S{claim.claim_id}" for claim in claims}
        ),
        claims=claims,
        claim_relations=[relation],
        max_block_claims=3,
        max_neighbors_per_claim=1,
    )

    assert {item.block_key for item in artifact.suppressed_blocks} == {
        "scripture:Matt.16",
        "term:彼得",
    }
    assert artifact.known_positive_recall.model_dump() == {
        "eligible_pair_count": 1,
        "found_pair_count": 1,
        "recall": 1.0,
    }
    by_id = {item.focal_claim_id: item for item in artifact.neighborhoods}
    assert by_id["C1"].neighbors[0].claim_id == "C4"


def test_recall_blocking_keeps_ineligible_claims_in_denominator_without_neighbors():
    claims = [
        _claim("C1", term="磐石"),
        _claim("C2", term="磐石", review_status="superseded"),
    ]
    artifact = build_viewpoint_recall_blocking(
        claim_manifest=_manifest(claims, {"C1": "S1", "C2": "S2"}),
        claims=claims,
    )

    assert artifact.statistics["input_claim_count"] == 2
    assert artifact.statistics["eligible_claim_count"] == 1
    assert artifact.source_ineligible_claim_ids == ["C2"]
    assert [item.focal_claim_id for item in artifact.neighborhoods] == ["C1"]
