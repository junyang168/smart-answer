"""Pending means must be checked, not must be mutated; never waive full audit."""
from copy import deepcopy
import json
import pytest
from backend.api.canonical_repository.postgres_store import record_content_sha,sha256_json
from backend.pipeline import cvp_retained_impact_resolution as management
from backend.pipeline.cvp_sql_flow import binding
from backend.pipeline.claim_first_management import VERSION as CLAIM_VERSION,REPROPOSAL_VERSION


@pytest.fixture
def case(tmp_path,monkeypatch):
    def save(name,body):
        p=tmp_path/name;p.write_text(json.dumps(body));return binding(p)
    before={'evidence_step_id':'E','revision':1,'review_status':'candidate','support_eligibility':'context_only',
            'claim_id':'C','source_fragment_ids':['F'],'statement':'unchanged own text'}
    after={**before,'revision':2,'review_status':'independent_terminal_reviewed',
           'support_eligibility':'eligible_with_label','source_reconciliation_review_sha256':'terminal',
           'review_note':'independent review approved this qualification'}
    op={'operation':'update','collection':'evidence_steps','before':before,'after':after,
        'before_sha256':record_content_sha(before),'after_sha256':record_content_sha(after)}
    terminal=save('terminal.json',{'artifact_sha256':'terminal'})
    proof=save('proof.json',{'saved_full_source':True})
    receipt=save('claim.json',{'schema_version':CLAIM_VERSION,'status':'claim_layer_audited_cvp_pending',
        'claim_ids':['C'],'change_set_ids':['source'],'audit':proof,'source_proofs':[proof],
        'cvp_management_complete':False,'group_complete':False,'next_group_started':False})
    human=save('human.json',{'exact_human_direction':'retain'})
    scope=save('scope.json',{'model_verdicts_changed':False,'all_pending_impacts_preserved':True,'human_disposition':human})
    payload={'viewpoint_id':'V','revision':1,'review_status':'system_approved'}
    pin=(1,record_content_sha(payload),False)
    readpins=[{'collection':'canonical_viewpoints','object_id':'V','pin':list(pin)}]
    rp=tmp_path/'claim.json';r=json.loads(rp.read_text());r['read_pins']=deepcopy(readpins);rp.write_text(json.dumps(r));receipt=binding(rp)
    context={'schema_version':REPROPOSAL_VERSION,'claim_layer_completion':receipt,
             'source_terminal_review':terminal,'source_terminal_packet':proof,
             'pending_existing_cvp_record_keys':['canonical_viewpoints/V']}
    value={'run_id':'run','claim_ids':['C'],'change_set_ids':['source','new'],'reviewed_sha256':'reviewed',
        'claim_layer_audit':{'receipt':receipt},'reviewed':{'evidence':{'approval':{'retained_write_scope':scope},
        'pending_cvp_management_record_keys':['canonical_viewpoints/V'],'viewpoint_ids':['V'],
        'action':{'materials':{'read_pins':readpins,'claim_management_resolution':context}}}}}
    audit={'meta':{'model':'gemini-3.8-flash','registry_read_scope':{'read_pins':readpins}},'layers':{
        '3':{'complete':True,'judged':1,'disputed':0,'results':[{'claim_id':'C','verdict':'supported'}]},
        '4':{'complete':True,'judged':1,'disputed':0,'results':[{'viewpoint_id':'V','verdict':'same'}]}}}
    audit_path=tmp_path/'audit.json';audit_path.write_text(json.dumps(audit))
    class Store:
        def get_cvp_group(self,_):return deepcopy(value)
        def get_change_history(self,_):return {'status':'applied','operations':[deepcopy(op)]}
        def get_record_state(self,*_):return {'revision':1,'content_sha256':pin[1],'retired':False,'payload':deepcopy(payload)}
    class Cursor:
        def execute(self,q,p):self.q=q
        def fetchone(self):return ('applied',)
        def fetchall(self):return [tuple(op[k] for k in ('operation','collection','before_sha256','after_sha256','before','after'))]
    checked=[]
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    guard=PostgresKnowledgeStore._assert_transaction_read_pins
    def fake_guard(c,p):
        checked.append(p);return {('canonical_viewpoints','V'):{'payload':deepcopy(payload)}}
    monkeypatch.setattr(PostgresKnowledgeStore,'_assert_transaction_read_pins',fake_guard)
    return locals()


def built(case):
    p=case['tmp_path']/'resolution.json'
    management.build(case['Store'](),'run',case['audit_path'],p)
    return p


