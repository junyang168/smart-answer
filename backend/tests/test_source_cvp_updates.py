"""Source continuation uses actual CVP/Route SQL with synthetic providers."""
from contextlib import contextmanager
from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.postgres_store import ChangeSetConflict
from backend.pipeline import source_cvp_updates as downstream, source_claim_updates as source
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline import viewpoint_route_cvp_worker as routes
from backend.api.canonical_repository.viewpoint_route_cvp_queue import CvpRouteQueue
from backend.tests.test_cvp_management_runner import setup, postgres_store, _temporary_postgres
from backend.tests.test_cvp_management_maintenance import _initial
from backend.tests.test_cvp_management_update import _update_claim, _member_candidate, _proposal_behavior, _new_claim
from backend.tests.test_cvp_management_models import envelope
from backend.tests.test_viewpoint_observation_support import Answer, observation_proposal, approved


@contextmanager
def guard(store):
    yield ()


def inline_routes(monkeypatch):
    original = downstream.route_job
    monkeypatch.setattr(routes,'build_route_reviewer',lambda *a,**k:Answer(approved,model_id='opus'))
    calls = []
    def run(store, root, job_id, **kw):
        queue = CvpRouteQueue(root/'route-queue-v2')
        state = json.loads((queue.states/(job_id+'.json')).read_text())
        if state['status']=='complete':
            return original(store,root,job_id)
        job = json.loads((queue.jobs/(job_id+'.json')).read_text())
        claimed = queue.claim(worker_id='test',policy_sha256=job['policy_sha256'],lease_seconds=60,job_id=job_id)
        assert claimed
        _,epoch = claimed
        result = routes.process_cvp_route(store=store,viewpoint_id=job['viewpoint_id'],
            output_dir=root/'arguments'/job_id,proposer=Answer(observation_proposal,model_id='gpt'),
            reviewer=Answer(approved,model_id='opus'),reconsiderer=Answer({}),max_correction_rounds=4,apply=True,include_connection_judgments=True)
        assert result['status']=='applied_readback_verified'
        queue.finish(job_id,worker_id='test',epoch=epoch,status='complete',detail=result['status'])
        calls.append(job_id)
        return True
    monkeypatch.setattr(downstream,'route_job',run)
    return calls


def context(setup, monkeypatch):
    store,initial,root,clients,src,view,link,_ = _initial(setup)
    clients['gpt'].requests.clear();clients['opus'].requests.clear()
    clients['gpt'].behavior = _proposal_behavior(lambda p:_member_candidate(p,summary='人子称号表明耶稣的神性。'))
    plan = root.parent/'source-plan.json';plan.write_text('{"source_id":"S1"}')
    claims = {'status':'claims_completed','all_actions_completed':True,'unowned_paragraphs':[],
        'downstream_handoff':{'status':'not_executed','claim_ids':['C1'],'retired_claim_ids':[]}}
    monkeypatch.setattr(downstream,'execution_guard',guard)
    return store,root.parent/'source-downstream',clients,binding(plan),claims,view


def test_source_update_commits_cvp_and_routes_then_zero_call_resume(setup,monkeypatch):
    store,root,clients,plan,claims,view = context(setup,monkeypatch)
    _update_claim(store,'C1',statement='此处人子称号表明耶稣的神性。')
    route_calls = inline_routes(monkeypatch)
    checked = []
    def run():
        return downstream.run(store,claims,root,source_plan=plan,instruction='完成来源更新',
                              guard_source=lambda:checked.append(True),clients=clients)
    result = run()
    assert result['status']=='completed',result
    assert result['results']['cvp']['update']['status']=='completed'
    assert len(route_calls)==1 and checked
    link = next(iter(store.read_cvp_links(claim_ids=['C1']).values()))['payload']
    assert link['pinned_claim_revision']==2
    assert store.read_cvp_route_records(view['viewpoint_id'])
    counts = [len(clients[k].requests) for k in ('gpt','opus')]
    assert run()==result
    assert counts==[len(clients[k].requests) for k in ('gpt','opus')]
    assert len(route_calls)==1


