from backend.pipeline.knowledge_coverage_report import (
    build_report,
    relation_closure,
    route_edge_expansion,
    seed_claims,
)
from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS


def test_seed_takes_only_refs_overlapping_the_windows():
    claims = [
        {"claim_id": "IN", "scripture_refs": ["太 16:18"]},
        {"claim_id": "EPH", "scripture_refs": ["弗 2:20"]},
        {"claim_id": "NONE", "scripture_refs": []},
    ]
    assert seed_claims(claims, PASSAGE_UNITS) == {"IN"}


def test_closure_pulls_cross_scripture_support_to_a_fixed_point():
    # EPH supports CORE; PSALM supports EPH — two rounds, both join.
    relations = [
        {"from_id": "EPH", "to_id": "CORE", "relation_type": "supports"},
        {"from_id": "PSALM", "to_id": "EPH", "relation_type": "supports"},
        {"from_id": "ELSEWHERE", "to_id": "UNRELATED", "relation_type": "supports"},
    ]
    scope, growth = relation_closure({"CORE"}, relations)
    assert scope == {"CORE", "EPH", "PSALM"}
    assert growth == [1, 1]


def test_route_attestation_is_the_second_belt():
    attestations = [
        {"argument_route_id": "AR-1", "claim_ids": ["CORE", "BOUND"]},
        {"argument_route_id": "AR-2", "claim_ids": ["FAR", "AWAY"]},
    ]
    assert route_edge_expansion({"CORE"}, attestations) == {"BOUND"}


def test_report_recovers_the_real_leak_shape_and_lists_orphans():
    claims = [
        {"claim_id": "CORE", "scripture_refs": ["太 16:18"]},
        {"claim_id": "EPH", "scripture_refs": ["弗 2:20"]},
        {"claim_id": "LOST", "scripture_refs": ["羅 1:1"]},
    ]
    relations = [{"from_id": "EPH", "to_id": "CORE", "relation_type": "supports"}]
    report = build_report(
        claims=claims,
        relations=relations,
        attestations=[],
        links=[{"claim_id": "CORE", "effective_state": "active"}],
        passage_units=PASSAGE_UNITS,
        scope_lanes={"EPH": "source_context_candidate", "CORE": "core"},
    )
    assert report["seed_count"] == 1
    assert report["scope_count"] == 2
    assert report["recovered_from_context_lane"] == ["EPH"]
    assert report["scope_unlinked"] == ["EPH"]
    assert report["orphans"] == ["LOST"]