def test_no_change_closes_separately_after_full_audit_and_exact_source_history(case):
    p=built(case);value=deepcopy(case['value']);before=deepcopy(value)
    result=management.validate_for_completion(value,p,binding(case['audit_path']),case['Cursor']())
    assert result['registry_mutations']==0 and result['model_verdicts_changed'] is False
    assert value==before and value['reviewed']['evidence']['pending_cvp_management_record_keys']
    assert case['checked']==[{('canonical_viewpoints','V'):case['pin']}]


@pytest.mark.parametrize('fault',['empty-write','claim-write','create','text','binding','source','sha',
    'eligibility','hold','terminal','missing-before','not-applied','missing-scope','wrong-receipt',
    'foreign-source-kcs','completed-claim','pending-denominator','disputed','incomplete','model',
    'missing-audit-pin','wrong-audit-pin','retired','current-drift','fake-decisions'])
def test_builder_rejects_content_change_missing_proof_or_incomplete_audit(case,fault):
    v,op,a=case['value'],case['op'],case['audit']
    if fault=='empty-write':case['Store'].get_change_history=lambda *_:{'status':'applied','operations':[]}
    elif fault=='claim-write':op['collection']='claims'
    elif fault=='create':op['operation']='create'
    elif fault=='text':op['after']['statement']='different'
    elif fault=='binding':op['after']['source_fragment_ids']=['other']
    elif fault=='source':op['after']['claim_id']='other'
    elif fault=='sha':op['after_sha256']='bad'
    elif fault=='eligibility':op['after']['support_eligibility']='context_only'
    elif fault=='hold':op['before']['review_status']='human_review_required'
    elif fault=='terminal':op['after']['source_reconciliation_review_sha256']='other'
    elif fault=='missing-before':op['before']=None
    elif fault=='not-applied':case['Store'].get_change_history=lambda *_:{'status':'prepared','operations':[op]}
    elif fault=='missing-scope':v['reviewed']['evidence']['approval']={}
    elif fault=='wrong-receipt':v['claim_layer_audit']['receipt']=case['proof']
    elif fault=='foreign-source-kcs':v['change_set_ids']=['other']
    elif fault=='completed-claim':
        p=case['tmp_path']/'claim.json';r=json.loads(p.read_text());r['group_complete']=True;p.write_text(json.dumps(r))
        v['claim_layer_audit']['receipt']=binding(p);v['reviewed']['evidence']['action']['materials']['claim_management_resolution']['claim_layer_completion']=binding(p)
    elif fault=='pending-denominator':v['reviewed']['evidence']['pending_cvp_management_record_keys']+=['canonical_viewpoints/other']
    elif fault=='disputed':a['layers']['4']['disputed']=1;a['layers']['4']['results'][0]['verdict']='disputed'
    elif fault=='incomplete':a['layers']['4']['complete']=False
    elif fault=='model':a['meta']['model']='other'
    elif fault=='missing-audit-pin':a['meta']['registry_read_scope']['read_pins']=[]
    elif fault=='wrong-audit-pin':a['meta']['registry_read_scope']['read_pins'][0]['pin'][0]=2
    elif fault=='retired':case['Store'].get_record_state=lambda *_:{'retired':True,'revision':1,'content_sha256':case['pin'][1],'payload':case['payload']}
    elif fault=='current-drift':case['payload']['viewpoint_id']='other'
    elif fault=='fake-decisions':
        p=case['tmp_path']/'scope.json';r=json.loads(p.read_text());r['model_verdicts_changed']=True;p.write_text(json.dumps(r));v['reviewed']['evidence']['approval']['retained_write_scope']=binding(p)
    if fault not in {'sha','missing-before'}:
        op['before_sha256']=record_content_sha(op['before']);op['after_sha256']=record_content_sha(op['after'])
    case['audit_path'].write_text(json.dumps(a))
    with pytest.raises(ValueError):built(case)


@pytest.mark.parametrize('fault',['run','reviewed','omit','pin','duplicate-pin','claim-receipt','human',
    'audit','scope','mutations','calls','decisions','semantic','source-sql-drift','source-sql-uncommitted'])
