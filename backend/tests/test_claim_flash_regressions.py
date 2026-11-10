from copy import deepcopy
from pathlib import Path
import json
from io import BytesIO
from urllib.error import HTTPError
import pytest
from backend.pipeline import claim_flash_pairs as pairs,claim_flash_common as f,evidence_refresh as e,claim_flash_flow as flow
from backend.tests.test_claim_flash_relations import ready,Ports
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store,_temporary_postgres
from backend.tests.test_claim_flash_flow import candidate,FlowPorts


def test_pair_order_reuses_complete_gpt_answers(ready):
    store,root,_=ready
    third={**deepcopy(store.get_record_state('claims','C')['payload']),'claim_id':'X'}
    store.apply_plan(store.plan_package({'package_id':'review-extra','claims':[third], 'claim_relations':[
        {'claim_relation_id':'R1','from_id':'C','to_id':'D','relation_type':'supports','reason':'第一條理由。'},
        {'claim_relation_id':'R2','from_id':'D','to_id':'X','relation_type':'supports','reason':'第二條理由。'}]}))
    records,sources=f.load_source(store,'S');keys=['claim_relations/R1','claim_relations/R2'];ports=Ports(choices={'relation':'uncertain'})
    pairs.review(records,sources,keys,[],root/'pair-order',ports,kind='claim')
    initial=len([1 for role,_ in ports.calls if role=='gpt'])
    replay=Ports(choices={'relation':'uncertain'})
    pairs.review(records,sources,list(reversed(keys)),[],root/'pair-order',replay,kind='claim')
    repeated=[req for role,req in replay.calls if role=='gpt']
    assert initial==1 and repeated==[]


@pytest.mark.parametrize('status',[400,401,413,422,429])
def test_known_http_rejection_keeps_failure_receipt_without_resending(tmp_path,status):
    calls=[]
    def rejected(request):
        calls.append(request)
        raise HTTPError('https://api.typesafe.ai/v1/systemone',status,'bad request',{'Retry-After':'12'},BytesIO(b'{"detail":{"error_type":"max_tokens_exceeded"}}'))
    req={'model':e.MODEL,'state':'text','questions':{}}
    with pytest.raises(HTTPError):e.recorded_call(tmp_path,'jev',req,rejected,jev_max_bytes=128000)
    with pytest.raises(e.RecordedCallRejected,match='HTTP '+str(status)):e.recorded_call(tmp_path,'jev',req,rejected,jev_max_bytes=128000)
    assert len(calls)==1
    receipt=json.loads(next(tmp_path.rglob('rejection.json')).read_text())
    assert receipt['http_status']==status and not receipt['retry_authorized']
    assert receipt['retry_after']=='12'
    assert len(list(tmp_path.rglob('started.json')))==1 and not list(tmp_path.rglob('response.json'))