def test_pending_route_never_counts_as_source_completion_and_resumes_same_cvp(setup,monkeypatch):
    store,root,clients,plan,claims,_ = context(setup,monkeypatch)
    monkeypatch.setattr(downstream,'route_job',lambda *a,**k:False)
    args = dict(source_plan=plan,instruction='refresh',guard_source=lambda:None,clients=clients)
    first = downstream.run(store,claims,root,**args)
    assert first['status']=='paused' and first['results']['cvp']['status']=='arguments_pending'
    req = (root/'cvp/request.json').read_bytes()
    counts = [len(clients[k].requests) for k in ('gpt','opus')]
    calls = inline_routes(monkeypatch)
    # Restore completion authentication after replacing the deliberately idle consumer.
    second = downstream.run(store,claims,root,**args)
    assert second['status']=='completed',second
    assert len(calls)==1 and (root/'cvp/request.json').read_bytes()==req
    assert counts==[len(clients[k].requests) for k in ('gpt','opus')]


def test_unlinked_current_claim_is_a_reviewed_candidate_not_an_inherited_member(setup,monkeypatch):
    store,root,clients,plan,claims,view = context(setup,monkeypatch)
    _new_claim(store,'C2')
    claims['downstream_handoff']['claim_ids'].append('C2')
    # Build background directly; _member_candidate expects each Claim to have a link.
    def candidate(packet):
        v = packet['material']['viewpoints'][0]['cvp_ref'];l=packet['material']['links'][0]['link_ref']
        return envelope('candidate',candidate={'changes':[],
            'component_dispositions':[{'component_ref':'k1','disposition':'existing_coverage','target_ref':v,'link_ref':l,'reason':'原成员'},
                                      {'component_ref':'k2','disposition':'background','reason':'独立背景'}],
            'member_dispositions':[{'member_ref':l,'disposition':'retained','reason':'保留'}],
            'summaries':[{'ref':v,'summary':'人子称号表明耶稣的神性。'}]})
    clients['gpt'].behavior = _proposal_behavior(candidate)
    inline_routes(monkeypatch)
    result = downstream.run(store,claims,root,source_plan=plan,instruction='refresh',guard_source=lambda:None,clients=clients)
    assert result['status']=='completed',(result,store.get_cvp_group('CVPM-'+downstream.cvp.sha256_json(json.loads((root/'cvp/request.json').read_text())['action_id']))['units'])
    request = json.loads((root/'cvp/request.json').read_text())
    assert request['input']['update']['replacement_claim_ids']==['C2']
    assert request['input']['claim_ids']==['C1','C2']
    assert not store.read_cvp_links(claim_ids=['C2'])


def test_drift_and_changed_intent_do_not_create_new_downstream_tasks(setup,monkeypatch):
    store,root,clients,plan,claims,_ = context(setup,monkeypatch)
    frozen = downstream.prepare(store,claims,root,source_plan=plan,instruction='refresh')
    with pytest.raises(ValueError):
        downstream.prepare(store,claims,root,source_plan=plan,instruction='different scope')
    def drift():raise ValueError('source drift')
    with pytest.raises(ValueError,match='source drift'):
        downstream.run(store,claims,root,source_plan=plan,instruction='refresh',guard_source=drift,clients=clients)
    assert not (root/'cvp').exists()


def test_unqualified_source_claim_does_not_enter_cvp(setup,monkeypatch):
    store,root,clients,plan,claims,_ = context(setup,monkeypatch)
    monkeypatch.setattr(store,'claim_management_qualification',lambda cid:{'ready':False,'reason':'formal_review_findings'})
    with pytest.raises(ValueError,match='not currently qualified'):
        downstream.prepare(store,claims,root,source_plan=plan,instruction='refresh')
    assert not (root/'plan.json').exists()
    assert not clients['gpt'].requests


def test_workflow_keeps_claim_receipt_and_reports_downstream_state(tmp_path,monkeypatch):
    root = tmp_path/'source';root.mkdir()
    claims = {'status':'claims_completed','all_actions_completed':True}
    (root/'result.json').write_text(json.dumps(claims))
    (root/'plan.json').write_text('{"source_id":"S"}')
    original = (root/'result.json').read_bytes()
    monkeypatch.setattr(source,'run',lambda *a:deepcopy(claims))
    monkeypatch.setattr(source,'guard',lambda *a:None)
    results = iter([{'status':'paused','stage':'cvp_update'},{'status':'completed'}])
    monkeypatch.setattr(downstream,'run',lambda *a,**k:next(results))
    first = source.run_workflow(None,'S','before','KCS',root,'direction')
    assert first['status']=='paused'
    second = source.run_workflow(None,'S','before','KCS',root,'direction')
    assert second['status']=='completed' and second['stage']=='completed'
    assert (root/'result.json').read_bytes()==original
    claims['status']='paused'
    third = source.run_workflow(None,'S','before','KCS',root,'direction')
    assert third['stage']=='claim_update' and third['status']=='paused'


