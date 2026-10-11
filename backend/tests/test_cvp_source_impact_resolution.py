"""Explicit current SQL readonly checks; no model or production mutations."""
from copy import deepcopy
import os
from pathlib import Path
import pytest
from backend.pipeline import cvp_source_impact_resolution as impact
from backend.api.canonical_repository.postgres_store import sha256_json

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
