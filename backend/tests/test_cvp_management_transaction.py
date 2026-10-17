"""CVP2 late failures recover on shared SQL without another content call."""
from copy import deepcopy

import pytest

from backend.api.canonical_repository import cvp_management_authority as authority
from backend.api.canonical_repository.postgres_store import ChangeSetConflict
from backend.pipeline import cvp_management_runner as runner
from test_cvp_management_runner import setup,postgres_store,_temporary_postgres,parent


@pytest.mark.parametrize('failure',['late_write','lost_reply','completion'])
def test_late_rollback_lost_reply_and_completion_resume_original_once_calls(setup,monkeypatch,failure):
    store,request,root,clients,source=setup
    injected=[]
    if failure=='late_write':
        original=store._assert_change_set_object_readback
        def check(*args,**kwargs):
            result=original(*args,**kwargs)
            if not injected:injected.append(True);raise ValueError('injected late readback rollback')
            return result
        monkeypatch.setattr(store,'_assert_change_set_object_readback',check)
    elif failure=='lost_reply':
        original=store.apply_plan
        def apply(*args,**kwargs):
            result=original(*args,**kwargs)
            if not injected:injected.append(True);raise ValueError('injected lost commit reply')
            return result
        monkeypatch.setattr(store,'apply_plan',apply)
    else:
        original=authority.validate_completion_scope
        def check(*args,**kwargs):
            result=original(*args,**kwargs)
            if not injected:injected.append(True);raise ValueError('injected completion rollback')
            return result
        monkeypatch.setattr(authority,'validate_completion_scope',check)
    first=runner.run(store,request,root=root,execute=True,clients=clients)
    assert first['status']=='partial' and injected,parent(store)
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    if failure=='late_write':assert store.read_cvp_heads()==[]
    else:assert len(store.read_cvp_heads())==1
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    with store.connect() as conn:
        count=conn.execute("SELECT count(*) FROM wang_knowledge.change_sets WHERE source_kind='wang_cvp_management_v2' AND status='applied'").fetchone()[0]
    assert count==1


def test_transaction_rejects_role_response_or_afterimage_tampering(setup,monkeypatch):
    store,request,root,clients,source=setup
    original=store.review_cvp_group
    def review(run_id,*,plan,evidence):
        forged=deepcopy(evidence)
        forged['cvp_management']['review_raw']['review']['novelty_approved']=False
        return original(run_id,plan=plan,evidence=forged)
    monkeypatch.setattr(store,'review_cvp_group',review)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='partial' and store.read_cvp_heads()==[]
    assert len(parent(store)['call_reservations'])==2


def test_original_action_and_root_cannot_be_replaced_to_reset_budget(setup):
    store,request,root,clients,source=setup
    # Persist a task that has exhausted its original two calls on an unknown
    # provider response. A different action/root is not a continuation.
    clients['opus'].behavior=lambda packet:(_ for _ in ()).throw(RuntimeError('provider unknown fixture'))
    first=runner.run(store,request,root=root,execute=True,clients=clients)
    assert first['status']=='partial'
    with pytest.raises(ChangeSetConflict,match='root/request/policy'):
        runner.run(store,request,root=root.parent/'another-root',execute=True,clients=clients)
