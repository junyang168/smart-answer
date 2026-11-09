"""Claim statement decisions consume grouped Evidence without re-reviewing it."""
from copy import deepcopy
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_flash_statements as stage,claim_flash_evidence as eo
from backend.pipeline import claim_flash_common as f,evidence_refresh as e
from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts,saved_inventory
from backend.tests.test_claim_update_flash import Ports
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store,_temporary_postgres

@pytest.fixture
def ready(sample,tmp_path):
    store,root,_=sample
    record=store.get_record_state('evidence_steps','E')['payload'];record['statement']='沒有聽從祂的話，顯明沒有真正相信祂。'
    store.apply_plan(store.plan_package({'package_id':'ready-evidence','evidence_steps':[record]}))
    records,sources=f.load_source(store,'S')
    plan={'source_id':'S','source_body_sha256':sources['S']['body_sha256'],'groups':[{'group_id':'G','title':'one',
       'paragraph_keys':list(sources['S']['paragraphs']),'lower_record_keys':['evidence_steps/E'],'reference_record_keys':[]}]}
    prior=saved_inventory(tmp_path/'prior',sources);eroot=root/'evidence'
    result=eo.run(store,'S',plan,eroot,EvidencePorts(),prior,apply=True);assert result['status']=='completed'
    return store,root,eroot

def reply(action='repair',**fields):
    def callback(request):
        return {'items':[{'job_id':j['job_id'],'action':action,'reason':'根據來源處置。',**fields} for j in request['input']['jobs']]}
    return callback


def test_actual_sql_repair_and_zero_call_resume(ready):
    store,root,eroot=ready;before=store.get_record_state('evidence_steps','E')
    good='沒有聽從祂的話，顯明沒有真正相信祂。'
    ports=Ports(choices={'statement_fidelity':'changed'},reply=reply(statement=good))
    result=stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert result['status']=='applied' and store.get_record_state('claims','C')['payload']['statement']==good
    assert store.get_record_state('evidence_steps','E')==before
    assert result['claim_qualification']=='not_granted'
    assert [r['input']['task'] for role,r in ports.calls if role=='gpt']==['claim_fidelity']
    assert not any('record_fidelity' in r['questions'] for role,r in ports.calls if role=='jev')
    cached=Ports();again=stage.run(store,'S',['C'],eroot,root/'claims',cached,apply=True)
    assert again['status']=='applied' and cached.calls==[]

@pytest.mark.parametrize('action,fields',[('keep',{'statement':'回聲不得當修改。'}),('needs_source',{}),('needs_connection',{})])
def test_keep_and_unresolved_are_not_silent_repairs(ready,action,fields):
    store,root,eroot=ready;before=store.get_record_state('claims','C')
    ports=Ports(choices={'support':'partial'},reply=reply(action,**fields))
    result=stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert store.get_record_state('claims','C')==before
    assert result['status']==('verified' if action=='keep' else 'needs_attention')
    assert bool(result['pending'])==(action!='keep')


def test_lower_drift_blocks_before_models(ready):
    store,root,eroot=ready;p=store.get_record_state('evidence_steps','E')['payload'];p['statement']='實質修改。'
    store.apply_plan(store.plan_package({'package_id':'drift','evidence_steps':[p]}));ports=Ports()
    with pytest.raises(ValueError,match='matching accepted'):stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert ports.calls==[]


def test_unrelated_unfinished_group_does_not_block(ready):
    store,root,eroot=ready;intent=json.loads((eroot/'intent.json').read_text())
    intent['groups']['groups'].append({'group_id':'H','lower_record_keys':['observations/PENDING']})
    (eroot/'intent.json').write_text(json.dumps(intent))
    assert stage.run(store,'S',['C'],eroot,root/'claims',Ports(),apply=True)['status']=='verified'


def test_group_not_completed_blocks_even_if_record_passed(ready):
    store,root,eroot=ready
    for p in (eroot/'groups/G').glob('*/result.json'):
        d=json.loads(p.read_text());d['pending']=[{'reason':'source issue'}];p.write_text(json.dumps(d))
    ports=Ports()
    with pytest.raises(ValueError,match='not completed'):stage.run(store,'S',['C'],eroot,root/'claims',ports)
    assert ports.calls==[]


def test_gpt_cannot_edit_lower_links(ready):
    store,root,eroot=ready;before=store.get_record_state('claims','C')
    with pytest.raises(ValueError,match='Extra inputs'):
        stage.run(store,'S',['C'],eroot,root/'claims',Ports(choices={'support':'partial'},reply=reply(evidence_step_ids=[])),apply=True)
    assert store.get_record_state('claims','C')==before


def test_commit_crash_recovers_without_model_or_duplicate_sql(ready,monkeypatch):
    store,root,eroot=ready;apply=store.apply_plan
    def crash(*a,**k):apply(*a,**k);raise RuntimeError('after commit')
    monkeypatch.setattr(store,'apply_plan',crash)
    with pytest.raises(RuntimeError,match='after commit'):
        stage.run(store,'S',['C'],eroot,root/'claims',Ports(choices={'statement_fidelity':'changed'},reply=reply(statement='忠實修訂。')),apply=True)
    monkeypatch.setattr(store,'apply_plan',lambda *a,**k:pytest.fail('duplicate write'))
    ports=Ports();assert stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)['status']=='applied'
    assert ports.calls==[]


