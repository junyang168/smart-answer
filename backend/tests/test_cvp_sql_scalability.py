"""Fixed affected graph with 1x/10x/100x unrelated current rows and history."""
import json
import resource
import time

from backend.api.canonical_repository.postgres_store import canonical_json,record_content_sha
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres,postgres_store
from backend.tests.test_source_reconciliation import scenario
from backend.tests.test_cvp_sql_actions_postgres import source_run
from backend.tests.test_cvp_sql_transaction_postgres import task,apply


def test_sql_read_and_recovery_cost_does_not_load_unrelated_history(postgres_store,scenario,monkeypatch):
    store=postgres_store;source_run(store,scenario)
    _,_,_=scenario
    baseline=store.read_affected_context([('claims','CL-test')])
    expected_bytes=sum(len(canonical_json(r['payload']).encode()) for r in baseline.values())
    plan,context=task(store);apply(store,plan,context)
    def forbidden(*a,**kw):raise AssertionError('global snapshot or historical body loaded')
    monkeypatch.setattr(store,'_assert_registry_fingerprint',forbidden)
    monkeypatch.setattr(store,'_assert_transaction_read_pins',forbidden)
    measurements=[];inserted=0
    for multiplier in (1,10,100):
        total=20*multiplier
        with store.connect() as conn,conn.cursor() as cursor:
            for i in range(inserted,total):
                payload={'source_id':f'SCALE-UNRELATED-{i}','source_type':'sermon_transcript','title':'unrelated',
                         'revision':1,'padding':'無關資料'*256}
                digest=record_content_sha(payload)
                cursor.execute("INSERT INTO wang_knowledge.objects(collection,object_id,revision,content_sha256,payload) VALUES ('source_documents',%s,1,%s,%s::jsonb)",
                    (payload['source_id'],digest,canonical_json(payload)))
                cursor.execute("INSERT INTO wang_knowledge.object_versions(collection,object_id,revision,content_sha256,payload,change_set_id) VALUES ('source_documents',%s,1,%s,%s::jsonb,%s)",
                    (payload['source_id'],digest,canonical_json(payload),plan.change_set_id))
            conn.execute('ANALYZE wang_knowledge.objects');conn.execute('ANALYZE wang_knowledge.object_versions')
        inserted=total
        for temperature in ('first','repeat'):
            started=time.perf_counter()
            actual=store.read_affected_context([('claims','CL-test')])
            assert actual==baseline
            assert apply(store,plan,context)['status']=='already_applied'
            seconds=time.perf_counter()-started
            assert seconds<5
            measurements.append({'multiplier':multiplier,'temperature':temperature,'seconds':seconds,
                'rows':len(actual),'payload_bytes':sum(len(canonical_json(r['payload']).encode()) for r in actual.values()),
                'historical_snapshot_reads':0,'proof_rebuilds':0,'peak_rss_platform_units':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss})
    assert all(m['payload_bytes']==expected_bytes and m['rows']==len(baseline) for m in measurements)
    print(json.dumps({'fixed_affected_graph_scale':measurements}))
