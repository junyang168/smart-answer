"""Explicitly reopen a legacy AI change suggestion, never a human decision.

This is admission to ordinary source review, not acceptance of the old finding
or approval of any postimage. Current SQL and review history are sealed again
at proposal and commit through the existing metadata projection.
"""
import re

from backend.api.canonical_repository.postgres_store import sha256_json


def marker(cursor, claim_id, state):
    if state.get('retired') or state['payload'].get('review_status') != 'human_review_required':
        return None
    events = cursor.execute("""SELECT object_revision,reviewer_kind,decision,artifact,reason
        FROM wang_knowledge.review_events WHERE collection='claims' AND object_id=%s
        ORDER BY created_at DESC,review_event_id DESC""", (claim_id,)).fetchall()
    if not events or any(row[1] == 'human' for row in events):
        return None
    revision, kind, decision, artifact, reason = events[0]
    if (revision != state['revision'] or kind != 'ai' or decision != 'human_review_required'
            or not isinstance(artifact, dict) or set(artifact) != {'change_set_id'}
            or not isinstance(artifact['change_set_id'], str)
            or not re.fullmatch(r'KCS-REVIEW-[a-f0-9]{20}', artifact['change_set_id'])
            or not isinstance(reason, str) or not reason.startswith('独立 AI 复审：changes_suggested ')
            or not re.search(r'\[artifact [^\[\]\n]+\.independent-review\.json sha256:[a-f0-9]{12}\]$', reason)):
        return None
    row = cursor.execute("""SELECT v.content_sha256,c.source_kind,c.status
        FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c USING(change_set_id)
        WHERE v.collection='claims' AND v.object_id=%s AND v.revision=%s AND v.change_set_id=%s""",
        (claim_id, revision, artifact['change_set_id'])).fetchone()
    if not row or row != (state['content_sha256'], 'review_decision', 'applied'):
        return None
    return {'before_pin': [revision, state['content_sha256'], False],
            'events_sha256': sha256_json(events)}


def authorized_markers(cursor, request, before, authority):
    grant = authority.get('legacy_ai_findings_refresh')
    if grant is None:
        return {}
    if (not isinstance(grant, dict) or set(grant) != {'authorization_quote', 'claims'}
            or not isinstance(grant['authorization_quote'], str) or not grant['authorization_quote'].strip()
            or not isinstance(grant['claims'], dict) or not grant['claims']
            or request.operation != 'Update'):
        raise ValueError('legacy AI finding refresh requires explicit bounded owner direction')
    writable = {p.key for p in request.permissions}
    result = {}
    for key, proof in grant['claims'].items():
        if (key not in request.target_keys or key not in writable or not key.startswith('claims/')
                or key not in before or marker(cursor, key.split('/', 1)[1], before[key]) != proof):
            raise ValueError('legacy AI finding or human review history changed')
        note = before[key]['payload'].get('review_note')
        if not note or note not in authority.get('review_questions', []):
            raise ValueError('legacy AI objection must remain an explicit review question')
        result[key] = proof
    return result
