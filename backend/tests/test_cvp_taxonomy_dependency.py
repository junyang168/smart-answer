from copy import deepcopy
import pytest
from backend.pipeline.cvp_taxonomy_dependency import validate_topic,verify,SCHEMA
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.source_reconciliation_runner import write
from backend.pipeline.cvp_sql_flow import binding

def topic():
    p={'topic_id':'christology','label':'基督論','revision':1,'definition':'','aliases':[],
       'legacy_ids':[],'parent_topic_id':None,'schema_version':1,'visibility':'internal','review_status':'candidate'}
    return {'revision':1,'content_sha256':record_content_sha(p),'retired':False,'payload':p}

@pytest.mark.parametrize('field,value',[('definition','content'),('parent_topic_id','parent'),('aliases',['a']),
    ('legacy_ids',['old']),('review_status','human_hold'),('unknown_id','FOREIGN')])
def test_no_content_hold_or_unknown_reference(field,value):
    row=topic();row['payload'][field]=value;row['content_sha256']=record_content_sha(row['payload'])
    with pytest.raises(ValueError):validate_topic('christology',row)

def test_exact_program_receipt_never_changes_model_report(tmp_path):
    audit=tmp_path/'audit.json';write(audit,{'saved_model_rejection':'unchanged'});before=audit.read_bytes()
    row=topic();key=('topic_nodes','christology');receipt=tmp_path/'receipt.json'
    r={'schema_version':SCHEMA,'run_id':'run','audit':binding(audit),'model_calls':0,'content_approval':False,
       'records':[{'collection':key[0],'object_id':key[1],**row}]};write(receipt,r)
    assert verify(receipt,'run',binding(audit),{key:row},{})=={key:(1,row['content_sha256'],False)}
    assert audit.read_bytes()==before
    for mutate in ('stale','extra','foreign','approval'):
        bad=deepcopy(r);required={key:deepcopy(row)}
        if mutate=='stale':required[key]['revision']=2
        if mutate=='extra':bad['records'].append(bad['records'][0])
        if mutate=='foreign':bad['run_id']='other'
        if mutate=='approval':bad['content_approval']=True
        bad_path=tmp_path/(mutate+'.json');write(bad_path,bad)
        with pytest.raises(ValueError):verify(bad_path,'run',binding(audit),required,{})
