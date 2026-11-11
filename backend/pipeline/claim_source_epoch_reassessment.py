"""Admit a legacy AI-only question to review against a genuinely NEW source.

Not a human-hold override, not a content verdict, and not a retry of the old
model request. Every use rechecks the committed source handoff and event history.
"""
import json
import re
from pathlib import Path

from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json
from backend.pipeline.cvp_sql_commit import check_binding

VERSION='wang_claim_source_epoch_reassessment_v1'


def _locator_lineage(cursor, claim_id, revision, reviewed, state, source_id):
    """Prove every intervening revision was a saved program-only anchor sync."""
    rows=cursor.execute('''SELECT v.revision,v.payload,v.content_sha256,
        c.change_set_id,c.source_kind,c.status,c.metadata
        FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id)
        WHERE v.collection='claims' AND v.object_id=%s AND v.revision>%s
        ORDER BY v.revision''',(claim_id,revision)).fetchall()
    if [r[0] for r in rows]!=list(range(revision+1,state['revision']+1)):return None
    previous=reviewed[0];proof=[]
    for rev,payload,digest,cs,kind,status,metadata in rows:
        if (kind!='wang_transcript_claim_anchor_sync_v1' or status!='applied'
                or metadata.get('actor')!='owner_authorized_locator_sync'
                or metadata.get('model_calls')!=0
                or metadata.get('claim_prose_changed') is not False
                or metadata.get('new_qualification_granted') is not False
                or record_content_sha(payload)!=digest
                or {k:v for k,v in previous.items() if k not in ('revision','occurrences')}
                    !={k:v for k,v in payload.items() if k not in ('revision','occurrences')}):return None
        bound=metadata.get('migration_plan')
        try:
            check_binding(bound)
            plan=json.loads(Path(bound['path']).read_bytes())
        except (ValueError,TypeError,KeyError,OSError):return None
        if (plan.get('schema_version')!=kind or plan.get('change_set_id')!=cs
                or plan.get('intent',{}).get('source_id')!=source_id):return None
        candidates=[p for p in plan.get('package',{}).get('claims',[])
                    if p.get('claim_id')==claim_id]
        if len(candidates)!=1 or {**candidates[0],'revision':rev}!=payload:return None
        op=cursor.execute('''SELECT before_sha256,after_sha256,after_revision
            FROM wang_knowledge.change_operations WHERE change_set_id=%s
            AND collection='claims' AND object_id=%s AND operation='update' ''',(cs,claim_id)).fetchall()
        if op!=[(record_content_sha(previous),digest,rev)]:return None
        proof.append([rev,digest,cs,bound]);previous=payload
    if previous!=state['payload'] or record_content_sha(previous)!=state['content_sha256']:return None
    return proof