def test_support_repair_rechecks_only_stale_earlier_task(ready):
    store,root,eroot=ready;good='沒有聽從祂的話，顯明沒有真正相信祂。'
    ports=Ports(choices={'support':lambda r:'supported' if r['state']['claim']['statement']==good else 'partial'},reply=reply(statement=good))
    result=stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert result['status']=='applied'
    assert [x['input']['task'] for role,x in ports.calls if role=='gpt']==['claim_support']
    assert any(set(x['questions'])=={'statement_fidelity'} and x['state']['claim']['statement']==good for role,x in ports.calls if role=='jev')


def test_scripture_metadata_repair_does_not_repeat_fidelity_or_support(ready):
    store,root,eroot=ready
    ports=Ports(choices={'scripture':'missing'},reply=reply(scripture_refs=['Matt 7:21']))
    result=stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert result['status']=='applied'
    assert len([x for role,x in ports.calls if role=='jev'])==1
    assert [x['input']['task'] for role,x in ports.calls if role=='gpt']==['claim_scripture']


def test_noop_replay_still_refuses_changed_target(ready):
    store,root,eroot=ready
    assert stage.run(store,'S',['C'],eroot,root/'claims',Ports(),apply=True)['status']=='verified'
    p=store.get_record_state('claims','C')['payload'];p['statement']='外部改動。'
    store.apply_plan(store.plan_package({'package_id':'external','claims':[p]}));ports=Ports()
    with pytest.raises(ValueError,match='inputs changed'):stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert ports.calls==[]


def test_later_human_evidence_hold_is_not_overridden_by_old_pass(ready):
    store,root,eroot=ready;p=store.get_record_state('evidence_steps','E')['payload'];p['review_status']='human_hold'
    store.apply_plan(store.plan_package({'package_id':'hold','evidence_steps':[p]}));ports=Ports()
    with pytest.raises(ValueError,match='current hold'):stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert ports.calls==[]


def test_semantic_oscillation_stops_without_writing(ready):
    store,root,eroot=ready;before=store.get_record_state('claims','C')
    def alternate(req):
        text='版本甲。' if req['input']['task']=='claim_fidelity' else '版本乙。'
        return reply(statement=text)(req)
    ports=Ports(choices={'statement_fidelity':'changed','support':'partial'},reply=alternate)
    result=stage.run(store,'S',['C'],eroot,root/'claims',ports,apply=True)
    assert result['status']=='needs_attention' and result['pending'][0]['reason']=='bounded_task_passes_exhausted'
    assert store.get_record_state('claims','C')==before


def test_missing_or_duplicate_disposition_cannot_write(ready):
    store,root,eroot=ready;before=store.get_record_state('claims','C')
    with pytest.raises(ValueError,match='scope|job_id|missing|exact'):
        stage.run(store,'S',['C'],eroot,root/'claims',Ports(choices={'statement_fidelity':'changed'},reply=lambda r:{'items':[]}),apply=True)
    assert store.get_record_state('claims','C')==before


def test_native_upstream_material_keeps_route_without_other_claim_expansion(ready):
    from backend.pipeline import claim_update_flash as flash
    store,_,_=ready
    records,sources=f.load_source(store,'S')
    def observation(oid):
        return {'payload':{'observation_id':oid,'statement':'詞義與紙草例子。',
                           'source_fragment_ids':['F'],'speaker':'professor','stance':'asserted'}}
    records['observations/O']=observation('O')
    records['observations/UNRELATED']=observation('UNRELATED')
    records['claims/B']={'payload':{'claim_id':'B','statement':'旁支。','occurrences':[{'source_id':'S','anchors':[]}]}}
    for rid,src,dst in [('R','O','E'),('CYCLE','E','O'),('OTHER','UNRELATED','B'),('CLAIM','B','E')]:
        records['knowledge_relations/'+rid]={'payload':{'relation_id':rid,'from_id':src,'to_id':dst,'relation_type':'supports','reason':'實際論證。'}}
    material=flash.claim_material('claims/C',records,sources,'S')
    assert [x['object_key'] for x in material['evidence']]==['evidence_steps/E']
    context=material['argument_context']
    assert [x['object_key'] for x in context['records']]==['observations/O']
    assert context['records'][0]['original_text_excerpts']
    assert {x['object_key'] for x in context['relations']}=={'knowledge_relations/R','knowledge_relations/CYCLE'}
    assert 'UNRELATED' not in json.dumps(material) and 'claims/B' not in json.dumps(material)


def test_upstream_lower_must_have_existing_completed_evidence_proof(ready):
    store,root,eroot=ready
    store.apply_plan(store.plan_package({'package_id':'upstream-premise',
      'observations':[{'observation_id':'O','statement':'另有詞義論據。','source_fragment_ids':['F'],
                       'argument_role':'background','speaker':'professor','stance':'asserted'}],
      'knowledge_relations':[{'relation_id':'R','from_id':'O','to_id':'E','relation_type':'supports','reason':'詞義支持。'}]}))
    ports=Ports()
    with pytest.raises(ValueError,match='required Evidence group not completed: observations/O'):
        stage.run(store,'S',['C'],eroot,root/'claims',ports)
    assert ports.calls==[]
