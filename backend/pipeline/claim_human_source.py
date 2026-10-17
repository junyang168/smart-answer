"""Durable human source corrections/confirmations, scoped to exact paragraphs.

This stores evidence, never Claim approval. Actions freeze a direct snapshot;
neither model calls nor normal reads replay an ancestry chain or scan the corpus.
"""
from copy import deepcopy
import hashlib
import json

from backend.pipeline import source_reconciliation as source
from backend.api.canonical_repository.postgres_store import canonical_json, sha256_json, ChangeSetConflict

VERSION = 'wang_human_source_decision_v1'


def text_sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def entry(bound, record, sid, snapshot, keys, kind):
    keys = sorted(set(keys))
    replacements = record.get('supersedes', {})
    if (len(keys) != 1 or not isinstance(replacements, dict)
            or any(not isinstance(v, list) or any(not isinstance(i, str) for i in v) for v in replacements.values())):
        raise ValueError('human source decisions require one paragraph and explicit replacement IDs')
    value = {'schema_version': VERSION, 'source_id': sid, 'kind': kind,
             'artifact': bound, 'record': deepcopy(record),
             'source_file_sha256': snapshot['file']['file_sha256'],
             'paragraph_sha256': {k: text_sha(snapshot['paragraphs'][k]['text']) for k in sorted(set(keys))},
             'supersedes': sorted(replacements.get(sid + '/' + keys[0], []))}
    return {**value, 'decision_id': 'HSD-' + sha256_json(value)}


def confirmation_entries(bound, bindings, snapshots):
    """Validate the existing human-answer contract before durable registration."""
    from backend.pipeline.claim_management import Citation, validate_citations
    bound = source.FileBinding.model_validate(bound).model_dump(mode='json')
    record = json.loads(source.verify_file(source.FileBinding.model_validate(bound)).read_text())
    answers = record.get('answers', [])
    ids = [a.get('finding_id') for a in answers]
    if (record.get('schema_version') != 'wang_claim_human_source_verification_v1'
            or record.get('verified_by') != 'user' or record.get('human_content_approval') is not False
            or record.get('source_bindings') != bindings or not record.get('user_confirmation')
            or not answers or any(not i for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError('durable human evidence requires exact source facts, not content approval')
    keys = {}
    for answer in answers:
        citation = Citation.model_validate(answer.get('citation'))
        if not answer.get('question') or not answer.get('answer') or citation.source_id not in snapshots:
            raise ValueError('human evidence omitted its source question or answer')
        validate_citations([citation], snapshots)
        keys.setdefault((citation.source_id, citation.paragraph_key), []).append(deepcopy(answer))
    replacements = record.get('supersedes', {})
    if not isinstance(replacements, dict) or set(replacements) - {sid + '/' + key for sid, key in keys}:
        raise ValueError('human replacement omitted its exact source question and paragraph')
    return [entry(bound, {**record, 'answers': scoped}, sid, snapshots[sid], [pkey], 'confirmation')
            for (sid, pkey), scoped in sorted(keys.items())]


def correction_entry(bound, verification, current):
    """Called only after the existing literal-correction/commit checks pass."""
    sid = verification['source_id']
    return entry(bound, verification, sid, current, [verification['paragraph_key']], 'transcription_correction')


def validate_entry(value):
    body = {k: v for k, v in value.items() if k != 'decision_id'}
    if (value.get('schema_version') != VERSION or value.get('decision_id') != 'HSD-' + sha256_json(body)
            or not value.get('paragraph_sha256') or value.get('kind') not in {'confirmation', 'transcription_correction'}):
        raise ValueError('human source evidence snapshot identity drift')


def register(cursor, entries, *, origin):
    """Caller holds the common advisory transaction lock; all or nothing."""
    if not origin:
        raise ValueError('human source evidence needs its workflow origin')
    for value in entries:
        validate_entry(value)
        ident, sid = value['decision_id'], value['source_id']
        previous = cursor.execute('SELECT entry FROM wang_knowledge.human_source_decisions WHERE decision_id=%s', (ident,)).fetchone()
        if previous:
            if previous[0] != value:
                raise ChangeSetConflict('immutable human source evidence changed')
            continue  # An old retry must never reactivate a replaced decision.
        supersedes = value['supersedes']
        if len(supersedes) != len(set(supersedes)):
            raise ValueError('duplicate human decision replacement')
        for old in supersedes:
            prior = cursor.execute('SELECT source_id,entry,superseded_by FROM wang_knowledge.human_source_decisions WHERE decision_id=%s FOR UPDATE', (old,)).fetchone()
            if (not prior or prior[0] != sid or prior[2] is not None
                    or set(prior[1]['paragraph_sha256']) != set(value['paragraph_sha256'])):
                raise ChangeSetConflict('human decision replacement must explicitly cover the same source positions')
        cursor.execute('INSERT INTO wang_knowledge.human_source_decisions(decision_id,source_id,entry,origin) VALUES (%s,%s,%s::jsonb,%s)',
                       (ident, sid, canonical_json(value), origin))
        if supersedes:
            cursor.execute('UPDATE wang_knowledge.human_source_decisions SET superseded_by=%s WHERE decision_id=ANY(%s)', (ident, supersedes))


def snapshot(cursor, source_ids):
    rows = cursor.execute('SELECT entry FROM wang_knowledge.human_source_decisions WHERE source_id=ANY(%s) AND superseded_by IS NULL ORDER BY decision_id',
                          (sorted(source_ids),)).fetchall()
    return [row[0] for row in rows]


def assert_current(cursor, source_ids, frozen):
    if snapshot(cursor, source_ids) != frozen:
        raise ChangeSetConflict('human source decisions changed; review the new evidence in a new action')


def project(sources, decisions):
    """Only unchanged complete paragraphs carry forward to another version."""
    for value in decisions:
        validate_entry(value)
        sid = value['source_id']
        if sid not in sources:
            continue
        current = sources[sid]
        applicable = all(k in current['paragraphs'] and text_sha(current['paragraphs'][k]['text']) == sha
                         for k, sha in value['paragraph_sha256'].items())
        current.setdefault('human_source_decisions', []).append({
            **deepcopy(value), 'applicability': 'current' if applicable else 'needs_reconfirmation',
            'source_version_changed': current['file']['file_sha256'] != value['source_file_sha256'],
            'claim_content_approval': False})


def persist_action_evidence(store, action_id):
    """Backfill a known, already admitted action; never discover evidence on disk."""
    from backend.pipeline import claim_management as cm
    from backend.api.canonical_repository.postgres_store import POSTGRES_APPLY_ADVISORY_LOCK_KEY
    parent = store.get_cvp_group('CMA-' + sha256_json(action_id))
    if not parent or parent.get('admission_refusal'):
        raise ValueError('human evidence requires its admitted original action')
    request = cm.Request.model_validate(parent['request'])
    _, sources = cm.load_authority(request, source_documents=cm.source_documents(parent['original_goal']['original_objects']))
    authority = json.loads(source.verify_file(request.authorization).read_text())
    entries = confirmation_entries(authority['human_source_verification'], request.model_dump(mode='json')['source_bindings'], sources)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (POSTGRES_APPLY_ADVISORY_LOCK_KEY,))
        register(cursor, entries, origin=parent['run_id'])
    return [e['decision_id'] for e in entries]