def test_new_source_uses_existing_generation_and_resumes_without_new_route_jobs(setup,monkeypatch):
    store,initial,base,clients,src = setup
    monkeypatch.setattr(downstream,'execution_guard',guard)
    plan = base.parent/'source-plan.json';plan.write_text('{"source_id":"S1"}')
    claims = {'all_actions_completed':True,'unowned_paragraphs':[],
              'downstream_handoff':{'claim_ids':['C1'],'retired_claim_ids':[]}}
    root = base.parent/'new-source'
    calls = inline_routes(monkeypatch)
    args = dict(source_plan=binding(plan),instruction='source refresh',guard_source=lambda:None,clients=clients)
    first = downstream.run(store,claims,root,**args)
    assert first['status']=='completed',first
    assert first['results']['cvp']['arguments_completed']
    assert store.read_cvp_heads()
    counts = [len(clients[k].requests) for k in ('gpt','opus')]
    assert downstream.run(store,claims,root,**args)==first
    assert counts==[len(clients[k].requests) for k in ('gpt','opus')]
    assert len(calls)==1


def test_changed_claim_after_completion_cannot_reuse_old_approval(setup,monkeypatch):
    store,root,clients,plan,claims,_ = context(setup,monkeypatch)
    inline_routes(monkeypatch)
    args = dict(source_plan=plan,instruction='refresh',guard_source=lambda:None,clients=clients)
    assert downstream.run(store,claims,root,**args)['status']=='completed'
    counts = [len(clients[k].requests) for k in ('gpt','opus')]
    _update_claim(store,'C1',statement='新的判断')
    with pytest.raises(ChangeSetConflict,match='current dependency drift'):downstream.run(store,claims,root,**args)
    assert counts==[len(clients[k].requests) for k in ('gpt','opus')]


def test_failed_route_is_not_automatically_retried(tmp_path,monkeypatch):
    from types import SimpleNamespace
    root = tmp_path/'action';queue=root/'route-queue-v2'
    (queue/'states').mkdir(parents=True);(queue/'jobs').mkdir()
    (queue/'states/J.json').write_text('{"status":"exception"}')
    (queue/'jobs/J.json').write_text('{}')
    def forbidden(*a,**k):raise AssertionError('must preserve the original exception')
    monkeypatch.setattr(downstream.subprocess,'run',forbidden)
    assert downstream.route_job(SimpleNamespace(),root,'J') is False


def test_applied_route_with_lost_queue_finish_recovers_without_models(setup,monkeypatch):
    from types import SimpleNamespace
    store,root,clients,plan,claims,_ = context(setup,monkeypatch)
    original = downstream.route_job
    calls = inline_routes(monkeypatch)
    args = dict(source_plan=plan,instruction='refresh',guard_source=lambda:None,clients=clients)
    assert downstream.run(store,claims,root,**args)['status']=='completed'
    job_id = calls[0];queue=root/'cvp/route-queue-v2';path=queue/'states'/(job_id+'.json')
    state = json.loads(path.read_text());state['status']='exception';path.write_text(json.dumps(state))
    counts = [len(clients[k].requests) for k in ('gpt','opus')]
    commands = []
    def finish(command,**kwargs):
        commands.append(command)
        assert kwargs['env']['KNOWLEDGE_DATABASE_URL']==store.database_url
        assert store.database_url not in command
        state['status']='complete';path.write_text(json.dumps(state))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(downstream,'route_job',original)
    monkeypatch.setattr(downstream.subprocess,'run',finish)
    assert downstream.run(store,claims,root,**args)['status']=='completed'
    assert len(commands)==1 and '--retry-exceptions' in commands[0]
    assert counts==[len(clients[k].requests) for k in ('gpt','opus')]


