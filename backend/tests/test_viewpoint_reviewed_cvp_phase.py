import copy
import hashlib
import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked, accepted_grouping_canary, accepted_grouping_in_passage_order, validate_grouping_envelope
from backend.pipeline.viewpoint_reviewed_grouping import admit_reviewed_grouping
from backend.pipeline.viewpoint_reviewed_cvp_prepare import verify_parent_current_pins
from backend.tests.test_viewpoint_reviewed_grouping import inputs


def seal(value, field="artifact_sha256"):
    value.pop(field, None)
    value[field] = sha256_json(value)
    return value


def phase_inputs(tmp_path):
    args = inputs(tmp_path)
    manifest = args["manifest"]
    source2 = tmp_path / "source2.md"
    source2.write_text("第二來源")
    manifest["sources"].append(manifest["sources"][0] | {"source_id": "S2", "path": str(source2), "file_sha256": hashlib.sha256(source2.read_bytes()).hexdigest()})
    c2 = copy.deepcopy(manifest["claim_packets"][0])
    c2.update(claim_id="C2", source_id="S2")
    c3 = copy.deepcopy(manifest["claim_packets"][0])
    c3.update(claim_id="C3")
    manifest["claim_packets"].extend([c2, c3])
    manifest["groups"] = [{"group_key": "pair", "claim_ids": ["C1", "C2"], "rationale": "原完整組", "unit_id": "u1"}, {"group_key": "single", "claim_ids": ["C3"], "rationale": "另一組", "unit_id": "u2"}]
    manifest["units"] = [{"unit_id": "u1", "passage_key": "Matt.1.1-Matt.1.17"}, {"unit_id": "u2", "passage_key": "Matt.5.1-Matt.5.12"}]
    seal(manifest)
    args["expected_manifest_sha256"] = manifest["artifact_sha256"]
    args["physical_review"]["manifest_sha256"] = manifest["artifact_sha256"]
    seal(args["physical_review"])
    args["acceptance"].update(manifest_sha256=manifest["artifact_sha256"], physical_review_evidence_sha256=args["physical_review"]["artifact_sha256"], claims=3, groups=2)
    seal(args["acceptance"])
    for role in ("manifest", "validation", "acceptance", "physical_review"):
        path = tmp_path / f"{role}.json"
        path.write_text(json.dumps(args[role], ensure_ascii=False))
        args["bindings"][role].update(file_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), artifact_sha256=args[role]["artifact_sha256"])
    scope = args["scope_packet"]
    scope["claims"].append(copy.deepcopy(scope["claims"][0]) | {"claim_id": "C2", "source_id": "S2"})
    scope["accepted_grouping_stage"] = "canary"
    seal(scope, "packet_sha256")
    args["freeze"].update(scope_packet_sha256=scope["packet_sha256"], accepted_grouping_stage="canary")
    return args


def test_canary_uses_full_parent_and_keeps_unprocessed_denominator(tmp_path):
    args = phase_inputs(tmp_path)
    envelope = admit_reviewed_grouping(**args)
    assert accepted_grouping_canary(args["manifest"])["group_key"] == "pair"
    assert envelope["parent_claim_count"] == 3
    assert envelope["parent_group_count"] == 2
    assert envelope["deferred_claim_ids"] == ["C3"]
    assert envelope["grouping"]["groups"] == [{key: args["manifest"]["groups"][0][key] for key in ("group_key", "claim_ids", "rationale")}]
    validate_grouping_envelope(envelope, freeze=args["freeze"])


@pytest.mark.parametrize("mutation", ["arbitrary_subset", "remove_stage", "unknown_stage", "unphased_freeze", "deferred", "parent_count"])
def test_phase_cannot_hide_claims_or_pick_an_easier_group(tmp_path, mutation):
    args = phase_inputs(tmp_path)
    if mutation == "arbitrary_subset":
        args["scope_packet"]["claims"][1]["claim_id"] = "C3"
        seal(args["scope_packet"], "packet_sha256")
    elif mutation == "remove_stage":
        args["scope_packet"].pop("accepted_grouping_stage")
        seal(args["scope_packet"], "packet_sha256")
    elif mutation == "unknown_stage":
        args["scope_packet"]["accepted_grouping_stage"] = "manual"
        seal(args["scope_packet"], "packet_sha256")
    elif mutation == "unphased_freeze":
        args["freeze"].pop("accepted_grouping_stage")
    else:
        envelope = admit_reviewed_grouping(**args)
        if mutation == "deferred":
            envelope["deferred_claim_ids"] = []
        else:
            envelope["parent_claim_count"] = 2
        seal(envelope)
        with pytest.raises(CvpProductionBlocked):
            validate_grouping_envelope(envelope, freeze=args["freeze"])
        return
    with pytest.raises(CvpProductionBlocked):
        admit_reviewed_grouping(**args)


