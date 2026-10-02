from copy import deepcopy

import pytest

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline import other_claim_partition_preflight as preflight
from backend.pipeline.viewpoint_partition_manifest import grouping_request_bytes


def fixture():
    claims = [{"claim_id": cid, "statement": "原文\nδίκαιος，信心不是心理力量。", "source_id": "SRC",
        "claim_revision": 1, "claim_content_sha256": cid * 32,
        "scripture_refs": ["罗马书 4:5"]} for cid in ("C1", "C2", "C3")]
    role = base._artifact({"claims": deepcopy(claims)})
    routing = base._artifact({"role_packet_sha256": role["artifact_sha256"], "claims": deepcopy(claims)})
    edge = {"claim_relation_id": "R1", "from_id": "C1", "to_id": "C3", "cross_partition": True,
            "review_status": "candidate", "ownership_inheritance": False, "read_only_context": True}
    m = base._artifact({"packet_sha256": routing["artifact_sha256"], "claim_denominator": 3,
        "owners": {c: {k: claims[i][k] for k in ("claim_revision", "claim_content_sha256", "source_id")} |
                   {"partition": "salvation"} for i, c in enumerate(("C1", "C2"))},
        "held": [{"claim_id": "C3"}], "counts": {"salvation": 2}, "preserved_relations": [edge]})
    deferred = base._artifact({"manifest_sha256": m["artifact_sha256"], "status": "deferred_by_user",
        "grouping_eligible": False, "claims": [{"claim_id": "C3"}]})
    policy = {"max_request_bytes": 500000, "target_request_bytes": 380000, "max_claims_per_partition": 1}
    return m, routing, role, deferred, policy


def rehash(value):
    return base._artifact({k: v for k, v in value.items() if k != "artifact_sha256"})


def test_actual_serialization_all_claims_no_split_and_deferred_excluded():
    args = fixture()
    r = preflight.build_report(*args, "commit")
    row = r["partitions"][0]
    assert row["claim_ids"] == ["C1", "C2"] and row["claim_count"] == 2
    assert row["legacy_planner_claim_cap_exceeded"] and not row["split_performed"]
    assert row["request_bytes"] == grouping_request_bytes("salvation", args[2]["claims"][:2])
    assert r["deferred_claim_ids"] == ["C3"]
    assert r["preserved_relation_count"] == row["cross_partition_relation_count"] == 1
    assert not r["would_call_models"] and not r["database_mutations"] and not r["grouping_authorization"]
    assert args[0]["preserved_relations"][0]["review_status"] == "candidate"
    assert r == preflight.build_report(*args, "commit")


def test_ceiling_boundary_rejected_without_truncation_or_auto_split():
    m, p, r, d, policy = fixture()
    size = grouping_request_bytes("salvation", r["claims"][:2])
    policy.update(max_request_bytes=size, target_request_bytes=size-1)
    assert preflight.build_report(m,p,r,d,policy,"commit")["oversized_partitions"] == []
    policy.update(max_request_bytes=size-1, target_request_bytes=size-2)
    result = preflight.build_report(m,p,r,d,policy,"commit")
    assert result["status"] == "capacity_blocked" and result["oversized_partitions"] == ["salvation"]
    assert result["partitions"][0]["claim_count"] == 2 and not result["partitions"][0]["split_performed"]


@pytest.mark.parametrize("bad", ["missing", "duplicate_hold", "foreign", "deferred_owner", "pin", "counts", "statement"])
def test_preflight_rejects_rehashed_scope_or_version_drift(bad):
    m,p,r,d,policy = fixture()
    if bad == "missing": m["owners"].pop("C2")
    elif bad == "duplicate_hold": m["held"].append({"claim_id": "C3"})
    elif bad == "foreign": m["owners"]["C9"] = deepcopy(m["owners"]["C1"])
    elif bad == "deferred_owner": d["claims"] = [{"claim_id": "C1"}]
    elif bad == "pin": m["owners"]["C1"]["claim_revision"] = 2
    elif bad == "counts": m["counts"] = {"salvation": 1}
    else:
        r["claims"][0]["statement"] = "changed"
        r = rehash(r); p["role_packet_sha256"] = r["artifact_sha256"]
        p = rehash(p); m["packet_sha256"] = p["artifact_sha256"]
    m = rehash(m); d["manifest_sha256"] = m["artifact_sha256"]; d = rehash(d)
    with pytest.raises(ValueError):
        preflight.build_report(m,p,r,d,policy,"commit")