def test_registered_source_to_claim_to_cvp_to_routes_in_one_call(setup,monkeypatch):
    """One composition test, without substituting a completed Claim result."""
    from backend.pipeline import claim_management as cm, claim_management_batch as batch
    from backend.pipeline.cvp_sql_actions import execute_claim_management
    from backend.pipeline.source_projection import project_script
    from backend.tests.test_claim_management_execution_postgres import SyntheticPorts
    from backend.tests.test_claim_management_intake import pass_review
    store,initial,base,clients,src,view,link,_ = _initial(setup)
    # The shared fixture inserts its initial rows directly. Establish a real
    # version-history generation before testing a registered source transition.
    seed = deepcopy(store.get_record_state('source_documents','S1')['payload'])
    seed['title'] = '原始登記來源'
    store.apply_plan(store.plan_package({'package_id':'source-history-seed','source_documents':[seed]}))
    old = base.parent/'before-source.json';old.write_bytes(src.read_bytes())
    rows = json.loads(src.read_text());rows[1]['text']='人子確是神。'
    src.write_text(json.dumps(rows,ensure_ascii=False))
    doc = deepcopy(store.get_record_state('source_documents','S1')['payload'])
    doc.update(source_file_sha256=binding(src)['file_sha256'],source_body_sha256=project_script(rows).body_sha256,
               source_sha256=project_script(rows).body_sha256)
    registered = store.plan_package({'package_id':'registered-source-change','source_documents':[doc]})
    store.apply_plan(registered)
    monkeypatch.setattr(downstream,'execution_guard',guard)
    calls = inline_routes(monkeypatch)
    quote = {'source_id':'S1','paragraph_key':'S0001','exact_quote':'人子確是神。'}
    def resolutions(packet):
        return [{'finding_id':fid,'disposition':'resolved','reason':'按校訂原文刷新',
                 'citations':[quote],'affected_target_keys':['claims/C1'],
                 'affected_write_keys':['claims/C1','source_fragments/F1'],'impact_reason':'自身引文與主張'}
                for fid in packet['unresolved']]
    def review(packet):
        result = pass_review(packet)
        candidate = cm.Candidate.model_validate(packet['candidate'])
        rows = {k:v['payload'] for k,v in packet['before'].items()}
        rows.update(cm.source.index(candidate.records))
        if rows['source_fragments/F1']['verbatim_excerpt']!='人子確是神。':
            result['findings'] = [{'target_key':'source_fragments/F1','component':'source_binding','category':'mapping',
                'reason':'原引文需要校訂','acceptance_condition':'使用當前原文','citations':[quote],
                'affected_target_keys':['claims/C1'],'affected_write_keys':[],'impact_reason':'目標證據'}]
        result['prior_findings'] = resolutions(packet)
        return result
    def propose(packet):
        return {'changes':[{'record_key':key,'fields_json':json.dumps(fields),'reason':'同步校訂原文','citations':[quote]}
            for key,fields in [('claims/C1',{'statement':'人子確是神。'}),
                               ('source_fragments/F1',{'verbatim_excerpt':'人子確是神。'})]],
            'creations':[],'retirements':[],'findings':[],'finding_resolutions':resolutions(packet)}
    executions = []
    def claim_batch(manifest,output):
        for request,execution in batch.load(manifest,output,store=store)[1].values():
            result = execute_claim_management(store,request,root=execution,
                ports=SyntheticPorts(store,execution,opus=review,proposal=propose))
            assert result['status']=='completed',result
            executions.append(request.action_id)
        # From here on consume the real Claim executor's SQL readback proof,
        # rather than the shared fixture's initial synthetic qualification.
        from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
        monkeypatch.setattr(store,'claim_management_qualification',
            PostgresKnowledgeStore.claim_management_qualification.__get__(store))
    monkeypatch.setattr(batch,'run',claim_batch)
    # Use the same bound synthetic CVP provider as the ordinary runner.
    clients['gpt'].behavior = _proposal_behavior(lambda p:_member_candidate(p,summary='人子確是神。'))
    actual = downstream.run
    monkeypatch.setattr(downstream,'run',lambda *a,**k:actual(*a,**k,clients=clients))
    root = base.parent/'complete-source'
    result = source.run_workflow(store,'S1',old,registered.change_set_id,root,'完成來源更新')
    req = json.loads((root/'downstream/cvp/request.json').read_text())
    parent = store.get_cvp_group('CVPM-'+downstream.cvp.sha256_json(req['action_id']))
    assert result['status']=='completed',(result,parent['units'])
    assert len(executions)==len(calls)==1
    assert store.get_record_state('claims','C1')['payload']['statement']=='人子確是神。'
    assert store.get_record_state('viewpoint_claim_links',link['viewpoint_claim_link_id'])['payload']['pinned_claim_revision']==2
    assert store.read_cvp_route_records(view['viewpoint_id'])
    assert source.run_workflow(store,'S1',old,registered.change_set_id,root,'完成來源更新')==result
    assert len(executions)==len(calls)==1
