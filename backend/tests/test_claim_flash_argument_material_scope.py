"""Adjacent verified material is a bounded candidate, not a reason to repeat lower review."""
from copy import deepcopy
import json
import pytest
from backend.pipeline import claim_flash_arguments as stage, claim_flash_evidence as eo
from backend.pipeline import claim_flash_common as f, claim_flash_statements as statements, evidence_refresh as e
from backend.tests.test_claim_flash_arguments import ready, Ports
from backend.tests.test_claim_flash_evidence import Ports as EvidencePorts, saved_inventory
from backend.tests.test_claim_update_flash import Ports as BasePorts
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def adjacent(ready,tmp_path,second_claim=False):
    store,root,plan,_,_=ready
    from pathlib import Path
    import hashlib
    doc=store.get_record_state('source_documents','S')['payload'];path=Path(doc['source_path'])
    body=json.loads(path.read_text());body['script'].append({'index':2,'start_time':2.0,'end_time':3.0,'text':'教授用行為說明信心。'})
    path.write_text(json.dumps(body,ensure_ascii=False));doc['source_file_sha256']=hashlib.sha256(path.read_bytes()).hexdigest()
    snap=e.source_snapshot({'path':str(path),'file_sha256':doc['source_file_sha256']},source=doc)
    first=store.get_record_state('source_fragments','F')['payload'];first['source_sha256']=snap['body_sha256']
    second={**first,'fragment_id':'F2','paragraph_key':'S0002','verbatim_excerpt':snap['paragraphs']['S0002']['text'],
        'source_segment_index':2,'media_time':2.0,'media_end_time':3.0}
    second['paragraph_text_sha256']=second['verbatim_excerpt_sha256']=hashlib.sha256(second['verbatim_excerpt'].encode()).hexdigest()
    ev=store.get_record_state('evidence_steps','E')['payload'];ev['source_fragment_ids']=['F','F2']
    ob=store.get_record_state('observations','O')['payload'];ob['source_fragment_id']='F2'
    store.apply_plan(store.plan_package({'package_id':'adjacent-source','source_documents':[doc],
        'source_fragments':[first,second],'evidence_steps':[ev],'observations':[ob]}))
    plan=deepcopy(plan);plan['source_body_sha256']=snap['body_sha256']
    plan['groups'][0].update(lower_record_keys=['evidence_steps/E'],paragraph_keys=['S0001'])
    plan['groups'].append({**plan['groups'][0],'group_id':'H','claim_ids':[],'paragraph_keys':['S0002'],'lower_record_keys':['observations/O']})
    if second_claim:
        claim=store.get_record_state('claims','C')['payload'];claim['claim_id']='C2'
        store.apply_plan(store.plan_package({'package_id':'second-claim','claims':[claim]}))
        plan['groups'][0]['claim_ids'].append('C2')
    eroot=root/'adjacent-evidence';records,sources=f.load_source(store,'S')
    assert eo.run(store,'S',plan,eroot,EvidencePorts(),saved_inventory(tmp_path/'adjacent-prior',sources),apply=True)['status']=='completed'
    sroot=root/'adjacent-statements'
    def handoff(req):
        return {'items':[{'job_id':j['job_id'],'action':'needs_connection' if j['job_id']=='claims/C' else 'keep','reason':'missing integration support'} for j in req['input']['jobs']]}
    assert statements.run(store,'S',plan['groups'][0]['claim_ids'],eroot,sroot,BasePorts(choices={'support':'partial'},reply=handoff),apply=True)['status']=='needs_attention'
    return store,root,plan,eroot,sroot


def test_candidates_are_source_bounded_nonrecursive_and_complete():
    def frag(sid,pk):return {'payload':{'source_id':sid,'paragraph_key':pk}}
    def node(*ids):return {'payload':{'source_fragment_ids':list(ids)}}
    records={'source_fragments/F':frag('S','P'), 'source_fragments/X':frag('OTHER','P'),
             'source_fragments/Y':frag('S','Q'), 'evidence_steps/E':node('F'),
             'observations/O':node('F'),'observations/foreign':node('X'),
             'observations/mixed':node('F','Y'),'observations/missing':node('ABSENT'),
             'observations/retired':{**node('F'),'retired':True}}
    assert stage.material_candidates({'evidence_steps/E'},records,{'P'},'S')=={'evidence_steps/E','observations/O'}


def test_adjacent_candidates_do_not_automatically_get_pair_reviews(ready,tmp_path):
    store,root,plan,eroot,sroot=adjacent(ready,tmp_path)
    ports=Ports();result=stage.run(store,'S',plan,'G',eroot,sroot,root/'unselected',ports,apply=True)
    assert result['status']=='verified' and not result['updates']
    req=next(r for role,r in ports.calls if role=='gpt' and r['input']['task']=='argument_completeness')
    assert 'observations/O' in req['input']['nodes']
    assert not any(role=='jev' and 'observations/O' in json.dumps(r) for role,r in ports.calls)
    assert not any(k.startswith('knowledge_relations/') for k in result['updates'])


