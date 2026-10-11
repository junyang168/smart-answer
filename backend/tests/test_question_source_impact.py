from copy import deepcopy
from pathlib import Path
import os
import pytest
from backend.pipeline import cvp_source_impact_resolution as flow
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json


def material():
    p={'schema_version':1,'question_id':'Q','text':'Question?','revision':1,'questioner':'professor',
       'question_type':'question','visibility':'internal','answer_state':'answered','review_status':'candidate',
       'answer_claim_ids':['C'],'source_fragment_id':None,'source_fragment_ids':['F']}
    row={'payload':p,'revision':1,'content_sha256':record_content_sha(p),'retired':False}
    creation={'source_kind':'knowledge_package','status':'applied','operation':'create','before_revision':None,
       'after_revision':1,'after':deepcopy(p),'after_sha256':row['content_sha256'],'change_set_id':'K'}
    return row,creation


@pytest.mark.parametrize('fault',[None,'approved','held','public','retired','revision','sha','extra','semantic',
    'history','uncommitted','update','before','consumer','review','edge','foreign'])
def test_question_context_is_not_content_approval(fault):
    row,creation=material();refs=[];reviews=[];edges=[];key='questions/Q'
    if fault=='approved':row['payload']['review_status']='system_approved'
    elif fault=='held':row['payload']['review_status']='human_hold'
    elif fault=='public':row['payload']['visibility']='public'
    elif fault=='retired':row['retired']=True
    elif fault=='revision':row['revision']=2
    elif fault=='sha':row['content_sha256']='foreign'
    elif fault=='extra':row['payload']['unknown_reference_id']='X'
    elif fault=='semantic':row['payload']['text']='Different question'
    elif fault=='history':creation['after_sha256']='foreign'
    elif fault=='uncommitted':creation['status']='reviewed'
    elif fault=='update':creation['operation']='update'
    elif fault=='before':creation['before_revision']=0
    elif fault=='consumer':refs=['C']
    elif fault=='review':reviews=['R']
    elif fault=='edge':edges=['E']
    elif fault=='foreign':key='claims/Q'
    if fault:
        with pytest.raises(ValueError):flow.question_context_item(key,row,creation,references=refs,reviews=reviews,edges=edges)
    else:
        result=flow.question_context_item(key,row,creation,references=refs,reviews=reviews,edges=edges)
        assert not result['semantic_judgment_resolved'] and not result['content_write_authority']
        assert result['pending_obligation_preserved'] and not result['approved_dependency']
        assert flow.audit_record_keys([key],{'pending_record_keys':[key],'items':[result]})==[]


@pytest.mark.skipif(not os.environ.get('WKP_QUESTION_IMPACT_ACTUAL'),reason='explicit current saved readonly SQL')
def test_actual_source_question_facts_transaction_and_immutable(tmp_path,monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    load_dotenv('.env');store=PostgresKnowledgeStore();value=store.get_cvp_group(os.environ['WKP_QUESTION_IMPACT_ACTUAL'])
    before=deepcopy(value);handoff=flow.context(value);root=Path(handoff['source_terminal_review']['path']).parent
    files={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    facts=flow.prepare(store,value)
    assert len(facts['items'])==1 and facts['items'][0]['semantic_judgment_resolved'] is False
    assert flow.audit_record_keys(facts['pending_record_keys'],facts)==[]
    # Exercise completion-domain validation in a transaction. The caller's
    # full independent audit/semantic/source gates remain separate and strict.
    audit=tmp_path/'audit.json';write_immutable(audit,{'meta':{'registry_read_scope':{'read_pins':[]}}})
    from backend.pipeline.cvp_sql_flow import binding
    proof={'schema_version':flow.VERSION,'run_id':value['run_id'],'reviewed_sha256':value['reviewed_sha256'],
        'audit':binding(audit),'audit_proofs':[],'semantic_resolution':None,**facts}
    proof['artifact_sha256']=sha256_json(proof);path=tmp_path/'proof.json';write_immutable(path,proof)
    with store.connect() as conn,conn.cursor() as cur:
        flow.validate_for_completion(value,path,proof['audit'],cur)
        conn.rollback()
    for fault in ('pin','kind','binding','population','seal'):
        changed=deepcopy(proof)
        if fault=='pin':changed['read_pins'][0]['pin'][0]+=1
        elif fault=='kind':changed['items'][0]['kind']='independently_retained_source_relation'
        elif fault=='binding':changed['bindings'].pop()
        elif fault=='population':changed['pending_record_keys']=[]
        changed['artifact_sha256']=sha256_json({k:v for k,v in changed.items() if k!='artifact_sha256'})
        if fault=='seal':changed['artifact_sha256']='wrong'
        target=tmp_path/(fault+'.json');write_immutable(target,changed)
        with store.connect() as conn,conn.cursor() as cur:
            with pytest.raises(ValueError):flow.validate_for_completion(value,target,proof['audit'],cur)
            conn.rollback()
    assert store.get_cvp_group(value['run_id'])==before
    assert all(p.read_bytes()==b for p,b in files.items())
