import json
from pathlib import Path

import pytest

from backend.pipeline.matthew_16_argument_integration import (
    SMALL_FAITH_CLAIMS,
    SMALL_FAITH_TOPIC_ID,
    build_integration_package,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = (
    Path(__file__).parent / "fixtures/wang_knowledge_platform/matthew_16_notes/comparison"
)
COMPARISON = FIXTURE_DIR / "comparison-knowledge.json"
PATCH = FIXTURE_DIR / "composition-update-candidate.json"


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_builds_one_claim_graph_with_separate_exposition_and_topic_routes():
    package, report = build_integration_package(_load(COMPARISON), _load(PATCH), b"manuscript")

    plans = {item["plan_id"]: item for item in package["product_plans"]}
    assert set(plans) == {
        "CP-matthew-16-1-12",
        "CP-topic-small-faith",
        "CP-topic-signs-revelation-scripture-authority",
    }
    assert len(plans["CP-matthew-16-1-12"]["decisions"]) == 6
    route_ids = [item["route_id"] for item in package["knowledge_routes"]]
    assert len(route_ids) == len(set(route_ids))

    repeated_claim_route = next(
        item
        for item in package["knowledge_routes"]
        if item["claim_id"] == "DK-91b546f25db1-CL003"
        and item["route_type"] == "scripture_exposition"
    )
    assert repeated_claim_route["decision_ids"] == ["CD-M16-001-03", "CD-M16-001-05"]

    routes_by_claim = {}
    for route in package["knowledge_routes"]:
        routes_by_claim.setdefault(route["claim_id"], set()).add(route["route_type"])
    for claim_id in SMALL_FAITH_CLAIMS:
        assert routes_by_claim[claim_id] == {"scripture_exposition", "topic_research"}

    small_faith_topic = next(
        item for item in package["topic_nodes"] if item["topic_id"] == SMALL_FAITH_TOPIC_ID
    )
    assert small_faith_topic["parent_topic_id"] == "disciple-faith-trust"
    assert "小信" in small_faith_topic["label"]
    assert report["status"] == "ready_for_ingest"


def test_missing_composition_claim_blocks_generation():
    comparison = _load(COMPARISON)
    comparison["claims"] = comparison["claims"][1:]

    with pytest.raises(ValueError, match="不存在的主張"):
        build_integration_package(comparison, _load(PATCH), b"manuscript")


def test_source_presentations_follow_composition_order_without_fragmenting_continuous_audio():
    package, _ = build_integration_package(_load(COMPARISON), _load(PATCH), b"manuscript")
    plan = next(item for item in package["product_plans"] if item["plan_id"] == "CP-matthew-16-1-12")
    decisions = {item["decision_id"]: item for item in plan["decisions"]}

    assert [
        (item["start_seconds"], item["end_seconds"])
        for item in decisions["CD-M16-001-01"]["source_presentations"]
    ] == [(220, 802)]
    assert decisions["CD-M16-001-01"]["source_presentation_summary"]["mode"] == "continuous"

    assert [
        (item["start_seconds"], item["end_seconds"])
        for item in decisions["CD-M16-001-04"]["source_presentations"]
    ] == [(802, 1208), (1396, 1503), (1755, 2099)]
    assert decisions["CD-M16-001-04"]["source_presentation_summary"]["mode"] == "segment_group"

    assert [
        (item["start_seconds"], item["end_seconds"])
        for item in decisions["CD-M16-001-05"]["source_presentations"]
    ] == [(1396, 2099)]

    assert decisions["CD-M16-001-03"]["source_presentations"] == []
    assert decisions["CD-M16-001-03"]["source_presentation_summary"]["mode"] == "unavailable"
    assert plan["source_presentation_policy"]["alignment"] == "composition_decision"