def test_even_resealed_standalone_completion_cannot_bypass_immutable_sql_authority(case,fault):
    p=built(case);body=json.loads(p.read_text());v=case['value']
    if fault=='run':body['run_id']='other'
    elif fault=='reviewed':body['reviewed_sha256']='other'
    elif fault=='omit':body['pending_record_keys']=[];body['read_pins']=[]
    elif fault=='pin':body['read_pins'][0]['pin'][0]=2
    elif fault=='duplicate-pin':body['read_pins']*=2
    elif fault=='claim-receipt':body['claim_layer_completion']=case['proof']
    elif fault=='human':body['human_disposition']=case['proof']
    elif fault=='audit':body['audit']=case['proof']
    elif fault=='scope':body['scope_authority']=case['proof']
    elif fault=='mutations':body['registry_mutations']=1
    elif fault=='calls':body['new_model_calls']=1
    elif fault=='decisions':body['model_verdicts_changed']=True
    elif fault=='semantic':body['semantic_resolution']=case['proof']
    elif fault=='source-sql-drift':case['op']['after']['statement']='changed'
    elif fault=='source-sql-uncommitted':case['Cursor'].fetchone=lambda _:('prepared',)
    body['artifact_sha256']=sha256_json({k:r for k,r in body.items() if k!='artifact_sha256'});p.write_text(json.dumps(body))
    with pytest.raises(ValueError):management.validate_for_completion(v,p,binding(case['audit_path']),case['Cursor']())
    assert case['checked']==[]


def test_human_no_change_blocks_demotion_and_any_storage_update(case):
    from types import SimpleNamespace
    c=case;v=c['value'];hp=c['tmp_path']/'human.json';sp=c['tmp_path']/'scope.json'
    hp.write_text(json.dumps({'group_id':'run','human_exact_disposition':True,
        'decision':'retain_existing_contextual_equivalence_unchanged','items':[{'link_id':'L'}]}))
    sp.write_text(json.dumps({'human_disposition':binding(hp),'human_retained_link_ids':['L']}))
    v['reviewed']['evidence']['approval']['retained_write_scope']=binding(sp)
    assert management.protected_link_ids(v)=={'L'}
    management.guard_operations(v,[SimpleNamespace(collection='viewpoint_claim_links',object_id='new')])
    with pytest.raises(ValueError,match='NO-CHANGE'):
        management.guard_operations(v,[SimpleNamespace(collection='viewpoint_claim_links',object_id='L')])


from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


def test_real_sql_history_current_pins_and_late_rollback(case,postgres_store,monkeypatch):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore,canonical_json,ChangeSetConflict
    c=case;s=postgres_store;p=built(c)
    monkeypatch.setattr(PostgresKnowledgeStore,'_assert_transaction_read_pins',c['guard'])
    with s.connect() as conn:
        for cid in ('before','source'):
            conn.execute("INSERT INTO wang_knowledge.change_sets(change_set_id,fingerprint_sha256,package_id,source_kind,source_sha256,status) VALUES (%s,%s,'test','source_claim_revision',%s,'applied')",(cid,sha256_json(cid),'a'*64))
        for rev,name,cid in ((1,'before','before'),(2,'after','source')):
            row=c['op'][name]
            conn.execute("INSERT INTO wang_knowledge.object_versions(collection,object_id,revision,content_sha256,payload,change_set_id) VALUES ('evidence_steps','E',%s,%s,%s::jsonb,%s)",(rev,record_content_sha(row),canonical_json(row),cid))
        conn.execute("INSERT INTO wang_knowledge.change_operations(change_set_id,operation_index,operation,collection,object_id,before_sha256,after_sha256,before_revision,after_revision) VALUES ('source',0,'update','evidence_steps','E',%s,%s,1,2)",(c['op']['before_sha256'],c['op']['after_sha256']))
        conn.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,review_status,content_sha256,payload) VALUES ('canonical_viewpoints','V',1,'system_approved',%s,%s::jsonb)",(c['pin'][1],canonical_json(c['payload'])))
    before=s.get_record_state('canonical_viewpoints','V')
    with s.connect() as conn,conn.cursor() as cursor:
        management.validate_for_completion(c['value'],p,binding(c['audit_path']),cursor)
    assert s.get_record_state('canonical_viewpoints','V')==before
    with pytest.raises(ValueError,match='late validator'):
        with s.connect() as conn,conn.cursor() as cursor:
            management.validate_for_completion(c['value'],p,binding(c['audit_path']),cursor)
            cursor.execute("UPDATE wang_knowledge.objects SET visibility='changed' WHERE object_id='V'")
            raise ValueError('late validator failure')
    assert s.get_record_state('canonical_viewpoints','V')==before
    with s.connect() as conn:conn.execute("UPDATE wang_knowledge.objects SET revision=2 WHERE object_id='V'")
    with pytest.raises(ChangeSetConflict,match='dependency drift'):
        with s.connect() as conn,conn.cursor() as cursor:
            management.validate_for_completion(c['value'],p,binding(c['audit_path']),cursor)