@pytest.mark.parametrize(('when','fault'),[('after_stage',None),('after_sql',None),('after_stage','outside'),('after_stage','source'),('after_stage','receipt'),('after_stage','human')])
def test_crash_after_own_argument_write_resumes_without_repeating_completed_stages(candidate,tmp_path,monkeypatch,when,fault):
    package,_=candidate
    original=package['evidence_steps'][0]
    extra={**deepcopy(original),'evidence_step_id':original['evidence_step_id']+'-extra','produced_claim_ids':[]}
    package['evidence_steps'].append(extra)
    path=tmp_path/'candidate.json';path.write_text(json.dumps(package))
    class ExtraEvidence(FlowPorts):
        def repair(self,request):
            if request['input']['task']=='argument_completeness':
                self.calls.append(('gpt',request))
                return {'items':[{'job_id':j['job_id'],'action':'complete','pairs':[{
                    'a':'evidence_steps/'+extra['evidence_step_id'],'b':j['job_id'],'relation_type':'supports','direction':'a_to_b',
                    'fragment_ids':e.evidence_fragment_ids(extra),'reason':'補上已確認的支持材料。'}],'reason':'完整。'} for j in request['input']['jobs']]}
            return super().repair(request)
    from backend.pipeline import claim_candidate_workspace as workspace
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    original_relations=flow.relations.run;original_apply=PostgresKnowledgeStore.apply_plan;writes=[];failed=False
    def apply(self,plan,*args,**kwargs):
        nonlocal failed
        result=original_apply(self,plan,*args,**kwargs)
        if plan.source_kind==flow.arguments.VERSION:
            writes.append(plan.change_set_id)
            if when=='after_sql' and not failed:
                failed=True;raise RuntimeError('process died after committed arguments')
        return result
    monkeypatch.setattr(PostgresKnowledgeStore,'apply_plan',apply)
    def crash(*a,**k):raise RuntimeError('process died after committed arguments')
    if when=='after_stage':monkeypatch.setattr(flow.relations,'run',crash)
    with pytest.raises(RuntimeError,match='process died'):flow.run_candidate(path,tmp_path/'crash-flow',ExtraEvidence())
    argroot=next((tmp_path/'crash-flow/flash/claims').glob('*/arguments'))
    assert (argroot/'commit-intent.json').exists() and len(writes)==1
    with workspace.open_candidate(package,tmp_path/'crash-flow/workspace') as store:
        assert store.get_change_history(writes[0])['status']=='applied'
        if fault=='outside':
            cid=package['claims'][0]['claim_id'];payload=store.get_record_state('claims',cid)['payload'];payload['statement']='另一操作的修改。'
            store.apply_plan(store.plan_package({'package_id':'outside-write','claims':[payload]}))
        if fault=='human':
            from backend.pipeline import claim_human_source as human
            from backend.tests.test_claim_flash_human_source import save
            sid=package['source_documents'][0]['source_id'];_,sources=f.load_source(store,sid);pk=next(iter(sources[sid]['paragraphs']))
            record={'schema_version':'wang_claim_human_source_verification_v1','verified_by':'user','human_content_approval':False,
                'user_confirmation':'新的來源澄清。','answers':[{'question':'指什麼？','answer':'使用者新確認。',
                    'citation':{'source_id':sid,'paragraph_key':pk,'exact_quote':sources[sid]['paragraphs'][pk]['text']}}]}
            save(store,[human.entry({'path':'/archived/new.json','file_sha256':'b'*64},record,sid,sources[sid],[pk],'confirmation')])
    if fault=='source':Path(package['source_documents'][0]['source_path']).write_text('{}')
    if fault=='receipt':next((tmp_path/'crash-flow/flash/claims').glob('*/statements/result.json')).write_text('{}')
    monkeypatch.setattr(flow.relations,'run',original_relations)
    completed=[flow.source,flow.coverage,flow.eo,flow.statements]
    if when=='after_stage':completed.append(flow.arguments)
    for module in completed:monkeypatch.setattr(module,'run',lambda *a,**k:pytest.fail('completed stage repeated'))
    replay=ExtraEvidence()
    if fault:
        with pytest.raises(ValueError,match='outside write|source|stage evidence changed'):
            flow.run_candidate(path,tmp_path/'crash-flow',replay)
        assert replay.calls==[]
    else:
        result=flow.run_candidate(path,tmp_path/'crash-flow',replay)
        assert result['status']=='completed' and not result['pending']
        assert not any(role=='gpt' and req['input']['task']=='argument_completeness' for role,req in replay.calls)
    assert len(writes)==1


