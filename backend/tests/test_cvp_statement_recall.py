import pytest
from backend.api.canonical_repository.cvp_recall import statement_recall_plan,merge_recall
from backend.pipeline.cvp_recall import recall_adapter
from test_cvp_recall_stages import Client
from test_cvp_recall import heads,claims


def make(hs=None,limit=500000):
    c=Client()
    return statement_recall_plan(hs or heads(750),claims(2),max_request_bytes=limit,
        measure=lambda p:recall_adapter(c,p,max_request_bytes=limit).request_bytes(p))


def test_all_750_statements_without_summary_scope_or_signature_and_no_gold_injection():
    plan=make();rows=plan['shards'][0]['viewpoints']
    assert len(rows)==750
    assert all(set(r)=={'cvp_ref','core_proposition'} for r in rows)
    result=merge_recall(plan,[{'claim_candidates':[{'claim_ref':c,'cvp_refs':[]} for c in plan['claim_refs']]}])
    assert all(r['viewpoint_ids']==[] for r in result['claim_candidates'])


def test_capacity_and_complete_replies_are_checked():
    with pytest.raises(ValueError,match='exceeds capacity'):make(limit=1)
    plan=make()
    with pytest.raises(ValueError,match='omitted Claims'):merge_recall(plan,[{'claim_candidates':[]}])
    with pytest.raises(ValueError,match='unique'):make([heads(1)[0],heads(1)[0]])
