from copy import deepcopy
import hashlib
import pytest

from backend.pipeline import claim_flash_source as source, evidence_refresh as e
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


class Ports:
    identity={'gpt':{'backend':'test','model':'test','effort':'medium'},'jev':e.MODEL}
    def __init__(self,quote='你沒有聽從祂的話，你根本沒信。',action='bind'):
        self.calls=[];self.quote=quote;self.action=action
    def judge(self,request):
        self.calls.append('jev')
        return {'model':e.MODEL,'answers':{key:{'type':'choice','choice':'S0001',
            'probabilities':{c:float(c=='S0001') for c in spec['criteria']}}
            for key,spec in request['questions'].items()}}
    def repair(self,request):
        self.calls.append('gpt')
        assert request['input']['task']=='source_binding_completion'
        return {'items':[{'job_id':job['job_id'],'action':self.action,
            'quotes':[{'paragraph_key':'S0001','quote':self.quote}],
            'reason':'補足原文指代；不修改摘要。'} for job in request['input']['jobs']]}


@pytest.mark.parametrize('old_quote',['你不知道神的旨意，怎麼遵行？','你'])
def test_source_handoff_commits_only_complete_bindings_and_reuses_exact_answer(sample,old_quote):
    store,root,_=sample
    fragment=store.get_record_state('source_fragments','F')['payload']
    fragment['verbatim_excerpt']=old_quote
    fragment['verbatim_excerpt_sha256']=hashlib.sha256(fragment['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id':'short-fragment','source_fragments':[fragment]}))
    fragment=store.get_record_state('source_fragments','F')['payload']
    before=deepcopy(store.get_record_state('evidence_steps','E')['payload'])
    findings=[{'object_key':'evidence_steps/E','layer':'source','reason':'缺少後半句原文。'}]
    ports=Ports();result=source.repair_bindings(store,'S',findings,root/'handoff',ports,apply=True)
    assert result['status']=='applied' and ports.calls==['jev','gpt']
    current=store.get_record_state('evidence_steps','E')['payload']
    assert {k:v for k,v in current.items() if k not in {'revision','source_fragment_id','source_fragment_ids'}} == {
        k:v for k,v in before.items() if k not in {'revision','source_fragment_id','source_fragment_ids'}}
    assert 'F' in current['source_fragment_ids'] and len(current['source_fragment_ids'])==2
    assert store.get_record_state('source_fragments','F')['payload']==fragment
    replay=Ports();again=source.repair_bindings(store,'S',findings,root/'handoff',replay,apply=True)
    assert again['status']=='applied' and replay.calls==[]


@pytest.mark.parametrize('action,quote',[('needs_source','你沒有聽從祂的話，你根本沒信。'),('bind','原文沒有這句話。')])
def test_source_handoff_does_not_write_missing_or_invented_quote(sample,action,quote):
    store,root,_=sample;before=deepcopy(store.get_record_state('evidence_steps','E'))
    findings=[{'object_key':'evidence_steps/E','layer':'source','reason':'需要補足原文。'}]
    if action=='bind':
        with pytest.raises(ValueError,match='unique literal'):
            source.repair_bindings(store,'S',findings,root/'handoff',Ports(quote,action),apply=True)
    else:
        assert source.repair_bindings(store,'S',findings,root/'handoff',Ports(quote,action),apply=True)['status']=='needs_attention'
    assert store.get_record_state('evidence_steps','E')==before


