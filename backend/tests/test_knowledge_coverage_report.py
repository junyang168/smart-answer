from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS
from backend.pipeline.knowledge_coverage_report import (
    build_report,
    closure_with_units,
    relation_closure,
    seed_claims,
)
from backend.pipeline.viewpoint_scope_selection import select_scope_units


def _claim(claim_id, refs=(), source="SRC-1"):
    return {
        "claim_id": claim_id,
        "source_id": source,
        "statement": claim_id,
        "scripture_refs": list(refs),
    }


def test_seed_takes_only_refs_overlapping_the_windows():
    claims = [
        _claim("IN", ["太 16:18"]),
        _claim("EPH", ["弗 2:20"]),
        _claim("NONE"),
    ]
    assert seed_claims(claims, PASSAGE_UNITS) == {"IN"}


def test_cl007_regression_walks_upstream_candidate_support_edges():
    claims = [
        _claim("CL007", ["弗 2:20"]),
        _claim("CL008", ["弗 2:20"]),
        _claim("CL009", ["太 16:18", "弗 2:20"]),
    ]
    relations = [
        {
            "claim_relation_id": "CR-7-9",
            "from_id": "CL007",
            "to_id": "CL009",
            "relation_type": "supports",
            "review_status": "candidate",
        },
        {
            "claim_relation_id": "CR-8-9",
            "from_id": "CL008",
            "to_id": "CL009",
            "relation_type": "supports",
            "review_status": "candidate",
        },
    ]
    scope, growth = relation_closure({"CL009"}, relations, claims=claims)
    assert scope == {"CL007", "CL008", "CL009"}
    assert growth == [2]


def test_reverse_cross_source_disallowed_and_nonclaim_edges_do_not_expand():
    claims = [
        _claim("CORE", ["太 16:18"]),
        _claim("UPSTREAM"),
        _claim("DOWNSTREAM"),
        _claim("CROSS", source="SRC-2"),
        _claim("DUPLICATE"),
    ]
    relations = [
        {"claim_relation_id": "GOOD", "from_id": "UPSTREAM", "to_id": "CORE", "relation_type": "supports"},
        {"claim_relation_id": "REVERSE", "from_id": "CORE", "to_id": "DOWNSTREAM", "relation_type": "supports"},
        {"claim_relation_id": "CROSS", "from_id": "CROSS", "to_id": "CORE", "relation_type": "supports"},
        {"claim_relation_id": "TYPE", "from_id": "DUPLICATE", "to_id": "CORE", "relation_type": "duplicate"},
        {"claim_relation_id": "POSITION", "from_id": "POS-NOT-CLAIM", "to_id": "CORE", "relation_type": "supports"},
    ]
    result = select_scope_units(
        claims=claims, passage_units=PASSAGE_UNITS, relations=relations
    )
    assert set(result["claim_units"]) == {"CORE", "UPSTREAM"}
    reasons = {row["relation_id"]: row["reason"] for row in result["rejected_relations"]}
    assert reasons == {
        "CROSS": "cross_source_relation_requires_argument_route",
        "POSITION": "endpoint_outside_claim_universe",
        "TYPE": "relation_type_not_argument_dependency",
    }


def test_only_approved_current_route_can_cross_sources():
    claims = [_claim("CORE", ["太 16:18"]), _claim("BOUND", source="SRC-2")]
    links = [
        {
            "viewpoint_claim_link_id": "VCL-1",
            "claim_id": "CORE",
            "viewpoint_id": "CV-1",
            "validated_against_viewpoint_revision_id": "CVR-1",
            "effective_state": "active",
            "review_status": "system_approved",
        }
    ]
    routes = [
        {
            "argument_route_id": "AR-1",
            "conclusion_viewpoint_id": "CV-1",
            "current_revision_id": "ARR-1",
            "route_status": "active",
            "review_status": "system_approved",
        }
    ]
    revisions = [
        {
            "argument_route_revision_id": "ARR-1",
            "argument_route_id": "AR-1",
            "validated_against_conclusion_viewpoint_revision_id": "CVR-1",
            "review_status": "system_approved",
        }
    ]
    attestations = [
        {
            "argument_route_attestation_id": "ARA-1",
            "argument_route_id": "AR-1",
            "validated_against_route_revision_id": "ARR-1",
            "source_id": "SRC-2",
            "claim_ids": ["BOUND"],
            "effective_state": "active",
            "review_status": "system_approved",
        }
    ]
    result = select_scope_units(
        claims=claims,
        passage_units=PASSAGE_UNITS,
        links=links,
        routes=routes,
        route_revisions=revisions,
        attestations=attestations,
    )
    assert result["claim_units"]["BOUND"] == ["16:13-18"]
    assert result["admissions"]["BOUND"][0]["signal"] == "argument_route"


def test_report_discloses_legacy_only_admission_and_occurrence_gap():
    claims = [_claim("CORE", ["太 16:18"]), _claim("DOWNSTREAM"), _claim("LOST")]
    relations = [
        {
            "claim_relation_id": "REVERSE",
            "from_id": "CORE",
            "to_id": "DOWNSTREAM",
            "relation_type": "supports",
        }
    ]
    report = build_report(
        claims=claims,
        relations=relations,
        attestations=[],
        links=[],
        passage_units=PASSAGE_UNITS,
        scope_lanes={"CORE": "core", "DOWNSTREAM": "core", "LOST": "source_context_candidate"},
    )
    assert report["legacy_undirected_scope_count"] == 2
    assert report["corrected_scope_count"] == 1
    assert report["disputed_legacy_admission_count"] == 1
    assert report["disputed_legacy_admissions"][0]["claim_id"] == "DOWNSTREAM"
    assert report["disputed_legacy_admissions"][0]["scope_qualification"] == "pending_occurrence_evidence"

    completed = build_report(
        claims=claims,
        relations=relations,
        attestations=[],
        links=[],
        passage_units=PASSAGE_UNITS,
        occurrence_admissions_by_claim={
            "DOWNSTREAM": [
                {
                    "passage_unit_ids": ["16:13-18"],
                    "source_fragment_id": "FR-DOWNSTREAM",
                    "section_index": 1,
                }
            ]
        },
        occurrence_status_by_claim={
            "DOWNSTREAM": "proved_by_occurrence_section"
        },
        occurrence_projection_sha256="a" * 64,
    )
    assert completed["disputed_legacy_admission_count"] == 1
    assert completed["disputed_proved_by_occurrence_count"] == 1
    assert completed["disputed_legacy_admissions"][0][
        "scope_qualification"
    ] == "proved_by_occurrence_section"


def test_units_propagate_only_upstream():
    claims = [_claim("CORE"), _claim("UPSTREAM"), _claim("DOWNSTREAM")]
    units = closure_with_units(
        {"CORE": {"16:13-18"}},
        [
            {"claim_relation_id": "UP", "from_id": "UPSTREAM", "to_id": "CORE", "relation_type": "supports"},
            {"claim_relation_id": "DOWN", "from_id": "CORE", "to_id": "DOWNSTREAM", "relation_type": "supports"},
        ],
        [],
        claims=claims,
    )
    assert units == {"CORE": {"16:13-18"}, "UPSTREAM": {"16:13-18"}}
