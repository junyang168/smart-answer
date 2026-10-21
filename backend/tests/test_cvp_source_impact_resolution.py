"""Explicit current SQL readonly checks; no model or production mutations."""
from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import cvp_source_impact_resolution as impact
from backend.api.canonical_repository.postgres_store import sha256_json

@pytest.mark.parametrize('mode',[impact.PARAGRAPH_MODE,'policy_claim_source_revision','policy_evidence_binding','foreign'])
@pytest.mark.parametrize('collection',['canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links','questions','knowledge_relations'])
def test_source_pending_reader_preserves_exact_mode_domain(monkeypatch,mode,collection):
    cursor=object();source={'complete':'original'};handoff={'source_slice_mode':mode}
    key=collection+'/original';calls=[]
    def paragraph(c,k,s,h):
        assert (c,k,s,h)==(cursor,key,source,handoff)
        calls.append('paragraph');return {'kind':'pending_not_approval'}
    def question(c,k):
        assert (c,k)==(cursor,key);calls.append('question');return {'kind':'unresolved'}
    def policy(c,k):
        assert (c,k)==(cursor,key);calls.append('policy')
        if collection not in {'claims','claim_relations','knowledge_relations'}:
            raise ValueError('policy relation context foreign domain')
        return {'kind':'internal_not_approval'}
    monkeypatch.setattr(impact,'read_paragraph_cvp_context',paragraph)
    monkeypatch.setattr(impact,'read_question_context',question)
    monkeypatch.setattr(impact,'read_policy_slice_item',policy)
    protected=deepcopy((source,handoff))
    cvp=collection in {'canonical_viewpoints','viewpoint_revisions','viewpoint_claim_links'}
    if cvp and mode!=impact.PARAGRAPH_MODE:
        with pytest.raises(ValueError,match='foreign domain'):
            impact.read_source_pending_item(cursor,key,source,handoff)
    else:
        result=impact.read_source_pending_item(cursor,key,source,handoff)
        assert result['kind'] in {'pending_not_approval','unresolved','internal_not_approval'}
    assert calls==['paragraph' if cvp and mode==impact.PARAGRAPH_MODE else 'question' if collection=='questions' else 'policy']
    assert (source,handoff)==protected

@pytest.mark.parametrize('fault',[None,'pin','permission','kind'])
def test_actual_completed_paragraph_revalidation_is_readonly(monkeypatch,fault):
    run=os.environ.get('WKP_COMPLETED_PARAGRAPH_ACTUAL')
    if not run:pytest.skip('explicit saved completed paragraph SQL proof')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    store=PostgresKnowledgeStore();value=store.get_cvp_group(run);original=deepcopy(value)
    old=read_artifact(value['completion']['retained_impacts_resolution']['path'])
    assert old['source_handoff']['source_slice_mode']==impact.PARAGRAPH_MODE
    reader=impact.read_source_pending_item
    def changed(*args):
        result=deepcopy(reader(*args))
        if fault=='pin':result['pin'][1]='foreign'
        elif fault=='permission':result['content_write_authority']=True
        elif fault=='kind':result['kind']='approved_candidate'
        return result
    monkeypatch.setattr(impact,'read_source_pending_item',changed)
    if fault:
        with pytest.raises(ValueError,match='pending context/approval/consumer drift'):
            impact.prepare(store,value)
    else:
        facts=impact.prepare(store,value)
        assert facts['items']==old['items']
        assert facts['pending_record_keys']==old['pending_record_keys']
        assert facts['prior_completed_resolution']==value['completion']['retained_impacts_resolution']
        assert facts['model_verdicts_changed'] is False
        assert facts['original_pending_list_preserved'] is True
    assert store.get_cvp_group(run)==original and value==original

