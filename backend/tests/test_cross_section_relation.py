from __future__ import annotations

import hashlib

import pytest

from backend.pipeline.cross_section_relation import (
    SCHEMA_VERSION,
    CrossSectionValidationError,
    apply_proposals,
    build_catalogue,
    discovery_identity,
    existing_edges,
    record_positions,
    validate_proposals,
)
from backend.pipeline.cross_section_relation_runner import _section_boundaries


def _package() -> dict:
    return {
        "source_documents": [{"source_id": "notes_manuscript:test"}],
        "extraction": {"section_plan": {"boundaries": [0, 25]}},
        "source_fragments": [
            {"fragment_id": "FR-1", "paragraph_key": "S0018", "verbatim_excerpt": "甲"},
            {"fragment_id": "FR-2", "paragraph_key": "S0034", "verbatim_excerpt": "乙"},
            {"fragment_id": "FR-3", "paragraph_key": "S0020", "verbatim_excerpt": "丙"},
        ],
        "observations": [
            {"observation_id": "OBS1", "statement": "彼得宣认后耶稣立刻预告受苦",
             "argument_role": "load_bearing", "source_fragment_ids": ["FR-1"]},
        ],
        "evidence_steps": [
            {"evidence_step_id": "E1", "statement": "门徒缺少的是对弥赛亚性质的认识",
             "source_fragment_ids": ["FR-2"]},
            {"evidence_step_id": "E2", "statement": "保密命令有处境原因",
             "source_fragment_ids": ["FR-3"]},
        ],
        "claims": [
            {"claim_id": "CL1", "title": "弥赛亚的性质是受苦", "evidence_step_ids": ["E1"]},
            {"claim_id": "CL2", "title": "保密命令并非否认身份", "evidence_step_ids": ["E2"]},
        ],
        "knowledge_relations": [],
        "claim_relations": [],
        "summary": {"evidence_relation_count": 0, "claim_relation_count": 0},
    }


def _proposal(from_id: str = "OBS1", to_id: str = "E1") -> dict:
    return {
        "evidence_relations": [{
            "relation_id": "XER001", "from_id": from_id, "to_id": to_id,
            "relation_type": "supports", "reason": "后者的结论靠前者的事实",
        }],
        "claim_relations": [],
    }


def test_positions_come_from_the_validated_paragraph_key() -> None:
    positions = record_positions(_package())
    assert positions["OBS1"] == 17
    assert positions["E1"] == 33
    # A claim has no anchors; it inherits the position of its earliest step.
    assert positions["CL1"] == 33


def test_catalogue_carries_statements_and_positions_but_never_source_text() -> None:
    package = _package()
    rows = build_catalogue(package, record_positions(package))
    assert [row["id"] for row in rows] == ["OBS1", "CL2", "E2", "CL1", "E1"]
    assert all("verbatim_excerpt" not in row for row in rows)
    assert rows[0]["segment"] == 18


def test_accepts_a_genuinely_long_relation() -> None:
    package = _package()
    validate_proposals(
        _proposal(), package, positions=record_positions(package), boundaries=[0, 25]
    )


def test_rejects_a_relation_extraction_could_already_see() -> None:
    """Same-section relations belong to the stage that holds the anchors."""

    package = _package()
    with pytest.raises(CrossSectionValidationError, match="same section"):
        validate_proposals(
            _proposal(to_id="E2"), package,
            positions=record_positions(package), boundaries=[0, 25],
        )


def test_rejects_an_invented_record() -> None:
    package = _package()
    with pytest.raises(CrossSectionValidationError, match="not a record this stage may relate"):
        validate_proposals(
            _proposal(to_id="E404"), package,
            positions=record_positions(package), boundaries=[0, 25],
        )


def test_rejects_an_edge_that_already_exists() -> None:
    package = _package()
    package["knowledge_relations"] = [{
        "relation_id": "ER001", "from_id": "OBS1", "to_id": "E1",
        "relation_type": "supports", "reason": "r",
    }]
    with pytest.raises(CrossSectionValidationError, match="already related"):
        validate_proposals(
            _proposal(), package, positions=record_positions(package), boundaries=[0, 25]
        )
    assert ("E1", "OBS1") in existing_edges(package), "edges must be undirected"


def test_rejects_a_relation_pointing_at_an_observation() -> None:
    """Same rule as extraction: nothing supports an observation."""

    package = _package()
    proposal = _proposal(from_id="E1", to_id="OBS1")
    with pytest.raises(CrossSectionValidationError, match="not a record this stage may relate to"):
        validate_proposals(
            proposal, package, positions=record_positions(package), boundaries=[0, 25]
        )


def test_added_relations_say_where_they_came_from() -> None:
    package = _package()
    updated = apply_proposals(package, _proposal(), identity={"fingerprint_sha256": "fp"})
    added = updated["knowledge_relations"][0]
    namespace = hashlib.sha256("notes_manuscript:test".encode()).hexdigest()[:12]
    assert added["relation_id"] == f"DK-{namespace}-XER001"
    assert added["discovered_by"] == SCHEMA_VERSION
    assert added["review_status"] == "candidate"
    assert updated["summary"]["evidence_relation_count"] == 1
    assert updated["cross_section_relations"]["evidence_relations_added"] == 1
    # The source package is not mutated.
    assert package["knowledge_relations"] == []


def test_claim_relations_receive_the_same_source_namespace() -> None:
    package = _package()
    proposal = {
        "evidence_relations": [],
        "claim_relations": [{
            "claim_relation_id": "XCR001",
            "from_id": "CL1",
            "to_id": "CL2",
            "relation_type": "qualifies",
            "reason": "后者限定前者",
        }],
    }

    updated = apply_proposals(package, proposal, identity={"fingerprint_sha256": "fp"})

    namespace = hashlib.sha256("notes_manuscript:test".encode()).hexdigest()[:12]
    assert updated["claim_relations"][0]["claim_relation_id"] == (
        f"DK-{namespace}-XCR001"
    )


def test_boundaries_follow_the_package_section_plan() -> None:
    """Resection the source and this stage follows, with nothing to remember."""

    assert _section_boundaries(_package()) == [0, 25]
    resectioned = _package()
    resectioned["extraction"]["section_plan"]["boundaries"] = [0, 10, 40]
    assert _section_boundaries(resectioned) == [0, 10, 40]
    # No plan means one section, so every proposal is same-section and rejected.
    assert _section_boundaries({"extraction": {}}) == [0]


def test_subscription_generation_has_a_distinct_backend_bound_fingerprint() -> None:
    kwargs = {
        "package_sha256": "package", "prompt": "prompt",
        "model_id": "gpt-5.6-sol", "section_count": 2,
    }
    api = discovery_identity(**kwargs)
    subscription = discovery_identity(**kwargs, backend="codex-subscription")
    assert "backend" not in api
    assert subscription["backend"] == "codex-subscription"
    assert subscription["fingerprint_sha256"] != api["fingerprint_sha256"]