def test_uncovered_saved_inventory_unit_is_compiled_without_model_or_upper_write(sample):
    from backend.pipeline import claim_flash_common as f, claim_flash_source_fill as fill
    from backend.tests.test_claim_flash_evidence import saved_inventory
    store,root,_=sample
    fragment=store.get_record_state('source_fragments','F')['payload']
    fragment['verbatim_excerpt']='你不知道神的旨意，怎麼遵行？'
    fragment['verbatim_excerpt_sha256']=hashlib.sha256(fragment['verbatim_excerpt'].encode()).hexdigest()
    store.apply_plan(store.plan_package({'package_id':'short-inventory-span','source_fragments':[fragment]}))
    records,sources=f.load_source(store,'S')
    unit={'paragraph_key':'S0001','quote':'你沒有聽從祂的話，你根本沒信。','statement':'沒有聽從顯明沒有真正相信。'}
    previous=saved_inventory(root/'prior',sources,units=[unit])
    handoff={'layer':'source','reason':'important_unit_not_covered_by_verified_fragments','unit':unit}
    result=fill.fill_inventory_handoff(store,'S',[handoff],previous,root/'fill',apply=True)
    assert result['status']=='applied' and result['model_calls']==0 and len(result['new_fragments'])==1
    assert result['updates']=={}
    for key,row in records.items():
        assert store.get_record_state(*key.split('/',1))==row
    with pytest.raises(ValueError,match='exact saved'):
        fill.fill_inventory_handoff(store,'S',[{**handoff,'unit':{**unit,'statement':'自行改過的意思。'}}],previous,root/'bad',apply=True)


def test_changed_diagnostic_wording_reuses_same_saved_source_answer_after_program_failure(sample,monkeypatch):
    store,root,_=sample;ports=Ports()
    first=[{'object_key':'evidence_steps/E','layer':'source','reason':'舊的缺口說明。'}]
    original=source.binding_postimage
    monkeypatch.setattr(source,'binding_postimage',lambda *a,**k:(_ for _ in ()).throw(ValueError('program defect')))
    with pytest.raises(ValueError,match='program defect'):
        source.repair_bindings(store,'S',first,root/'repair',ports,apply=True)
    assert ports.calls==['jev','gpt']
    monkeypatch.setattr(source,'binding_postimage',original)
    second=[{**first[0],'reason':'相同材料的另一種缺口說明。'}]
    result=source.repair_bindings(store,'S',second,root/'repair',ports,apply=True)
    assert result['status']=='applied' and ports.calls==['jev','gpt']
    import json
    assert json.loads((root/'repair/intent.json').read_text())['findings']==first


def add_targets(store, count=3):
    original=store.get_record_state('evidence_steps','E')['payload']
    targets=[{**deepcopy(original),'evidence_step_id':f'E{i}'} for i in range(count)]
    store.apply_plan(store.plan_package({'package_id':'batch-targets','evidence_steps':targets}))
    return [{'object_key':'evidence_steps/'+p['evidence_step_id'],'layer':'source','reason':'補足後半句。'} for p in targets]


def test_source_handoffs_share_paragraphs_in_one_call_and_replay_without_calls(sample):
    store,root,_=sample;findings=add_targets(store);ports=Ports()
    before={p['object_key']:deepcopy(store.get_record_state(*p['object_key'].split('/'))['payload']) for p in findings}
    requests=[];repair=ports.repair
    def capture(req):
        requests.append(req)
        data=req['input']
        assert len(data['jobs'])==3 and list(data['source_paragraphs'])==['S0001']
        for job in data['jobs']:
            assert 'nearby_paragraphs' not in job['material']
            assert job['material']['nearby_paragraph_keys']==['S0001']
        return repair(req)
    ports.repair=capture
    result=source.repair_bindings(store,'S',findings,root/'batch',ports,apply=True)
    assert result['status']=='applied' and ports.calls==['jev','gpt']
    assert {x['job_id'] for x in result['outcomes']}==set(before)
    for key,old in before.items():
        current=store.get_record_state(*key.split('/'))['payload']
        excluded={'revision','source_fragment_id','source_fragment_ids'}
        assert {k:v for k,v in old.items() if k not in excluded}=={k:v for k,v in current.items() if k not in excluded}
        assert len(current['source_fragment_ids'])==2
    replay=Ports()
    assert source.repair_bindings(store,'S',findings,root/'batch',replay,apply=True)['status']=='applied'
    assert replay.calls==[]


