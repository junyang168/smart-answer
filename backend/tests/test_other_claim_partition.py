from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline import other_claim_partition as routing


def fixture():
    claims = [{"claim_id": cid, "claim_revision": 1, "claim_content_sha256": cid * 32,
               "source_id": "SRC", "source_revision": 1, "source_content_sha256": "s" * 64,
               "statement": "教授认为信心不是心理力量。", "evidence_steps": [
                   {"fragments": [{"verbatim_excerpt": "信心不是心理力量。"}]}]}
              for cid in ("C1", "C2", "C3")]
    packet = base._artifact({"claims": claims})
    roles = base._artifact({"schema_version": "wang_claim_passage_role_ledger_v8",
                           "packet_sha256": packet["artifact_sha256"],
                           "counts": {"other": 2, "passage_exegesis": 1},
                           "decisions": [{"claim_id": cid, "role": role} for cid, role in
                                         [("C1", "other"), ("C2", "other"), ("C3", "passage_exegesis")]]})
    relation = {"claim_relation_id": "R1", "from_id": "C1", "to_id": "C3",
                "relation_type": "supports", "review_status": "candidate", "revision": 1}
    return routing.prepare(roles, packet, [relation])


def answer(partition="salvation"):
    return {"partition": partition, "basis_quote": "信心不是心理力量", "reason": "Focus is nature of faith."}


def artifact(packet, role, values):
    rows = {r["claim_id"]: r for r in packet["claims"]}
    expected, _, _ = routing.binding(packet, [rows[cid] for cid in values], role)
    return base._artifact(expected | {"response": {"decisions": values}})


def test_exact_once_and_preserves_candidate_cross_partition_support():
    packet = fixture()
    artifacts = [artifact(packet, role, {cid: answer() for cid in ("C1", "C2")})
                 for role in ("primary", "independent")]
    result = routing.manifest(packet, artifacts)
    assert result["counts"] == {"salvation": 2}
    assert result["claim_denominator"] == 2
    assert result["held"] == []
    edge = result["preserved_relations"][0]
    assert edge["review_status"] == "candidate"
    assert edge["from_partition"] == "salvation"
    assert edge["to_partition"] == "passage_exegesis"
    assert edge["cross_partition"] and not edge["ownership_inheritance"]
    assert not result["grouping_authorization"]


def test_missing_review_and_disagreement_are_held_not_default_other():
    packet = fixture()
    result = routing.manifest(packet, [artifact(packet, "primary", {"C1": answer()}),
                                      artifact(packet, "independent", {"C1": answer("other")})])
    assert not result["owners"]
    assert len(result["held"]) == 2
    assert {r["reason_code"] for r in result["held"]} == {
        "routing_disagreement_or_uncertainty", "awaiting_independent_classification"}


def test_duplicate_batch_rejected():
    packet = fixture()
    a = artifact(packet, "primary", {"C1": answer()})
    with pytest.raises(ValueError, match="duplicate"):
        routing.manifest(packet, [a, a])


@pytest.mark.parametrize("bad", ["missing", "foreign", "quote", "concat", "category"])
def test_bad_decisions_fail_closed(bad):
    packet = fixture()
    response = {"decisions": {"C1": answer(), "C2": answer()}}
    if bad == "missing":
        response["decisions"].pop("C2")
    elif bad == "foreign":
        response["decisions"]["C4"] = answer()
    elif bad == "quote":
        response["decisions"]["C1"]["basis_quote"] = "信心就是心理力量"
    elif bad == "concat":
        response["decisions"]["C1"]["basis_quote"] = "教授认为信心不是心理力量。信心不是心理力量。"
    else:
        response["decisions"]["C1"]["partition"] = "invented"
    with pytest.raises(ValueError):
        routing.validate(response, packet["claims"])


