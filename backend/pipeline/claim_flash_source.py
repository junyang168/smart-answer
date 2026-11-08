"""Flash source-only preparation and read-only verification; no semantic model calls.

Reuses literal binding checks and unique migration lookup. Calibration is a
preview; only an already-authorized full Update may persist its candidates.
"""
from copy import deepcopy
import fcntl
from pathlib import Path

from backend.pipeline import claim_flash_common as f, evidence_refresh as e
from backend.pipeline import claim_flash_source_lookup as source_lookup

CONTRACT = 'claim_flash_source_verification_v1'
HISTORICAL = {'source_deleted', 'replaced_by_precise_fragments', 'context_only'}


def excluded_context(key, records):
    """Preserve the existing unused-dialogue exclusion, never mark it verified."""
    p = records[key]['payload']; oid = key.split('/', 1)[1]
    if (not key.startswith('evidence_steps/') or p.get('step_type') != 'dialogue_context'
            or p.get('support_eligibility') != 'context_only' or p.get('produced_claim_ids')
            or p.get('review_status') in f.PROTECTED):
        return False
    for k, r in records.items():
        q = r['payload']
        if k.startswith('claims/') and oid in q.get('evidence_step_ids', []):
            return False
        if (k.startswith('knowledge_relations/') and oid in {q['from_id'], q['to_id']}
                and q['relation_type'] != 'contextualizes'):
            return False
    return True


def binding_postimage(key, record, quotes, material, records, sources):
    """Use the existing fragment builder while preserving every content field."""
    synthetic = {**record, 'evidence_step_id': key,
                 'statement': record.get('statement', record.get('text', record.get('title', ''))),
                 'speaker': record.get('speaker') or 'professor',
                 'stance': record.get('stance') or 'asserted'}
    post, fragments, projection = e.repair_postimage(synthetic, {
        'evidence_id': key, 'action': 'repair', 'statement': synthetic['statement'],
        'speaker': synthetic['speaker'], 'stance': synthetic['stance'],
        'quotes': quotes, 'reason': '唯一逐字／排版／繁簡對應，校準現版位置。'},
        material, records, sources)
    result = deepcopy(record)
    result.update(source_fragment_id=None, source_fragment_ids=post['source_fragment_ids'])
    if result.get('source_binding_status') in {'source_deleted', 'partially_deleted'}:
        result['source_binding_status'] = 'needs_claim_refresh'
    return result, fragments, projection


def prepare(records, sources):
    """The former lower-stage mechanical preparation, without content review."""
    working = deepcopy(records)
    materials = {}; calibration = {}; mechanical = []; excluded = []; outcomes = []; pending = []
    updates = {}; generated = {}; locator = None
    for key in sorted(records):
        if key.split('/')[0] not in f.LOWER or records[key].get('retired'):
            continue
        try:
            materials[key] = f.value(key, records, sources)
        except (ValueError, KeyError) as exc:
            if excluded_context(key, records):
                excluded.append(key)
                outcomes.append({'object_key': key, 'basis': 'excluded_unbound_dialogue_context',
                                 'source_verified': False, 'argument_use_permitted': False})
                continue
            if locator is None:
                locator = source_lookup.Locator(sources)
            try:
                material, quotes = locator.prepare(key, records, sources)
            except (ValueError, KeyError) as failure:
                pending.append({'object_key': key, 'reason': 'source_calibration_required', 'detail': str(failure)})
                continue
            if quotes is None:
                materials[key] = material; calibration[key] = str(exc)
                continue
            p = records[key]['payload']
            if p.get('review_status') in f.PROTECTED:
                pending.append({'object_key': key, 'reason': 'protected_lower_record'})
                continue
            post, frags, projection = binding_postimage(key, p, quotes, material, working, sources)
            allowed = {'source_fragment_id', 'source_fragment_ids', 'source_binding_status'}
            if any(post.get(k) != p.get(k) for k in set(post) | set(p) if k not in allowed):
                raise ValueError('mechanical source calibration changed record content')
            updates[key] = post; generated.update(frags)
            working = f.apply_overlay(working, {key: post}, frags)
            materials[key] = f.value(key, working, sources)
            mechanical.append({'object_key': key, 'quote_projection': projection})
    return dict(working=working, materials=materials, calibration=calibration, mechanical=mechanical,
                excluded=excluded, outcomes=outcomes, pending=pending, updates=updates, generated=generated)


