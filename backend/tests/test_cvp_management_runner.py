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


def test_existing_summary_capability_can_be_clarified_without_resetting_history(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    from backend.pipeline.cvp_sql_commit import binding
    store,request,root,_,_=setup
    request['input']=seal({k:v for k,v in request['input'].items() if k!='artifact_sha256'}|
                          {'recall_profile':STATEMENT_SCOPE_PROFILE})
    admission=preflight.prepare_recall(store,request['input'],allow_unqualified=True,defer_index=True)
    task=persistence.prepare_action(store,request,root=str(root),preflight=admission)
    pid=task['run_id']
    step=persistence.prepare_step(store,pid,group_key='g1',generation='legacy',ordinal=0,materials={'legacy':True})
    intent={'cache_path':str(root/'proposal.json'),'request_bytes':100,'model_id':'gpt-6.1-sol'}
    persistence.reserve_call(store,pid,key='proposal.json',role='gpt',intent=intent)
    persistence.begin_call(store,pid,key='proposal.json',intent=intent)
    implementation=[binding(Path(runner.__file__))]
    with pytest.raises(ChangeSetConflict,match='exact returned original step'):
        persistence.clarify_summary_enrichment(store,pid,step_id=step,implementation=implementation)
    persistence.returned_call(store,pid,key='proposal.json',raw={'saved':'answer'},artifact_sha256='sha')
    before=store.get_cvp_group(pid)
    receipt=persistence.clarify_summary_enrichment(store,pid,step_id=step,implementation=implementation)
    assert persistence.clarify_summary_enrichment(store,pid,step_id=step,implementation=implementation)==receipt
    after=store.get_cvp_group(pid)
    for key in ('request','call_reservations','revision_rounds','execution_root','units'):
        assert before[key]==after[key]
    bundle=seal({'schema_version':'wang_cvp_common_material_v1','group_key':'g1','records':[],'material':{}})
    clarified=persistence.bind_task_constraints(store,pid,bundle)
    assert clarified['summary_enrichment_contract']==domain.SUMMARY_ENRICHMENT_CONTRACT
    assert 'summary_enrichment_contract' not in bundle


def test_fitting_initial_proposal_does_not_screen_recalled_cvps(setup, monkeypatch):
    from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
    from backend.pipeline import cvp_candidate_screening
    store, request, root, clients, _ = setup
    request['input'] = seal({k: v for k, v in request['input'].items() if k != 'artifact_sha256'} |
                            {'recall_profile': STATEMENT_SCOPE_PROFILE})
    path = Path(request['authorization']['path'])
    direction = json.loads(path.read_text())
    direction['input_sha256'] = request['input']['artifact_sha256']
    path.write_text(json.dumps(direction))
    request['authorization']['file_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(cvp_candidate_screening, 'screen_to_fit',
                        lambda *args, **kwargs: pytest.fail('fitting initial proposal was screened'))
    result = runner.run(store, request, root=root, execute=True, clients=clients)
    assert result['status'] == 'completed', parent(store)['units']['g1'].get('detail')
    assert any(json.loads(row['user_prompt']).get('schema_version', '').endswith('_proposal_packet')
               for row in clients['gpt'].requests)


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


def test_obsolete_unwritten_history_keeps_call_outcome_unknown(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store, request, root, _, _ = setup
    admission = preflight.prepare_recall(store, request['input'], allow_unqualified=True, defer_index=True)
    parent = persistence.prepare_action(store, request, root=str(root), preflight=admission)
    run_id = parent['run_id']
    step_id = persistence.prepare_step(store, run_id, group_key='g1', generation='old',
        ordinal=0, materials={'old': True})
    intent = {'cache_path': str(root / 'review.json'), 'request_bytes': 100,
              'model_id': 'claude-opus-5-5'}
    persistence.reserve_call(store, run_id, key='review.json', role='opus', intent=intent)
    persistence.begin_call(store, run_id, key='review.json', intent=intent)
    with pytest.raises(ChangeSetConflict, match='every exact unwritten attempt'):
        persistence.mark_obsolete_unwritten_history(store, run_id, call_keys=[], step_ids=[step_id])
    record = persistence.mark_obsolete_unwritten_history(store, run_id,
        call_keys=['review.json'], step_ids=[step_id])
    assert persistence.mark_obsolete_unwritten_history(store, run_id,
        call_keys=['review.json'], step_ids=[step_id]) == record
    saved = store.get_cvp_group(run_id)
    assert saved['call_reservations']['review.json']['state'] == 'started'
    assert saved['call_reservations']['review.json']['applicability'] == 'obsolete_for_execution'
    assert store.get_cvp_group(step_id)['obsolete_for_execution'] is True


def test_exact_pre_dispatch_failure_can_be_superseded_without_erasing_call(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    store,request,root,_,_=setup
    admission=preflight.prepare_recall(store,request['input'],allow_unqualified=True,defer_index=True)
    original=persistence.prepare_action(store,request,root=str(root),preflight=admission)
    intent={'cache_path':str(root/'recall.json'),'request_bytes':100,'model_id':'gpt-6.1-sol'}
    persistence.reserve_call(store,original['run_id'],key='recall.json',role='gpt',intent=intent)
    persistence.begin_call(store,original['run_id'],key='recall.json',intent=intent)
    successor=deepcopy(request);successor['action_id']='test-cvp2-after-nonlaunch'
    with pytest.raises(ChangeSetConflict,match='unwritten exact original group'):
        persistence.prepare_action(store,successor,root=str(root/'replacement'),preflight=admission,
            supersede_unwritten_run=original['run_id'])
    proof={'schema_version':'wang_cvp2_pre_dispatch_nonlaunch_v1','run_id':original['run_id'],
           'call_key':'recall.json','intent_sha256':persistence.sha256_json(intent)}
    with pytest.raises(ChangeSetConflict,match='exact started call'):
        persistence.prepare_action(store,successor,root=str(root/'replacement'),preflight=admission,
            supersede_unwritten_run=original['run_id'],nonlaunch={**proof,'intent_sha256':'0'*64})
    new=persistence.prepare_action(store,successor,root=str(root/'replacement'),preflight=admission,
        supersede_unwritten_run=original['run_id'],nonlaunch=proof)
    old=store.get_cvp_group(original['run_id'])
    assert old['result']['successor_run_id']==new['run_id']
    assert old['call_reservations']['recall.json']['state']=='failed_before_dispatch'
    assert old['call_reservations']['recall.json']['nonlaunch']==proof


def test_interrupted_dispatched_call_can_only_be_abandoned_with_exact_unwritten_proof(setup):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    from backend.pipeline.cvp_management_interrupted import verify as verify_interruption
    store, request, root, _, _ = setup
    admission = preflight.prepare_recall(store, request['input'], allow_unqualified=True, defer_index=True)
    original = persistence.prepare_action(store, request, root=str(root), preflight=admission)
    cache = root / 'proposal.json'
    identity = {'cache_path': str(cache), 'request_bytes': 100,
                'model_id': 'gpt-6.1-sol', 'backend': 'codex_subscription',
                'request_payload_sha256': 'a' * 64}
    persistence.reserve_call(store, original['run_id'], key='proposal.json', role='gpt', intent=identity)
    persistence.begin_call(store, original['run_id'], key='proposal.json', intent=identity)
    intent = seal({'schema_version': 'wang_model_call_intent_v1',
                   'model_id': identity['model_id'], 'backend': identity['backend'],
                   'request_payload_sha256': identity['request_payload_sha256'],
                   'cache_path': str(cache), 'automatic_retry': False})
    failure = seal({'schema_version': 'wang_model_call_failure_v1',
                    'invocation_sha256': intent['artifact_sha256'],
                    'error_type': 'KeyboardInterrupt', 'execution_status': 'outcome_unknown',
                    'automatic_retry': False})
    write_immutable(root / 'proposal.call-intent.json', intent)
    write_immutable(root / 'proposal.call-failure.json', failure)
    transport = root / 'proposal.transport'
    transport.mkdir(parents=True)
    (transport / 'stdout.log').write_bytes(b'')
    write_immutable(transport / 'exit.json', {'error_type': 'KeyboardInterrupt',
        'returncode': None, 'files': {'stdout.log': hashlib.sha256(b'').hexdigest()}})
    proof = verify_interruption(store, original['run_id'])
    write_immutable(cache, seal({'saved': 'answer'}))
    with pytest.raises(ValueError, match='found an answer'):
        verify_interruption(store, original['run_id'])
    cache.unlink()
    successor = deepcopy(request)
    successor['action_id'] = 'test-cvp2-after-interruption'
    with pytest.raises(ChangeSetConflict, match='unwritten exact original group'):
        persistence.prepare_action(store, successor, root=str(root / 'replacement'),
            preflight=admission, supersede_unwritten_run=original['run_id'])
    with pytest.raises(ChangeSetConflict, match='proof differs'):
        persistence.prepare_action(store, successor, root=str(root / 'replacement'),
            preflight=admission, supersede_unwritten_run=original['run_id'],
            interruption={**proof, 'intent_sha256': '0' * 64})
    new = persistence.prepare_action(store, successor, root=str(root / 'replacement'),
        preflight=admission, supersede_unwritten_run=original['run_id'], interruption=proof)
    old = store.get_cvp_group(original['run_id'])
    assert old['result']['successor_run_id'] == new['run_id']
    assert old['call_reservations']['proposal.json']['state'] == 'abandoned_after_dispatch'
    assert old['call_reservations']['proposal.json']['interruption'] == proof


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


def test_original_task_retains_calls_across_bounded_continuations(setup,tmp_path):
    from backend.api.canonical_repository import cvp_management_store as persistence
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    from backend.pipeline.cvp_management_control import run as control
    store,request,root,_,_=setup
    request['budget']['max_calls']=1
    auth=Path(request['authorization']['path'])
    direction=json.loads(auth.read_text());direction['budget']=request['budget']
    auth.unlink();write_immutable(auth,direction)
    request['authorization']['file_sha256']=hashlib.sha256(auth.read_bytes()).hexdigest()
    admission=preflight.prepare_recall(store,request['input'],allow_unqualified=True,defer_index=True)
    task=persistence.prepare_action(store,request,root=str(root),preflight=admission)
    run_id=task['run_id'];intent={'cache_path':str(root/'old.json'),'request_bytes':100}
    persistence.reserve_call(store,run_id,key='old',role='gpt',intent=intent)
    persistence.begin_call(store,run_id,key='old',intent=intent)
    persistence.returned_call(store,run_id,key='old',raw={'ok':True},artifact_sha256='a'*64)
    with pytest.raises(ChangeSetConflict,match='budget exhausted'):
        persistence.reserve_call(store,run_id,key='new',role='gpt',intent=intent)
    owner=tmp_path/'extension.json'
    write_immutable(owner,{'schema_version':domain.VERSION+'_call_budget_extension_v1',
        'authorized_by':'owner','parent_run_id':run_id,'action_id':request['action_id'],
        'original_max_calls':1,'total_max_calls':3,'group_key':'g1'})
    manifest={'schema_version':domain.VERSION+'_control','operation':'call_budget_extension',
        'parent_run_id':run_id,'group_key':'g1','total_max_calls':3,
        'authorization':{'path':str(owner),'file_sha256':hashlib.sha256(owner.read_bytes()).hexdigest()}}
    assert control(store,manifest,execute=True)['total_max_calls']==3
    assert control(store,manifest,execute=True)['original_calls']==1
    with pytest.raises(ChangeSetConflict,match='fully returned'):
        persistence.extend_call_budget(store,run_id,authorization=manifest['authorization'],total_max_calls=4)
    for key in ('new','last'):
        persistence.reserve_call(store,run_id,key=key,role='gpt',intent={**intent,'cache_path':str(root/key)})
    with pytest.raises(ChangeSetConflict,match='budget exhausted'):
        persistence.reserve_call(store,run_id,key='fourth',role='gpt',intent=intent)
    assert len(store.get_cvp_group(run_id)['call_reservations'])==3
    for key in ('new','last'):
        fresh={**intent,'cache_path':str(root/key)}
        persistence.begin_call(store,run_id,key=key,intent=fresh)
        persistence.returned_call(store,run_id,key=key,raw={'key':key},artifact_sha256='b'*64)
    next_auth=tmp_path/'continuation.json'
    write_immutable(next_auth,{'schema_version':domain.VERSION+'_call_budget_extension_v1',
        'authorized_by':'agent_under_user_task_scope','numerical_cap_selected_by':'agent',
        'prior_cap_note':'The earlier 14 and 18 call caps were selected by the agent, not specified by the user.',
        'parent_run_id':run_id,'action_id':request['action_id'],
        'original_max_calls':1,'total_max_calls':5,'group_key':'g1'})
    next_manifest={**manifest,'total_max_calls':5,'authorization':{
        'path':str(next_auth),'file_sha256':hashlib.sha256(next_auth.read_bytes()).hexdigest()}}
    assert control(store,next_manifest,execute=True)['previous_max_calls']==3
    assert control(store,next_manifest,execute=True)['total_max_calls']==5
    with pytest.raises(ChangeSetConflict,match='bounded original task allowance'):
        persistence.extend_call_budget(store,run_id,authorization=next_manifest['authorization'],total_max_calls=9)


def test_promoted_summary_requires_fresh_proposal_and_preserves_identity_guard():
    base={'claim_input':{'claims':[{'claim_id':'C1'}]},'records':[
        {'collection':'canonical_viewpoints','object_id':'V1','revision':1,'content_sha256':'a',
         'payload':{'viewpoint_id':'V1','revision':1,'core_proposition':'人子是神','summary':None,
                    'summary_basis':None}}]}
    current=deepcopy(base);row=current['records'][0]
    row.update(revision=2,content_sha256='b')
    row['payload'].update(revision=2,summary='與舊成員一致的完整摘要',summary_basis={'current':True})
    assert runner._summary_only_preimage_refresh(base,current)
    changed=deepcopy(current);changed['records'][0]['payload']['core_proposition']='另一個命題'
    assert not runner._summary_only_preimage_refresh(base,changed)
    changed=deepcopy(current);changed['claim_input']['claims'][0]['claim_id']='C2'
    assert not runner._summary_only_preimage_refresh(base,changed)


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


def test_saved_passing_review_finishes_prepared_sql_failure_without_new_model_round(setup,monkeypatch):
    store,request,root,clients,_=setup
    commit=runner.commit_package
    def failed_admission(*args,**kwargs):raise ValueError('test SQL admission defect')
    monkeypatch.setattr(runner,'commit_package',failed_admission)
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='partial'
    state=parent(store);unit=state['units']['g1'];step=store.get_cvp_group(unit['steps'][-1])
    assert step['checkpoint']=='prepared' and len(unit['steps'])==1
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    monkeypatch.setattr(runner,'commit_package',commit)
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='completed'
    assert len(clients['gpt'].requests)==len(clients['opus'].requests)==1
    assert len(parent(store)['units']['g1']['steps'])==1


@pytest.mark.parametrize('stop_at', ['candidate', 'review_plan', 'approval', 'sql_admission',
                                   'reviewed', 'applied', 'readback'])
def test_multiround_resume_keeps_exact_evidence_review_and_single_sql_write(setup,monkeypatch,stop_at):
    store,request,root,clients,source=setup
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    from backend.api.canonical_repository.knowledge_models import SourceFragmentRecord
    # One eligible evidence step has two fragments, only one of which belongs
    # on this connection. A later summary-only correction must keep that choice.
    fragment=SourceFragmentRecord.model_validate({'fragment_id':'F2','source_id':'S1',
        'paragraph_key':'S0002','verbatim_excerpt':'這是另一個完整論證。',
        'source_sha256':project_script(json.loads(source.read_text())).body_sha256,
        'anchor_state':'source_version_bound'}).model_dump(mode='json')
    evidence=deepcopy(store.get_record_state('evidence_steps','E1')['payload'])
    evidence['source_fragment_ids']=['F1','F2']
    with store.connect() as conn:
        conn.execute('''INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload,review_status,visibility)
            VALUES('source_fragments','F2',1,%s,%s::jsonb,%s,%s)''',
            (record_content_sha(fragment),canonical_json(fragment),fragment['review_status'],fragment['visibility']))
        conn.execute("UPDATE wang_knowledge.objects SET content_sha256=%s,payload=%s::jsonb WHERE collection='evidence_steps' AND object_id='E1'",
            (record_content_sha(evidence),canonical_json(evidence)))
    before_claim=store.get_record_state('claims','C1');before_source=source.read_bytes()
    proposals=[];reviews=[];stopped=[]
    def propose(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return
        if not proposals:
            value=proposal();cref=packet['input']['claims'][0]['claim_ref']
            for change in value['changes']:
                fields=json.loads(change['fields_json'])
                if fields.get('claim_id')=='c1':
                    fields['claim_id']=cref
                    fields['source_fragment_refs']=[next(f['fragment_ref'] for f in packet['material']['fragments']
                        if f['content']['verbatim_excerpt']=='人子是神。')]
                change['fields_json']=json.dumps(fields,ensure_ascii=False)
        else:
            value={'summaries':[{'ref':'nv1','summary':f'第{len(proposals)}次補充：此處的人子稱號指向神性。'}]}
        proposals.append(True)
        return envelope('candidate',candidate=value)
    def review(packet):
        if not packet.get('schema_version','').endswith('_review_packet'):return
        candidate=packet['candidate'];value=passing(candidate);reviews.append(True)
        link=next(w['payload'] for w in candidate['records'] if w['ref']=='nl1')
        assert len(link['evidence_bindings'])==1
        assert link['evidence_bindings'][0]['source_fragment_id']==next(
            f['fragment_ref'] for f in packet['material']['fragments'] if f['content']['verbatim_excerpt']=='人子是神。')
        value['prior_findings']=[{'ref':ref,'disposition':'resolved','reason':'已完整核對本輪候選。'}
            for ref in candidate['review_sets']['prior_findings']]
        if len(reviews)<3:
            ref='nv1';next(r for r in value['object_reviews'] if r['ref']==ref).update(decision='revise',finding_refs=['f1'])
            value['findings']=[{'slot':'f1','target_refs':[ref],'category':'wording','reason':'摘要需補充限定。'}]
        return envelope('review',review=value)
    clients['gpt'].behavior=propose;clients['opus'].behavior=review
    write=runner.write_immutable;commit=runner.commit_package;readback=runner._readback
    def stop():
        if not stopped:stopped.append(True);raise RuntimeError('test interrupted '+stop_at)
    def write_then_stop(path,value,**kwargs):
        write(path,value,**kwargs)
        names={'candidate':'candidate.json','review_plan':'review-plan.json','approval':'approval.json'}
        if Path(path).parent.name=='step-0002' and Path(path).name==names.get(stop_at):stop()
    def commit_then_stop(*args,**kwargs):
        if len(reviews)==3 and stop_at=='sql_admission':stop()
        result=commit(*args,**kwargs)
        if len(reviews)==3 and stop_at=='applied':stop()
        return result
    apply=store.apply_plan
    def apply_then_stop(*args,**kwargs):
        if len(reviews)==3 and stop_at=='reviewed':stop()
        return apply(*args,**kwargs)
    def read_then_stop(*args,**kwargs):
        result=readback(*args,**kwargs)
        if len(reviews)==3 and stop_at=='readback':stop()
        return result
    monkeypatch.setattr(runner,'write_immutable',write_then_stop)
    monkeypatch.setattr(runner,'commit_package',commit_then_stop)
    monkeypatch.setattr(store,'apply_plan',apply_then_stop)
    monkeypatch.setattr(runner,'_readback',read_then_stop)
    assert runner.run(store,request,root=root,execute=True,clients=clients)['status']=='partial'
    assert stopped, parent(store)['units']['g1'].get('detail')
    original=parent(store);unit=original['units']['g1'];step=store.get_cvp_group(unit['steps'][-1])
    assert len(unit['steps'])==3 and step['revision'] and len(original['revision_rounds'])==2
    step_root=Path(step['materials']['step_root'])
    immutable={p:p.read_bytes() for p in step_root.iterdir() if p.is_file()}
    monkeypatch.setattr(runner,'write_immutable',write)
    monkeypatch.setattr(runner,'commit_package',commit)
    monkeypatch.setattr(store,'apply_plan',apply)
    monkeypatch.setattr(runner,'_readback',readback)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    assert result['status']=='completed',parent(store)['units']['g1'].get('detail')
    assert len(proposals)==len(reviews)==3
    current=parent(store)
    assert current['units']['g1']['steps']==unit['steps'] and len(current['call_reservations'])==6
    assert current['revision_rounds']==original['revision_rounds']
    assert all(p.read_bytes()==data for p,data in immutable.items())
    completed=store.get_cvp_group(unit['steps'][-1])
    assert completed['checkpoint']=='audited' and len(completed['change_set_ids'])==1
    assert completed['completion']['candidate_sha256']==json.loads((step_root/'candidate.json').read_text())['artifact_sha256']
    links=store.read_cvp_links(claim_ids=['C1']);assert len(links)==1
    link=next(iter(links.values()))
    assert link['revision']==1 and link['payload']['evidence_bindings']==[
        {'evidence_step_id':'E1','source_fragment_id':'F1'}]
    assert store.get_record_state('claims','C1')==before_claim and source.read_bytes()==before_source
    assert runner.run(store,request,root=root,execute=True,clients=clients)==result
    assert len(proposals)==len(reviews)==3


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
    def proposals():
        return [row for row in clients['gpt'].requests if
                json.loads(row['user_prompt']).get('schema_version','').endswith('_proposal_packet')]
    before_proposals=len(proposals())
    result=runner.run(store,second,root=root.parent/'enrich',execute=True,clients=clients)
    assert result['status']=='completed',store.get_cvp_group('CVPM-'+runner.sha256_json(second['action_id']))['units']['g1'].get('detail')
    assert len(proposals())==before_proposals+1
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


@pytest.mark.parametrize('external_change',[False,True])
def test_parent_closes_by_committed_object_versions_when_held_first_group_finishes_last(setup,monkeypatch,external_change):
    from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
    from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
    store,request,root,clients,_=setup;request=add_second_claim(store,request)
    inp={k:v for k,v in request['input'].items() if k!='artifact_sha256'}
    inp['recall_profile']=STATEMENT_SCOPE_PROFILE
    inp['grouping']['groups']=[{'group_key':key,'claim_ids':[cid],'rationale':'完整獨立判斷'}
        for key,cid in [('g1','C1'),('g2','C2')]]
    request['input']=seal(inp)
    auth=Path(request['authorization']['path']);direction=json.loads(auth.read_text())
    direction['input_sha256']=request['input']['artifact_sha256'];auth.write_text(json.dumps(direction))
    request['authorization']['file_sha256']=hashlib.sha256(auth.read_bytes()).hexdigest()
    qualification=store.claim_management_qualification
    monkeypatch.setattr(store,'claim_management_qualification',lambda cid:
        {'ready':False,'reason':'upstream verification in progress'} if cid=='C1' else qualification(cid))
    first=runner.run(store,request,root=root,execute=True,clients=clients)
    assert first['status']=='partial' and first['units']=={'g1':'needs_upstream','g2':'completed'}
    original=parent(store);second_step=original['units']['g2']['completed_step']
    view=store.read_cvp_heads()[0];vid=view['viewpoint_id']
    old_version=store.get_record_state('canonical_viewpoints',vid)['revision']
    second_record=store.get_cvp_group(second_step)
    monkeypatch.setattr(store,'claim_management_qualification',qualification)
    def connect(packet):
        if not packet.get('schema_version','').endswith('_proposal_packet'):return
        vref=packet['material']['viewpoints'][0]['cvp_ref'];cref=packet['input']['claims'][0]['claim_ref']
        return envelope('candidate',candidate={
            'component_dispositions':[{'component_ref':'k1','disposition':'connected','target_ref':vref,
                'link_ref':'nl1','reason':'完整語境內的相同判斷。'}],
            'changes':[{'ref':'nl1','fields_json':json.dumps({'claim_id':cref,'viewpoint_id':vref,
                'link_type':'equivalent_full'})}]})
    clients['gpt'].behavior=connect
    progress=runner.persistence.progress
    if external_change:
        def tamper_after_readback(*args,**kwargs):
            if kwargs.get('status')=='completed' and kwargs.get('group_key')=='g1':
                row=store.get_record_state('canonical_viewpoints',vid);payload=deepcopy(row['payload'])
                payload.update(summary='未經本任務批准的外部修改。',revision=row['revision']+1)
                with store.connect() as conn:
                    conn.execute("UPDATE wang_knowledge.objects SET revision=%s,content_sha256=%s,payload=%s::jsonb WHERE collection='canonical_viewpoints' AND object_id=%s",
                        (payload['revision'],record_content_sha(payload),canonical_json(payload),vid))
            return progress(*args,**kwargs)
        monkeypatch.setattr(runner.persistence,'progress',tamper_after_readback)
    result=runner.run(store,request,root=root,execute=True,clients=clients)
    action=parent(store)
    assert store.get_cvp_group(second_step)==second_record  # never reopen/rewrite completed g2
    assert action['units']['g2']['completed_step']==second_step
    assert len(store.read_cvp_links(claim_ids=['C1','C2']))==2
    if external_change:
        assert result['status']=='partial' and result['units']['g1']=='engineering_required'
        assert 'unapproved current business dependency change: canonical_viewpoints/'+vid in action['units']['g1']['detail']
    else:
        assert result['status']=='completed',{k:u.get('detail') for k,u in action['units'].items()}
        current=store.get_record_state('canonical_viewpoints',vid)
        assert current['revision']>old_version and len(current['payload']['summary_basis']['member_link_ids'])==2
        snapshot={r:len(c.requests) for r,c in clients.items()}
        assert runner.run(store,request,root=root,execute=True,clients=clients)==result
        assert snapshot=={r:len(c.requests) for r,c in clients.items()}


@pytest.mark.parametrize('interrupt',[False,True])
@pytest.mark.parametrize('statement_scope',[False,True])
def test_capacity_children_complete_independently_reuse_recall_and_current_approved_summary(setup,monkeypatch,interrupt,statement_scope):
    store,request,root,clients,_=setup;request=add_second_claim(store,request)
    screened=[]
    if statement_scope:
        from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE
        from backend.pipeline import cvp_candidate_screening
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
        def fitting_screen(*args,measure,**kwargs):
            screened.append(True)
            measure(args[3],{})
        monkeypatch.setattr(cvp_candidate_screening,'screen_expansion_to_fit',fitting_screen)
    original_adapter=runner.adapter
    def measured(role,req,bundle,packet,**kwargs):
        result=original_adapter(role,req,bundle,packet,**kwargs)
        # Simulate a provider byte ceiling at whole-Claim boundaries. The
        # real scoped packet/schema is still used by the common cache runtime.
        if role=='proposal':
            result.request_bytes=lambda payload:600000 if len(payload['input']['claims'])>1 else 100000
        if role=='review' and statement_scope and bundle['member_support_ids'] and not screened:
            result.request_bytes=lambda payload:request['budget']['max_request_bytes']+1
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
    # The second unit recalls the first CVP. Its initial create proposal loads
    # that CVP's member source, then goes directly to Opus on the rebound
    # candidate; loading comparison detail does not trigger another GPT call.
    assert len(clients['gpt'].requests)==3 and len(clients['opus'].requests)==2
    assert len(screened)==(1 if statement_scope else 0)
    second=next(g for g in action['work_groups'] if g['claim_ids']==['C2'])
    steps=action['units'][second['group_key']]['steps']
    assert len(steps)==2
    assert store.get_cvp_group(steps[-1])['materials']['proposal_origin']['step_id']==steps[0]
    assert not (Path(store.get_cvp_group(steps[-1])['materials']['step_root'])/'proposal.json').exists()
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