def test_invalid_raw_is_retained_single_retry_and_cache_replay(tmp_path):
    packet = fixture()
    class Client:
        calls = 0
        def generate_json(self, *args):
            self.calls += 1
            return {"decisions": {cid: answer() for cid in ("C1", "C2")}}
    client = Client()
    first = routing.run_batch(packet, packet["claims"], tmp_path, "primary", client)
    assert routing.run_batch(packet, packet["claims"], tmp_path, "primary", client) == first
    assert client.calls == 1
    other = tmp_path / "invalid"
    class Invalid:
        calls = 0
        def generate_json(self, *args):
            self.calls += 1
            return {"decisions": {"C1": answer()}}
    invalid = Invalid()
    with pytest.raises(ValueError):
        routing.run_batch(packet, packet["claims"], other, "primary", invalid)
    assert invalid.calls == 2
    assert len(list(other.glob("*.validation-failure.json"))) == 2
    assert len(list(other.glob("*.json"))) == 4
    with pytest.raises(ValueError):
        routing.run_batch(packet, packet["claims"], other, "primary", invalid)
    assert invalid.calls == 2


def test_retains_transport_raw_and_never_auto_restarts(tmp_path):
    class Failure:
        last_raw_response = "malformed answer"
        def generate_json(self, *args):
            raise RuntimeError("subscription failure")
    packet = fixture()
    with pytest.raises(RuntimeError):
        routing.run_batch(packet, packet["claims"], tmp_path, "primary", Failure())
    saved = base._read_json(next(tmp_path.glob("*.failure.json")))
    assert saved["raw_response"] == "malformed answer"
    with pytest.raises(ValueError, match="inspection"):
        routing.run_batch(packet, packet["claims"], tmp_path, "primary", Failure())


def test_input_binding_counts_and_determinism():
    packet = fixture()
    assert packet == fixture()
    changed = deepcopy(packet)
    changed["claim_count"] = 99
    with pytest.raises(ValueError):
        routing.manifest(changed, [])


@pytest.mark.parametrize("corruption", ["missing_edge", "approved_edge", "endpoint", "duplicate_hold"])
def test_independent_audit_detects_even_rehashed_corruption(corruption):
    path = Path(__file__).resolve().parents[2] / "scripts/audit-other-claim-partitions.py"
    spec = importlib.util.spec_from_file_location("independent_partition_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    packet = fixture()
    manifest = routing.manifest(packet, [])
    if corruption == "missing_edge":
        manifest["preserved_relations"] = []
    elif corruption == "approved_edge":
        manifest["preserved_relations"][0]["review_status"] = "human_approved"
    elif corruption == "endpoint":
        manifest["preserved_relations"][0]["to_partition"] = "salvation"
    else:
        manifest["held"].append(manifest["held"][0])
    manifest = base._artifact({k: v for k, v in manifest.items() if k != "artifact_sha256"})
    # The auditor only needs the role ledger's ID scope/counts. Rebind this
    # small fixture with its explicit parent to test independent invariants.
    roles = base._artifact({"counts": {"other": 2}, "decisions": [
        {"claim_id": "C1", "role": "other"}, {"claim_id": "C2", "role": "other"}]})
    packet["role_ledger_sha256"] = roles["artifact_sha256"]
    packet = base._artifact({k: v for k, v in packet.items() if k != "artifact_sha256"})
    manifest["packet_sha256"] = packet["artifact_sha256"]
    manifest["role_ledger_sha256"] = roles["artifact_sha256"]
    manifest = base._artifact({k: v for k, v in manifest.items() if k != "artifact_sha256"})
    with pytest.raises(ValueError):
        module.audit(packet, manifest, roles)
def test_disjoint_worker_lanes_cover_frozen_batches():
    from backend.pipeline.other_claim_partition import worker_starts
    a = worker_starts(0, 8743, 2, 0)
    b = worker_starts(0, 8743, 2, 1)
    assert not set(a) & set(b)
    assert sorted(a + b) == list(range(0, 8743, 16))
    assert len(a) + len(b) == 547
    assert (a + b).count(8736) == 1


def test_worker_lane_resume_is_absolute_and_rejects_partial_boundaries():
    from backend.pipeline.other_claim_partition import worker_starts
    import pytest
    assert worker_starts(576, 640, 2, 0) == [576, 608]
    assert worker_starts(576, 640, 2, 1) == [592, 624]
    for count, index, start in ((2, 2, 0), (0, 0, 0), (2, 0, 577)):
        with pytest.raises(ValueError):
            worker_starts(start, 8743, count, index)