@pytest.mark.parametrize('replace_fact',[False,True])
def test_cross_group_human_context_reaches_next_missing_stage(sample,tmp_path,replace_fact):
    import hashlib
    from backend.pipeline import claim_flash_evidence as eo,claim_flash_statements as statements,claim_flash_arguments as arguments,claim_flash_relations as relations,claim_flash_missing as missing
    from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts,saved_inventory
    from backend.tests.test_claim_flash_arguments import Ports as ArgumentPorts
    from backend.tests.test_claim_update_flash import Ports as BasePorts
    from backend.tests.test_claim_flash_human_source import fact,save
    store,root,path=sample
    doc=store.get_record_state('source_documents','S')['payload'];data=json.loads(path.read_text());data['script'].append({'index':2,'text':'另一段原文，教授解釋同一個判斷。'})
    path.write_text(json.dumps(data,ensure_ascii=False));doc['source_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    snap=e.source_snapshot({'path':str(path),'file_sha256':doc['source_file_sha256']},source=doc)
    f1=store.get_record_state('source_fragments','F')['payload'];f1['source_sha256']=snap['body_sha256']
    text=snap['paragraphs']['S0002']['text'];f2={**deepcopy(f1),'fragment_id':'F2','paragraph_key':'S0002','verbatim_excerpt':text,
        'source_segment_index':snap['paragraphs']['S0002']['index'],'media_time':None,'media_end_time':None,'paragraph_text_sha256':hashlib.sha256(text.encode()).hexdigest(),'verbatim_excerpt_sha256':hashlib.sha256(text.encode()).hexdigest()}
    ev=store.get_record_state('evidence_steps','E')['payload'];ev.update(statement='沒有聽從祂的話，顯明沒有真正相信祂。',produced_claim_ids=['X'])
    ev2={**deepcopy(ev),'evidence_step_id':'E2','statement':text,'source_fragment_ids':['F2'],'produced_claim_ids':['C']}
    cp=store.get_record_state('claims','C')['payload'];other={**deepcopy(cp),'claim_id':'X'};cp['evidence_step_ids']=['E2']
    store.apply_plan(store.plan_package({'package_id':'cross-human-fixture','source_documents':[doc],'source_fragments':[f1,f2],
        'evidence_steps':[ev,ev2],'claims':[cp,other],'claim_relations':[{'claim_relation_id':'R','from_id':'C','to_id':'X','relation_type':'supports','reason':'兩段的承接。'}]}))
    _,sources=f.load_source(store,'S');save(store,[fact(sources,'S0001')])
    groups=[{'group_id':'G0','title':'one','claim_ids':['X'],'paragraph_keys':['S0001'],'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]},
        {'group_id':'G1','title':'two','claim_ids':['C'],'paragraph_keys':['S0002'],'lower_record_keys':['evidence_steps/E2'],'reference_record_keys':['claims/X']}]
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':groups};eroot=root/'evidence';sroot=root/'statements';aroot=root/'arguments';rroot=root/'relations'
    lower=eo.run(store,'S',plan,eroot,EvidencePorts(),saved_inventory(tmp_path/'prior',sources),apply=True)
    assert lower['status']=='completed',lower
    assert statements.run(store,'S',['C'],eroot,sroot,BasePorts(),apply=True)['status']=='verified'
    assert arguments.run(store,'S',plan,'G1',eroot,sroot,aroot,ArgumentPorts(),apply=True)['status']=='verified'
    assert relations.run(store,'S',aroot,rroot,Ports(choices={'relation':'a_to_b_supports'}),apply=True)['status']=='verified'
    from backend.tests.test_claim_flash_missing import Ports as MissingPorts
    ports=MissingPorts()
    if replace_fact:
        original_fact=fact(sources,'S0001')
        save(store,[fact(sources,'S0001',supersedes=[original_fact['decision_id']])])
        with pytest.raises(ValueError,match='completed group Claim relations required'):
            missing.run(store,'S',rroot,root/'missing',ports,apply=True)
        assert ports.calls==[]
    else:
        result=missing.run(store,'S',rroot,root/'missing',ports,apply=True)
        assert result['status']=='verified' and not result['pending']
        frozen=json.loads((root/'missing/before.json').read_text())
        parent=json.loads((rroot/'before.json').read_text())
        assert frozen['sources']==parent['sources']
        assert frozen['sources']['S']['human_source_decisions'][0]['paragraph_sha256']==fact(sources,'S0001')['paragraph_sha256']


@pytest.mark.parametrize('status',[408,500,503])
def test_ambiguous_http_failure_stays_unknown_and_is_not_resent(tmp_path,status):
    calls=[]
    def failed(request):
        calls.append(request);raise HTTPError('https://api.typesafe.ai/v1/systemone',status,'failed',{},BytesIO(b'failed'))
    request={'model':e.MODEL,'state':'same','questions':{}}
    with pytest.raises(HTTPError):e.recorded_call(tmp_path,'jev',request,failed)
    with pytest.raises(ValueError,match='no saved reply'):e.recorded_call(tmp_path,'jev',request,failed)
    assert len(calls)==1 and not list(tmp_path.rglob('rejection.json'))


def test_rejection_receipt_is_checked_by_flash_and_tampering_never_resends(tmp_path):
    from types import SimpleNamespace
    calls=[]
    def failed(request):
        calls.append(request);raise HTTPError('https://api.typesafe.ai/v1/systemone',400,'too large',{},BytesIO(b'exact body'))
    ports=SimpleNamespace(judge=failed)
    question={'type':'choice','instructions':'Is this supported?','criteria':{'yes':'yes','no':'no'}}
    with pytest.raises(HTTPError):f.jev(tmp_path,ports,{'text':'unchanged'},{'q':question})
    with pytest.raises(e.RecordedCallRejected):f.jev(tmp_path,ports,{'text':'unchanged'},{'q':question})
    next(tmp_path.rglob('rejection-body.bin')).write_bytes(b'tampered')
    with pytest.raises(ValueError,match='binding differs'):f.jev(tmp_path,ports,{'text':'unchanged'},{'q':question})
    assert len(calls)==1


def test_normal_flow_does_not_rebuild_recovery_or_snapshot_after_every_stage(candidate,tmp_path,monkeypatch):
    package,_=candidate;path=tmp_path/'candidate.json';path.write_text(json.dumps(package));snapshots=[]
    original=flow.snapshot
    def snapshot(*args,**kwargs):snapshots.append(1);return original(*args,**kwargs)
    monkeypatch.setattr(flow,'snapshot',snapshot)
    monkeypatch.setattr(flow.recovery,'assert_owned_changes',lambda *a,**k:pytest.fail('normal flow rebuilt recovery'))
    result=flow.run_candidate(path,tmp_path/'normal',FlowPorts())
    assert result['status']=='completed'
    # Initial census, optional mechanical calibration, final export only.
    assert len(snapshots)<=3


def test_recovery_accepts_an_own_retirement_receipt(ready):
    store,root,_=ready
    edge={'claim_relation_id':'R','from_id':'C','to_id':'D','relation_type':'supports','reason':'原來的關係。'}
    store.apply_plan(store.plan_package({'package_id':'before-retirement','claim_relations':[edge]}))
    before,sources=f.load_source(store,'S');phase=root/'retire'
    verification={'version':'isolated_recovery_retirement','status':'verified','updates':{},'new_fragments':{},
        'retiring_keys':['claim_relations/R'],'pending':[],'applied':False}
    e.save(phase/'before.json',{'records':before,'sources':sources});e.save(phase/'verification.json',verification)
    assert e.commit_verified(store,phase)['status']=='applied'
    current,_=f.load_source(store,'S')
    flow.recovery.assert_owned_changes(store,root,before,current)
    assert store.get_record_state('claim_relations','R')['retired']