def marker(cursor, claim_id, state, epoch):
    if state.get('retired') or state['payload'].get('review_status')!='human_review_required':
        return None
    check_binding(epoch)
    grant=json.loads(Path(epoch['path']).read_bytes())
    if set(grant)!={'schema_version','manifest','change_set_id'} or grant['schema_version']!=VERSION:
        raise ValueError('invalid source epoch reassessment grant')
    check_binding(grant['manifest'])
    manifest=json.loads(Path(grant['manifest']['path']).read_bytes())
    if (manifest.get('schema_version')!='wang_transcript_claim_migration_v1'
            or claim_id not in manifest['claim_ids'] or not manifest['instruction'].strip()
            or manifest['before']['file_sha256']==manifest['after']['file_sha256']):
        raise ValueError('source epoch has no exact new-version Claim authority')
    for key in ('before','after'):check_binding(manifest[key])
    from backend.pipeline.source_projection import project_script
    def speech(bound):
        value=json.loads(Path(bound['path']).read_bytes())
        rows=value['script'] if isinstance(value,dict) else value
        return re.sub(r'\s+','',''.join(r['text'] for r in project_script(rows).spoken_rows))
    if speech(manifest['before'])==speech(manifest['after']):
        raise ValueError('AI source question needs a changed spoken source, not metadata/timing only')
    history=cursor.execute('''SELECT source_kind,status,metadata FROM wang_knowledge.change_sets
                             WHERE change_set_id=%s''',(grant['change_set_id'],)).fetchone()
    if (not history or history[:2]!=('transcript_claim_source_registration_v1','applied')
            or history[2].get('migration_manifest')!=grant['manifest']
            or history[2].get('human_content_approval') is not False):
        raise ValueError('source epoch is not the committed migration registration')
    operation=cursor.execute('''SELECT before_sha256,after_sha256,after_revision
        FROM wang_knowledge.change_operations WHERE change_set_id=%s
        AND collection='source_documents' AND object_id=%s AND operation='update' ''',
        (grant['change_set_id'],manifest['source_id'])).fetchall()
    current=cursor.execute('''SELECT revision,content_sha256,payload FROM wang_knowledge.objects
        WHERE collection='source_documents' AND object_id=%s AND retired_at IS NULL''',
        (manifest['source_id'],)).fetchone()
    if (len(operation)!=1 or not current
            or [operation[0][2],operation[0][1]]!=list(current[:2])
            or operation[0][0]!=manifest['before_source_record']['content_sha256']
            or current[2].get('source_file_sha256')!=manifest['after']['file_sha256']
            or current[2].get('source_body_sha256')==manifest['before_source_record']['payload']['source_body_sha256']):
        raise ValueError('source epoch current identity/version drift')
    events=cursor.execute('''SELECT object_revision,reviewer_kind,decision,artifact,reason
        FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s
        ORDER BY created_at DESC,review_event_id DESC''',(claim_id,)).fetchall()
    if not events or any(event[1]=='human' for event in events):return None
    revision,kind,decision,artifact,reason=events[0]
    resolution=(artifact or {}).get('resolution',{})
    if (kind!='ai' or decision!='human_review_required' or revision>state['revision']
            or resolution.get('schema_version')!='wang_claim_ai_review_provenance_v1'
            or resolution.get('claim_id')!=claim_id
            or resolution.get('approval_status')!='not_human_approved'
            or resolution.get('target_review_status')!='human_review_required'
            or reason!=state['payload'].get('review_note')):
        return None
    reviewed=cursor.execute('''SELECT v.payload,v.content_sha256,c.status
        FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id)
        WHERE v.collection='claims' AND v.object_id=%s AND v.revision=%s''',
        (claim_id,revision)).fetchone()
    if (not reviewed or reviewed[2]!='applied'
            or record_content_sha(reviewed[0])!=reviewed[1]):return None
    lineage=None
    if revision==state['revision']:
        if reviewed[0]!=state['payload'] or reviewed[1]!=state['content_sha256']:return None
    else:
        lineage=_locator_lineage(cursor,claim_id,revision,reviewed,state,manifest['source_id'])
        if not lineage:return None
    facts={'events':events,'epoch':epoch,'operation':operation,'source_pin':list(current[:2])}
    if lineage:facts['locator_lineage']=lineage
    return {'before_pin':[state['revision'],state['content_sha256'],False],
            'events_sha256':sha256_json(facts)}


def authorized_markers(cursor, request, before, authority):
    grant=authority.get('source_epoch_ai_reassessment')
    if grant is None:return {}
    if (request.operation!='Update' or not isinstance(grant,dict)
            or set(grant)!={'epoch','claims'} or not grant['claims']):
        raise ValueError('source epoch reassessment requires exact Update authority')
    writable={p.key for p in request.permissions};result={}
    for key,proof in grant['claims'].items():
        if (key not in request.target_keys or key not in writable or key not in before
                or not key.startswith('claims/')
                or marker(cursor,key.split('/',1)[1],before[key],grant['epoch'])!=proof):
            raise ValueError('source epoch/AI question/human review history changed')
        note=before[key]['payload'].get('review_note')
        if not note or note not in authority.get('review_questions',[]):
            raise ValueError('original AI objection must remain an explicit review question')
        result[key]=proof
    return result
