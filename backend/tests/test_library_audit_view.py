"""The audit page must not soften what the audit said."""

from __future__ import annotations

import json

from backend.pipeline.library_audit_view import build_view, latest_run, load_view


AUDIT = {
    "meta": {
        "generated_at": "2026-08-26T14:50:12+00:00",
        "model": "gemini-3.7-flash",
        "seed": 241,
        "scope": "current-run",
        "sources": 22,
        "sources_out_of_scope": 13,
        "duplicate_sources": [["（五）3", ["SRC-2016_NYSC_3-f35be4755f9b", "SRC-L3"]]],
        "fragments": 7343,
        "claims": 1587,
        "viewpoints": 31,
    },
    "layers": {
        "1": {
            "layer": 1,
            "name": "逐字對得上",
            "total": 7343,
            "passed": 7333,
            "counts": {"pass": 7333, "no_excerpt": 6, "punctuation_only": 4},
            "findings": [
                {
                    "fragment_id": "FR-L3-E005",
                    "source_id": "SRC-L3",
                    "verdict": "no_excerpt",
                    "detail": "片段沒有 verbatim_excerpt",
                    "anchor_state": "unresolved",
                }
            ],
        },
        "2": {
            "layer": 2,
            "name": "覆蓋誠實",
            "checked_objects": 23485,
            "checked_clean": 23428,
            "approved_objects": 360,
            "approved_clean": 358,
            "references_resolved": 47852,
            "references_to_retired": 896,
            "references_dangling": 97,
            "dangling": [
                {
                    "collection": "knowledge_routes",
                    "object_id": "KR-a",
                    "field": "target_id",
                    "value": "CP-MISSING",
                },
                {
                    "collection": "knowledge_routes",
                    "object_id": "KR-b",
                    "field": "target_id",
                    "value": "CP-MISSING",
                },
            ],
            "component_locator_findings": [
                {
                    "object_id": "VCL-1",
                    "claim_id": "DK-x-CL008",
                    "verdict": "stitched",
                    "component": "「釋放」與准許某事，",
                    "statement": "「捆綁」和「釋放」既可指禁止與准許某事…",
                }
            ],
        },
        "3": {
            "layer": 3,
            "name": "主張站得住",
            "population": 1365,
            "sampled": 20,
            "judged": 20,
            "disputed": 1,
            "model_errors": 0,
            "review_status_mix": {"ai_consensus_reviewed": 19, "candidate": 1},
            "results": [
                {
                    "claim_id": "DK-y-CL010",
                    "review_status": "ai_consensus_reviewed",
                    "statement": "門徒必須防備…",
                    "evidence_step_ids": ["DK-y-E050"],
                    "verdict": "disputed",
                    "issue": "overreach",
                    "reason": "證據沒有說到這一半。",
                    "quote": "本段經文告訴人…",
                },
                {"claim_id": "DK-y-CL011", "verdict": "supported", "issue": "none", "reason": ""},
            ],
        },
        "4": {
            "layer": 4,
            "name": "觀點歸併對",
            "population": 12,
            "sampled": 10,
            "judged": 10,
            "disputed": 1,
            "model_errors": 0,
            "results": [
                {
                    "viewpoint_id": "CV-1",
                    "revision_id": "CVR-1",
                    "core_proposition": "太16:19…",
                    "linked_claims": 2,
                    "verdict": "disputed",
                    "issue": "over_merge",
                    "reason": "兩處經文被併成一條。",
                    "claim_ids_in_question": ["DK-a-CL002"],
                }
            ],
        },
    },
}


def test_the_four_layers_keep_the_audits_own_names_and_numbers():
    view = build_view(AUDIT, "2026-08-26T145012Z")
    layers = {layer["key"]: layer for layer in view["layers"]}
    assert layers["verbatim"]["passed"] == 7333
    assert layers["verbatim"]["total"] == 7343
    assert layers["coverage"]["passed"] == 23428
    assert layers["claims"]["name"] == "主張站得住"