def test_batch_program_failure_prevents_every_write_and_reuses_saved_reply(sample,monkeypatch):
    store,root,_=sample;findings=add_targets(store);ports=Ports()
    before={p['object_key']:store.get_record_state(*p['object_key'].split('/')) for p in findings}
    original=source.binding_postimage;seen=[]
    def fail_second(key,*args,**kwargs):
        seen.append(key)
        if len(seen)==2:raise ValueError('program defect in batch')
        return original(key,*args,**kwargs)
    monkeypatch.setattr(source,'binding_postimage',fail_second)
    with pytest.raises(ValueError,match='program defect'):
        source.repair_bindings(store,'S',findings,root/'batch',ports,apply=True)
    assert all(store.get_record_state(*key.split('/'))==row for key,row in before.items())
    monkeypatch.setattr(source,'binding_postimage',original)
    assert source.repair_bindings(store,'S',findings,root/'batch',ports,apply=True)['status']=='applied'
    assert ports.calls==['jev','gpt']


def test_batch_rejects_quote_from_other_jobs_allowed_paragraph_without_any_write(sample,monkeypatch):
    store,root,_=sample;findings=add_targets(store,2);before=store.compile_package()
    prepare=source.source_lookup.Locator.prepare
    def restricted(self,key,*args):
        material,quotes=prepare(self,key,*args)
        if key.endswith('/E1'):material['nearby_paragraphs']=[]
        return material,quotes
    monkeypatch.setattr(source.source_lookup.Locator,'prepare',restricted)
    monkeypatch.setattr(source.source_lookup,'lookup',lambda jobs,*a:{j['job_id']:j['material'] for j in jobs})
    with pytest.raises(ValueError,match='unprovided source repair paragraph'):
        source.repair_bindings(store,'S',findings,root/'batch',Ports(),apply=True)
    after=store.compile_package()
    after.pop('compiled_at');before.pop('compiled_at')
    assert after==before


def test_flow_recovers_original_batch_after_commit_crash_without_new_model_call(sample,monkeypatch):
    from backend.pipeline import claim_flash_flow as flow
    from backend.tests.test_claim_flash_flow import FlowPorts
    store,root,_=sample;findings=add_targets(store)
    bindings=Ports()
    class Combined(FlowPorts):
        def judge(self,request):
            if 'current_paragraphs' in request['state']:return bindings.judge(request)
            return super().judge(request)
        def repair(self,request):
            if request['input']['task']=='source_binding_completion':return bindings.repair(request)
            return super().repair(request)
    def lower(*a,**kw):
        needs=any(len(store.get_record_state(*p['object_key'].split('/'))['payload']['source_fragment_ids'])==1 for p in findings)
        return {'status':'needs_attention','groups':[{'group_id':'FG001','pending':findings if needs else [{'layer':'test_stop','reason':'end of source test'}]}]}
    monkeypatch.setattr(flow.eo,'run',lower)
    publish=e.publish
    def crash(path,result):
        if path.name.startswith('batch-') and result.get('applied'):raise RuntimeError('crash after batch commit')
        return publish(path,result)
    monkeypatch.setattr(e,'publish',crash)
    with pytest.raises(RuntimeError,match='crash after batch commit'):
        flow.run(store,'S',root/'flow',Combined())
    assert bindings.calls==['jev','gpt']
    revisions={p['object_key']:store.get_record_state(*p['object_key'].split('/'))['revision'] for p in findings}
    monkeypatch.setattr(e,'publish',publish)
    result=flow.run(store,'S',root/'flow',Combined())
    assert result['status']=='needs_attention' and bindings.calls==['jev','gpt']
    assert all(store.get_record_state(*key.split('/'))['revision']==revision for key,revision in revisions.items())
    assert len(list((root/'flow/source-handoffs').glob('batch-*/result.json')))==1
