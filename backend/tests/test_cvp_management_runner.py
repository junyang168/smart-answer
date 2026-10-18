"""CVP2 full orchestration against socket-only disposable SQL and fake providers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_recall import seal
from backend.pipeline import cvp_management_runner as runner
from backend.pipeline import cvp_management_models as models
from backend.pipeline import cvp_recall_preparation as preflight
from backend.pipeline.source_projection import project_script
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store
from test_cvp_management import proposal,passing
from test_cvp_management_models import envelope
from test_cvp_recall_preparation import POLICY,INPUT_POLICY
from test_cvp_recall_stages import Client,review_client


class Provider(Client):
    def __init__(self,*,reviewer=False,behavior=None):
        super().__init__();self.behavior=behavior
        if reviewer:self.backend='claude_subscription';self.model='claude-opus-5-5'
    def generate_json(self,**request):
        self.requests.append(request);p=json.loads(request['user_prompt'])
        if self.behavior:
            response=self.behavior(p)
            if response is not None:return response
        if p.get('schema_version','').endswith('_proposal_packet'):
            value=proposal()
            definitions=request['json_schema']['schema'].get('$defs',{})
            if not {'Summary','Summary500'} & set(definitions):value.pop('summaries')
            for ref in (p.get('previous') or {}).get('summary_material_pending',[]):
                if not any(s['ref']==ref for s in value.get('summaries',[])):
                    value.setdefault('summaries',[]).append({'ref':ref,'summary':'原有與新增成員的完整解釋。'})
            cref=p['input']['claims'][0]['claim_ref']
            for row in value['changes']:
                fields=json.loads(row['fields_json'])
                if fields.get('claim_id')=='c1':fields['claim_id']=cref
                row['fields_json']=json.dumps(fields,ensure_ascii=False)
            return envelope('candidate',candidate=value)
        if p.get('schema_version','').endswith('_review_packet'):
            return envelope('review',review=passing(p['candidate']))
        if 'summaries' in p:
            return {'verdicts':[{'cvp_ref':v['cvp_ref'],'decision':'approve','reason':'忠實'} for v in p['viewpoints']]}
        if 'claims' in p:
            return {'claim_candidates':[{'claim_ref':c['claim_ref'],'cvp_refs':[v['cvp_ref'] for v in p['viewpoints']]} for c in p['claims']]}
        return {'summaries':[{'cvp_ref':v['cvp_ref'],'summary':'人子稱號指向神性。'} for v in p['viewpoints']]}


@pytest.fixture
def setup(postgres_store,tmp_path,monkeypatch):
    store=postgres_store
    source=tmp_path/'source.json'
    script=[{'index':'subtitle-1','type':'subtitle','text':'## 人子'},
            {'index':1,'text':'人子是神。'},{'index':'subtitle-2','type':'subtitle','text':'## 後文'},
            {'index':2,'text':'這是另一個完整論證。'}]
    source.write_text(json.dumps(script,ensure_ascii=False))
    body=project_script(script).body_sha256
    package={'package_id':'integration-cvp2-seed','source_documents':[{
        'source_id':'S1','source_type':'sermon_transcript','transcript_id':'source','source_path':str(source),
        'locator_space':'spoken_body_v1','source_body_sha256':body,'source_sha256':body,
        'source_file_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}],
        'source_fragments':[{'fragment_id':'F1','source_id':'S1','paragraph_key':'S0001',
            'verbatim_excerpt':'人子是神。','source_sha256':body,'anchor_state':'source_version_bound'}],
        'claims':[{'claim_id':'C1','claim_type':'explicit_claim','statement':'人子是神。',
            'attribution':'professor','review_status':'ai_reviewed','evidence_step_ids':['E1']}],
        'evidence_steps':[{'evidence_step_id':'E1','source_fragment_id':'F1','statement':'教授原話。',
            'produced_claim_ids':['C1'],'support_eligibility':'eligible','review_status':'approved'}]}
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    from backend.api.canonical_repository.knowledge_models import KNOWLEDGE_COLLECTIONS
    with store.connect() as conn:
        for collection,rows in package.items():
            if collection not in KNOWLEDGE_COLLECTIONS:continue
            model,id_field=KNOWLEDGE_COLLECTIONS[collection]
            for row in rows:
                payload=model.model_validate(row).model_dump(mode='json')
                conn.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,review_status,visibility)
                    VALUES(%s,%s,1,%s,%s::jsonb,%s,%s)''',(collection,payload[id_field],record_content_sha(payload),canonical_json(payload),payload['review_status'],payload['visibility']))
    audit=tmp_path/'upstream-program-readback.json';write_immutable(audit,{'fixture':'upstream completed Claim program readback'})
    audit_binding={'path':str(audit),'file_sha256':hashlib.sha256(audit.read_bytes()).hexdigest()}
    row=store.get_record_state('claims','C1')
    round_={'kind':'claim_management_round','checkpoint':'audited','outcome':'completed',
        'program_readback_complete':True,'revisit_required':False,
        'stage_results':{'readback':audit_binding},
        'audited_pins':[{'collection':'claims','object_id':'C1','pin':[row['revision'],row['content_sha256'],False]}]}
    with store.connect() as conn:
        conn.execute('''INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata)
            VALUES('claim-round-C1','batch','C1','cvp_group','cli','succeeded',%s::jsonb)''',(canonical_json(round_),))
    def qualify(cid):
        row=store.get_record_state('claims',cid)
        return {'ready':True,'run_id':'claim-round-'+cid,'revision':row['revision'],'content_sha256':row['content_sha256'],
            'audit':audit_binding,'validation_kind':'opus_and_program_readback','independent_audit_complete':False} if row else None
    monkeypatch.setattr(store,'claim_management_qualification',qualify)
    gpt,opus=Provider(),Provider(reviewer=True)
    factory=lambda provider,model,effort:gpt if provider=='codex' else opus
    monkeypatch.setattr(preflight,'subscription_client',factory)
    monkeypatch.setattr(models,'subscription_client',factory)
    inp=seal({'schema_version':preflight.VERSION,'claim_ids':['C1'],
        'grouping':{'scope_label':'人子','groups':[{'group_key':'g1','claim_ids':['C1'],'rationale':'測試完整組'}]},
        'cvp_policy':str(POLICY),'claim_input_policy':str(INPUT_POLICY),'index_root':None,
        'recall_request_bytes':500000,'recall_max_calls':100})
    auth=tmp_path/'authorization.json'
    request={'schema_version':domain.VERSION,'action_id':'test-cvp2','input':inp,
        'authorization':{'path':str(auth),'file_sha256':'0'*64},
        'budget':{'max_calls':20,'max_revisions':5,'max_request_bytes':500000,'max_output_tokens':64000},
        'mode':'create','maintenance_viewpoint_ids':[],'permissions':[],
        'creation_slots':{'canonical_viewpoints':1,'viewpoint_revisions':1,'viewpoint_claim_links':1}}
    direction={'schema_version':'wang_cvp_management_authorization_v1','authorized_by':'owner',
        **{k:request[k] for k in ['action_id','mode','maintenance_viewpoint_ids','permissions','creation_slots','budget']},
        'input_sha256':inp['artifact_sha256']}
    write_immutable(auth,direction)
    request['authorization']['file_sha256']=hashlib.sha256(auth.read_bytes()).hexdigest()
    return store,request,tmp_path/'action',{'gpt':gpt,'opus':opus},source


def parent(store):
    return store.get_cvp_group('CVPM-'+runner.sha256_json('test-cvp2'))


def test_unwritten_oversized_action_can_yield_one_exact_group_without_erasing_history(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store,request,root,_,_=setup
    admission=preflight.prepare_recall(store,request['input'],allow_unqualified=True,defer_index=True)
    original=persistence.prepare_action(store,request,root=str(root),preflight=admission)
    step_id=persistence.prepare_step(store,original['run_id'],group_key='g1',generation='original',
        ordinal=0,materials={'original':'retained'},revision=False)
    successor=deepcopy(request);successor['action_id']='test-cvp2-first-group'
    with pytest.raises(ChangeSetConflict,match='resume their original task'):
        persistence.prepare_action(store,successor,root=str(root/'first-group'),preflight=admission)
    new=persistence.prepare_action(store,successor,root=str(root/'first-group'),preflight=admission,
        supersede_unwritten_run=original['run_id'])
    assert new['result']['status']=='running'
    assert store.get_cvp_group(original['run_id'])['result']['successor_run_id']==new['run_id']
    assert store.get_cvp_group(step_id)['checkpoint']=='prepared'
    with pytest.raises(ChangeSetConflict,match='closed CVP2 task'):
        persistence.prepare_step(store,original['run_id'],group_key='g1',generation='original',
            ordinal=0,materials={'original':'retained'},revision=False)


def test_reviewed_old_action_cannot_be_superseded(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict,canonical_json
    store,request,root,_,_=setup
    admission=preflight.prepare_recall(store,request['input'],allow_unqualified=True,defer_index=True)
    original=persistence.prepare_action(store,request,root=str(root),preflight=admission)
    step_id=persistence.prepare_step(store,original['run_id'],group_key='g1',generation='original',
        ordinal=0,materials={'original':'retained'},revision=False)
    saved=store.get_cvp_group(step_id);saved['checkpoint']='reviewed'
    with store.connect() as conn:
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
            (canonical_json(saved),step_id))
    successor=deepcopy(request);successor['action_id']='test-cvp2-first-group'
    with pytest.raises(ChangeSetConflict,match='reviewed or written'):
        persistence.prepare_action(store,successor,root=str(root/'first-group'),preflight=admission,
            supersede_unwritten_run=original['run_id'])
    assert store.get_cvp_group(original['run_id'])['result']['status']=='running'


def test_undispatched_call_plan_repair_retains_old_identity_and_blocks_started_call(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store,request,root,_,_=setup
    admission=preflight.prepare_recall(store,request['input'],allow_unqualified=True,defer_index=True)
    task=persistence.prepare_action(store,request,root=str(root),preflight=admission)
    run_id=task['run_id'];old={'cache_path':str(root/'proposal.json'),'request_bytes':100,'model_id':'gpt-6.1-sol'}
    slot=persistence.reserve_call(store,run_id,key='proposal.json',role='gpt',intent=old)
    new={**old,'request_bytes':90}
    updated=persistence.replace_undispatched_call_plan(store,run_id,key='proposal.json',
        prior_intent_sha256=slot['intent_sha256'],role='gpt',intent=new)
    assert updated['intent']==new
    assert updated['pre_dispatch_plans']==[{'intent':old,'intent_sha256':slot['intent_sha256']}]
    persistence.begin_call(store,run_id,key='proposal.json',intent=new)
    with pytest.raises(ChangeSetConflict,match='undispatched'):
        persistence.replace_undispatched_call_plan(store,run_id,key='proposal.json',
            prior_intent_sha256=updated['intent_sha256'],role='gpt',intent=old)


def test_success_chain_commits_actual_reviewed_postimage_and_replays_without_calls(setup):
    store,request,root,clients,source=setup
    before_source=source.read_bytes();before_claim=store.get_record_state('claims','C1')
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    assert store.get_record_state('claims','C1')==before_claim and source.read_bytes()==before_source
    assert len(store.read_cvp_heads())==1
    links=store.read_cvp_links(claim_ids=['C1'])
    assert len(links)==1 and next(iter(links.values()))['payload']['effective_state']=='active'
    assert runner.run(store,request,root=root,execute=True,clients=clients)==result
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    assert len(parent(store)['call_reservations'])==2


def test_existing_viewpoint_connection_rebinds_unchanged_summary_atomically(setup):
    from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
    store,request,root,clients,_=setup
    request['input']=seal({k:v for k,v in request['input'].items() if k!='artifact_sha256'} |
                          {'recall_profile':STATEMENT_SCOPE_PROFILE})
    auth=Path(request['authorization']['path']);direction=json.loads(auth.read_text())
    direction['input_sha256']=request['input']['artifact_sha256'];auth.write_text(json.dumps(direction))
    request['authorization']['file_sha256']=hashlib.sha256(auth.read_bytes()).hexdigest()
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='completed'
    original=store.get_record_state('canonical_viewpoints',store.read_cvp_heads()[0]['viewpoint_id'])['payload']
    second=add_second_claim(store,request)
    second['action_id']='test-cvp2-enrich'
    second['input']=seal({**{k:v for k,v in second['input'].items() if k!='artifact_sha256'},
        'claim_ids':['C2'],'grouping':{'scope_label':'人子','groups':[
            {'group_key':'g1','claim_ids':['C2'],'rationale':'增量連接'}]}})
    direction=json.loads(auth.read_text())
    direction.update(action_id=second['action_id'],input_sha256=second['input']['artifact_sha256'])
    auth.write_text(json.dumps(direction));second['authorization']['file_sha256']=hashlib.sha256(auth.read_bytes()).hexdigest()
    def connect(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return None
        cvp=packet['material']['viewpoints'][0]['cvp_ref']
        claim=packet['input']['claims'][0]['claim_ref']
        value=proposal()
        value['changes']=[{'ref':'nl1','fields_json':json.dumps(
            {'viewpoint_id':cvp,'claim_id':claim,'link_type':'equivalent_full'})}]
        value['component_dispositions'][0]['target_ref']=cvp
        value['summaries']=[]
        return envelope('candidate',candidate=value)
    clients['gpt'].behavior=connect
    result=runner.run(store,second,root=root.parent/'enrich',execute=True,clients=clients)
    assert result['status']=='completed',store.get_cvp_group('CVPM-'+runner.sha256_json(second['action_id']))['units']['g1'].get('detail')
    vid=store.read_cvp_heads()[0]['viewpoint_id']
    current=store.get_record_state('canonical_viewpoints',vid)['payload']
    assert current['summary']==original['summary']
    assert current['summary_basis']['member_link_ids']==sorted(
        oid for (col,oid) in store.read_cvp_links(viewpoint_ids=[vid]) if col=='viewpoint_claim_links')
    assert len(current['summary_basis']['member_link_ids'])==2


def authorize(request,**budget):
    request=deepcopy(request);request['budget'].update(budget)
    path=Path(request['authorization']['path'])
    direction=json.loads(path.read_text());direction['budget']=request['budget']
    path.write_text(json.dumps(direction,ensure_ascii=False))
    request['authorization']['file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return request


def test_prepared_candidate_resume_uses_original_materials_and_cached_proposal(setup,monkeypatch):
    store,request,root,clients,_=setup
    write=runner.write_immutable;crashed=[]
    def crash_after_candidate(path,value,**kwargs):
        write(path,value,**kwargs)
        if Path(path).name=='candidate.json' and not crashed:
            crashed.append(True);raise RuntimeError('process stopped after candidate checkpoint')
    monkeypatch.setattr(runner,'write_immutable',crash_after_candidate)
    first=runner.run(store,request,root=root,execute=True,clients=clients)
    assert first['status']=='partial'
    action=parent(store);step_id=action['units']['g1']['steps'][0]
    before=store.get_cvp_group(step_id)['materials']
    assert before['previous'] is None and len(clients['gpt'].requests)==1 and not clients['opus'].requests
    monkeypatch.setattr(runner,'write_immutable',write)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert store.get_cvp_group(step_id)['materials']==before
    assert len(parent(store)['units']['g1']['steps'])==1
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1


def test_unknown_opus_cannot_obtain_new_step_generation_or_root(setup):
    store,request,root,clients,_=setup
    def unknown(packet):
        if packet.get('schema_version','').endswith('_review_packet'):raise TimeoutError('unknown Opus result')
    clients['opus'].behavior=unknown
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['units']['g1']=='resource_hold'
    clients['opus'].behavior=None
    again=runner.run(store,request,root=root,execute=True,clients=clients)
    assert again['units']['g1']=='resource_hold'
    assert 'unknown' in parent(store)['units']['g1']['detail']
    assert len(parent(store)['units']['g1']['steps'])==1
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    with pytest.raises(Exception,match='root/request/policy cannot reset'):
        runner.run(store,request,root=root.parent/'different-root',execute=True,clients=clients)
    assert len(clients['opus'].requests)==1


def test_cumulative_call_budget_stays_spent_on_prepared_candidate_resume(setup):
    store,request,root,clients,_=setup;request=authorize(request,max_calls=1)
    for _ in range(2):
        result=runner.run(store,request,root=root,execute=True,clients=clients)
        assert result['units']['g1']=='resource_hold',parent(store)['units']['g1'].get('detail')
    assert len(clients['gpt'].requests)==1 and not clients['opus'].requests
    assert len(parent(store)['call_reservations'])==1
    assert len(parent(store)['units']['g1']['steps'])==1
    assert store.read_cvp_heads()==[]


def test_no_semantic_progress_is_held_and_replay_never_adds_calls(setup):
    store,request,root,clients,_=setup
    def reject(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        candidate=packet['candidate'];value=passing(candidate)
        component=candidate['review_sets']['components'][0]
        value['component_reviews'][0].update(decision='revise',finding_refs=['f1'])
        value['findings']=[{'slot':'f1','target_refs':[component],'category':'wording','reason':'需說清限定'}]
        value['prior_findings']=[{'ref':ref,'disposition':'resolved','reason':'逐項核對'} for ref in candidate['review_sets']['prior_findings']]
        return envelope('review',review=value)
    clients['opus'].behavior=reject
    for _ in range(2):
        result=runner.run(store,request,root=root,execute=True,clients=clients)
        assert result['units']['g1']=='resource_hold',parent(store)['units']['g1'].get('detail')
        assert 'progress' in parent(store)['units']['g1']['detail']
    assert len(clients['gpt'].requests)==2 and len(clients['opus'].requests)==1
    assert store.read_cvp_heads()==[]


def test_material_expansion_is_same_action_step_without_semantic_revision_charge(setup):
    store,request,root,clients,_=setup;asked=[]
    def expand(packet):
        if packet.get('schema_version','').endswith('_proposal_packet') and not asked:
            asked.append(True);source=packet['material']['sources'][0]
            return envelope('context_insufficient',contexts=[{'source_ref':source['source_ref'],
                'section_ref':source['sections'][0]['section_ref'],'extent':'adjacent','reason':'需讀下段承接'}])
    clients['gpt'].behavior=expand
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert len(clients['gpt'].requests)==2 and len(clients['opus'].requests)==1
    assert len(parent(store)['units']['g1']['steps'])==2
    assert parent(store)['revision_rounds']==[]
    step=store.get_cvp_group(parent(store)['units']['g1']['completed_step'])
    assert step['materials']['bundle']['material']['sources'][0]['expansion_level']==1


def test_proposal_finding_returns_to_proposer_before_first_review(setup):
    store,request,root,clients,_=setup;proposals=[]
    def propose_with_first_finding(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return
        value=proposal();cref=packet['input']['claims'][0]['claim_ref']
        for row in value['changes']:
            fields=json.loads(row['fields_json'])
            if fields.get('claim_id')=='c1':fields['claim_id']=cref
            if 'core_proposition' in fields and proposals:
                fields['core_proposition']='此處的人子是神。'
            row['fields_json']=json.dumps(fields,ensure_ascii=False)
        if not proposals:
            value['findings']=[{'slot':'f1','target_refs':['nv1'],
                'category':'wording','reason':'先釐清觀點的適用語境。'}]
        proposals.append(True)
        return envelope('candidate',candidate=value)
    def review_resolved(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        candidate=packet['candidate'];value=passing(candidate)
        assert not candidate['findings']
        value['prior_findings']=[{'ref':ref,'disposition':'resolved',
            'reason':'已核對修訂後的適用語境。'} for ref in candidate['review_sets']['prior_findings']]
        return envelope('review',review=value)
    clients['gpt'].behavior=propose_with_first_finding
    clients['opus'].behavior=review_resolved
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert len(clients['gpt'].requests)==2 and len(clients['opus'].requests)==1
    assert len(parent(store)['revision_rounds'])==1


def test_more_than_two_bounded_revisions_with_real_sql_and_original_call_budget(setup):
    store,request,root,clients,_=setup;proposal_count=[];review_count=[]
    def revise(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return
        wording=['人子是神。','此處的人子是神。','在這段話中，人子是神。','教授在此宣認人子是神。'][len(proposal_count)]
        proposal_count.append(True);value=proposal();cref=packet['input']['claims'][0]['claim_ref']
        for row in value['changes']:
            fields=json.loads(row['fields_json'])
            if fields.get('claim_id')=='c1':fields['claim_id']=cref
            if 'core_proposition' in fields:fields['core_proposition']=wording
            row['fields_json']=json.dumps(fields,ensure_ascii=False)
        return envelope('candidate',candidate=value)
    def review(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        candidate=packet['candidate'];value=passing(candidate);review_count.append(True)
        value['prior_findings']=[{'ref':ref,'disposition':'resolved','reason':'本輪重新核對已修訂內容'} for ref in candidate['review_sets']['prior_findings']]
        if len(review_count)<=3:
            component=candidate['review_sets']['components'][0]
            value['component_reviews'][0].update(decision='revise',finding_refs=['f1'])
            value['findings']=[{'slot':'f1','target_refs':[component],'category':'wording','reason':'本輪仍需說清一處限定'}]
        return envelope('review',review=value)
    clients['gpt'].behavior=revise;clients['opus'].behavior=review
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert len(parent(store)['revision_rounds'])==3
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==4
    assert len(parent(store)['call_reservations'])==8


def test_indivisible_complete_material_hold_preserves_denominator_without_dispatch(setup):
    store,request,root,clients,_=setup;request=authorize(request,max_request_bytes=1000)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    action=parent(store);original=action['units']['g1']
    assert original['status']=='partitioned',original.get('detail')
    assert action['groups'][0]['claim_ids']==['C1']
    child=action['work_groups'][0]
    assert result['units'][child['group_key']]=='resource_hold' and result['units']['g1']=='partitioned'
    assert child['claim_ids']==['C1'] and action['units'][child['group_key']]['status']=='resource_hold'
    assert original['capacity']['plan']['denominator']=={'original_claim_count':1,'ready_claim_count':0,'held_claim_count':1}
    assert not clients['gpt'].requests and not clients['opus'].requests and not action['call_reservations']
    assert runner.run(store,request,root=root,execute=True,clients=clients)==result


def add_second_claim(store,request):
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    old=store.get_record_state('claims','C1')['payload'];claim=deepcopy(old);claim['claim_id']='C2';claim['evidence_step_ids']=['E2']
    old=store.get_record_state('evidence_steps','E1')['payload'];evidence=deepcopy(old);evidence['evidence_step_id']='E2';evidence['produced_claim_ids']=['C2']
    with store.connect() as conn:
        for col,oid,payload in [('claims','C2',claim),('evidence_steps','E2',evidence)]:
            conn.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,review_status,visibility)
                VALUES(%s,%s,1,%s,%s::jsonb,%s,%s)''',(col,oid,record_content_sha(payload),canonical_json(payload),payload['review_status'],payload['visibility']))
        previous=conn.execute("SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id='claim-round-C1'").fetchone()[0]
        previous=deepcopy(previous);pin=previous['audited_pins'][0];pin.update(object_id='C2',pin=[1,record_content_sha(claim),False])
        conn.execute('''INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata)
            VALUES('claim-round-C2','batch','C2','cvp_group','cli','succeeded',%s::jsonb)''',(canonical_json(previous),))
    request=deepcopy(request);inp={k:v for k,v in request['input'].items() if k!='artifact_sha256'}
    inp['claim_ids']=['C1','C2'];inp['grouping']['groups'][0]['claim_ids']=['C1','C2']
    request['input']=seal(inp)
    path=Path(request['authorization']['path']);direction=json.loads(path.read_text())
    direction['input_sha256']=request['input']['artifact_sha256'];path.write_text(json.dumps(direction))
    request['authorization']['file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    return request


@pytest.mark.parametrize('interrupt',[False,True])
@pytest.mark.parametrize('statement_scope',[False,True])
def test_capacity_children_complete_independently_reuse_recall_and_current_approved_summary(setup,monkeypatch,interrupt,statement_scope):
    store,request,root,clients,_=setup;request=add_second_claim(store,request)
    if statement_scope:
        from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
        request['input']=seal({k:v for k,v in request['input'].items() if k!='artifact_sha256'} |
                              {'recall_profile':STATEMENT_SCOPE_PROFILE})
        path=Path(request['authorization']['path']);direction=json.loads(path.read_text())
        direction['input_sha256']=request['input']['artifact_sha256'];path.write_text(json.dumps(direction))
        request['authorization']['file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
        def no_summary_stage(*args,**kwargs):raise AssertionError('statement scope needs no summary model stage')
        monkeypatch.setattr(runner,'maintain_reviewed_index',no_summary_stage)
        monkeypatch.setattr(runner,'load_reviewed_index',no_summary_stage)
        monkeypatch.setattr(runner,'_completed_summary_proofs',no_summary_stage)
        from backend.pipeline import cvp_management_index
        monkeypatch.setattr(cvp_management_index,'write_committed_summary_proofs',no_summary_stage)
    original_adapter=runner.adapter
    def measured(role,req,bundle,packet,**kwargs):
        result=original_adapter(role,req,bundle,packet,**kwargs)
        # Simulate a provider byte ceiling at whole-Claim boundaries. The
        # real scoped packet/schema is still used by the common cache runtime.
        if role=='proposal':
            result.request_bytes=lambda payload:600000 if len(payload['input']['claims'])>1 else 100000
        return result
    monkeypatch.setattr(runner,'adapter',measured)
    if interrupt:
        progress=runner.persistence.progress
        def crash_after_first_completed(*args,**kwargs):
            result=progress(*args,**kwargs)
            if kwargs['status']=='completed':raise KeyboardInterrupt('process exited after durable completed step')
            return result
        monkeypatch.setattr(runner.persistence,'progress',crash_after_first_completed)
        with pytest.raises(KeyboardInterrupt):runner.run(store,request,root=root,execute=True,clients=clients)
        assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
        monkeypatch.setattr(runner.persistence,'progress',progress)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    action=parent(store)
    assert result['status']=='completed',{k:v.get('detail') for k,v in action['units'].items()}
    assert len(action['work_groups'])==2 and action['units']['g1']['status']=='completed'
    assert {tuple(g['claim_ids']) for g in action['work_groups']}=={('C1',),('C2',)}
    assert all(action['units'][g['group_key']]['status']=='completed' for g in action['work_groups'])
    # The second unit recalls the first CVP. Its initial create proposal must
    # read that CVP's member source and receive one propose follow-up before
    # Opus may compare the new viewpoint against it.
    assert len(clients['gpt'].requests)==4 and len(clients['opus'].requests)==2
    second=next(g for g in action['work_groups'] if g['claim_ids']==['C2'])
    steps=action['units'][second['group_key']]['steps']
    assert len(steps)==2
    final_bundle=store.get_cvp_group(steps[-1])['materials']['bundle']
    assert final_bundle['member_support_ids']==['C1']
    assert any(row['claim_ref']==final_bundle['references']['claims']['C1'] and row['support_loaded']
               for row in final_bundle['material']['claims'])
    assert len(store.read_cvp_heads())==2
    assert len(store.read_cvp_links(claim_ids=['C1','C2']))==2
    snapshot={r:len(c.requests) for r,c in clients.items()}
    assert runner.run(store,request,root=root,execute=True,clients=clients)==result
    assert snapshot=={r:len(c.requests) for r,c in clients.items()}


def test_new_human_constraint_versions_materials_keeps_original_candidate_and_budget(setup,monkeypatch):
    store,request,root,clients,_=setup
    write=runner.write_immutable;stopped=[]
    def crash(path,value,**kwargs):
        write(path,value,**kwargs)
        if Path(path).name=='candidate.json' and not stopped:
            stopped.append(True);raise RuntimeError('interrupted before review')
    monkeypatch.setattr(runner,'write_immutable',crash)
    runner.run(store,request,root=root,execute=True,clients=clients)
    original=store.get_cvp_group(parent(store)['units']['g1']['steps'][0])
    oldroot=Path(original['materials']['step_root']);oldbytes=(oldroot/'candidate.json').read_bytes()
    constraint=seal({'schema_version':domain.VERSION+'_human_constraint','constraint_id':'human-scope-1',
        'action_id':request['action_id'],'group_key':'g1','kind':'interpretation',
        'target_bindings':{'k1':{'claim_id':'C1'}},'reason':'依原文保留此範圍。',
        'supersedes_constraint_ids':[],'approval_granted':False,'budget_reset':False})
    from backend.api.canonical_repository.postgres_store import canonical_json
    with store.connect() as conn:
        metadata=parent(store);metadata['human_constraints']=[constraint]
        conn.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
            (canonical_json(metadata),metadata['run_id']))
    monkeypatch.setattr(runner,'write_immutable',write)
    def review_with_constraint(packet):
        if packet.get('schema_version','').endswith('_review_packet'):
            review=passing(packet['candidate'])
            review['prior_findings']=[{'ref':ref,'disposition':'resolved','reason':'核對人工釐清與原文後像。'}
                                      for ref in packet['candidate']['prior_finding_refs']]
            return envelope('review',review=review)
    clients['opus'].behavior=review_with_constraint
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    steps=parent(store)['units']['g1']['steps']
    assert len(steps)==2 and len(clients['gpt'].requests)==2 and len(clients['opus'].requests)==1
    new=store.get_cvp_group(steps[-1])
    assert new['generation']!=original['generation']
    assert new['materials']['previous']['rebase_history'][0]['candidate_sha256']==json.loads(oldbytes)['artifact_sha256']
    assert oldbytes==(oldroot/'candidate.json').read_bytes()
    assert store.get_cvp_group(original['run_id'])['materials']==original['materials']
    assert len(parent(store)['call_reservations'])==3


def test_admission_repair_reenters_same_action_and_original_budget(setup,monkeypatch):
    store,request,root,clients,_=setup
    qualification=store.claim_management_qualification
    monkeypatch.setattr(store,'claim_management_qualification',lambda cid:None)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['units']['g1']=='needs_upstream'
    original=parent(store)
    assert not original['call_reservations'] and not original['units']['g1']['steps']
    monkeypatch.setattr(store,'claim_management_qualification',qualification)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert parent(store)['run_id']==original['run_id']
    assert len(parent(store)['call_reservations'])==2
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1


def test_saved_answer_before_return_receipt_reconciles_without_new_provider_dispatch(setup,monkeypatch):
    store,request,root,clients,_=setup
    returned=runner.persistence.returned_call;crashed=[]
    def crash_before_receipt(*args,**kwargs):
        if not crashed:
            crashed.append(True);raise KeyboardInterrupt('process exited between raw cache and returned receipt')
        return returned(*args,**kwargs)
    monkeypatch.setattr(runner.persistence,'returned_call',crash_before_receipt)
    with pytest.raises(KeyboardInterrupt):runner.run(store,request,root=root,execute=True,clients=clients)
    assert len(clients['gpt'].requests)==1
    assert next(iter(parent(store)['call_reservations'].values()))['state']=='started'
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    assert len(parent(store)['call_reservations'])==2


@pytest.mark.parametrize('interrupt_frame',[False,True])
def test_changed_qualified_claim_reenters_same_task_with_explicit_new_positions_and_old_mapping(setup,monkeypatch,interrupt_frame):
    store,request,root,clients,_=setup
    write=runner.write_immutable;stopped=[]
    def stop_after_rejection(path,value,**kwargs):
        write(path,value,**kwargs)
        if Path(path).name=='approval.json' and not value['approved'] and not stopped:
            stopped.append(True);raise RuntimeError('process stopped after actual rejected review')
    monkeypatch.setattr(runner,'write_immutable',stop_after_rejection)
    def reject(packet):
        if packet.get('schema_version','').endswith('_review_packet'):
            value=passing(packet['candidate']);ref=value['component_reviews'][0]['ref']
            value['component_reviews'][0].update(decision='revise',finding_refs=['f1'])
            value['findings']=[{'slot':'f1','target_refs':[ref],'category':'wording','reason':'需要忠實保留語境限定。'}]
            return envelope('review',review=value)
    clients['opus'].behavior=reject
    first=runner.run(store,request,root=root,execute=True,clients=clients)
    assert first['status']=='partial'
    action=parent(store);original_step=store.get_cvp_group(action['units']['g1']['steps'][0])
    oldroot=Path(original_step['materials']['step_root']);oldcandidate=(oldroot/'candidate.json').read_bytes()
    oldapproval=(oldroot/'approval.json').read_bytes()
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    row=store.get_record_state('claims','C1');changed=deepcopy(row['payload'])
    changed.update(statement='在此語境中，人子是神。',revision=2)
    audit=root/'new-upstream-readback.json';write_immutable(audit,{'fixture':'new independently reviewed Claim wording'})
    binding={'path':str(audit),'file_sha256':hashlib.sha256(audit.read_bytes()).hexdigest()}
    qrun={'kind':'claim_management_round','checkpoint':'audited','outcome':'completed',
        'program_readback_complete':True,'revisit_required':False,'stage_results':{'readback':binding},
        'audited_pins':[{'collection':'claims','object_id':'C1','pin':[2,record_content_sha(changed),False]}]}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=2,content_sha256=%s,payload=%s::jsonb WHERE collection='claims' AND object_id='C1'",
            (record_content_sha(changed),canonical_json(changed)))
        conn.execute('''INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata)
            VALUES('claim-round-C1-new','batch','C1','cvp_group','cli','succeeded',%s::jsonb)''',(canonical_json(qrun),))
    monkeypatch.setattr(store,'claim_management_qualification',lambda cid:{'ready':True,'run_id':'claim-round-C1-new',
        'revision':2,'content_sha256':record_content_sha(changed),'audit':binding,
        'validation_kind':'opus_and_program_readback','independent_audit_complete':False})
    monkeypatch.setattr(runner,'write_immutable',write)
    seen=[]
    def explicit_replacement(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return
        assert packet['previous']['component_plan']['components']==json.loads(oldcandidate)['component_plan']['components']
        prepared=store.get_cvp_group(parent(store)['units']['g1']['steps'][-1])['materials']
        with pytest.raises(ValueError):
            domain.compile_candidate(request,prepared['bundle'],{},previous=prepared['previous'],prior_findings=prepared['prior_findings'])
        assert not store.read_cvp_heads()  # old candidate cannot submit on new wording
        unit=packet['input']['text_units'][0];cref=packet['input']['claims'][0]['claim_ref']
        value=proposal();value['components']=[{'claim_ref':cref,'spans':[{'text_ref':unit['text_ref'],
            'start_char':0,'end_char':len(unit['text'])}]}]
        value['component_mappings']=[{'old_ref':'k1','new_refs':['k1']}]
        for change in value['changes']:
            fields=json.loads(change['fields_json'])
            if fields.get('claim_id')=='c1':fields['claim_id']=cref
            if 'core_proposition' in fields:fields['core_proposition']=changed['statement']
            change['fields_json']=json.dumps(fields,ensure_ascii=False)
        seen.append(packet);return envelope('candidate',candidate=value)
    def accept(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        value=passing(packet['candidate']);value['prior_findings']=[{'ref':ref,'disposition':'resolved',
            'reason':'明確核對新位置、舊成分映射與原文限定。'} for ref in packet['candidate']['review_sets']['prior_findings']]
        return envelope('review',review=value)
    clients['gpt'].behavior=explicit_replacement;clients['opus'].behavior=accept
    if interrupt_frame:
        frame_stopped=[]
        def stop_after_frame(path,value,**kwargs):
            write(path,value,**kwargs)
            if Path(path).name=='materials.json' and value.get('previous',{}).get('schema_version')==domain.VERSION+'_rebase_frame' and not frame_stopped:
                frame_stopped.append(True);raise RuntimeError('process stopped with pending rebase frame')
        monkeypatch.setattr(runner,'write_immutable',stop_after_frame)
        held=runner.run(store,request,root=root,execute=True,clients=clients)
        assert held['status']=='partial' and frame_stopped
        assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
        monkeypatch.setattr(runner,'write_immutable',write)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    current=parent(store);assert current['run_id']==action['run_id']
    assert len(current['call_reservations'])==4 and len(clients['gpt'].requests)==len(clients['opus'].requests)==2
    assert len(seen)==1 and (oldroot/'candidate.json').read_bytes()==oldcandidate and (oldroot/'approval.json').read_bytes()==oldapproval
    completed=store.get_cvp_group(current['units']['g1']['completed_step'])
    candidate=runner.read_artifact(Path(completed['materials']['step_root'])/'candidate.json')
    assert candidate['component_plan']['old_components']==json.loads(oldcandidate)['component_plan']['components']
    assert candidate['component_plan']['components'][0]['spans'][0]['exact_text']==changed['statement']
    assert candidate['prior_findings'] and candidate['rebase_history'][0]['wording_changed'] is True
    link=next(iter(store.read_cvp_links(claim_ids=['C1']).values()))['payload']
    assert link['pinned_claim_revision']==2
    assert candidate['component_plan']['input_sha256']==completed['materials']['bundle']['claim_input']['artifact_sha256']
    assert store.get_record_state('claims','C1')['payload']==changed


def test_changed_upstream_evidence_same_claim_reenters_same_task_and_reviews_current_empty_delta(setup,monkeypatch):
    store,request,root,clients,_=setup
    write=runner.write_immutable;interrupted=[]
    def crash_after_rejected_review(path,value,**kwargs):
        write(path,value,**kwargs)
        if Path(path).name=='approval.json' and not value['approved'] and not interrupted:
            interrupted.append(True);raise RuntimeError('interrupted with actual rejected review')
    monkeypatch.setattr(runner,'write_immutable',crash_after_rejected_review)
    def reject(packet):
        if packet.get('schema_version','').endswith('_review_packet'):
            review=passing(packet['candidate']);ref=review['component_reviews'][0]['ref']
            review['component_reviews'][0].update(decision='revise',finding_refs=['f1'])
            review['findings']=[{'slot':'f1','target_refs':[ref],'category':'source','reason':'需核對支持此判斷的完整證據。'}]
            return envelope('review',review=review)
    clients['opus'].behavior=reject
    runner.run(store,request,root=root,execute=True,clients=clients)
    original=parent(store);oldstep=store.get_cvp_group(original['units']['g1']['steps'][0])
    oldroot=Path(oldstep['materials']['step_root']);oldcandidate=(oldroot/'candidate.json').read_bytes()
    oldapproval=(oldroot/'approval.json').read_bytes();claim_before=store.get_record_state('claims','C1')
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    evidence=deepcopy(store.get_record_state('evidence_steps','E1')['payload'])
    evidence.update(statement='教授在完整語境內明說人子是神。',revision=2)
    audit=root/'evidence-upstream-readback.json';write_immutable(audit,{'fixture':'Claim audited again against updated evidence'})
    audit_binding={'path':str(audit),'file_sha256':hashlib.sha256(audit.read_bytes()).hexdigest()}
    qualified={'ready':True,'run_id':'claim-round-evidence-new','revision':claim_before['revision'],
        'content_sha256':claim_before['content_sha256'],'audit':audit_binding,
        'validation_kind':'opus_and_program_readback','independent_audit_complete':False}
    qrun={'kind':'claim_management_round','checkpoint':'audited','outcome':'completed',
        'program_readback_complete':True,'revisit_required':False,'stage_results':{'readback':audit_binding},
        'audited_pins':[{'collection':'claims','object_id':'C1','pin':[claim_before['revision'],claim_before['content_sha256'],False]}]}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET revision=2,content_sha256=%s,payload=%s::jsonb WHERE collection='evidence_steps' AND object_id='E1'",
            (record_content_sha(evidence),canonical_json(evidence)))
        conn.execute('''INSERT INTO wang_knowledge.pipeline_runs(run_id,subject_kind,subject_id,stage,trigger,status,metadata)
            VALUES('claim-round-evidence-new','batch','C1','cvp_group','cli','succeeded',%s::jsonb)''',(canonical_json(qrun),))
    monkeypatch.setattr(store,'claim_management_qualification',lambda cid:deepcopy(qualified))
    monkeypatch.setattr(runner,'write_immutable',write);seen=[]
    def retain_after_current_evidence(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return
        assert packet['previous']['requires_explicit_component_plan'] is False
        assert packet['previous']['component_plan']['components']==json.loads(oldcandidate)['component_plan']['components']
        assert packet['material']['evidence'][0]['content']['statement']==evidence['statement']
        seen.append(packet);return envelope('candidate',candidate={})
    def review_current_afterimage(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        assert packet['material']['evidence'][0]['content']['statement']==evidence['statement']
        review=passing(packet['candidate']);review['prior_findings']=[{'ref':ref,'disposition':'resolved',
            'reason':'已依當前完整證據重新核對判斷，保留原有成分。'} for ref in packet['candidate']['review_sets']['prior_findings']]
        return envelope('review',review=review)
    clients['gpt'].behavior=retain_after_current_evidence;clients['opus'].behavior=review_current_afterimage
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    action=parent(store);assert action['run_id']==original['run_id'] and len(action['call_reservations'])==4
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==2 and len(seen)==1
    assert (oldroot/'candidate.json').read_bytes()==oldcandidate and (oldroot/'approval.json').read_bytes()==oldapproval
    completed=store.get_cvp_group(action['units']['g1']['completed_step'])
    candidate=runner.read_artifact(Path(completed['materials']['step_root'])/'candidate.json')
    assert candidate['component_plan']==json.loads(oldcandidate)['component_plan']
    assert candidate['rebase_history'][-1]['wording_changed'] is False and candidate['prior_findings']
    assert store.get_record_state('claims','C1')==claim_before
    assert store.get_record_state('evidence_steps','E1')['payload']==evidence
    # The old rejection and new approval remain bound to different complete
    # materials despite identical Claim wording and semantic candidate writes.
    approval=runner.read_artifact(Path(completed['materials']['step_root'])/'approval.json')
    assert approval['approved'] is True and approval['candidate_sha256']!=json.loads(oldapproval)['candidate_sha256']
    assert completed['materials']['bundle']['artifact_sha256']!=oldstep['materials']['bundle']['artifact_sha256']