def test_audit_root_domains_do_not_discard_obligations():
    keys=['groups/declared','knowledge_relations/retired']
    facts={'pending_record_keys':keys,'items':[{'record_key':keys[0],'kind':'declared_source_group_not_cvp'},
                                              {'record_key':keys[1],'kind':'exact_human_logical_retirement'}]}
    assert impact.audit_record_keys(keys,facts)==[]
    assert impact.audit_record_keys(['viewpoint_relations/x'],None)==['viewpoint_relations/x']
    with pytest.raises(ValueError):impact.audit_record_keys(keys[:-1],facts)
    facts['items'][1]['kind']='unknown'
    with pytest.raises(ValueError):impact.audit_record_keys(keys,facts)

@pytest.mark.parametrize('fault',[None,'mode','kind','domain','missing','duplicate'])
def test_paragraph_pending_roots_require_complete_cvp_audit(fault):
    keys=['canonical_viewpoints/V','viewpoint_revisions/R','knowledge_relations/internal']
    facts={'source_handoff':{'source_slice_mode':impact.PARAGRAPH_MODE},
           'pending_record_keys':keys.copy(),'items':[
               {'record_key':keys[0],'kind':'unchanged_cvp_context_requires_complete_current_audit'},
               {'record_key':keys[1],'kind':'unchanged_cvp_context_requires_complete_current_audit'},
               {'record_key':keys[2],'kind':'unchanged_unapproved_unconsumed_policy_relation_context'}]}
    if fault=='mode':facts['source_handoff']['source_slice_mode']='foreign'
    elif fault=='kind':facts['items'][2]['kind']='approved_candidate'
    elif fault=='domain':facts['items'][2]['kind']='unchanged_cvp_context_requires_complete_current_audit'
    elif fault=='missing':facts['items'].pop()
    elif fault=='duplicate':facts['items'][1]=deepcopy(facts['items'][0])
    old=deepcopy(facts)
    if fault:
        with pytest.raises(ValueError):impact.audit_record_keys(keys,facts)
    else:assert impact.audit_record_keys(keys,facts)==keys[:2]
    assert facts==old

@pytest.mark.parametrize('fault',[None,'used','raw','unknown','changed_intent','changed_log','foreign_auditor'])
@pytest.mark.parametrize('corrected',[[],['canonical_viewpoints/V']])
def test_constructor_recovery_never_retries_model_execution(tmp_path,fault,corrected):
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    from backend.pipeline.cvp_sql_flow import binding
    expected={'claim_ids':['C'],'automatic_retry':False}
    intent=tmp_path/'audit-intent.json';write_immutable(intent,expected)
    log=tmp_path.parent/(tmp_path.name+'-failure.log')
    log.write_text("File auditor, in __init__\nraise ValueError('independent SQL pending record is outside CVP management')\ngroups/G\n")
    proof={'schema_version':'wang_source_audit_constructor_recovery_v1','intent':binding(intent),
           'failure_log':binding(log),'auditor':binding(Path(impact.__file__).resolve().parents[2]/'scripts/audit-library.py'),
           'vendor_model_constructed':False,'original_record_keys':['groups/G'],'corrected_record_keys':corrected}
    if fault=='unknown':proof['vendor_model_constructed']=True
    if fault=='foreign_auditor':proof['auditor']=binding(log)
    proof['artifact_sha256']=sha256_json(proof)
    write_immutable(tmp_path/'source-domain-startup-recovery.json',proof)
    if fault in {'used','raw'}:write_immutable(tmp_path/(('source-domain-startup-recovery-used' if fault=='used' else 'raw')+'.json'),{})
    if fault=='changed_intent':expected={'claim_ids':['foreign']}
    if fault=='changed_log':log.write_text('timeout after vendor model construction')
    if fault:
        with pytest.raises(ValueError):impact.validate_pre_model_startup_recovery(tmp_path,expected,corrected_record_keys=corrected)
    else:
        assert impact.validate_pre_model_startup_recovery(tmp_path,expected,corrected_record_keys=corrected)==proof
        with pytest.raises(ValueError):
            impact.validate_pre_model_startup_recovery(tmp_path,expected,corrected_record_keys=['foreign/X'])

