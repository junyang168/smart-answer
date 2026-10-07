from copy import deepcopy
from types import SimpleNamespace
import pytest

from backend.pipeline import deferred_claim_cvp_continuation as d


def op(collection="viewpoint_claim_links",object_id="L",operation="create",claim_id="C"):
    return {"collection":collection,"object_id":object_id,"operation":operation,"before_revision":None,
        "after_revision":1,"payload":{"claim_id":claim_id}}


@pytest.mark.parametrize("change",["update","retire","foreign_claim","existing_cvp","duplicate","source","evidence","claim"])
def test_delta_never_replays_original_or_changes_claim_source_identity(change):
    item = op(); plan = {"operations":[item]}
    if change == "update": item.update(operation="update",before_revision=1,after_revision=2)
    if change == "retire": item["operation"] = "retire"
    if change == "foreign_claim": item["payload"]["claim_id"] = "OTHER"
    if change == "existing_cvp": item.update(collection="canonical_viewpoints",before_revision=1)
    if change == "duplicate": plan["operations"].append(deepcopy(item))
    if change in {"source","evidence","claim"}: item["collection"] = {"source":"source_fragments","evidence":"evidence_steps","claim":"claims"}[change]
    with pytest.raises(ValueError): d.validate_delta(plan,["C"])


def test_normal_delta_retains_existing_components_only_creates_fresh_coverage():
    d.validate_delta({"operations":[op(),op("canonical_viewpoints","V"),op("viewpoint_revisions","VR"),op("viewpoint_identity_decisions","D")]},["C"])


@pytest.mark.parametrize("problem",["rejected","escalated","deferred","foreign","core","route"])
def test_repaired_claim_not_whole_group_complete_until_normal_cvp_review_resolved(problem):
    p = SimpleNamespace(claim_decisions=[SimpleNamespace(claim_id="C",components=[SimpleNamespace(disposition="new_viewpoint")])],
        viewpoint_revisions=[],argument_route_candidates=[])
    report = {"approval_outcome":"pass","escalations":[],"_effective_proposal":p}
    if problem == "rejected": report["approval_outcome"] = "correct"
    if problem == "escalated": report["escalations"] = ["human hold"]
    if problem == "deferred": p.claim_decisions[0].components[0].disposition = "deferred"
    if problem == "foreign": p.claim_decisions[0].claim_id = "OTHER"
    if problem == "core": p.viewpoint_revisions = ["rewrite"]
    if problem == "route": p.argument_route_candidates = ["route"]
    with pytest.raises(ValueError): d.resolved_report(report,["C"])


def test_reverse_boundary_requires_every_exact_normalized_postimage():
    # stored_operation_payload uses actual after_revision, not a draft version.
    item = {**op("claims","C","update"),"before_revision":1,"after_revision":2,"payload":{"claim_id":"C","statement":"corrected"},
        "before_sha256":"0"*64,"after_sha256":"1"*64}
    from backend.api.canonical_repository.postgres_store import ChangeOperation,stored_operation_payload
    before = {"claims/C":{"claim_id":"C","revision":1,"statement":"old"}}
    live = {"claims/C":stored_operation_payload(ChangeOperation(**item))}
    assert d.rewind({"operations":[item]},live,before) == before
    live["claims/C"]["statement"] = "unreviewed"
    with pytest.raises(ValueError,match="postimage"): d.rewind({"operations":[item]},live,before)
