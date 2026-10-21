"""Opt-in real saved-data acceptance; live DB is strictly read-only.

Only the socket-only disposable PostgreSQL receives copied history/run metadata.
No model, content compiler, apply_plan or legacy recovery is invoked.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import hashlib
import importlib.util
import random

import pytest
from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore,sha256_json,ChangeSetConflict
from backend.pipeline.cvp_sql_admission import admit
from backend.pipeline.cvp_sql_flow import PROJECT
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store


pytestmark=pytest.mark.skipif(os.environ.get('WKP_CVP_REAL_ACCEPTANCE')!='1',reason='explicit real saved-data acceptance only')


def test_original_57_history_and_cold_g058_without_historical_reads(postgres_store,tmp_path,monkeypatch):
    load_dotenv(PROJECT/'.env')
    result=json.loads((PROJECT/'.local-cvp-transaction-validation/admission-preview-semantic-index.json').read_text())
    assert result['artifact_sha256']==sha256_json({k:v for k,v in result.items() if k!='artifact_sha256'})
    assert len(result['definition']['groups'])==156
    assert sum(len(g['claim_ids']) for g in result['definition']['groups'])==973
    completed=result['completions']; assert len(completed)==57
    assert sum(len(r['certificate'].get('semantic_adjudications',{})) for r in completed)==19
    assert sum(len(g['group']['claim_ids']) for g in completed)==321
    assert {h['change_set_id']:h['operation_count'] for h in completed[-1]['certificate']['history']}=={
        'KCS-1b2850d74d71c6824299':59,'KCS-f5d3969df38f0adb9831':6,
        'KCS-ccb6f76a11c679a5f29f':3,'KCS-107d076eb4632d9ecbdb':9}
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            conn=super().connect();conn.execute('SET TRANSACTION READ ONLY');return conn
    live=ReadOnly();target=postgres_store
    ids=sorted({cid for item in completed for cid in item['certificate']['change_set_ids']})
    next_ids=result['definition']['groups'][57]['claim_ids']
    records=live.read_cvp_context(next_ids)
    # Copy the complete actual g057 graph as well, for the new independent
    # completion gate. No old before/frozen/readback file is parsed.
    records.update(live.read_cvp_context(completed[-1]['group']['claim_ids'],
                                       viewpoint_ids=completed[-1]['certificate']['viewpoint_ids']))
    from psycopg.sql import SQL,Identifier,Placeholder
    from psycopg.types.json import Jsonb
    with live.connect() as source,target.connect() as dest:
        heads=source.execute("""SELECT v.object_id,r.object_id FROM wang_knowledge.objects v
                               JOIN wang_knowledge.objects r ON r.collection='viewpoint_revisions'
                               AND r.object_id=v.payload->>'current_revision_id'
                               WHERE v.collection='canonical_viewpoints' AND v.retired_at IS NULL""").fetchall()
        object_ids=sorted({oid for _,oid in records}|{oid for row in heads for oid in row})
        # Include the before-version producers solely for genuine history joins.
        extra=source.execute("""SELECT DISTINCT v.change_set_id FROM wang_knowledge.object_versions v
            JOIN wang_knowledge.change_operations o ON (v.collection,v.object_id,v.revision)=(o.collection,o.object_id,o.before_revision)
            WHERE o.change_set_id=ANY(%s)""",(ids,)).fetchall()
        all_history=sorted(set(ids)|{r[0] for r in extra})
        selections=[('change_sets','change_set_id=ANY(%s)',all_history),
                    ('objects','object_id=ANY(%s)',object_ids),
                    ('object_versions','change_set_id=ANY(%s)',all_history),
                    ('change_operations','change_set_id=ANY(%s)',all_history),
                    ('review_events','object_id=ANY(%s)',next_ids),
                    ('edges','edge_id=ANY(%s)',object_ids)]
        for table,where,values in selections:
            cursor=source.execute(SQL('SELECT * FROM wang_knowledge.{} WHERE '+where).format(Identifier(table)),(values,))
            columns=[column.name for column in cursor.description]
            insert=SQL('INSERT INTO wang_knowledge.{} ({}) VALUES ({})').format(
                Identifier(table),SQL(',').join(map(Identifier,columns)),SQL(',').join(Placeholder()*len(columns)))
            for row in cursor.fetchall():
                dest.execute(insert,tuple(Jsonb(value) if isinstance(value,(dict,list)) else value for value in row))
    campaign=admit(target,result)
    progress=target.cvp_campaign_progress(campaign)
    assert len(progress)==57 and sum(len(r['claim_ids']) for r in progress)==321
    for seq in (35,37):
        assert progress[seq-1]['revisit_required'] is True
        assert progress[seq-1]['independent_audit_complete'] is False
    # A new process reads only SQL roster/checkpoints and current g058 sources.
    started=time.perf_counter()
    output=subprocess.run([sys.executable,'-m','backend.pipeline.cvp_sql_flow','--sql-campaign',campaign,
        '--database-url',target.database_url,'--output-root',str(tmp_path),'--dry-run'],
        cwd=PROJECT,capture_output=True,text=True,check=True,timeout=10)
    cold=time.perf_counter()-started
    measurement=json.loads(output.stdout)
    assert measurement['group']['sequence']==58
    assert measurement['historical_snapshot_reads']==0
    assert measurement['status']=='prepared_no_models_no_writes'
    assert cold<=5 and measurement['packet_bytes']<64*1024**2
    assert len(target.cvp_campaign_progress(campaign))==57
    print(json.dumps({'cold_process_seconds':cold,'completed_groups':57,'completed_claims':321,**measurement}))
    audit,scope=cache_only_g057_audit(result,target.database_url,monkeypatch)
    path=tmp_path/'actual-g057-new-reader-audit.json';path.write_text(json.dumps(audit,ensure_ascii=False))
    from backend.pipeline.cvp_sql_flow import binding
    run=progress[-1]['run_id']
    with target.connect() as conn:
        conn.execute("UPDATE wang_knowledge.pipeline_runs SET metadata=metadata||'{\"checkpoint\":\"applied\"}'::jsonb WHERE run_id=%s",(run,))
    # The original audit denominator and independently-read versions are
    # required. A pipeline caller cannot omit one pin or one retained CVP.
    bad=json.loads(json.dumps(audit));bad['meta']['registry_read_scope']['read_pins']=[]
    bad_path=tmp_path/'missing-pins.json';bad_path.write_text(json.dumps(bad,ensure_ascii=False))
    with pytest.raises(ChangeSetConflict,match='version pins'):
        target.complete_cvp_group(run,audit=bad,audit_binding=binding(bad_path),read_pins={})
    assert target.get_cvp_group(run)['checkpoint']=='applied'
    target.complete_cvp_group(run,audit=audit,audit_binding=binding(path),read_pins={})
    assert target.get_cvp_group(run)['checkpoint']=='audited'
    assert len(target.cvp_campaign_progress(campaign))==57
    print(json.dumps({'g057_new_sql_completion':'audited','original_claims':16,'all_cvps':11,'new_model_calls':0,'content_writes':0}))


def cache_only_g057_audit(result,database_url,monkeypatch):
    module_path=PROJECT/'scripts/audit-library.py'
    spec=importlib.util.spec_from_file_location('cvp_independent_sql_acceptance',module_path)
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    last=result['completions'][-1];claim_ids=set(last['group']['claim_ids']);views=set(last['certificate']['viewpoint_ids'])
    settings=module.load_settings()
    store=module.SqlGroupStore(database_url,claim_ids,views)
    sources=module.SourceIndex(store.collection('source_documents'),Path(settings['DATA_BASE_DIR']))
    old=json.loads(Path(last['certificate']['audit_path']).read_text())
    cache_root=Path(settings['DATA_BASE_DIR'])/'wang-knowledge-platform/staging/reports/library-audit/.judgements'
    model=module.IndependentModel('gemini-3.8-flash','',preserve_full_context=True,cache_dir=cache_root)
    consumed=[]
    def only_saved(prompt,schema):
        key=hashlib.sha256(('gemini-3.8-flash\n'+prompt).encode()).hexdigest()
        path=cache_root/(key+'.json')
        if not path.is_file():raise AssertionError('no exact saved full-input judgement; new model forbidden')
        consumed.append(key);return json.loads(path.read_text())
    monkeypatch.setattr(model,'judge',only_saved)
    claims=module.audit_claims(store,sources,model,None,random.Random(old['meta']['seed']),2,None,claim_ids)
    viewpoints=module.audit_viewpoints(store,sources,model,None,random.Random(old['meta']['seed']),2,None,views)
    assert claims['results']==old['layers']['3']['results']
    assert viewpoints['results']==old['layers']['4']['results']
    assert claims['judged']==16 and viewpoints['judged']==11 and len(consumed)==27
    first=module.audit_verbatim(store,sources);second=module.audit_coverage(store,sources)
    assert first['counts']=={'pass':67}
    assert second['checked_clean']==second['checked_objects']==217
    assert not second['dangling'] and not second['source_document_findings']
    print(json.dumps({'group':57,'exact_saved_full_input_judgements':len(consumed),'new_model_calls':0,
                      'independent_claims':16,'independent_cvps':11,'read_metrics':store.read_metrics}))
    return {'schema_version':'wang_library_audit_v1','meta':{**old['meta'],'registry_read_scope':{
        'kind':'sql_transaction_group','claim_ids':sorted(claim_ids),'viewpoint_ids':sorted(views),'complete_target_members':True,
        'read_pins':[{'collection':k[0],'object_id':k[1],'pin':p} for k,p in sorted(store.read_pins.items())]}},
        'layers':{'1':first,'2':second,'3':claims,'4':viewpoints}},store


def test_g057_independent_reader_exact_saved_semantic_inputs_zero_models(monkeypatch):
    load_dotenv(PROJECT/'.env')
    result=json.loads((PROJECT/'.local-cvp-transaction-validation/admission-preview-sealed.json').read_text())
    cache_only_g057_audit(result,PostgresKnowledgeStore().database_url,monkeypatch)