def verify(records, sources, source_id):
    """Report physical bindings only; no database writes, theology or statement checks."""
    if set(sources) != {source_id}:
        raise ValueError('source verification requires one explicit source')
    source = sources[source_id]
    doc = records['source_documents/' + source_id]['payload']
    for field in ('source_body_sha256', 'source_sha256'):
        if doc.get(field) and doc[field] != source['body_sha256']:
            raise ValueError('registered source body differs: ' + field)
    f.source_unchanged(sources)
    prepared = prepare(records, sources)
    owners = {}
    for key, row in records.items():
        if key.split('/')[0] in f.LOWER and not row.get('retired'):
            for fid in e.evidence_fragment_ids(row['payload']):
                owners.setdefault(fid, []).append(key)
    fragments = []
    for key, row in sorted(records.items()):
        if not key.startswith('source_fragments/') or row.get('retired'):
            continue
        p = row['payload']
        if p.get('source_id') != source_id:
            continue
        fid = key.split('/', 1)[1]; used_by = owners.get(fid, [])
        item = {'fragment_id': fid, 'paragraph_key': p.get('paragraph_key'),
                'anchor_state': p.get('anchor_state'), 'used_by': used_by, 'source_verified': False}
        if p.get('fragment_id') != fid:
            item.update(status='unresolved' if used_by else 'unreferenced_unverified',
                        reason='fragment identity differs from record key')
        elif p.get('source_modality') == 'visual':
            item.update(status='future_release', reason='visual_semantic_verification_out_of_scope')
        elif p.get('anchor_state') in HISTORICAL and not used_by:
            item.update(status='historical', reason=p['anchor_state'])
        else:
            try:
                e.material({'evidence_step_id': key, 'statement': '', 'source_fragment_ids': [fid]}, records, sources)
                item.update(status='valid', source_verified=True)
            except (ValueError, KeyError) as exc:
                status = 'historical_excluded' if used_by and set(used_by) <= set(prepared['excluded']) else 'unresolved' if used_by else 'unreferenced_unverified'
                item.update(status=status, reason=str(exc))
        fragments.append(item)
    excluded = set(prepared['excluded']); updates = prepared['updates']
    visual_ids = {x['fragment_id'] for x in fragments if x['status'] == 'future_release'}
    valid_ids = {x['fragment_id'] for x in fragments if x['status'] == 'valid'}
    pending = {p['object_key']: p for p in prepared['pending']}
    entries = []
    for key in sorted(k for k in records if k.split('/')[0] in f.LOWER and not records[k].get('retired')):
        ids = set(e.evidence_fragment_ids(records[key]['payload']))
        if ids & visual_ids and ids <= visual_ids | valid_ids:
            status = 'future_release'
        elif key in excluded:
            status = 'historical_excluded'
        elif key in pending or key in prepared['calibration']:
            status = 'unresolved'
        elif key in updates:
            status = 'calibration_available'
        else:
            status = 'valid'
        entries.append({'object_key': key, 'status': status,
                        'fragment_ids': e.evidence_fragment_ids(records[key]['payload']),
                        **({'detail': pending[key]} if key in pending else {}),
                        **({'reason': prepared['calibration'][key]} if key in prepared['calibration'] else {})})
    # A payload/key mismatch must never pass merely because material() found the quote.
    bad_identity = {x['fragment_id'] for x in fragments if x.get('reason') == 'fragment identity differs from record key'}
    for item in entries:
        if bad_identity.intersection(item['fragment_ids']):
            item.update(status='unresolved', reason='fragment_identity_mismatch')
    unresolved = [x for x in entries if x['status'] == 'unresolved']
    from collections import Counter
    f.source_unchanged(sources)
    return {'contract': CONTRACT, 'stage': 'source', 'source_id': source_id,
            'source_body_sha256': source['body_sha256'], 'source_file': source['file'],
            'status': 'needs_attention' if unresolved else 'calibration_available' if updates else 'verified',
            'scope': 'existing textual source bindings; not semantic completeness or content approval',
            'records': entries, 'fragments': fragments,
            'counts': {'records': dict(Counter(x['status'] for x in entries)),
                       'fragments': dict(Counter(x['status'] for x in fragments))},
            'calibration_candidates': {'updates': updates, 'new_fragments': prepared['generated'],
                                       'receipts': prepared['mechanical']},
            'model_calls': 0, 'database_writes': 0, 'claim_qualification': 'not_granted',
            'future_release': ['visual_semantics', 'word_level_audio_alignment'],
            'timestamp_precision': 'paragraph_not_word'}


def run(store, source_id, root):
    root = Path(root).resolve(); root.mkdir(parents=True, exist_ok=True)
    with (root / 'source.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = f.bind_source(store, source_id, root)
        records, sources = f.load_source(store, source_id, visual_context=True)
        if pin != {'source_id': source_id, 'path': sources[source_id]['file']['path'],
                   'file_sha256': sources[source_id]['file']['file_sha256']}:
            raise ValueError('source changed between binding and census')
        e.save(root / 'intent.json', {'contract': CONTRACT, 'source_id': source_id,
               'source': sources[source_id]['file'], 'input_sha256': e.digest({'records': records, 'sources': sources}),
               'database_writes': False, 'model_calls': False})
        result = verify(records, sources, source_id)
        e.save(root / 'source-verification.json', result)
        return result
