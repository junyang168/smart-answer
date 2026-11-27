"""Saved results drive layer order without repeating content work."""
import hashlib
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_flash_progress as progress


def save(root,name,value):
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}


@pytest.fixture
def manifest(tmp_path):
    plan={'source_id':'S','source_body_sha256':'BODY','groups':[
        {'group_id':'G1','claim_ids':['C']},{'group_id':'G2','claim_ids':['D']}]}
    m={'version':'claim_flash_saved_flow_v1','source_id':'S','grouping':save(tmp_path,'plan.json',plan),
       'source':save(tmp_path,'source.json',{'source_id':'S','source_body_sha256':'BODY','status':'verified'}),
       'evidence':save(tmp_path,'evidence.json',{'source_id':'S','status':'completed','groups':[
           {'group_id':gid,'status':'verified','pending':[],'updates':{}} for gid in ['G1','G2']]}),
       'claims':{},'holds':[]}
    for gid,cid in [('G1','C'),('G2','D')]:
        m['claims'][gid]={name:save(tmp_path,gid+'/'+name+'.json',{'source_id':'S','claim_ids':[cid],
            'completion_scope':scope,'status':'verified','pending':[],'updates':{}})
            for name,scope in progress.CLAIM_STEPS.items()}
    return m


def amend(reference,**changes):
    value=json.loads(Path(reference['path']).read_text());value.update(changes)
    return save(Path(reference['path']).parent,Path(reference['path']).name,value)


def test_completed_layers_are_skipped_and_inputs_unchanged(manifest):
    paths=[manifest['grouping'],manifest['source'],manifest['evidence']]+[p for steps in manifest['claims'].values() for p in steps.values()]
    before={p['path']:Path(p['path']).read_bytes() for p in paths}
    result=progress.inspect(manifest)
    assert result['execution_order']==['grouping','source','evidence_observation','claims']
    assert result['next_action']['layer'] is None
    assert all(s['action']=='skip' for g in result['claim_groups'] for s in g['steps'])
    assert result['completed_existing_claim_checks']==['C','D']
    assert before=={p:Path(p).read_bytes() for p in before}
    assert result['model_calls']==result['database_writes']==0


def test_source_must_finish_before_later_layers(manifest):
    manifest['source']=amend(manifest['source'],status='needs_attention')
    result=progress.inspect(manifest)
    assert result['next_action']['layer']=='source'
    assert result['completed_existing_claim_checks']==['C','D'] # Historical results are retained, not repeated.


def test_whole_evidence_layer_before_claims(manifest):
    manifest['evidence']=amend(manifest['evidence'],groups=[{'group_id':'G1','status':'verified'}])
    result=progress.inspect(manifest)
    assert result['next_action']=={'layer':'evidence_observation','group_ids':['G2'],'reason':'Evidence groups not completed'}
    assert all(s['action']=='skip' for s in result['claim_groups'][0]['steps'])


def test_held_new_card_does_not_repeat_completed_original_checks(manifest):
    manifest['claims']['G1']['missing']=amend(manifest['claims']['G1']['missing'],new_claim_ids=['NEW'],applied=True,status='applied')
    manifest['holds']=[{'group_id':'G1','claim_id':'NEW','reason':'Owner holds grain decision'}]
    result=progress.inspect(manifest);steps=result['claim_groups'][0]['steps']
    assert [s['action'] for s in steps]==['skip','skip','skip','hold']
    assert result['completed_existing_claim_checks']==['C','D']
    assert result['next_action']['reason']=='only_holds_remain' and result['holds']==manifest['holds']
    assert json.loads(Path(manifest['claims']['G1']['missing']['path']).read_text())['status']=='applied'


def test_saved_pending_source_handoff_returns_to_source(manifest):
    manifest['evidence']=amend(manifest['evidence'],status='source_clarification_required',
        completed_groups_in_this_scope=['G1'],previously_completed_group_preserved=None,
        total_completed_sermon_groups=1,sermon_group_count=2,held_group='G2',hold_reason='Two source versions differ')
    result=progress.inspect(manifest)
    assert result['next_action']['layer']=='source' and result['next_action']['group_ids']==['G2']


def test_unapplied_preview_is_not_completion(manifest):
    manifest['claims']['G1']['arguments']=amend(manifest['claims']['G1']['arguments'],updates={'knowledge_relations/R':{}},applied=False)
    result=progress.inspect(manifest)
    assert result['claim_groups'][0]['steps'][1]['state']=='unfinished'
    assert result['next_action']['layer']=='claims'
    assert result['next_action']['remaining_steps'][0]['action']=='resume_saved_stage'


@pytest.mark.parametrize('defect',['tamper','source','scope','claim_ids','duplicate','unknown_hold'])
def test_invalid_bindings_or_scope_rejected(manifest,defect):
    if defect=='tamper':Path(manifest['source']['path']).write_text('{}')
    elif defect=='source':manifest['source']=amend(manifest['source'],source_body_sha256='OTHER')
    elif defect in {'scope','claim_ids'}:
        manifest['claims']['G1']['statements']=amend(manifest['claims']['G1']['statements'],
            **({'completion_scope':'OTHER'} if defect=='scope' else {'claim_ids':['D']}))
    elif defect=='duplicate':manifest['grouping']=amend(manifest['grouping'],groups=[{'group_id':'G1','claim_ids':['C']},{'group_id':'G2','claim_ids':['C']}])
    else:manifest['holds']=[{'group_id':'G1','claim_id':'UNKNOWN','reason':'Invalid hold'}]
    with pytest.raises(ValueError):progress.inspect(manifest)
