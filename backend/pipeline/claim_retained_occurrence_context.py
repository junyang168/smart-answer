"""Read-only historical occurrences after the fixed reviewed Evidence subset.

This is not multi-source repair authority. The primary source remains the sole
current Evidence source; historical occurrence records cannot be edited. The
ordinary policy may still repair anchors in the current source occurrence.
"""
import json
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline import source_reconciliation as c


def validate_occurrence_delta(frozen, claim_id, old, new):
    """Protect exact historical occurrences, not the whole Claim field."""
    receipts = [r for r in frozen.get('claim_issue_context', {}).get(
        'retained_occurrence_context', []) if r['claim_id'] == claim_id]
    if not receipts:
        return
    if len(receipts) != 1 or receipts[0]['occurrences'] != old['occurrences']:
        raise ValueError('retained occurrence original generation drift')
    sid = frozen['manifest']['source_id']
    states = c.index(frozen['before_records'])
    own = states[c.key('source_documents', sid)]
    aliases = {sid, own.get('transcript_id'), own.get('canonical_source_id')} - {None, ''}
    previous = old['occurrences']; current = new['occurrences']
    if len(previous) != len(current):
        raise ValueError('retained occurrence membership cannot change')
    for before, after in zip(previous, current):
        is_own = bool(aliases.intersection(c.strings(before)))
        if not is_own:
            if before != after:
                raise ValueError('historical occurrence must remain exact')
        elif ({k:v for k,v in before.items() if k != 'anchors'} !=
              {k:v for k,v in after.items() if k != 'anchors'}):
            raise ValueError('current occurrence identity must remain exact')


def build(store, claim_id, source_id):
    from backend.pipeline.cvp_sql_retained_evidence_locator_authority import authority
    row = store.get_record_state('claims', claim_id)
    proof = authority(store, claim_id, row)
    if proof is None:
        raise ValueError('foreign occurrence lacks exact reviewed subset authority')
    plan = json.loads(c.bound_bytes(proof['bindings'][0]))
    sid = plan['source']['source_id']
    doc = store.get_record_state('source_documents', sid)
    if not doc or doc['retired']:
        raise ValueError('historical occurrence source missing/retired')
    return {'claim_id': claim_id, 'source_id': source_id, 'authority': proof,
            'historical_source_pin': {'collection': 'source_documents', 'object_id': sid,
                'pin': [doc['revision'], doc['content_sha256'], False]},
            'occurrences': row['payload']['occurrences']}


def validate(receipts, states, source_id, claim_ids, snapshots=None):
    accepted = set()
    for receipt in receipts:
        cid = receipt['claim_id']; proof = receipt['authority']
        claim = states[c.key('claims', cid)]
        if (cid not in claim_ids or cid in accepted or receipt['source_id'] != source_id
                or proof['authority_kind'] != 'fixed_reviewed_evidence_subset_notes_then_alias_locator'
                or proof['claim_id'] != cid or proof['revision'] != claim['revision']
                or proof['content_sha256'] != record_content_sha(claim)
                or receipt['occurrences'] != claim['occurrences']
                or proof['decision'] != 'ai_consensus_reviewed'
                or len(proof['exact_applied_change_set_ids']) != 4):
            raise ValueError('historical occurrence exact reviewed generation drift')
        for binding in proof['bindings']:
            c.bound_bytes(binding, snapshots)
        plan = json.loads(c.bound_bytes(proof['bindings'][0], snapshots))
        if receipt['historical_source_pin']['object_id'] != plan['source']['source_id']:
            raise ValueError('historical occurrence source is not the sealed notes transform')
        for pin in [receipt['historical_source_pin'], *proof['current_dependency_pins']]:
            value = states[c.key(pin['collection'], pin['object_id'])]
            if pin['pin'] != [value['revision'], record_content_sha(value), False]:
                raise ValueError('historical occurrence dependency pin drift')
        historical = states[c.key('source_documents', receipt['historical_source_pin']['object_id'])]
        own = states[c.key('source_documents', source_id)]
        if (historical.get('review_status') in c.HUMAN_HOLDS | {'human_hold', 'rejected', 'withheld_unreviewed'}
                or historical['source_path'] != plan['source']['source_path']
                or historical['source_file_sha256'] != plan['source']['source_file_sha256']):
            raise ValueError('historical occurrence physical source/hold drift')
        aliases = [{d['source_id'], d.get('transcript_id'), d.get('canonical_source_id')} - {None, ''}
                   for d in [historical, own]]
        if (historical['source_id'] == source_id or not claim['occurrences']
                or any(not any(a & set(c.strings(o)) for a in aliases) for o in claim['occurrences'])):
            raise ValueError('historical occurrence is foreign/ambiguous')
        accepted.add(cid)
    return accepted
