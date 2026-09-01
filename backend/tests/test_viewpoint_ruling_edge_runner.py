"""The ruling file decides; the runner refuses everything it does not license."""

from __future__ import annotations

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_ruling_edge_runner import (
    RULING_SCHEMA_VERSION,
    RulingExecutionError,
    _validated_ruling,
    build_claim_relation_additions,
    build_viewpoint_relation_additions,
)


def _ruling(**overrides):
    payload = {
        "schema_version": RULING_SCHEMA_VERSION,
        "decided_by": "junyang",
        "decided_at": "2026-09-01",
        "recorded_at_urls": ["https://github.com/junyang168/smart-answer/issues/322"],
        "claim_relation_additions": [],
        "viewpoint_relation_additions": [],
    }
    payload.update(overrides)
    payload["artifact_sha256"] = sha256_json(payload)
    return _validated_ruling(payload)


CLAIMS = {
    "DK-s1-CL006": {"claim_id": "DK-s1-CL006", "source_id": "SRC-1"},
    "DK-s1-CL007": {"claim_id": "DK-s1-CL007", "source_id": "SRC-1"},
    "DK-s2-CL001": {"claim_id": "DK-s2-CL001", "source_id": "SRC-2"},
}


def _claim_edge(**overrides):
    edge = {
        "from_claim_id": "DK-s1-CL007",
        "to_claim_id": "DK-s1-CL006",
        "relation_type": "supports",
        "reason": "四兽=帝国是人子识别的承重前提。判定者：junyang。",
    }
    edge.update(overrides)
    return edge


def test_ruled_claim_edge_is_built_with_provenance():
    rows = build_claim_relation_additions(
        _ruling(claim_relation_additions=[_claim_edge()]),
        claims_by_id=CLAIMS,
        active_relations=[],
    )
    assert len(rows) == 1
    assert rows[0]["review_status"] == "human_approved"
    assert rows[0]["claim_relation_id"].startswith("CR-")


def test_claim_edge_outside_the_dependency_allowlist_is_refused():
    with pytest.raises(RulingExecutionError, match="not an argument dependency"):
        build_claim_relation_additions(
            _ruling(
                claim_relation_additions=[_claim_edge(relation_type="contextualizes")]
            ),
            claims_by_id=CLAIMS,
            active_relations=[],
        )


def test_cross_source_claim_edge_is_refused():
    with pytest.raises(RulingExecutionError, match="source-local"):
        build_claim_relation_additions(
            _ruling(
                claim_relation_additions=[_claim_edge(to_claim_id="DK-s2-CL001")]
            ),
            claims_by_id=CLAIMS,
            active_relations=[],
        )


def test_duplicate_active_claim_edge_is_refused():
    with pytest.raises(RulingExecutionError, match="already exists"):
        build_claim_relation_additions(
            _ruling(claim_relation_additions=[_claim_edge()]),
            claims_by_id=CLAIMS,
            active_relations=[
                {
                    "from_id": "DK-s1-CL007",
                    "to_id": "DK-s1-CL006",
                    "relation_type": "supports",
                }
            ],
        )


VIEWPOINTS = {
    "CV-A": {"viewpoint_id": "CV-A", "current_revision_id": "CVR-A2"},
    "CV-B": {"viewpoint_id": "CV-B", "current_revision_id": "CVR-B1"},
}
REVISIONS = {
    "CVR-A1": {"viewpoint_revision_id": "CVR-A1", "viewpoint_id": "CV-A"},
    "CVR-A2": {"viewpoint_revision_id": "CVR-A2", "viewpoint_id": "CV-A"},
    "CVR-B1": {"viewpoint_revision_id": "CVR-B1", "viewpoint_id": "CV-B"},
}


def _viewpoint_edge(**overrides):
    edge = {
        "source_viewpoint_revision_id": "CVR-A2",
        "target_viewpoint_revision_id": "CVR-B1",
        "relation_type": "entails",
        "reason": "若角色仅是执行天上标准，则不存在任意权柄。判定者：junyang。",
    }
    edge.update(overrides)
    return edge


def test_ruled_viewpoint_edge_carries_the_ruling_as_provenance():
    ruling = _ruling(viewpoint_relation_additions=[_viewpoint_edge()])
    rows = build_viewpoint_relation_additions(
        ruling,
        viewpoints_by_id=VIEWPOINTS,
        revisions_by_id=REVISIONS,
        active_relations=[],
    )
    assert rows[0]["review_provenance"]["review_artifact_sha256"] == ruling["artifact_sha256"]
    assert rows[0]["source_viewpoint_id"] == "CV-A"


def test_stale_revision_returns_to_the_owner_instead_of_following_the_head():
    with pytest.raises(RulingExecutionError, match="no longer the current revision"):
        build_viewpoint_relation_additions(
            _ruling(
                viewpoint_relation_additions=[
                    _viewpoint_edge(source_viewpoint_revision_id="CVR-A1")
                ]
            ),
            viewpoints_by_id=VIEWPOINTS,
            revisions_by_id=REVISIONS,
            active_relations=[],
        )


def test_tampered_ruling_file_is_refused():
    payload = {
        "schema_version": RULING_SCHEMA_VERSION,
        "decided_by": "junyang",
        "decided_at": "2026-09-01",
        "recorded_at_urls": ["https://example.invalid"],
    }
    payload["artifact_sha256"] = sha256_json(payload)
    payload["decided_by"] = "someone-else"
    with pytest.raises(RulingExecutionError, match="SHA mismatch"):
        _validated_ruling(payload)