def test_unselected_parent_pin_drift_still_blocks():
    manifest = {"claim_packets": [{"claim_id": "C1", "claim_revision": 1, "claim_content_sha256": "one", "evidence_steps": []},
                                  {"claim_id": "C2", "claim_revision": 1, "claim_content_sha256": "two", "evidence_steps": []}], "sources": []}
    records = {("claims", "C1"): {"revision": 1, "content_sha256": "one", "retired": False},
               ("claims", "C2"): {"revision": 1, "content_sha256": "two", "retired": False}}
    verify_parent_current_pins(manifest, records)
    records[("claims", "C2")]["content_sha256"] = "drift"
    with pytest.raises(CvpProductionBlocked, match="parent current pin drift"):
        verify_parent_current_pins(manifest, records)


def test_passage_order_is_numeric_and_keeps_argument_boundaries():
    manifest = {
        "units": [{"unit_id": "ten", "passage_key": "Matt.10.1-Matt.10.5"},
                  {"unit_id": "two", "passage_key": "Matt.2.1-Matt.2.12"},
                  {"unit_id": "early", "passage_key": "Matt.2.1-Matt.2.5"}],
        "groups": [{"group_key": "p000", "unit_id": "ten", "claim_ids": ["C10"]},
                   {"group_key": "later", "unit_id": "two", "claim_ids": ["C2"]},
                   {"group_key": "split-b", "unit_id": "early", "claim_ids": ["B"]},
                   {"group_key": "split-a", "unit_id": "early", "claim_ids": ["A"]}],
    }
    original = copy.deepcopy(manifest)
    ordered = accepted_grouping_in_passage_order(manifest)
    assert [row["group_key"] for row in ordered] == ["split-b", "split-a", "later", "p000"]
    assert manifest == original
    assert ordered == [original["groups"][i] for i in (2, 3, 1, 0)]


def test_first_passage_wins_over_smaller_later_canary(tmp_path):
    manifest = phase_inputs(tmp_path)["manifest"]
    # The earlier group has 3 Claims, the later group only 2. Do not jump ahead.
    manifest["groups"][0]["claim_ids"].append("C3")
    manifest["groups"][1]["claim_ids"] = ["C4", "C5"]
    manifest["claim_packets"].extend([{"claim_id": "C4", "source_id": "S1"}, {"claim_id": "C5", "source_id": "S2"}])
    manifest["groups"].reverse()
    assert accepted_grouping_canary(manifest)["group_key"] == "pair"


def test_unqualified_first_passage_cannot_be_silently_skipped(tmp_path):
    manifest = phase_inputs(tmp_path)["manifest"]
    manifest["units"][0]["passage_key"] = "Matt.5.1-Matt.5.12"
    manifest["units"][1]["passage_key"] = "Matt.1.1-Matt.1.17"
    with pytest.raises(CvpProductionBlocked, match="cannot skip chapter order"):
        accepted_grouping_canary(manifest)


def test_single_verse_passage_orders_like_a_one_verse_range(tmp_path):
    manifest = phase_inputs(tmp_path)["manifest"]
    manifest["units"][0]["passage_key"] = "Matt.16.19"
    manifest["units"][1]["passage_key"] = "Matt.16.20"
    manifest["groups"].reverse()
    assert [g["group_key"] for g in accepted_grouping_in_passage_order(manifest)] == ["pair", "single"]


@pytest.mark.parametrize("passage", [None, "Matt.2.1-Matt.1.17", "Matt.1.0-Matt.1.17", "Matt.29.1-Matt.29.2", "Matt.1.17-Matt.1.1"])
def test_invalid_passage_order_is_rejected(tmp_path, passage):
    manifest = phase_inputs(tmp_path)["manifest"]
    manifest["units"][0]["passage_key"] = passage
    with pytest.raises(CvpProductionBlocked, match="passage"):
        accepted_grouping_in_passage_order(manifest)
