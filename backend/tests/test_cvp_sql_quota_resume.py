from copy import deepcopy
from pathlib import Path
import pytest
from backend.pipeline import cvp_sql_quota_resume as q
from backend.pipeline import viewpoint_audit_source_adjudication as a
from backend.pipeline.viewpoint_resolution_runtime import write_immutable,subscription_client,model_request_identity
from backend.pipeline.viewpoint_audit_owner_resolution import binding
from backend.api.canonical_repository.postgres_store import sha256_json,ChangeSetConflict
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_cvp_sql_transaction_postgres import task,apply
from backend.pipeline.viewpoint_adjudication_history import target_identity


def seal(x):return {**x,'artifact_sha256':sha256_json(x)}


def failed(tmp_path, fault=None):
    r=tmp_path/'original';packet=seal({'items':[{'item_id':'4:v:c'},{'item_id':'4:v:h'}]})
    adapter=a.source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),r/'raw-gpt.json')
    identity=model_request_identity(adapter,packet)
    intent=seal({'schema_version':'wang_model_call_intent_v1',**identity,
        'cache_path':str((r/'raw-gpt.json').resolve()),'automatic_retry':False})
    failure=seal({'schema_version':'wang_model_call_failure_v1','invocation_sha256':intent['artifact_sha256'],
                 'error_type':'CodexSubscriptionError','execution_status':'outcome_unknown','automatic_retry':False})
    raw={'stdout':'','last_message':None,'stderr':
        'startup websocket prewarm setup failed: You’ve hit your usage limit.\nERROR: You’ve hit your usage limit.'}
    if fault=='stdout':raw['stdout']='answer'
    if fault=='answer':raw['last_message']='{}'
    if fault=='timeout':failure['error_type']='TimeoutExpired';failure=seal({k:v for k,v in failure.items() if k!='artifact_sha256'})
    if fault=='network':raw['stderr']='ERROR: connection reset'
    if fault=='no_startup':raw['stderr']='ERROR: You’ve hit your usage limit.'
    if fault=='foreign_model':intent['model_id']='foreign';intent=seal({k:v for k,v in intent.items() if k!='artifact_sha256'})
    if fault=='intent_drift':intent['request_payload_sha256']='foreign'
    encoding=seal({'schema_version':'wang_model_request_encoding_proof_v1',**adapter.request_encoding_proof(packet)})
    if fault=='encoding':encoding['wire_sha256']='foreign'
    budget={'request_identity':identity,'actual_request_bytes':adapter.request_bytes(packet),'max_request_bytes':600000}
    if fault=='budget':budget['actual_request_bytes']+=1
    values={'packet.json':packet,'raw-gpt.call-intent.json':intent,'raw-gpt.call-failure.json':failure,
            'raw-gpt.request-encoding.json':encoding,'raw-gpt.request-budget.json':budget,
            'failed-transport-gpt.json':{'raw_response':raw,'error':'Codex subscription generation failed (exit 1): quota'},
            'failure.json':{'error':'quota'}}
    for n,v in values.items():write_immutable(r/n,v)
    if fault=='saved':write_immutable(r/'raw-gpt.json',{'answer':'saved'})
    return r,packet


def test_exact_quota_receipt_retains_unknown_and_all_files(tmp_path):
    r,p=failed(tmp_path);before={f.name:f.read_bytes() for f in r.iterdir()}
    assert q.quota_failure(r)==p
    assert {f.name:f.read_bytes() for f in r.iterdir()}==before
    assert q.read_artifact(r/'raw-gpt.call-failure.json')['execution_status']=='outcome_unknown'


@pytest.mark.parametrize('fault',['stdout','answer','timeout','network','no_startup','foreign_model','intent_drift','encoding','budget','saved'])
def test_not_a_general_unknown_retry(tmp_path,fault):
    r,_=failed(tmp_path,fault)
    with pytest.raises(ValueError):q.quota_failure(r)


@pytest.mark.parametrize('fault',['none','all','foreign','changed','missing_context'])
def test_human_partition_never_changes_remaining_source_or_content(fault):
    old=seal({'items':[{'item_id':'4:v:c','source':'own'},{'item_id':'4:v:h'}],'context':'complete'})
    new=a.restricted_packet(old,{'4:v:c'});human={'4:v:h'}
    if fault=='none':human=set()
    if fault=='all':human={'4:v:c','4:v:h'}
    if fault=='foreign':human={'foreign'}
    if fault=='changed':new['items'][0]['source']='other'
    if fault=='missing_context':new.pop('context')
    with pytest.raises(ValueError):q.verify_delta(old,new,human)


def test_exact_human_partition_preserves_complete_other_material():
    old=seal({'items':[{'item_id':'4:v:c','source':'own'},{'item_id':'4:v:h'}],'context':'complete'})
    new=a.restricted_packet(old,{'4:v:c'});q.verify_delta(old,new,{'4:v:h'})
    assert new['context']=='complete' and old['items'][1]['item_id']=='4:v:h'


