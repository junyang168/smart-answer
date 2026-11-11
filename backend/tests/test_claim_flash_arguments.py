"""Argument routes: existing node decisions, typed pairs, synchronized SQL and replay."""
from copy import deepcopy
import json
import pytest
from backend.pipeline import claim_flash_arguments as stage,claim_flash_statements as statements
from backend.pipeline import claim_flash_evidence as eo,claim_flash_common as f,evidence_refresh as e
from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts,saved_inventory
from backend.tests.test_claim_update_flash import Ports as BasePorts
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store,_temporary_postgres

class Ports(BasePorts):
    def __init__(self,proposed=(),**kwargs):
        super().__init__(**kwargs)
        self.proposed=[{'relation_type':'supports','direction':'a_to_b',**p} for p in proposed]
    def repair(self,request):
        task=request['input']['task']
        if task=='argument_completeness':
            self.calls.append(('gpt',request))
            return {'items':[{'job_id':j['job_id'],'action':'complete','pairs':self.proposed,'reason':'對照原文與實際路線。'} for j in request['input']['jobs']]}
        if task.startswith('pair_'):
            self.calls.append(('gpt',request))
            return {'items':[{'job_id':j['job_id'],'action':'set','relation_type':'supports','direction':'a_to_b','reason':'原文的行為觀察支持這項判斷。'} for j in request['input']['jobs']]}
        if task=='argument_final_support':
            self.calls.append(('gpt',request));return {'items':[{'job_id':j['job_id'],'action':'keep','reason':'完整材料合起來支持。'} for j in request['input']['jobs']]}
        return super().repair(request)

@pytest.fixture
def ready(sample,tmp_path):
    store,root,_=sample;p=store.get_record_state('evidence_steps','E')['payload'];p['statement']='沒有聽從祂的話，顯明沒有真正相信祂。'
    cp=store.get_record_state('claims','C')['payload'];cp['statement']=p['statement']
    store.apply_plan(store.plan_package({'package_id':'arguments-fixture','evidence_steps':[p],'claims':[cp],
        'observations':[{'observation_id':'O','statement':'教授以沒有聽從的行為說明沒有真正相信。','source_fragment_id':'F'}]}))
    records,sources=f.load_source(store,'S');plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],
        'groups':[{'group_id':'G','title':'one','paragraph_keys':list(sources['S']['paragraphs']),'claim_ids':['C'],
                   'lower_record_keys':['evidence_steps/E','observations/O'],'reference_record_keys':[]}]}
    eroot=root/'evidence';prior=saved_inventory(tmp_path/'prior',sources)
    assert eo.run(store,'S',plan,eroot,EvidencePorts(),prior,apply=True)['status']=='completed'
    sroot=root/'statements';assert statements.run(store,'S',['C'],eroot,sroot,BasePorts(),apply=True)['status']=='verified'
    return store,root,plan,eroot,sroot


