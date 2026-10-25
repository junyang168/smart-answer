from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import cvp_source_impact_resolution as flow
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json


@pytest.mark.parametrize('fault',[None,'missing','held','candidate','retired','public','revision','sha','unreviewed','system','conflict','duplicate'])
def test_readonly_claim_context_requires_unique_current_independent_approval(fault):
    p={'schema_version':1,'claim_id':'C','statement':'Original Claim','claim_type':'explicit_claim',
       'revision':2,'visibility':'internal','review_status':'ai_consensus_reviewed'}
    revision=2;sha=record_content_sha(p);retired=False;reviews=[('ai','ai_consensus_reviewed')]
    if fault=='held':p['review_status']='human_hold'
    elif fault=='candidate':p['review_status']='candidate'
    elif fault=='retired':retired=True
    elif fault=='public':p['visibility']='public'
    elif fault=='revision':revision=3
    elif fault=='sha':sha='wrong'
    elif fault=='unreviewed':reviews=[]
    elif fault=='system':reviews=[('system','ai_consensus_reviewed')]
    elif fault=='conflict':reviews+=[('human','rejected')]
    elif fault=='duplicate':reviews*=2
    class Cursor:
        def execute(self,sql,args):
            self.sql=sql;return self
        def fetchone(self):return None if fault=='missing' else (p,revision,sha,retired)
        def fetchall(self):return reviews
    if fault:
        with pytest.raises(ValueError):flow.read_policy_slice_item(Cursor(),'claims/C')
    else:
        item=flow.read_policy_slice_item(Cursor(),'claims/C')
        assert not item['original_annotation_resolved'] and not item['content_write_authority']
        assert item['pending_obligation_preserved']


@pytest.mark.parametrize('col',['knowledge_relations','claim_relations'])
@pytest.mark.parametrize('fault',[None,'approved','held','public','retired','revision','sha','extra','changed','history','uncommitted','update','before','consumer','review','edge','foreign'])
def test_original_candidate_relation_is_context_not_approval(col,fault):
    field='relation_id' if col=='knowledge_relations' else 'claim_relation_id'
    p={'schema_version':1,field:'R','from_id':'A','to_id':'B','relation_type':'refutes',
       'reason':'Original unresolved inference','review_status':'candidate','visibility':'internal','revision':1}
    row={'payload':p,'revision':1,'content_sha256':record_content_sha(p),'retired':False}
    creation={'change_set_id':'K','source_kind':'knowledge_package','status':'applied','operation':'create',
        'before_revision':None,'after_revision':1,'after':deepcopy(p),'after_sha256':row['content_sha256']}
    edge={'payload':deepcopy(p),'review_status':'candidate','revision':1,'retired':False}
    refs=[];reviews=[];key=col+'/R'
    if fault=='approved':p['review_status']='system_approved'
    elif fault=='held':p['review_status']='human_hold'
    elif fault=='public':p['visibility']='public'
    elif fault=='retired':row['retired']=True
    elif fault=='revision':row['revision']=2
    elif fault=='sha':row['content_sha256']='wrong'
    elif fault=='extra':p['unknown_reference_id']='X'
    elif fault=='changed':p['reason']='Other inference'
    elif fault=='history':creation['after_sha256']='wrong'
    elif fault=='uncommitted':creation['status']='reviewed'
    elif fault=='update':creation['operation']='update'
    elif fault=='before':creation['before_revision']=0
    elif fault=='consumer':refs=['C']
    elif fault=='review':reviews=['V']
    elif fault=='edge':edge['review_status']='system_approved'
    elif fault=='foreign':key='questions/R'
    if fault:
        with pytest.raises(ValueError):flow.policy_relation_context_item(key,row,creation,references=refs,reviews=reviews,edge=edge)
    else:
        item=flow.policy_relation_context_item(key,row,creation,references=refs,reviews=reviews,edge=edge)
        assert not item['semantic_judgment_resolved'] and not item['approved_dependency']
        assert not item['content_write_authority'] and item['pending_obligation_preserved']
        assert flow.audit_record_keys([key],{'pending_record_keys':[key],'items':[item]})==[]


@pytest.mark.skipif(not os.environ.get('WKP_POLICY_CLAIM_IMPACT_ACTUAL'),reason='explicit actual current readonly SQL')
def test_actual_policy_pending_transaction_scope_and_immutability(tmp_path):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.cvp_sql_flow import binding
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    load_dotenv('.env');store=PostgresKnowledgeStore();value=store.get_cvp_group(os.environ['WKP_POLICY_CLAIM_IMPACT_ACTUAL'])
    before=deepcopy(value);handoff=flow.context(value,store)
    root=Path(handoff['source_terminal_review']['path']).parent
    files={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    facts=flow.prepare(store,value)
    assert len(facts['items'])==len(facts['pending_record_keys'])==4
    assert {i['kind'] for i in facts['items']}=={'unchanged_independently_approved_readonly_claim_context','unchanged_unapproved_unconsumed_policy_relation_context'}
    assert flow.audit_record_keys(facts['pending_record_keys'],facts)==[]
    pins=[{'collection':'claims','object_id':i['record_key'].split('/',1)[1],'pin':i['pin']}
          for i in facts['items'] if i['record_key'].startswith('claims/')]
    audit=tmp_path/'audit.json';write_immutable(audit,{'meta':{'registry_read_scope':{'read_pins':pins}}})
    proof={'schema_version':flow.VERSION,'run_id':value['run_id'],'reviewed_sha256':value['reviewed_sha256'],
        'audit':binding(audit),'audit_proofs':[],'semantic_resolution':None,**facts}
    proof['artifact_sha256']=sha256_json(proof);path=tmp_path/'proof.json';write_immutable(path,proof)
    with store.connect() as conn,conn.cursor() as cur:
        flow.validate_for_completion(value,path,proof['audit'],cur);conn.rollback()
    for fault in ('pin','kind','binding','population','seal','claim_audit','relation_audit'):
        changed=deepcopy(proof)
        if fault=='pin':changed['read_pins'][0]['pin'][0]+=1
        elif fault=='kind':changed['items'][0]['kind']='independently_retained_source_relation'
        elif fault=='binding':changed['bindings'].pop()
        elif fault=='population':changed['pending_record_keys']=[]
        elif fault in {'claim_audit','relation_audit'}:
            ap=tmp_path/(fault+'-audit.json');pp=[] if fault=='claim_audit' else pins+[{'collection':'claim_relations','object_id':facts['items'][0]['record_key'].split('/',1)[1],'pin':facts['items'][0]['pin']}]
            write_immutable(ap,{'meta':{'registry_read_scope':{'read_pins':pp}}});changed['audit']=binding(ap)
        changed['artifact_sha256']=sha256_json({k:v for k,v in changed.items() if k!='artifact_sha256'})
        if fault=='seal':changed['artifact_sha256']='wrong'
        target=tmp_path/(fault+'.json');write_immutable(target,changed)
        with store.connect() as conn,conn.cursor() as cur:
            with pytest.raises(ValueError):flow.validate_for_completion(value,target,changed['audit'],cur)
            conn.rollback()
    assert store.get_cvp_group(value['run_id'])==before
    assert all(p.read_bytes()==b for p,b in files.items())
