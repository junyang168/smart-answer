"""Fixed imported graph -> ID-only namespace recovery, no ancestor walker."""
import json
from pathlib import Path
from backend.api.canonical_repository.postgres_store import record_content_sha,normalize_package,sha256_json
from backend.pipeline.cvp_sql_retained_evidence_locator_authority import require,exact_operation
from backend.pipeline.cvp_sql_flow import binding

def recovery_graph(store,cid,previous,history,source):
    require(history['source_kind']=='wkp353_relation_id_incident_recovery' and history['status']=='applied',
        'wrong/uncommitted namespace recovery')
    all_ops=history['operations'];fields=('operation','collection','object_id','before_sha256',
        'after_sha256','before_revision','after_revision')
    fingerprint=sha256_json({'planner_schema':'wang_relation_id_incident_recovery_report_v1',
        'source_kind':history['source_kind'],'source_sha256':history['source_sha256'],
        'operations':[{k:o[k] for k in fields} for o in all_ops]})
    require(0<len(all_ops)<=4096 and len({(o['collection'],o['object_id']) for o in all_ops})==len(all_ops)
        and sha256_json(history['metadata']['authority'])==history['source_sha256']
        and fingerprint==history['fingerprint_sha256'] and history['change_set_id']=='KCS-'+fingerprint[:20]
        and all(o['after_sha256']==record_content_sha(o['after']) and o['after_revision']==o['after']['revision']
            and (o['before'] is None or o['before_sha256']==record_content_sha(o['before'])) for o in all_ops),
        'complete namespace SQL operations/authority seal drift')
    after=previous[2]
    with store.connect() as c:
        v=c.execute("SELECT change_set_id,content_sha256,payload FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s",(cid,after['revision']-1)).fetchall()
    require(len(v)==1 and v[0][1]==record_content_sha(v[0][2]),'namespace recovery predecessor missing')
    before=v[0][2];original=store.get_change_history(v[0][0])
    require(original and original['status']=='applied' and original['source_kind']=='knowledge_package',
            'namespace recovery requires exact immediate imported graph')
    exact_operation(history,cid,before,after)
    require({k:v for k,v in before.items() if k not in {'revision','extraction_fingerprints'}}==
        {k:v for k,v in after.items() if k not in {'revision','extraction_fingerprints'}},
        'namespace recovery changed Claim semantics')
    entries=history['metadata']['authority']['authoritative_packages']
    matches=[x for x in entries if x.get('source_key')==source['transcript_id']]
    require(len(matches)==1 and len(entries)<=128,'namespace recovery source missing/ambiguous')
    entry=matches[0];path=Path(entry['path'])
    require(path.is_absolute() and path.stat().st_size<=8*1024**2
        and binding(path)['file_sha256']==entry['raw_sha256'],'namespace authority physical drift')
    from backend.pipeline.relation_id_namespace import migrate_legacy_cross_section_relation_ids
    effective,manifest=migrate_legacy_cross_section_relation_ids(json.loads(path.read_bytes()))
    require(manifest['round_trip_verified'] is True
        and manifest['input_canonical_sha256']==entry['input_canonical_sha256']
        and manifest['output_canonical_sha256']==entry['effective_canonical_sha256'],
        'namespace authority not exact ID-only roundtrip')
    normalized=normalize_package(effective);target=normalized.get('claims',{}).get(cid)
    ignore={'revision','review_status','review_note','reviewed_by','reviewed_at'}
    require(target and {k:v for k,v in target.items() if k not in ignore}==
        {k:v for k,v in after.items() if k not in ignore},'recovery Claim not exact authoritative input')
    graph={('claims',cid)}
    for eid in after['evidence_step_ids']:
        ev=normalized.get('evidence_steps',{}).get(eid)
        require(ev,'authoritative Evidence missing')
        graph.add(('evidence_steps',eid));fids=set(ev.get('source_fragment_ids',[]))
        if ev.get('source_fragment_id'):fids.add(ev['source_fragment_id'])
        require(fids,'authoritative Evidence unbound')
        graph.update(('source_fragments',i) for i in fids)
    original_ops={(o['collection'],o['object_id']):o for o in original['operations']}
    recovery_ops={(o['collection'],o['object_id']):o for o in history['operations']}
    ops={};origins={}
    for key in sorted(graph):
        old=original_ops.get(key);changed=recovery_ops.get(key)
        origin_id=original['change_set_id']
        if old is None:
            # An unchanged graph row need not have been rewritten by that
            # package. Read its single exact committed generation at the named
            # import, never walk an ancestry chain or use current state as old.
            with store.connect() as c:
                row=c.execute("SELECT v.change_set_id,v.revision,v.content_sha256,v.payload,c.status,o.operation,o.after_sha256 FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id) JOIN wang_knowledge.change_operations o ON o.change_set_id=v.change_set_id AND o.collection=v.collection AND o.object_id=v.object_id AND o.after_revision=v.revision WHERE v.collection=%s AND v.object_id=%s AND v.recorded_at<=(SELECT recorded_at FROM wang_knowledge.object_versions WHERE collection='claims' AND object_id=%s AND revision=%s) ORDER BY v.revision DESC LIMIT 1",(*key,cid,before['revision'])).fetchone()
            require(row and row[4]=='applied' and row[5] in {'create','update'} and row[2]==row[6]==record_content_sha(row[3]),
                    'named imported graph generation missing/uncommitted')
            origin_id=row[0]
            old={'collection':key[0],'object_id':key[1],'operation':row[5],
                 'after_revision':row[1],'after_sha256':row[2],'after':row[3]}
        require(old and old['operation'] in {'create','update'} and old['after_sha256']==record_content_sha(old['after']),
            'exact imported graph operation absent')
        if changed:
            require(changed['operation']=='update' and changed['before']==old['after']
                and changed['before_revision']==old['after_revision'] and changed['before_sha256']==old['after_sha256']
                and changed['after_sha256']==record_content_sha(changed['after']), 'recovery graph is not direct exact transform')
        ops[key]=changed or old;origins[key]=history['change_set_id'] if changed else origin_id
    return original['metadata']['input_path'],ops,origins,{**binding(path),
        'input_canonical_sha256':entry['input_canonical_sha256'],
        'effective_canonical_sha256':entry['effective_canonical_sha256']}