def test_samples_never_render_as_a_ratio():
    """3 of 20 is not 15% of the library, and a percent sign would say it is."""

    view = build_view(AUDIT, "run")
    samples = [layer for layer in view["layers"] if layer["kind"] == "sample"]
    assert {layer["key"] for layer in samples} == {"claims", "viewpoints"}
    for layer in samples:
        assert "passed" not in layer and "total" not in layer
        assert layer["population"] > layer["judged"]


def test_every_follow_up_carries_the_record_the_verdict_and_the_evidence():
    view = build_view(AUDIT, "run")
    for group in view["followups"]:
        for item in group["items"]:
            assert item["object_id"]
            assert item["collection"]
            assert item["verdict"]["code"]
            # Said in words, not only as a code the reader has to look up.
            assert item["verdict"]["text"]


def test_only_disputed_samples_become_follow_ups():
    view = build_view(AUDIT, "run")
    groups = {group["kind"]: group for group in view["followups"]}
    assert groups["claim_support"]["count"] == 1
    assert groups["claim_support"]["items"][0]["object_id"] == "DK-y-CL010"


def test_dangling_references_collapse_onto_what_they_fail_to_reach():
    """Two rows pointing at one missing plan is one problem, not two."""

    view = build_view(AUDIT, "run")
    group = next(g for g in view["followups"] if g["kind"] == "dangling_reference")
    assert group["count"] == 2
    assert group["targets"] == [
        {
            "target": "CP-MISSING",
            "count": 2,
            "collections": ["knowledge_routes"],
            "object_ids": ["KR-a", "KR-b"],
        }
    ]


def test_scope_is_carried_through_so_a_ratio_is_never_read_as_library_wide():
    view = build_view(AUDIT, "run")
    assert view["scope"]["mode"] == "current-run"
    assert view["scope"]["sources_out_of_scope"] == 13
    assert view["scope"]["duplicate_sources"][0]["source_ids"] == [
        "SRC-2016_NYSC_3-f35be4755f9b",
        "SRC-L3",
    ]


def test_never_run_is_none_rather_than_a_zero(tmp_path):
    assert latest_run(tmp_path) is None
    assert load_view(tmp_path) is None
    assert load_view(tmp_path / "does-not-exist") is None


def test_the_newest_run_wins(tmp_path):
    for name in ("2026-08-25T000000Z", "2026-08-26T145012Z", "2026-08-24T000000Z"):
        run = tmp_path / name
        run.mkdir()
        (run / "audit.json").write_text(json.dumps(AUDIT), encoding="utf-8")
    assert latest_run(tmp_path).name == "2026-08-26T145012Z"
    assert load_view(tmp_path)["run_id"] == "2026-08-26T145012Z"


def test_a_fragment_with_no_quote_is_not_reported_as_a_mismatch():
    """"對不上" reads as "the professor never said this".

    Six of the ten layer-1 findings carry no `verbatim_excerpt` at all, so
    nothing failed to match -- there was nothing to match. Counting them as
    mismatches makes the page accuse the library of quoting words the professor
    did not say, which is the one thing this layer must never say by accident.
    """

    audit = json.loads(json.dumps(AUDIT))
    audit["layers"]["1"]["counts"] = {"pass": 90, "no_excerpt": 10}
    audit["layers"]["1"]["total"] = 100
    audit["layers"]["1"]["passed"] = 90
    layer = next(l for l in build_view(audit, "run")["layers"] if l["key"] == "verbatim")
    assert "對不上" not in layer["headline"]
    assert "沒有存引文" in layer["headline"]


def test_a_real_mismatch_is_still_said_plainly():
    audit = json.loads(json.dumps(AUDIT))
    audit["layers"]["1"]["counts"] = {"pass": 90, "absent": 10}
    audit["layers"]["1"]["total"] = 100
    audit["layers"]["1"]["passed"] = 90
    layer = next(l for l in build_view(audit, "run")["layers"] if l["key"] == "verbatim")
    assert "10 條對不上" in layer["headline"]
