from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.pipeline import viewpoint_saved_audit_certificate as a
from backend.pipeline.source_reconciliation_runner import seal,binding
from backend.pipeline.viewpoint_resolution_runtime import write_immutable
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def audit():
    return {'layers':{str(i):{'complete':True,'population':1,'sampled':1,'judged':1,
        'disputed':0,'blocked':0,'model_errors':0,'no_source_text':0,
        'results':[{field:'target','verdict':verdict,'paragraphs_dropped_to_pass_filter':0}]}
        for i,field,verdict in ((3,'claim_id','supported'),(4,'viewpoint_id','same'))}}


def test_complete_exact_original_audit_is_not_arbitrated_or_relabelled():
    value=audit();original=deepcopy(value);a._reports(value,['target'],['target']);assert value==original


@pytest.mark.parametrize('change',['missing','extra','duplicate','blocked','error','disputed','no_source',
    'shortened','sampled','judged','population','incomplete','unknown_verdict'])
def test_incomplete_or_semantic_objection_never_auto_promoted_to_pass(change):
    value=audit();report=value['layers']['3']
    if change=='missing':report['results']=[]
    elif change=='extra':report['results'].append({'claim_id':'other','verdict':'supported'})
    elif change=='duplicate':report['results']*=2
    elif change in {'blocked','error','disputed','no_source'}:report[{'error':'model_errors','no_source':'no_source_text'}.get(change,change)]=1
    elif change=='shortened':report['results'][0]['paragraphs_dropped_to_pass_filter']=1
    elif change in {'sampled','judged','population'}:report[change]=0
    elif change=='incomplete':report['complete']=False
    else:report['results'][0]['verdict']='new_pass_alias'
    with pytest.raises(ValueError):a._reports(value,['target'],['target'])


@pytest.fixture
def certificate(tmp_path,monkeypatch):
    paths={name:tmp_path/(name+'.json') for name in ('code','audit','source','cache3','cache4')}
    for name,path in paths.items():write_immutable(path,{'fixture':name})
    pins={name:binding(path) for name,path in paths.items()}
    state={'rows':{'source_documents':{'S':{'retired':False,'payload':{'source_path':str(paths['source'])}}}}}
    declaration={'audit':pins['audit'],'claim_ids':['C'],'viewpoint_ids':['V'],'data_directory':str(tmp_path),'cache_directory':str(tmp_path)}
    value={'schema_version':a.SCHEMA,'status':'saved_model_source_inputs_verified_not_group_admission',
        'declaration':declaration,'declaration_sha256':sha256_json(declaration),
        'current_state':state,'current_state_sha256':sha256_json(state),'source_bindings':{'S':pins['source']},
        'runtime_identity':{'python':a.sys.version.split()[0]},'requests':[
            {'layer':'3','target_id':'C','cache':pins['cache3']},{'layer':'4','target_id':'V','cache':pins['cache4']}],
        'dependencies':list(pins.values()),'scoped_model_layers_complete':True,
        'global_mechanical_layers_verified':False,'sql_admission_granted':False,'provider_calls':0,'registry_writes':0}
    calls=[]
    class Frame:
        def __init__(self,*args):calls.append('current bounded SQL');self.metrics={'rows':1}
        def state(self):return deepcopy(state)
        def assert_unchanged(self):calls.append('closing bounded SQL');return {'rows':1}
    monkeypatch.setattr(a,'CODE',(paths['code'],))
    monkeypatch.setattr(a,'module',lambda *args:SimpleNamespace(AuditReadset=Frame))
    return value,declaration,calls,paths


def publish(tmp_path,value):
    path=tmp_path/'certificate.json';write_immutable(path,seal(value));return binding(path)


def test_current_consumer_never_rebuilds_prompts_or_claims_global_admission(tmp_path,certificate):
    value,declaration,calls,_=certificate
    result,metrics=a.consume_saved_audit(publish(tmp_path,value),declaration=declaration,database_url='fixture')
    assert calls==['current bounded SQL','closing bounded SQL']
    assert metrics['files']['json_parses']==1
    assert not result['sql_admission_granted'] and not result['global_mechanical_layers_verified']


@pytest.mark.parametrize('drift',['source','cache','code','audit','missing_source','wrong_source_path','requests',
    'dependency','runtime','sql','mechanical','seal','declared_binding'])
def test_consumer_fails_before_SQL_on_file_identity_or_approval_drift(tmp_path,certificate,drift):
    value,declaration,calls,paths=certificate
    value=deepcopy(value)
    if drift in {'source','cache','code','audit'}:paths['cache3' if drift=='cache' else drift].write_text('changed')
    elif drift=='missing_source':value['source_bindings'].clear()
    elif drift=='wrong_source_path':value['source_bindings']['S']['path']=str(tmp_path/'foreign')
    elif drift=='requests':value['requests'][1]['target_id']='foreign'
    elif drift=='dependency':value['dependencies'].pop()
    elif drift=='runtime':value['runtime_identity']={'python':'old'}
    elif drift=='sql':value['sql_admission_granted']=True
    elif drift=='mechanical':value['global_mechanical_layers_verified']=True
    elif drift=='declared_binding':value['dependencies'][1]['file_sha256']='0'*64
    pin=publish(tmp_path,value)
    if drift=='seal':
        import json
        path=Path(pin['path']);raw=json.loads(path.read_text());raw['artifact_sha256']='0'*64
        path.write_text(json.dumps(raw));pin=binding(path)
    with pytest.raises(ValueError):a.consume_saved_audit(pin,declaration=declaration,database_url='fixture')
    assert calls==[]
