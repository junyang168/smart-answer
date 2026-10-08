"""Bounded SQL authority for an already-applied, non-semantic locator step."""
from pathlib import Path

from backend.api.canonical_repository.postgres_store import (
    canonical_json, record_content_sha, sha256_json,
)


def coordinate_authority(store, cid, row):
    from backend.pipeline.cvp_sql_flow import check_binding, plan_from_document
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    from backend.api.canonical_repository.postgres_store import validate_change_set_plan_integrity
    from backend.pipeline.knowledge_source import markdown_source_document
    from backend.pipeline.source_contract_cleanup import build_projection, migrate_claim_occurrence_anchors

    def require(condition, reason):
        if not condition:
            raise ValueError('current Claim locator review authority failed: ' + cid + ': ' + reason)

    require(not row.get('retired') and row['revision'] > 1, 'no eligible predecessor')
    with store.connect() as conn:
        current = conn.execute("""SELECT change_set_id,content_sha256,payload FROM wang_knowledge.object_versions
            WHERE collection='claims' AND object_id=%s AND revision=%s""", (cid,row['revision'])).fetchone()
        previous = conn.execute("""SELECT change_set_id,content_sha256,payload FROM wang_knowledge.object_versions
            WHERE collection='claims' AND object_id=%s AND revision=%s""", (cid,row['revision']-1)).fetchone()
        events = conn.execute("""SELECT review_event_id,reviewer_kind,decision,artifact
            FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s AND object_revision=%s
            AND decision=%s ORDER BY review_event_id""", (cid,row['revision']-1,row['payload'].get('review_status'))).fetchall()
    require(current and previous, 'missing exact SQL versions')
    require(current[1] == row['content_sha256'] == record_content_sha(current[2])
            and canonical_json(current[2]) == canonical_json(row['payload'])
            and previous[1] == record_content_sha(previous[2]), 'version payload/SHA drift')
    require(len(events) == 1 and events[0][1] in {'ai','human','system'} and events[0][3], 'missing/ambiguous predecessor review')
    history = store.get_change_history(current[0])
    require(history and history['status'] == 'applied'
            and history['source_kind'] == 'wkp368_source_contract_cleanup', 'not an applied locator cleanup')
    prior = store.get_change_history(previous[0])
    require(prior and prior['status'] == 'applied', 'predecessor not committed')
    reviewed = [a for a in prior['operations'] if a['collection']=='claims' and a['object_id']==cid]
    review_ids = set(prior['metadata'].get('review_event_ids', []))
    review_ids.add(prior['metadata'].get('review_event_id'))
    require(len(reviewed)==1 and reviewed[0]['after_revision']==row['revision']-1
            and reviewed[0]['after_sha256']==previous[1] and reviewed[0]['after']==previous[2]
            and events[0][0] in review_ids
            and events[0][3].get('change_set_id',previous[0])==previous[0],
            'review is not bound to exact predecessor commit')
    metadata = history['metadata']
    plan_binding = {'path':metadata['source_plan_path'],'file_sha256':metadata['source_plan_file_sha256']}
    require(Path(plan_binding['path']).stat().st_size <= 2*1024**2, 'bounded source plan exceeded')
    check_binding(plan_binding)
    document = read_artifact(plan_binding['path'])
    require(document.get('schema_version') == 'wang_source_contract_cleanup_source_plan_v1'
            and document.get('artifact_sha256') == sha256_json({k:v for k,v in document.items() if k!='artifact_sha256'}),
            'missing exact source plan seal/schema')
    plan = plan_from_document(document['plan'])
    validate_change_set_plan_integrity(plan)
    require((plan.change_set_id,plan.fingerprint_sha256,plan.source_sha256,plan.source_kind) ==
            (current[0],history['fingerprint_sha256'],history['source_sha256'],history['source_kind']), 'plan/commit identity drift')
    planned = plan.as_dict()['operations']
    actual = history['operations']
    fields = ('operation','collection','object_id','before_revision','after_revision','before_sha256','after_sha256')
    require(len(planned) == len(actual) and all(all(p[k] == a[k] for k in fields)
            and canonical_json(dict(p['payload'],revision=p['after_revision'])) == canonical_json(a['after'])
            for p,a in zip(planned,actual)), 'complete applied operation binding drift')
    targets = [a for a in actual if a['collection']=='claims' and a['object_id']==cid]
    require(len(targets)==1, 'ambiguous target operation')
    target = targets[0]
    require(target['operation']=='update' and target['before_revision']==row['revision']-1
            and target['after_revision']==row['revision'] and target['before_sha256']==previous[1]
            and target['after_sha256']==current[1] and target['before']==previous[2]
            and target['after']==current[2], 'target versions not exact')
    source = document['source']
    require(source['source_type']=='notes_manuscript' and Path(source['source_path']).is_absolute(), 'unsupported source')
    source_binding = {'path':source['source_path'],'file_sha256':source['source_file_sha256']}
    check_binding(source_binding)
    script,_,_ = markdown_source_document(source)
    index,projection = build_projection(script['script'])
    require(projection.body_sha256 == source['source_body_sha256'], 'physical body drift')
    migrated,findings = migrate_claim_occurrence_anchors(previous[2], index,
        source_id=source['source_id'],transcript_id=source['transcript_id'])
    require(migrated and findings and all(f['status']=='changed' for f in findings)
            and canonical_json(dict(migrated,revision=row['revision'])) == canonical_json(current[2]),
            'full deterministic locator postimage differs')
    return {'claim_id':cid,'revision':row['revision'],'content_sha256':row['content_sha256'],
        'review_event_id':events[0][0],'reviewer_kind':events[0][1],'decision':events[0][2],
        'review_artifact_sha256':sha256_json(events[0][3]),'reviewed_revision':row['revision']-1,
        'authority_kind':'exact_sql_applied_locator_postimage','coordinate_change_set_id':current[0],
        'coordinate_plan':plan_binding,'source':source_binding,'model_calls':0,'master_data_mutations':0}
