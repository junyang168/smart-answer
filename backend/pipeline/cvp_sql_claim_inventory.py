"""Explicit read-only campaign qualification maintenance, never normal startup.

Inventory exact indexed SQL generations once for the frozen campaign. Completed
groups consume their current completion pins; pending groups use the very same
authority validator as production. This does not admit, migrate or call models.
"""
from collections import Counter
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.cvp_sql_commit import decoded_pins
from backend.pipeline.cvp_sql_claim_authority import current_claim_authority

def inventory(store, campaign_sha):
    definition=store.get_cvp_campaign(campaign_sha)
    groups=definition['groups'];ids=[i for g in groups for i in g['claim_ids']]
    if len(ids)!=len(set(ids)):raise ValueError('frozen campaign duplicate Claim')
    progress={g['group_key']:g for g in store.cvp_campaign_progress(campaign_sha)}
    with store.connect() as c:
        completed_ids=[g['run_id'] for g in progress.values() if g['checkpoint']=='audited' and g['status']=='succeeded']
        certificates=dict(c.execute("SELECT run_id,metadata->'completion' FROM wang_knowledge.pipeline_runs WHERE run_id=ANY(%s)",(completed_ids,)).fetchall())
        imported=set(r[0] for r in c.execute("SELECT p.run_id FROM wang_knowledge.pipeline_runs p WHERE p.run_id=ANY(%s) AND p.metadata->'import_certificate'->>'status'='verified_historical_completion' AND NOT EXISTS (SELECT 1 FROM wang_knowledge.object_versions v JOIN wang_knowledge.objects o ON (o.collection,o.object_id,o.revision)=(v.collection,v.object_id,v.revision) WHERE v.collection='claims' AND (p.metadata->'claim_ids') ? v.object_id AND v.recorded_at>p.finished_at)",(completed_ids,)).fetchall())
        rows=c.execute("SELECT object_id,revision,content_sha256,payload,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection='claims' AND object_id=ANY(%s)",(ids,)).fetchall()
        history=c.execute("SELECT v.object_id,v.revision,v.change_set_id,v.content_sha256,c.source_kind,c.status FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) WHERE v.collection='claims' AND v.object_id=ANY(%s) ORDER BY v.object_id,v.revision",(ids,)).fetchall()
    current={r[0]:{'revision':r[1],'content_sha256':r[2],'payload':r[3],'retired':r[4]} for r in rows}
    sequences={i:[] for i in ids}
    for cid,rev,kcs,sha,kind,status in history:
        sequences[cid].append({'revision':rev,'change_set_id':kcs,'content_sha256':sha,'source_kind':kind,'status':status})
    results=[]
    for g in groups:
        saved=progress.get(g['group_key'],{})
        completed=saved.get('checkpoint')=='audited' and saved.get('status')=='succeeded'
        completion=decoded_pins((certificates.get(saved.get('run_id')) or {}).get('read_pins',[])) if completed else {}
        for cid in g['claim_ids']:
            row=current.get(cid);out={'claim_id':cid,'sequence':g['sequence'],'history':sequences[cid]}
            try:
                if not row or record_content_sha(row['payload'])!=row['content_sha256']:
                    raise ValueError('missing current Claim or current content SHA drift')
                out['current_pin']=[row['revision'],row['content_sha256'],row['retired']]
                if completed and completion.get(('claims',cid))==tuple(out['current_pin']):
                    out.update(status='current_audited_generation',authority_kind='SQL_group_completion')
                elif completed and saved['run_id'] in imported:
                    out.update(status='historically_admitted_unchanged_generation',
                        authority_kind='existing_SQL_historical_completion_no_new_qualification')
                else:
                    a=current_claim_authority(store,cid,row)
                    out.update(status='current_authority_verified',authority_kind=a.get('authority_kind','exact_SQL_review'))
            except (ValueError,KeyError) as e:
                out.update(status='qualification_unresolved',error=str(e))
            results.append(out)
    return {'schema_version':'wang_cvp_claim_qualification_inventory_v1','campaign_sha256':campaign_sha,
        'original_claim_count':len(ids),'original_group_count':len(groups),'model_calls':0,'registry_writes':0,
        'results':results,'status_counts':dict(Counter(r['status'] for r in results)),
        'current_type_counts':dict(Counter(r['history'][-1]['source_kind'] if r['history'] else 'missing' for r in results)),
        'unresolved':[r for r in results if r['status']=='qualification_unresolved']}
