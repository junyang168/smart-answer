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
    def __init__(self,proposed=(),**kwargs):super().__init__(**kwargs);self.proposed=list(proposed)
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


def test_rejected_candidate_goes_to_gpt_for_final_disposition(ready):
    store,root,plan,eroot,sroot=ready
    class Remove(Ports):
        def repair(self,request):
            raw=super().repair(request)
            if request['input']['task']=='pair_lower':
                for x in raw['items']:x.update(action='remove',reason='已有路線，不必建立直接邊。')
            return raw
    ports=Remove([{'a':'observations/O','b':'evidence_steps/E','fragment_ids':['F'],'reason':'候選。'}],choices={'relation':'none'})
    result=stage.run(store,'S',plan,'G',eroot,sroot,root/'arguments',ports,apply=True)
    assert any(role=='gpt' and r['input']['task']=='pair_lower' for role,r in ports.calls)
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