def test_new_argument_and_zero_model_replay(ready):
    store,root,plan,eroot,sroot=ready;old=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    proposed={'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'行為觀察作為論據。'}
    ports=Ports([proposed]);result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert result['status']=='applied'
    rel=[p for k,p in result['updates'].items() if k.startswith('knowledge_relations/')]
    assert len(rel)==1 and (rel[0]['from_id'],rel[0]['to_id'],rel[0]['relation_type'])==('O','E','supports')
    assert store.get_record_state('evidence_steps','E')['payload']==old
    request=next(r for role,r in ports.calls if role=='gpt' and r['input']['task']=='argument_completeness')
    assert f.RELATION_REASON_RULES in request['system']
    assert not any('record_fidelity' in r.get('questions',{}) or 'statement_fidelity' in r.get('questions',{}) for role,r in ports.calls if role=='jev')
    replay=Ports();assert stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',replay,apply=True)['status']=='applied'
    assert replay.calls==[]


def test_missing_support_restored_and_owners_synchronized(ready):
    store,root,plan,eroot,sroot=ready;c=store.get_record_state('claims','C')['payload'];c['evidence_step_ids']=[]
    p=store.get_record_state('evidence_steps','E')['payload'];p['produced_claim_ids']=[]
    store.apply_plan(store.plan_package({'package_id':'remove-only-route','claims':[c],'evidence_steps':[p]}))
    ports=Ports([{'a':'evidence_steps/E','b':'claims/C','fragment_ids':['F'],'reason':'恢復教授已表達的支持。'}])
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert result['status']=='applied'
    assert store.get_record_state('claims','C')['payload']['evidence_step_ids']==['E']
    assert store.get_record_state('claims','C')['payload']['statement']==c['statement']
    assert store.get_record_state('evidence_steps','E')['payload']['produced_claim_ids']==['C']


@pytest.mark.parametrize('defect',['endpoint','source','claim_pair'])
def test_unprovided_proposals_cannot_write(ready,defect):
    store,root,plan,eroot,sroot=ready;before=store.get_record_state('claims','C')
    p={'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'候選。'}
    if defect=='endpoint':p['a']='observations/OTHER'
    elif defect=='source':p['fragment_ids']=['OTHER']
    else:p.update(a='claims/C',b='claims/C')
    with pytest.raises(ValueError):stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',Ports([p]),apply=True)
    assert store.get_record_state('claims','C')==before
    assert not list((root/'arguments').glob('commit*.json'))


def test_missing_material_is_handoff_not_automatic_creation(ready):
    store,root,plan,eroot,sroot=ready;before=store.get_record_state('claims','C')
    class Handoff(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='argument_completeness':raw['items'][0].update(action='needs_material',reason='這項論據缺少材料節點。')
            return raw
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',Handoff(),apply=True)
    assert result['status']=='needs_attention' and result['pending'][0]['layer']=='evidence'
    assert store.get_record_state('claims','C')==before


def test_unsettled_or_changed_statement_does_not_reuse(ready):
    store,root,plan,eroot,sroot=ready;p=store.get_record_state('claims','C')['payload'];p['statement']='另外一個判斷。'
    store.apply_plan(store.plan_package({'package_id':'statement-drift','claims':[p]}));ports=Ports()
    with pytest.raises(ValueError,match='statement changed'):stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert ports.calls==[]


def test_human_approved_relation_is_not_rewritten(ready):
    store,root,plan,eroot,sroot=ready
    store.apply_plan(store.plan_package({'package_id':'approved-relation','knowledge_relations':[{
        'relation_id':'H','from_id':'O','to_id':'E','relation_type':'supports','reason':'已有人確認。',
        'record_namespace':f.VERSION,'review_status':'human_approved'}]}))
    before=store.get_record_state('knowledge_relations','H')
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',Ports(choices={'relation':'none'}),apply=True)
    assert result['status']=='needs_attention' and any(x['reason']=='protected_relation' for x in result['pending'])
    assert store.get_record_state('knowledge_relations','H')==before


def test_completed_proposal_is_not_rejudged_but_existing_support_is_still_checked(ready):
    store,root,plan,eroot,sroot=ready
    class Remove(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='pair_lower':
                for x in raw['items']:x.update(action='remove',reason='已有路線，不必建立直接邊。')
            return raw
    ports=Remove([{'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'候選。'}],choices={'relation':'none'})
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert not any(role=='gpt' and r['input']['task']=='pair_lower' for role,r in ports.calls)
    assert not any(role=='jev' and r['state'].get('A',{}).get('object_key')=='observations/O' for role,r in ports.calls)
    assert result['status']=='needs_attention' # Existing sole support was also rejected; never complete with none.


def test_sole_support_removal_cannot_be_completed(ready):
    store,root,plan,eroot,sroot=ready;before=store.get_record_state('claims','C')
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',Ports(choices={'relation':'none'}),apply=True)
    assert result['status']=='needs_attention' and any(x['reason']=='final_argument_has_no_support' for x in result['pending'])
    assert store.get_record_state('claims','C')==before


def test_statement_connection_handoff_commits_candidate_and_route_together(ready):
    store,root,plan,eroot,_=ready
    original=store.get_record_state('claims','C')
    text='沒有聽從耶穌的話，表示並未真正相信祂。'
    class StatementPorts(BasePorts):
        def repair(self,request):
            task=request['input']['task'];action='repair' if task=='claim_fidelity' else 'needs_connection'
            return {'items':[{'job_id':j['job_id'],'action':action,'statement':text if action=='repair' else None,
                             'reason':'原文有此判斷，另有論據需要補連。'} for j in request['input']['jobs']]}
    sroot=root/'pending-statements'
    checked=statements.run(store,'S',['C'],eroot,sroot,StatementPorts(choices={'statement_fidelity':'changed','support':'partial'}),apply=True)
    assert checked['status']=='needs_attention' and store.get_record_state('claims','C')==original
    ports=Ports([{'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'補齊原文觀察。'}])
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert result['status']=='applied' and result['resolved_statement_handoffs']
    assert store.get_record_state('claims','C')['payload']['statement']==text
    assert len([k for k in result['updates'] if k.startswith('knowledge_relations/')])==1
    assert not any('statement_fidelity' in r.get('questions',{}) for role,r in ports.calls if role=='jev')


def test_argument_commit_crash_reuses_saved_candidate_without_new_models(ready,monkeypatch):
    store,root,plan,eroot,sroot=ready;apply=store.apply_plan
    def crash(*a,**kw):apply(*a,**kw);raise RuntimeError('after SQL')
    monkeypatch.setattr(store,'apply_plan',crash)
    ports=Ports([{'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'原文觀察。'}])
    with pytest.raises(RuntimeError,match='after SQL'):stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    monkeypatch.setattr(store,'apply_plan',lambda *a,**kw:pytest.fail('duplicate SQL'))
    replay=Ports();assert stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',replay,apply=True)['status']=='applied'
    assert replay.calls==[]


def test_saved_plan_cannot_silently_shrink_or_change_source(ready):
    store,root,plan,eroot,sroot=ready;plan=deepcopy(plan);plan['groups'][0]['lower_record_keys']=['evidence_steps/E'];ports=Ports()
    with pytest.raises(ValueError,match='differs'):stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert ports.calls==[]


@pytest.fixture
def ready_pair(sample,tmp_path):
    store,root,_=sample
    ev=store.get_record_state('evidence_steps','E')['payload'];ev.update(statement='沒有聽從祂的話，顯明沒有真正相信祂。',produced_claim_ids=['C','D'])
    claim=store.get_record_state('claims','C')['payload'];claim['statement']=ev['statement']
    second={**deepcopy(claim),'claim_id':'D','statement':'教授以有沒有聽從耶穌的話判斷是否真正相信祂。'}
    store.apply_plan(store.plan_package({'package_id':'two-final-checks','evidence_steps':[ev],'claims':[claim,second],
        'observations':[{'observation_id':'O','statement':'教授以沒有聽從的行為說明沒有真正相信。','source_fragment_id':'F'}]}))
    _,sources=f.load_source(store,'S');plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],
        'groups':[{'group_id':'G','title':'one','paragraph_keys':list(sources['S']['paragraphs']),'claim_ids':['C','D'],
                   'lower_record_keys':['evidence_steps/E','observations/O'],'reference_record_keys':[]}]}
    eroot=root/'evidence';prior=saved_inventory(tmp_path/'prior-pair',sources)
    assert eo.run(store,'S',plan,eroot,EvidencePorts(),prior,apply=True)['status']=='completed'
    sroot=root/'statements';assert statements.run(store,'S',['C','D'],eroot,sroot,BasePorts(),apply=True)['status']=='verified'
    return store,root,plan,eroot,sroot


def test_two_changed_claim_support_checks_share_one_call_and_replay(ready_pair):
    store,root,plan,eroot,sroot=ready_pair
    ports=Ports([{'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'行為觀察支持判斷。'}],choices={'support':'partial'})
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert result['status']=='applied'
    requests=[r for role,r in ports.calls if role=='gpt' and r['input']['task']=='argument_final_support']
    assert len(requests)==1 and [j['job_id'] for j in requests[0]['input']['jobs']]==['claims/C','claims/D']
    assert '每個job_id獨立判定' in requests[0]['system']
    outcomes=[v for v in result['outcomes'] if v['basis']=='gpt_final_support']
    assert {v['object_key'] for v in outcomes}=={'claims/C','claims/D'} and len(outcomes)==2
    replay=Ports();assert stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',replay,apply=True)['status']=='applied'
    assert replay.calls==[]


def test_batch_keeps_unresolved_claim_separate_and_does_not_commit(ready_pair):
    store,root,plan,eroot,sroot=ready_pair;old={k:store.get_record_state('claims',k) for k in ['C','D']}
    class Mixed(Ports):
        def repair(self,request):
            result=super().repair(request)
            if request['input']['task']=='argument_final_support':
                for item in result['items']:
                    if item['job_id']=='claims/D':item.update(action='needs_connection',reason='D仍缺少連接。')
            return result
    ports=Mixed([{'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'行為觀察支持判斷。'}],choices={'support':'partial'})
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert result['status']=='needs_attention'
    assert [v['object_key'] for v in result['pending'] if v.get('reason')=='D仍缺少連接。']==['claims/D']
    assert {k:store.get_record_state('claims',k) for k in old}==old
    assert not (root/'arguments/commit.json').exists()


def _final_jobs():
    return [{'job_id':'claims/'+k,'material':{'claim':{'statement':k}},'findings':{'support':'partial'}} for k in ['C','D']]


def test_saved_legacy_single_answer_is_reused_only_unsolved_claim_sent(tmp_path):
    jobs=_final_jobs();ports=Ports();root=tmp_path/'final'
    list(f.gpt(root,ports,'argument_final_support',jobs[:1],statements.Edits.model_json_schema(),stage.FINAL_SUPPORT_PROMPT,semantic_review=False))
    ports.calls=[];result=stage.final_support_decisions(root,ports,jobs)
    assert [v.job_id for v in result]==['claims/C','claims/D']
    assert len(ports.calls)==1 and [j['job_id'] for j in ports.calls[0][1]['input']['jobs']]==['claims/D']


def test_unknown_legacy_single_call_cannot_be_reissued_as_batch(tmp_path):
    jobs=_final_jobs();ports=Ports();root=tmp_path/'final'
    request={'identity':ports.identity['gpt'],'system':stage.FINAL_SUPPORT_PROMPT,
        'input':{'task':'argument_final_support','jobs':jobs[:1]},'schema':statements.Edits.model_json_schema()}
    e.save(root/'gpt'/e.digest(request)/'request.json',request);e.save(root/'gpt'/e.digest(request)/'started.json',{'request_sha256':e.digest(request)})
    with pytest.raises(ValueError,match='no saved reply'):stage.final_support_decisions(root,ports,jobs)
    assert ports.calls==[]


@pytest.mark.parametrize('defect',['duplicate','foreign'])
def test_invalid_batched_final_answer_cannot_assign_a_verdict_to_another_claim(tmp_path,defect):
    class Bad(Ports):
        def repair(self,request):
            result=super().repair(request)
            result['items'][1]['job_id']='claims/C' if defect=='duplicate' else 'claims/OTHER'
            return result
    with pytest.raises(ValueError):stage.final_support_decisions(tmp_path,Bad(),_final_jobs())


def test_omitted_job_only_receives_one_bounded_correction(tmp_path):
    class Omit(Ports):
        def repair(self,request):
            result=super().repair(request)
            if len(request['input']['jobs'])==2:result['items']=result['items'][:1]
            return result
    ports=Omit();result=stage.final_support_decisions(tmp_path,ports,_final_jobs())
    assert [v.job_id for v in result]==['claims/C','claims/D']
    assert [[j['job_id'] for j in req['input']['jobs']] for _,req in ports.calls]==[['claims/C','claims/D'],['claims/D']]


def test_fragment_id_correction_restores_only_supplied_repeated_prefix():
    source_id = 'SRC-lecture-123456789abc'
    canonical = 'FR-lecture-123456789abc-DK-123456789abc-P04-E009-01'
    shortened = 'FR-lecture-123456789abc-P04-E009-01'
    missing = 'FR-lecture-123456789abc-P04-E009-02'
    assert stage.expand_fragment_ids(source_id, {canonical}, [shortened, missing]) == [canonical, missing]
    assert stage.expand_fragment_ids(source_id, {canonical, shortened}, [shortened]) == [shortened]


@pytest.mark.parametrize('approve', [True, False])
def test_source_clarification_checks_only_stopped_route(ready_pair, approve):
    store, root, plan, eroot, sroot = ready_pair
    target = root / 'arguments'

    class Review(Ports):
        def repair(self, request):
            result = super().repair(request)
            task = request['input']['task']
            if task == 'argument_completeness':
                for item in result['items']:
                    if item['job_id'] == 'claims/D':
                        item.update(action='needs_source', reason='補核前段經文。')
            if task == 'argument_final_support' and not approve:
                result['items'][0].update(action='needs_connection', reason='仍缺連接。')
            return result

    original = stage.run(store, 'S', plan, 'G', eroot, sroot, target, Review(), apply=True)
    assert original['status'] == 'needs_attention'
    before = json.loads((target / 'before.json').read_text())
    parent = (target / 'verification.json').read_bytes()
    e.save(target / 'material-scope-continuation.json', {
        'contract': stage.CANDIDATE_SCOPE, 'owner_direction': 'Resolve source scope',
        'parent_verification_sha256': e.digest(json.loads(parent)),
        'parent_before_sha256': e.digest(before),
        'source_context_keys': {'claims/D': ['source_fragments/F']},
    })
    ports = Review()
    result = stage.run(store, 'S', plan, 'G', eroot, sroot, target, ports, apply=True)
    assert result['status'] == ('verified' if approve else 'needs_attention')
    assert (target / 'verification.json').read_bytes() == parent
    assert len(ports.calls) == 1
    _, request = ports.calls[0]
    jobs = request['input']['jobs']
    assert [job['job_id'] for job in jobs] == ['claims/D']
    assert jobs[0]['source_context'][0]['fragment_id'] == 'F'
    replay = Review()
    assert stage.run(store, 'S', plan, 'G', eroot, sroot, target, replay, apply=True) == result
    assert replay.calls == []


def test_argument_continuation_corrects_only_the_pending_claim_statement(ready_pair):
    store, root, plan, eroot, sroot = ready_pair
    target = root / 'arguments'
    proposal = {'a': 'observations/O', 'b': 'evidence_steps/E',
                'fragment_ids': ['F'], 'reason': '原文行為支持判斷。'}

    class Finding(Ports):
        def repair(self, request):
            reply = super().repair(request)
            if request['input']['task'] == 'argument_final_support':
                for item in reply['items']:
                    if item['job_id'] == 'claims/D':
                        item.update(action='needs_connection', reason='措辭超出教授原話。')
            return reply

    result = stage.run(store, 'S', plan, 'G', eroot, sroot, target,
                       Finding([proposal], choices={'support': 'partial'}), apply=True)
    assert result['status'] == 'needs_attention'
    before = json.loads((target / 'before.json').read_text())
    parent = (target / 'verification.json').read_bytes()
    old_c = store.get_record_state('claims', 'C')
    old_e = store.get_record_state('evidence_steps', 'E')
    statement = '沒有聽從祂的話，顯明沒有真正相信祂。'
    e.save(target / 'material-scope-continuation.json', {
        'contract': stage.CANDIDATE_SCOPE, 'owner_direction': 'Correct the stopped statement',
        'parent_verification_sha256': e.digest(json.loads(parent)),
        'parent_before_sha256': e.digest(before),
        'statement_correction': {'object_key': 'claims/D', 'statement': statement,
                                 'before_sha256': e.digest(before['records']['claims/D']['payload'])},
    })
    ports = Ports()
    completed = stage.run(store, 'S', plan, 'G', eroot, sroot, target, ports, apply=True)
    assert completed['status'] == 'applied', completed.get('pending')
    assert store.get_record_state('claims', 'D')['payload']['statement'] == statement
    assert store.get_record_state('claims', 'C') == old_c
    assert store.get_record_state('evidence_steps', 'E') == old_e
    assert (target / 'verification.json').read_bytes() == parent
    assert [j['job_id'] for role, req in ports.calls if role == 'gpt'
            and req['input']['task'] == 'argument_completeness' for j in req['input']['jobs']] == ['claims/D']


def test_route_context_continuation_binds_the_saved_connection_finding(tmp_path):
    root = tmp_path / 'arguments'
    pending = {'object_key': 'claims/C', 'layer': 'source', 'reason': '需要前段原文。'}
    parent = {'version': stage.VERSION, 'status': 'needs_attention', 'pending': [pending]}
    before = {'records': {}, 'sources': {}}
    e.save(root/'verification.json', parent)
    e.save(root/'before.json', before)
    e.save(root/'material-scope-continuation.json', {
        'contract': stage.CANDIDATE_SCOPE, 'owner_direction': 'Check source context',
        'parent_verification_sha256': e.digest(parent), 'parent_before_sha256': e.digest(before),
        'source_context_keys': {'claims/C': ['source_fragments/F']},
    })
    source_root = stage.active_root(root)
    finding = '前段解釋仍缺論證連接。'
    review = {**parent, 'outcomes': [{'basis': 'route_source_clarification',
        'object_key': 'claims/C', 'action': 'needs_connection', 'reason': finding}]}
    e.save(source_root/'verification.json', review)
    followup = {'owner_direction': 'Resolve the recorded connection',
                'review_sha256': e.digest(review), 'finding': finding,
                'record_keys': ['evidence_steps/E']}
    e.save(root/'route-context-continuation.json', followup)
    assert stage.active_root(root) == source_root/'route-context'/e.digest(followup)
    assert json.loads((source_root/'verification.json').read_text()) == review
    (root/'route-context-continuation.json').write_text(json.dumps({**followup, 'finding': 'Different finding'}))
    with pytest.raises(ValueError, match='saved source clarification'):
        stage.active_root(root)
