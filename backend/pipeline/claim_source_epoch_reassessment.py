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
    if (kind!='ai' or decision!='human_review_required' or revision!=state['revision']
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
    if (not reviewed or reviewed[2]!='applied' or reviewed[0]!=state['payload']
            or record_content_sha(reviewed[0])!=reviewed[1] or reviewed[1]!=state['content_sha256']):return None
    return {'before_pin':[state['revision'],state['content_sha256'],False],
            'events_sha256':sha256_json({'events':events,'epoch':epoch,'operation':operation,
                                        'source_pin':list(current[:2])})}


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
