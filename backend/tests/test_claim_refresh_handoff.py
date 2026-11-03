"""Operator handoff is additive reporting, never new approval or call authority."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline import claim_management_intake as intake, claim_refresh_handoff as handoff
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_intake import claims, pass_review
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


@pytest.fixture
def prepared(claims):
    store,root=claims
    value=intake.prepare(['C'],root,instruction='Refresh only C',store=store)
    _,jobs=batch.load(value['manifest']['path'],root/'batch',store=store)
    request,execution=next(iter(jobs.values()))
    return store,root/'batch',request,execution,value


def files(root):
    return {str(p):p.read_bytes()for p in root.rglob('*')if p.is_file()}


def test_inspection_collects_frozen_inputs_without_writes_or_calls(prepared,monkeypatch):
    store,root,request,execution,_=prepared
    before=files(root.parent)
    forbidden=Mock(side_effect=AssertionError('inspection must not dispatch'))
    monkeypatch.setattr(batch,'run',forbidden)
    result=handoff.inspect(root,store=store)
    row=result['actions'][request.action_id]
    assert row['request']==request.model_dump(mode='json')
    assert row['execution_root']==str(execution)
    assert row['result']=={'status':'not_started'}
    assert result['unfinished_claims']['claims/C']['reason']=='no_current_qualification'
    assert result['model_calls']==result['content_writes']==0
    assert files(root.parent)==before
    forbidden.assert_not_called()
    assert store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id))is None


def test_stopped_action_reports_failure_budget_and_no_inferred_retry(prepared):
    store,root,request,execution,_=prepared
    def fail(packet):raise RuntimeError('synthetic service failure')
    assert execute_claim_management(store,request,root=execution,
        ports=SyntheticPorts(store,execution,opus=fail))['status']=='paused'
    report=handoff.inspect(root,store=store)
    row=report['actions'][request.action_id]
    assert row['result']['reason']=='model_failed'
    assert row['budget']['opus']['reservations_by_state']['started']==1
    stage=next(iter(row['rounds'].values()))
    assert 'synthetic service failure' in stage['roles']['opus']['error']
    assert stage['roles']['opus']['reply_saved']is False
    assert stage['roles']['opus']['transport_started']is False
    assert stage['program_readback_complete']is False
    assert report['unfinished_actions'][request.action_id]['status']=='paused'


def test_completed_action_uses_current_qualification_and_saved_readback(prepared):
    store,root,request,execution,_=prepared
    ports=SyntheticPorts(store,execution,opus=pass_review)
    assert execute_claim_management(store,request,root=execution,ports=ports)['status']=='completed'
    before=list(ports.calls)
    report=handoff.inspect(root,store=store)
    assert not report['unfinished_claims'] and not report['unfinished_actions']
    assert report['qualifications']['claims/C']['ready']
    stage=next(iter(report['actions'][request.action_id]['rounds'].values()))
    assert stage['opus_approved'] and stage['program_readback_complete']
    assert stage['roles']['opus']['reply_saved']
    assert ports.calls==before


def test_resume_uses_exact_saved_manifest_root_and_capacity(prepared,monkeypatch):
    store,root,request,_,value=prepared
    config={'root':'/existing-capacity','action_window':4,'spacing_seconds':3,'cooldown_seconds':60}
    intake.immutable(root/'dispatch-capacity.json',{'manifest':value['manifest'],'capacity':config})
    before=files(root.parent)
    execute=Mock()
    monkeypatch.setattr(batch,'run',execute)
    result=handoff.resume(root,store=store)
    execute.assert_called_once_with(value['manifest']['path'],str(root),model_capacity=config)
    assert result['execution_dispatched'] and 'model_calls'not in result
    assert files(root.parent)==before


@pytest.mark.parametrize('fault',['request','authorization','policy','source','sql_request','sql_root'])
def test_resume_refuses_changed_original_bindings_before_dispatch(prepared,monkeypatch,fault):
    store,root,request,_,value=prepared
    if fault.startswith('sql_'):
        original=store.get_cvp_group
        def changed(key):
            if key=='CMA-'+cm.sha256_json(request.action_id):
                return {'request':request.model_dump(mode='json')if fault=='sql_root'else {},
                        'execution_root':'/different-root','result':{'status':'paused'}}
            return original(key)
        monkeypatch.setattr(store,'get_cvp_group',changed)
    else:
        paths={'request':value['inputs'][0]['path'],'authorization':request.authorization.path,
               'policy':request.policy.path,'source':next(iter(request.source_bindings.values())).path}
        path=Path(paths[fault]);path.write_text(path.read_text()+' ')
    execute=Mock()
    monkeypatch.setattr(batch,'run',execute)
    with pytest.raises(ValueError):handoff.resume(root,store=store)
    execute.assert_not_called()


def test_refill_inputs_are_read_without_recursive_history(prepared):
    store,root,request,_,_=prepared
    extra=intake.prepare(['D'],root.parent/'second',instruction='Refresh D',store=store)
    (root/'dispatch-inputs').mkdir()
    intake.immutable(root/'dispatch-inputs/000000.json',extra['manifest'])
    report=handoff.inspect(root,store=store)
    assert len(report['actions'])==2
    assert set(report['qualifications'])=={'claims/C','claims/D'}


def test_completion_summary_preserves_exact_reason_and_retirement():
    q={'claims/A':{'ready':True},'claims/B':{'ready':False,'reason':'newer_formal_review_objection','detail':'evidence_steps/E'},
       'claims/C':None,'claims/R':{'ready':False,'reason':'retired'}}
    result=handoff.completion_details(q,{'a':{'status':'completed'},'b':{'status':'paused','reason':'budget_exhausted'}},['claims/R'])
    assert set(result['unfinished_claims'])=={'claims/B','claims/C'}
    assert result['unfinished_claims']['claims/B']==q['claims/B']
    assert result['unfinished_actions']=={'b':{'status':'paused','reason':'budget_exhausted'}}


def test_readback_failure_is_not_a_content_rejection(prepared,monkeypatch):
    store,root,*_=prepared
    monkeypatch.setattr(store,'claim_management_qualification',Mock(side_effect=ValueError('exact dependency changed: evidence_steps/E')))
    report=handoff.inspect(root,store=store)
    q=report['unfinished_claims']['claims/C']
    assert q['reason']=='qualification_check_failed'
    assert 'evidence_steps/E'in q['detail']


def test_inspection_keeps_source_drift_visible_without_claiming_repair(prepared):
    store,root,request,*_=prepared
    source=Path(next(iter(request.source_bindings.values())).path)
    source.write_text(source.read_text()+' ')
    report=handoff.inspect(root,store=store,check_completion=False)
    errors=report['actions'][request.action_id]['input_errors']
    assert errors and errors[0]['path']==str(source)
    assert report['completion_checked']is False


def test_inspection_includes_new_claims_and_does_not_follow_successors(prepared,monkeypatch):
    store,root,request,execution,_=prepared
    original=store.get_cvp_group
    def parent(key):
        if key=='CMA-'+cm.sha256_json(request.action_id):
            return {'request':request.model_dump(mode='json'),'execution_root':str(execution),
                'result':{'status':'paused','reason':'continued_in_linked_action'},
                'continued_by':'CMA-next','created_keys':['claims/NEW']}
        assert key!='CMA-next','inspection must not recursively replay a successor chain'
        return original(key)
    monkeypatch.setattr(store,'get_cvp_group',parent)
    report=handoff.inspect(root,store=store)
    assert 'claims/NEW'in report['unfinished_claims']
    assert report['actions'][request.action_id]['continued_by']=='CMA-next'


@pytest.mark.parametrize('flag',['--inspect','--resume'])
def test_cli_reuses_saved_root_without_a_new_list(monkeypatch,flag,capsys):
    import sys
    operation=Mock(return_value={'saved_root':True})
    monkeypatch.setattr(handoff,'inspect'if flag=='--inspect'else 'resume',operation)
    monkeypatch.setattr(sys,'argv',['claim-batch',flag,'--root','/existing/batch'])
    batch.main()
    operation.assert_called_once_with(Path('/existing/batch'))
    assert json.loads(capsys.readouterr().out)=={'saved_root':True}


@pytest.mark.parametrize('extra',[
    ['--manifest','/new.json'],['--model-capacity','/new-capacity.json'],
    ['--instruction','new task'],['--claim-ids','/new-ids.json'],['--resume'],
])
def test_inspect_cli_rejects_new_authority_or_execution_options(monkeypatch,extra):
    import sys
    monkeypatch.setattr(sys,'argv',['claim-batch','--inspect','--root','/existing/batch',*extra])
    with pytest.raises(SystemExit)as error:batch.main()
    assert error.value.code==2