def test_sql_manual_once_preserves_first_reservation_and_refuses_other_budget(postgres_store,tmp_path,monkeypatch):
    plan,ctx=task(postgres_store);apply(postgres_store,plan,ctx)
    old=tmp_path/'original';out=old/'manual-quota-resume-once'
    packet=seal({'items':[{'item_id':'4:v:c'},{'item_id':'4:v:h'}]})
    new=a.restricted_packet(packet,{'4:v:c'})
    write_immutable(old/'packet.json',packet);write_immutable(out/'packet.json',new)
    op=binding(old/'packet.json');np=binding(out/'packet.json');auth={'path':'owner','file_sha256':'a'*64}
    direction={'run_id':ctx.run_id,'campaign_sha256':postgres_store.get_cvp_group(ctx.run_id)['campaign_sha256'],
               'original_root':str(old),'original_bindings':[op]}
    monkeypatch.setattr(q,'verify_proof',lambda _: (direction,{'authorization':auth}))
    targets=[target_identity(i) for i in packet['items']]
    postgres_store.register_adjudication(ctx.run_id,targets=targets,root=old,packet_binding=op)
    args={'targets':targets[:1],'original_root':old,'original_packet':op,'root':out,
          'packet_binding':np,'authorization':auth,'quota_resume':True}
    for _ in range(2):postgres_store.register_adjudication_input_repair(ctx.run_id,**args)
    value=postgres_store.get_cvp_group(ctx.run_id)['semantic_adjudications']
    assert value[targets[0]]['root']==str(old) and value[targets[0]]['packet']==op
    assert value[targets[0]]['manual_quota_resume_once']['root']==str(out)
    assert 'additional_roots' not in value[targets[1]]
    for delta in ({'targets':[]},{'authorization':{'path':'foreign'}},{'original_root':tmp_path/'foreign'}):
        with pytest.raises(ChangeSetConflict):
            postgres_store.register_adjudication_input_repair(ctx.run_id,**{**args,**delta})
    with postgres_store.connect() as c:
        c.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=jsonb_set(metadata,%s,%s::jsonb) WHERE run_id=%s",
            (['semantic_adjudications',targets[0],'input_repair_once'],'{"foreign":true}',ctx.run_id))
    with pytest.raises(ChangeSetConflict):postgres_store.register_adjudication_input_repair(ctx.run_id,**args)
    assert len(postgres_store.get_change_history(plan.change_set_id)['operations'])==1


def test_quota_effective_roots_removes_only_bound_failed_parent(tmp_path,monkeypatch):
    from backend.pipeline.cvp_sql_adjudication_input_repair import effective_roots
    old=tmp_path/'original';out=old/'manual-quota-resume-once';other=tmp_path/'other'
    write_immutable(out/q.PROOF,{'fixture':True})
    monkeypatch.setattr(q,'verify_proof',lambda _:({'original_root':str(old)},{}))
    assert effective_roots([old,out,other])==[out,other]


def test_resume_child_unknown_is_not_retried(tmp_path):
    from backend.pipeline.viewpoint_resolution_runtime import call_model
    r,p=failed(tmp_path);out=r/'manual-quota-resume-once'
    write_immutable(out/'raw-gpt.call-intent.json',{'unknown':True})
    adapter=a.source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),out/'raw-gpt.json')
    with pytest.raises(ValueError,match='outcome unknown'):
        call_model(adapter,a.restricted_packet(p,{'4:v:c'}),out/'raw-gpt.json')


@pytest.mark.parametrize('fault',[None,'owner','quote','budget','automatic','content','root','receipt','subset','seal','human_identity'])
def test_bound_proof_direction_and_fixed_once_child(fault,tmp_path,monkeypatch):
    from backend.pipeline import cvp_exact_human_audit_retention as h
    oldroot=tmp_path/'original';out=oldroot/'manual-quota-resume-once'
    audit=tmp_path/'audit.json';write_immutable(audit,{'original':'audit'})
    old=seal({'items':[{'item_id':'4:v:c'},{'item_id':'4:v:h'}],'audit_binding':binding(audit)})
    for n in q.FILES:write_immutable(oldroot/n,old if n=='packet.json' else {'original':n})
    new=a.restricted_packet(old,{'4:v:c'});human=tmp_path/'human.json'
    write_immutable(human,{'items':[{'item_id':'4:v:h'}],'campaign_sha256':'campaign','run_id':'run'})
    auth=tmp_path/'owner.json'
    d={'schema_version':q.SCHEMA,'authorized_by':'owner','authorization_quote':'restored quota, continue',
       'maximum_additional_calls':1,'automatic_future_retries_authorized':False,'human_content_approval':False,
       'original_root':str(oldroot),'original_bindings':[binding(oldroot/n) for n in q.FILES],
       'audit_binding':binding(audit),'human_direction':binding(human),'human_item_ids':['4:v:h'],
       'corrected_packet_sha256':new['artifact_sha256'],'campaign_sha256':'campaign','run_id':'run'}
    if fault=='owner':d['authorized_by']='model'
    if fault=='quote':d['authorization_quote']=''
    if fault=='budget':d['maximum_additional_calls']=2
    if fault=='automatic':d['automatic_future_retries_authorized']=True
    if fault=='content':d['human_content_approval']=True
    if fault=='root':d['original_root']=str(tmp_path/'foreign')
    if fault=='receipt':d['original_bindings'].pop()
    if fault=='subset':new=old
    if fault=='human_identity':d['human_item_ids']=['4:v:c']
    d=seal(d)
    if fault=='seal':d['authorization_quote']='different'
    write_immutable(auth,d);write_immutable(out/'packet.json',new)
    write_immutable(out/q.PROOF,seal({'authorization':binding(auth)}))
    monkeypatch.setattr(q,'quota_failure',lambda _:old)
    monkeypatch.setattr(h,'validate_direction',lambda _:None)
    monkeypatch.setattr(h,'verify_item',lambda *args:None)
    if fault:
        with pytest.raises((ValueError,FileNotFoundError)):q.verify_proof(out/q.PROOF)
    else:assert q.verify_proof(out/q.PROOF)[0]==d