@pytest.mark.parametrize('repair',[True,False])
def test_stopped_parent_correction_reuses_completed_tasks_and_commits(ready,tmp_path,monkeypatch,repair):
    store,root,plan,eroot,sroot=adjacent(ready,tmp_path,second_claim=True);aroot=root/'arguments'
    class Stop(Ports):
        def repair(self,req):
            raw=super().repair(req)
            if req['input']['task']=='argument_final_support':
                for item in raw['items']:
                    if item['job_id']=='claims/C':item.update(action='needs_connection',reason='verified adjacent material missing')
            return raw
    with monkeypatch.context() as m:
        m.setattr(stage,'material_candidates',lambda pool,*args:set(pool))
        stopped=stage.run(store,'S',plan,'G',eroot,sroot,aroot,Stop(choices={'support':'partial'}),apply=True)
    assert stopped['status']=='needs_attention'
    parent=(aroot/'verification.json').read_bytes();before=json.loads((aroot/'before.json').read_text())
    e.save(aroot/'material-scope-continuation.json',{'contract':stage.CANDIDATE_SCOPE,'owner_direction':'continue authorized refresh',
        'parent_verification_sha256':e.digest(json.loads(parent)),'parent_before_sha256':e.digest(before)})
    ports=Ports([{'a':'observations/O','b':'claims/C','fragment_ids':['F2'],'reason':'已驗證的行為觀察補足支持。'}] if repair else [])
    result=stage.run(store,'S',plan,'G',eroot,sroot,aroot,ports,apply=True)
    if not repair:
        assert result['status']=='needs_attention' and result['pending']==stopped['pending']
        assert not any(role=='jev' and 'support' in r['questions'] for role,r in ports.calls)
        assert not (stage.active_root(aroot)/'commit-intent.json').exists()
        assert (aroot/'verification.json').read_bytes()==parent
        return
    assert result['status']=='applied' and not result['pending']
    assert (aroot/'verification.json').read_bytes()==parent
    requests=[r for role,r in ports.calls if role=='gpt' and r['input']['task']=='argument_completeness']
    assert len(requests)==1 and [j['job_id'] for j in requests[0]['input']['jobs']]==['claims/C']
    assert not any(role=='gpt' and r['input']['task'].startswith('pair_') for role,r in ports.calls)
    assert not any(role=='jev' and 'record_fidelity' in r['questions'] for role,r in ports.calls)
    assert any(p.get('from_id')=='O' and p.get('to_id')=='C' for k,p in result['updates'].items() if k.startswith('knowledge_relations/'))
    from backend.pipeline import claim_flash_relations as relations
    records,sources=f.load_source(store,'S')
    assert relations.consume_arguments(store,aroot,['C','C2'],records,sources)
    replay=Ports();assert stage.run(store,'S',plan,'G',eroot,sroot,aroot,replay,apply=True)['status']=='applied'
    assert replay.calls==[]


@pytest.mark.parametrize('bad',['hash','scope','committed','source','record'])
def test_correction_refuses_drift_before_model_calls(ready,bad):
    store,root,plan,eroot,sroot=ready;aroot=root/'stopped';aroot.mkdir()
    records,sources=f.load_source(store,'S');before={'records':records,'sources':sources}
    parent={'version':stage.VERSION,'status':'needs_attention','claim_ids':['C'],
            'pending':[{'object_key':'claims/C','layer':'claim_connections'}]}
    authority={'contract':stage.CANDIDATE_SCOPE,'owner_direction':'continue',
               'parent_verification_sha256':e.digest(parent),'parent_before_sha256':e.digest(before)}
    if bad=='hash':authority['parent_verification_sha256']='wrong'
    if bad=='scope':authority['contract']='other'
    if bad=='committed':e.save(aroot/'commit-intent.json',{})
    if bad=='source':before['sources']=deepcopy(sources);before['sources']['S']['body_sha256']='changed';authority['parent_before_sha256']=e.digest(before)
    if bad=='record':before['records']=deepcopy(records);before['records']['claims/C']['revision']+=1;authority['parent_before_sha256']=e.digest(before)
    for name,value in [('before',before),('verification',parent),('result',parent),('material-scope-continuation',authority)]:e.save(aroot/(name+'.json'),value)
    ports=Ports()
    with pytest.raises(ValueError):stage.run(store,'S',plan,'G',eroot,sroot,aroot,ports,apply=True)
    assert ports.calls==[]


def test_legacy_frozen_input_is_not_silently_expanded(ready,monkeypatch):
    store,root,plan,eroot,sroot=ready;aroot=root/'legacy';aroot.mkdir()
    pin=f.bind_source(store,'S',aroot)
    intent={'version':stage.VERSION,'source':pin,'group':plan['groups'][0],'models':Ports.identity,
            'evidence_root':str(eroot.resolve()),'statement_root':str(sroot.resolve()),
            'policy_sha256':e.digest([stage.PROMPT,stage.Routes.model_json_schema()])}
    e.save(aroot/'intent.json',intent)
    def forbidden(*args):raise AssertionError('legacy frozen input expanded')
    monkeypatch.setattr(stage,'material_candidates',forbidden)
    assert stage.run(store,'S',plan,'G',eroot,sroot,aroot,Ports(),apply=True)['status']=='verified'
    assert json.loads((aroot/'intent.json').read_text())==intent
