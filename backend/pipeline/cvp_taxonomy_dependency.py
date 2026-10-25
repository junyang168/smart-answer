"""Separate program-only closure for non-semantic taxonomy identity leaves.

Never edits an independent model report or supplies a missing content judgement.
Only empty-definition, parentless topic identities may be supplemented.
"""
from pathlib import Path
from backend.pipeline.cvp_sql_commit import binding, check_binding, decoded_pins
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from backend.api.canonical_repository.postgres_store import record_content_sha

SCHEMA='wang_cvp_taxonomy_dependency_v1'

def validate_topic(oid, row):
    from backend.api.canonical_repository.knowledge_models import TopicNodeRecord
    p=row['payload'];TopicNodeRecord.model_validate(p)
    if (row.get('retired') or p.get('topic_id')!=oid or p.get('definition')
            or p.get('parent_topic_id') or p.get('aliases') or p.get('legacy_ids')
            or p.get('review_status') in {'human_hold','human_review_required','rejected','withheld_unreviewed'}
            or record_content_sha(p)!=row['content_sha256']):
        raise ValueError('taxonomy supplement is not a current identity-only leaf')
    # Do not silently admit future content-bearing fields.
    allowed={'topic_id','label','parent_topic_id','aliases','definition','legacy_ids',
             'revision','visibility','review_status','schema_version'}
    if set(p)-allowed:raise ValueError('unclassified taxonomy field')

def prepare(store, run_id, audit_path, current):
    audit=read_artifact(audit_path)
    independent=decoded_pins(audit['meta']['registry_read_scope']['read_pins'])
    g=store.get_cvp_group(run_id)
    views=audit['meta']['registry_read_scope']['viewpoint_ids']
    with store.connect() as conn:
        needed=store._existing_cvp_context(conn,{'claims':{i:{'claim_id':i} for i in g['claim_ids']},
            'canonical_viewpoints':{i:{'viewpoint_id':i} for i in views}},historical_pins_only=True)
    missing=set(needed)-set(independent)
    if not missing:return None
    if any(c!='topic_nodes' for c,i in missing):
        raise ValueError('taxonomy supplement cannot fill missing content audit dependencies')
    rows=[]
    # An independent SQL read, not a producer payload projection.
    with store.connect() as conn:
        for c,i in sorted(missing):
            raw=conn.execute('SELECT revision,content_sha256,payload,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',(c,i)).fetchone()
            if raw is None:raise ValueError('taxonomy identity missing')
            row={'revision':raw[0],'content_sha256':raw[1],'payload':raw[2],'retired':raw[3]}
            validate_topic(i,row)
            if any(row[k]!=needed[c,i][k] for k in row):raise ValueError('taxonomy SQL generation changed')
            rows.append({'collection':c,'object_id':i,**row})
    receipt={'schema_version':SCHEMA,'run_id':run_id,'audit':binding(audit_path),
             'model_calls':0,'content_approval':False,'records':rows}
    path=Path(audit_path).parent/'taxonomy-dependency-receipt.json'
    write_immutable(path,receipt)
    return path

def verify(path, run_id, audit_binding, required, independent):
    r=read_artifact(path);check_binding(r['audit'])
    if (r['schema_version']!=SCHEMA or r['run_id']!=run_id or r['audit']!=audit_binding
            or r['model_calls']!=0 or r['content_approval'] is not False):
        raise ValueError('taxonomy receipt authority drift')
    missing=set(required)-set(independent)
    rows={(x['collection'],x['object_id']):x for x in r['records']}
    if len(rows)!=len(r['records']) or set(rows)!=missing:
        raise ValueError('taxonomy receipt omitted/added dependencies')
    result=dict(independent)
    for (c,i),row in rows.items():
        if c!='topic_nodes':raise ValueError('non-taxonomy dependency')
        validate_topic(i,row)
        if any(row[k]!=required[c,i][k] for k in ('revision','content_sha256','payload','retired')):
            raise ValueError('taxonomy receipt stale')
        result[c,i]=(row['revision'],row['content_sha256'],row['retired'])
    return result
