"""Neutral omitted-contract repair; synthetic transports are NOT provider approval."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import cvp_sql_adjudication_input_repair as repair
from backend.pipeline import viewpoint_audit_source_adjudication as adjudication
from backend.pipeline.viewpoint_audit_owner_resolution import binding
from backend.pipeline.viewpoint_adjudication_history import validated_history,target_identity
from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable,subscription_client
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.pipeline.viewpoint_semantic_contract import relation_rules,RELATION_ROLES
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_cvp_sql_transaction_postgres import task,apply


def seal(v):return {**v,'artifact_sha256':sha256_json(v)}


def fixture(tmp_path,monkeypatch,*,campaign='campaign',run_id='run'):
    root=(tmp_path/'original').resolve()
    source=tmp_path/'source';source.write_text('教授原話。')
    audit=tmp_path/'audit.json'
    audit_finding={'viewpoint_id':'v','revision_id':'r','verdict':'disputed','reason':'original role objection',
        'claim_ids_in_question':['c']}
    write_immutable(audit,{'meta':{'semantic_contract_version':'wang_cvp_component_roles_v1','model':'gemini-3.8-flash'},
        'layers':{'3':{'complete':True,'judged':1,'disputed':0,'results':[{'claim_id':'c','verdict':'supported'}]},
                  '4':{'complete':True,'judged':1,'disputed':1,'results':[audit_finding]}}})
    item={'item_id':'4:v:c','audit_finding':audit_finding,
        'viewpoint':{'viewpoint_id':'v','current_revision_id':'r'},
        'viewpoint_revision':{'viewpoint_id':'v','viewpoint_revision_id':'r','review_status':'system_approved'},
        'claim':{'claim_id':'c','revision':1},'connections':[{'viewpoint_claim_link_id':'l',
            'link_type':'qualifies','effective_state':'active','review_status':'system_approved',
            'pinned_claim_revision':1,'validated_against_viewpoint_revision_id':'r'}],
        'sources':[{'source_id':'s',**binding(source),'paragraphs':[{'paragraph_key':'S0001','text':'教授原話。'}]}]}
    packet=seal({'schema_version':'wang_audit_source_adjudication_packet_v2',
        'audit_binding':binding(audit),'items':[item]})
    adapter=StructuredJsonReviewerAdapter(client=subscription_client('codex','gpt-6.1-sol','high'),
        prompt=adjudication.contextual_adjudication_prompt(),response_model=adjudication.Findings,
        schema_name='wang_audit_source_findings_v1',compact_payload=True,
        shared_text_payload=True,shared_record_payload=True)
    finding={'item_id':'4:v:c','verdict':'audit_objection_upheld','source_id':'s','paragraph_key':'S0001',
        'exact_quote':'教授原話。','reason':'original type objection remains unchanged'}
    response={'findings':[finding]}
    ids={'model_id':adapter.model_id,'backend':adapter.backend,'prompt_sha256':adapter.prompt_sha256,
        'generation_config_sha256':adapter.generation_config_sha256,'request_payload_sha256':sha256_json(packet)}
    raw=seal({**ids,'response':response,'response_sha256':sha256_json(response)})
    handoff=seal({'packet_sha256':packet['artifact_sha256'],'maximum_model_calls':1,'master_data_mutations':0,
        'actions':[{'item_id':'4:v:c','verdict':'audit_objection_upheld','gpt':finding}]})
    for n,v in [('packet.json',packet),('raw-gpt.json',raw),('gpt-final-handoff.json',handoff),
        ('raw-gpt.call-intent.json',seal({'schema_version':'wang_model_call_intent_v1',**ids,
            'cache_path':str(root/'raw-gpt.json'),'automatic_retry':False})),
        ('raw-gpt.request-budget.json',{'fixture':'synthetic'}),('raw-gpt.request-encoding.json',{'fixture':'synthetic'})]:
        write_immutable(root/n,v)
    monkeypatch.setattr(adjudication,'build_packet',lambda *args:deepcopy(packet))
    direction=repair.make_relation_direction(object(),campaign,run_id,root,'那还不继续；standing AGENTS engineering')
    auth=tmp_path/'direction.json';write_immutable(auth,direction)
    return root,auth,packet,finding,direction


def proof(root,auth,packet,direction):
    corrected=root/'input-repair-once';write_immutable(corrected/'packet.json',packet)
    write_immutable(corrected/'owner-input-repair-proof.json',seal({'schema_version':repair.PROOF,
        'authorization':binding(auth),'bindings':[binding(auth),*direction['original_bindings'],
            direction['audit_binding'],direction['relation_contract'],*direction['original_request_bindings']]}))
    return corrected


def test_new_prompt_complete_roles_historic_config_unchanged(tmp_path,monkeypatch):
    root,auth,packet,f,d=fixture(tmp_path,monkeypatch)
    client=subscription_client('codex','gpt-6.1-sol','high')
    old=adjudication.source_review_adapter(client,root/'raw-gpt.json')
    new=adjudication.source_review_adapter(client,root/'unused.json')
    assert relation_rules() not in old._base_prompt and relation_rules() in new._base_prompt
    assert old.prompt_sha256==read_artifact(root/'raw-gpt.json')['prompt_sha256']
    assert new.prompt_sha256!=old.prompt_sha256
    assert validated_history(root)[1]['4:v:c']['verdict']=='audit_objection_upheld'
    intent=root/'unused.call-intent.json'
    write_immutable(intent,{'prompt_sha256':old.prompt_sha256,'generation_config_sha256':old.generation_config_sha256})
    assert adjudication.source_review_adapter(client,root/'unused.json')._base_prompt==old._base_prompt


@pytest.mark.parametrize('fault',['claim','link','source','finding','population','cvr','approved','role',
    'contract','prompt','ids','authority','already_disclosed','intent'])
def test_exact_delta_and_authority_denials(fault,tmp_path,monkeypatch):
    root,auth,old,f,d=fixture(tmp_path,monkeypatch);new=deepcopy(old);findings={'4:v:c':f}
    if fault=='claim':new['items'][0]['claim']['revision']=2
    if fault=='link':new['items'][0]['connections'][0]['link_type']='supports'
    if fault=='source':new['items'][0]['sources'][0]['file_sha256']='changed'
    if fault=='finding':new['items'][0]['audit_finding']['reason']='changed'
    if fault=='population':new['items'].append(deepcopy(new['items'][0]))
    if fault=='cvr':old['items'][0]['viewpoint_revision']['viewpoint_id']='foreign';new=deepcopy(old)
    if fault=='approved':old['items'][0]['connections'][0]['review_status']='human_review_required';new=deepcopy(old)
    if fault=='role':old['items'][0]['connections'][0]['link_type']='supports';new=deepcopy(old)
    if fault=='contract':d['relation_contract']['file_sha256']='changed'
    if fault=='prompt':d['original_prompt_sha256']='changed'
    if fault=='ids':d['affected_link_ids']=['foreign']
    if fault=='authority':d['standing_engineering_authority']=False
    if fault=='already_disclosed':
        a=read_artifact(Path(old['audit_binding']['path']));a['meta']['semantic_contract_version']=RELATION_ROLES
        Path(old['audit_binding']['path']).write_text(__import__('json').dumps(a))
    if fault=='intent':
        i=read_artifact(root/'raw-gpt.call-intent.json');i.pop('artifact_sha256');i['prompt_sha256']='foreign'
        (root/'raw-gpt.call-intent.json').write_text(__import__('json').dumps(seal(i)))
    with pytest.raises(ValueError):repair.verify_relation_delta(old,new,findings,d)


def test_sql_once_original_upheld_not_rewritten(postgres_store,tmp_path,monkeypatch):
    plan,context=task(postgres_store);apply(postgres_store,plan,context)
    row=postgres_store.get_cvp_group(context.run_id)
    root,auth,packet,f,d=fixture(tmp_path,monkeypatch,campaign=row['campaign_sha256'],run_id=context.run_id)
    corrected=proof(root,auth,packet,d);targets=[target_identity(i) for i in packet['items']]
    postgres_store.register_adjudication(context.run_id,targets=targets,root=root,packet_binding=binding(root/'packet.json'))
    args=dict(targets=targets,original_root=root,original_packet=binding(root/'packet.json'),root=corrected,
        packet_binding=binding(corrected/'packet.json'),authorization=binding(auth))
    before=(root/'raw-gpt.json').read_bytes()
    for _ in range(2):postgres_store.register_adjudication_input_repair(context.run_id,**args)
    saved=postgres_store.get_cvp_group(context.run_id)['semantic_adjudications'][targets[0]]
    assert saved['root']==str(root) and saved['additional_roots']==[str(corrected)]
    with pytest.raises((ValueError,FileNotFoundError)):
        postgres_store.register_adjudication_input_repair(context.run_id,**{**args,'root':root/'second'})
    assert (root/'raw-gpt.json').read_bytes()==before
    assert len(postgres_store.get_change_history(plan.change_set_id)['operations'])==1


@pytest.mark.parametrize('verdict',['audit_objection_rejected','audit_objection_upheld','unresolved'])
def test_original_parent_once_preserves_real_result_not_forced_pass(verdict,tmp_path,monkeypatch):
    root,auth,packet,f,d=fixture(tmp_path,monkeypatch)
    before={p:p.read_bytes() for p in root.iterdir()}
    client=subscription_client('codex','gpt-6.1-sol','high');calls=[]
    def synthetic_transport(**kwargs):
        calls.append(kwargs)
        return {'findings':[{**f,'verdict':verdict}]}
    monkeypatch.setattr(client,'generate_json',synthetic_transport)
    adapter=adjudication.source_review_adapter(client,root/'input-repair-once/raw-gpt.json')
    original_run=adjudication.run
    monkeypatch.setattr(adjudication,'run',lambda *a,**k:original_run(*a,adapters=[adapter],**k))
    monkeypatch.setattr('backend.pipeline.viewpoint_s201129_source_continuation.canonical_runtime',
        lambda:{'identity':{'cli':{'path':'synthetic-no-call'}}})
    store=SimpleNamespace(get_cvp_group=lambda r:{'campaign_sha256':'campaign','checkpoint':'applied'},
        register_adjudication_input_repair=lambda *a,**k:None)
    for _ in range(2):repair.execute(store,'run',auth)
    assert len(calls)==1
    assert read_artifact(root/'input-repair-once/gpt-final-handoff.json')['actions'][0]['verdict']==verdict
    assert all(p.read_bytes()==v for p,v in before.items())
    assert validated_history(root/'input-repair-once')[1]['4:v:c']['verdict']==verdict
    from backend.pipeline.viewpoint_reused_source_resolution import compose,verify_composed
    output=tmp_path/'resolution/result.json'
    if verdict=='audit_objection_rejected':
        result=compose(Path(packet['audit_binding']['path']),[root/'input-repair-once'],output,store)
        assert verify_composed(read_artifact(Path(packet['audit_binding']['path'])),result)=={'3':set(),'4':{'v'}}
    else:
        with pytest.raises(ValueError):compose(Path(packet['audit_binding']['path']),[root/'input-repair-once'],output,store)
        assert not output.exists()


def test_old_answer_cannot_be_copied_into_corrected_generation(tmp_path,monkeypatch):
    root,auth,packet,f,d=fixture(tmp_path,monkeypatch);corrected=proof(root,auth,packet,d)
    write_immutable(corrected/'raw-gpt.json',read_artifact(root/'raw-gpt.json'))
    with pytest.raises(ValueError,match='complete role contract'):
        repair.verify_proof(corrected/'owner-input-repair-proof.json')


def test_new_independent_profile_keeps_old_prompt_and_complete_component_rules():
    from backend.tests.test_viewpoint_component_roles import audit as reader
    old=reader._viewpoint_prompt('v',{},['full body'],semantic_contract_version='wang_cvp_component_roles_v1')
    new=reader._viewpoint_prompt('v',{},['full body'],semantic_contract_version=RELATION_ROLES)
    assert relation_rules() not in old and relation_rules() in new
    assert '同一 Claim 對同一 CVP' in new and 'full body' in new
    assert new.replace('\n\n'+relation_rules(),'')==old


def test_unknown_recovery_intent_never_reissued(tmp_path,monkeypatch):
    root,auth,packet,f,d=fixture(tmp_path,monkeypatch);corrected=proof(root,auth,packet,d)
    write_immutable(corrected/'raw-gpt.call-intent.json',{'previous':'unknown'})
    from backend.tests.test_viewpoint_deferred_source_check import Adapter
    adapter=Adapter({'findings':[f]});original_run=adjudication.run
    monkeypatch.setattr(adjudication,'run',lambda *a,**k:original_run(*a,adapters=[adapter],**k))
    monkeypatch.setattr('backend.pipeline.viewpoint_s201129_source_continuation.canonical_runtime',
        lambda:{'identity':{'cli':{'path':'synthetic-no-call'}}})
    store=SimpleNamespace(get_cvp_group=lambda r:{'campaign_sha256':'campaign','checkpoint':'applied'},
        register_adjudication_input_repair=lambda *a,**k:None)
    with pytest.raises(ValueError):repair.execute(store,'run',auth)
    assert adapter.calls==0 and not (corrected/'raw-gpt.json').exists()


def test_actual_saved_complete_packet_current_history_and_full_audit_readonly():
    if os.environ.get('WKP357_RELATION_CONTRACT_SAVED')!='1':pytest.skip('explicit actual saved readonly opt-in')
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_content_blocked_fallback import verify_saved_full_audit
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    load_dotenv('.env');store=PostgresKnowledgeStore()
    base=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    run='CVPG-ca0dc079810fe394ff223ec983d71dcf5da4b5fc40913dec21fdfb7cb278f83c'
    root=base/'execution'/run/'audit-03f2fb1e49c2420556de/own-source-adjudication-e062c05a947b2eede098'
    old,f=validated_history(root)
    files=[*root.glob('*.json'),*[Path(s['path']) for i in old['items'] for s in i['sources']]]
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    row=store.get_cvp_group(run)
    d=repair.make_relation_direction(store,row['campaign_sha256'],run,root,'那还不继续；standing engineering')
    assert d['affected_link_ids']==['VCL-14d590d3b17b52ac11bc']
    current=build_packet(Path(old['audit_binding']['path']),store,True);assert current==old
    repair.verify_relation_delta(old,current,f,d)
    bad=deepcopy(current);bad['items'][0]['connections'][0]['component_locator']=None
    with pytest.raises(ValueError):repair.verify_relation_delta(old,bad,f,d)
    history=store.get_change_history('KCS-c9a55d8d4a943b4b2987')
    assert history['status']=='applied' and len(history['operations'])==63
    for op in history['operations']:
        state=store.get_record_state(op['collection'],op['object_id'])
        assert not state['retired'] and state['payload']==op['after']
        assert state['revision']==op['after_revision'] and state['content_sha256']==op['after_sha256']
    written={(op['collection'],op['object_id']) for op in history['operations']}
    evidence=row['reviewed']['evidence']
    from backend.pipeline.cvp_sql_flow import decoded_pins,check_binding
    for key,pin in decoded_pins(evidence['read_pins']).items():
        if key in written:continue
        state=store.get_record_state(*key)
        assert (None if state is None else (state['revision'],state['content_sha256'],state['retired']))==pin
    for p in evidence['sources']:check_binding(p)
    for p in evidence['approval'].values():
        if isinstance(p,dict) and {'path','file_sha256'}<=p.keys():check_binding(p)
    decision=store.get_record('viewpoint_identity_decisions',old['items'][0]['connections'][0]['decision_id'])
    assert decision['approval_basis']=='dual_model_consensus'
    assert {'gpt-6.1-sol','claude-opus-5-5'}==set(decision['reviewer_model_ids'])
    assert {'claim_id':old['items'][0]['claim']['claim_id'],'link_type':'qualifies'} in decision['claim_link_decisions']
    proofs=verify_saved_full_audit(Path(old['audit_binding']['path']))
    assert len(proofs)==34
    assert store.get_cvp_group(run)==row
    assert all(hashlib.sha256(p.read_bytes()).hexdigest()==v for p,v in before.items())