@pytest.fixture
def actual():
    run=os.environ.get('WKP_SOURCE_IMPACT_ACTUAL')
    if not run:pytest.skip('explicit actual current readonly SQL proof')
    from dotenv import load_dotenv
    load_dotenv('.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    store=PostgresKnowledgeStore()
    return store,store.get_cvp_group(run)

@pytest.mark.parametrize('fault',[None,'membership','domain','denominator','relation_sha','retirement_history','commit'])
def test_actual_prepare_exact_and_immutable(actual,monkeypatch,fault):
    store,value=actual
    root=Path(impact.context(value)['source_terminal_review']['path']).parent
    original={p:p.read_bytes() for p in root.iterdir() if p.is_file()}
    if fault=='membership':value['claim_ids'].append('foreign')
    elif fault=='domain':value['reviewed']['evidence']['pending_cvp_management_record_keys'].append('canonical_viewpoints/foreign')
    elif fault=='denominator':value['reviewed']['evidence']['pending_cvp_management_record_keys'].pop()
    elif fault=='relation_sha':
        get=store.get_record_state
        def changed(c,i):
            row=get(c,i)
            if c=='knowledge_relations':row['content_sha256']='foreign'
            return row
        monkeypatch.setattr(store,'get_record_state',changed)
    elif fault=='retirement_history':
        get=store.get_change_history
        def changed(cid):
            row=get(cid)
            for op in row['operations']:
                if op['operation']=='retire':op['after']['relation_type']='foreign'
            return row
        monkeypatch.setattr(store,'get_change_history',changed)
    elif fault=='commit':
        original_method=impact.applied_review
        def changed(s,v,h,**kw):
            r=original_method(s,v,h,**kw)
            r['plan']['change_set_id']='foreign'
            return r
        monkeypatch.setattr(impact,'applied_review',changed)
    if fault:
        with pytest.raises((ValueError,KeyError)):impact.prepare(store,value)
    else:
        facts=impact.prepare(store,value)
        assert len(facts['items'])==6 and len(facts['read_pins'])==273
        assert facts['original_pending_list_preserved'] and not facts['model_verdicts_changed']
        assert [i['kind'] for i in facts['items']].count('declared_source_group_not_cvp')==4
    assert all(p.read_bytes()==b for p,b in original.items())

@pytest.mark.parametrize('fault',[None,'missing_pin','wrong_pin','missing_binding','classification','group','pending','seal'])
def test_actual_transaction_completion_proof(actual,tmp_path,fault):
    store,value=actual
    facts=impact.prepare(store,value)
    proof={'schema_version':impact.VERSION,'run_id':value['run_id'],'reviewed_sha256':value['reviewed_sha256'],
           'audit':{'readonly_test':True},'audit_proofs':[],'semantic_resolution':None,**facts}
    if fault=='missing_pin':proof['read_pins'].pop()
    elif fault=='wrong_pin':proof['read_pins'][0]['pin'][0]+=1
    elif fault=='missing_binding':proof['bindings'].pop()
    elif fault=='classification':proof['items'][0]['kind']='independently_retained_source_relation'
    elif fault=='group':proof['items'][2]['record_key']='groups/foreign'
    elif fault=='pending':proof['pending_record_keys'].pop()
    proof['artifact_sha256']=sha256_json(proof)
    if fault=='seal':proof['artifact_sha256']='foreign'
    from backend.pipeline.viewpoint_resolution_runtime import write_immutable
    path=tmp_path/'proof.json';write_immutable(path,proof)
    with store.connect() as conn,conn.cursor() as cur:
        # The ordinary completion caller separately verifies the actual full audit.
        # This exercises only its typed source-impact proof inside the SQL transaction.
        if fault:
            with pytest.raises(ValueError):impact.validate_for_completion(value,path,proof['audit'],cur)
        else:
            impact.validate_for_completion(value,path,proof['audit'],cur)
        conn.rollback()
