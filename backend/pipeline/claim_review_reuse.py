"""Reuse an actual complete prior review without manufacturing a new model vote.

The proof is additive metadata on its original round. Raw answers, targets,
policies, budgets and content remain unchanged. Every consumer checks current
records and adjacency; newly rejected content cannot hide behind an old pass.
"""
import json
from copy import deepcopy
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_commit import check_binding, pins, pin_rows, decoded_pins

CONTEXT_CONTRACT = 'claim_complete_review_context_reuse_v2'


def build(store, claim_id, run_id):
    from backend.pipeline.claim_direct_scope import scope
    from backend.pipeline.claim_model_output import review as decode_review
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    value = store.get_cvp_group(run_id)
    if (not value or value.get('kind') != 'claim_management_round'
            or value.get('outcome') != 'completed' or not value.get('opus_approved')
            or not value.get('program_readback_complete')):
        raise ValueError('review reuse requires a completed, approved original review and readback')
    key = 'claims/' + claim_id
    stages = value['stage_results']
    approval = value['reviewed']['evidence']['approval']
    for bound in (stages['opus'], stages['readback'], approval):
        check_binding(bound)
    receipt = json.loads(Path(stages['readback']['path']).read_text())
    for name in ('readback', 'candidate', 'materials'):
        check_binding(receipt[name])
    old = json.loads(Path(receipt['readback']['path']).read_text())['actual_records']
    if key not in old:
        raise ValueError('Claim was not part of the saved complete review')
    materials = value['materials']
    candidate = cm.Candidate.model_validate(value['candidate'])
    raw = json.loads(Path(approval['path']).read_text())['response']
    if isinstance(raw, str):
        raw = json.loads(raw)
    review = decode_review(raw, materials, candidate)
    request = cm.Request.model_validate(materials['unit_request'])
    if materials['policy'].get('claim_scope_contract') == 'claim_direct_neighbours_v1':
        if key not in scope(request, materials['before'], candidate)['content_claim_keys']:
            raise ValueError('reference-only context is not a complete Claim review')
    denominator = cm.audit_keys(request, materials['before'], candidate)
    cm.validate_review(candidate, review, denominator, cm.operation_questions(materials['original_goal'], candidate),
                       materials['unresolved'], materials['sources'], request=request, records=materials['before'])
    approved = {v.record_key for v in review.verdicts if v.approved and not v.problems}
    if key not in approved:
        raise ValueError('Claim has an unresolved objection in the prior review')
    current_states = store.read_claim_context([('claims', claim_id)])
    current = {cm.source.key(*k): cm.model_state(v) for k, v in current_states.items()}
    if key not in current or current[key]['retired'] or current[key]['payload'].get('review_status') in cm.HELD:
        raise ValueError('review reuse cannot release retired or held content')
    # Capabilities and other target identities from the old task do not seed
    # a new review. Both old and current direct scope are compared exactly.
    own = request.model_copy(deep=True, update={'target_keys': [key], 'permissions': []})
    left, right = scope(own, old), scope(own, current)
    required = set(left['record_keys']) | set(right['record_keys'])
    sources = {}
    for sid, bound in materials['request']['source_bindings'].items():
        sources[sid] = verify_current_source_binding(bound)
    for record in required:
        if record not in old or record not in current:
            raise ValueError('review reuse has a new or missing required dependency: ' + record)
        a, b = old[record], current[record]
        pa = {k:v for k,v in a['payload'].items() if k != 'revision'}
        pb = {k:v for k,v in b['payload'].items() if k != 'revision'}
        changes = {k for k in pa.keys() | pb.keys() if pa.get(k) != pb.get(k)}
        editorial = (record.startswith('source_documents/') and record.split('/', 1)[1] in sources
                     and changes <= {'source_file_sha256', 'editorial_structure_sha256', 'editorial_topology_sha256'})
        if (changes and not editorial) or a['retired'] != b['retired']:
            raise ValueError('review reuse argument/context changed: ' + record)
    neighbours = set(left['neighbour_claim_keys']) | set(right['neighbour_claim_keys'])
    if required - neighbours - approved:
        raise ValueError('review reuse cannot conceal an unapproved required object')
    proof = {'contract': 'claim_complete_review_reuse_v1', 'claim_id': claim_id,
             'review_run_id': run_id, 'approval': approval, 'readback': receipt['readback'],
             'source_bindings': sources, 'scope_request': own.model_dump(mode='json'),
             'protected_record_keys': sorted(required - neighbours),
             'read_pins': pin_rows(pins({k:v for k,v in current_states.items() if cm.source.key(*k) in required}))}
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        assert_no_newer_rejection(cursor, claim_id, run_id, proof['protected_record_keys'])
    proof['proof_sha256'] = cm.sha256_json(proof)
    return proof


def assert_no_newer_rejection(cursor, claim_id, run_id, protected):
    cursor.execute("""SELECT metadata->'findings',metadata->'context_findings',
        metadata->'blocked_current_claim_ids',metadata->'materials'->'original_goal'->'target_keys',
        metadata->>'outcome',metadata->'opus_approved',metadata->'program_readback_complete',
        metadata->'materials'->'policy'->>'review_unit_contract'
        FROM wang_knowledge.pipeline_runs
        WHERE metadata->>'kind'='claim_management_round' AND metadata->'claim_ids' ? %s
          AND started_at >= (SELECT started_at FROM wang_knowledge.pipeline_runs WHERE run_id=%s)
        ORDER BY started_at DESC,run_id DESC""", (claim_id, run_id))
    key = 'claims/' + claim_id
    for findings, context, blocked, targets, outcome, approved, readback, unit in cursor.fetchall():
        if unit == 'claim_bounded_review_unit_v1':
            # Reference leads stay unadjudicated. An issue on this target's
            # own data, wrongly deferred by an older classifier, still means
            # its complete Update is unfinished; do not reuse that old green.
            context = {fid:f for fid,f in (context or {}).items()
                       if key in (targets or []) and f.get('target_key') in protected
                       and not f.get('target_key','').startswith('claims/')}
        if (claim_id in (blocked or [])
                or any(f.get('target_key') in protected or key in (f.get('affected_target_keys') or [])
                       for f in list((findings or {}).values()) + list((context or {}).values()))):
            raise ValueError('review reuse has a newer unresolved Claim objection')
        # A newer completed target pass supersedes earlier rejected rounds.
        if (key in (targets or []) and outcome == 'completed' and approved and readback):
            return


def verify(store, cursor, claim_id, proof):
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    if proof.get('contract') not in {'claim_complete_review_reuse_v1', CONTEXT_CONTRACT} or proof.get('claim_id') != claim_id:
        raise ValueError('review reuse identity drift')
    if proof.get('proof_sha256') != cm.sha256_json({k:v for k,v in proof.items() if k != 'proof_sha256'}):
        raise ValueError('review reuse proof seal drift')
    cursor.execute("""SELECT metadata->>'outcome',metadata->'opus_approved',
        metadata->'program_readback_complete',metadata->'reviewed'->'evidence'->'approval'
        FROM wang_knowledge.pipeline_runs WHERE run_id=%s""", (proof['review_run_id'],))
    row = cursor.fetchone()
    if not row or row[:3] != ('completed', True, True) or row[3] != proof['approval']:
        raise ValueError('original completed review approval changed')
    for name in ('approval', 'readback'):
        check_binding(proof[name])
    for bound in proof['source_bindings'].values():
        verify_current_source_binding(bound)
    assert_no_newer_rejection(cursor, claim_id, proof['review_run_id'], proof['protected_record_keys'])
    states = store._assert_transaction_read_pins(cursor, decoded_pins(proof['read_pins']))
    if proof['contract'] == CONTEXT_CONTRACT:
        verify_context_reviews(store, cursor, proof)
    # The reused proof explicitly protects a direct Claim basis, even when
    # its original review used the legacy transitive scope. Dispatching by
    # that old policy would demand neighbours' private Evidence again.
    from backend.pipeline.claim_direct_scope import assert_current
    assert_current(store, cursor, cm.Request.model_validate(proof['scope_request']), states)
    state = states[('claims', claim_id)]
    if state['retired_at'] is not None or state['payload'].get('review_status') in cm.HELD:
        raise ValueError('review reuse cannot release retired or held content')
    return {'ready': True, 'run_id': proof['review_run_id'], 'revision': state['revision'],
            'content_sha256': state['content_sha256'], 'audit': proof['readback'],
            'validation_kind': 'reused_complete_review_and_current_program_check',
            'independent_audit_complete': False}


def admit(store, claim_id, run_id):
    proof = build(store, claim_id, run_id)
    from backend.api.canonical_repository.viewpoint_foundation import canonical_json
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (run_id,))
        value = cursor.fetchone()[0]
        verify(store, cursor, claim_id, proof)
        value.setdefault('readonly_claim_qualifications', {})[claim_id] = proof
        cursor.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
                       (canonical_json(value), run_id))
    return proof


def verify_context_reviews(store, cursor, proof):
    """Old full approval plus a current, role-scoped check of changed neighbours.

    Own Claim/evidence/source/incident edges must remain exactly unchanged.
    This is not permission to promote a reference into a new full approval.
    One latest review per changed neighbour keeps the proof flat.
    """
    from backend.pipeline.claim_qualification_reuse import evidence
    from backend.pipeline.claim_direct_scope import scope, assert_current
    original = decoded_pins(proof['original_read_pins'])
    current = decoded_pins(proof['read_pins'])
    protected = {cm.source.split_key(k) for k in proof['protected_record_keys']}
    if set(original) != set(current) or any(original[k] != current[k] for k in protected):
        raise ValueError('context reuse changed the original Claim or its own basis')
    changed = {cm.source.key(*k) for k in original if original[k] != current[k]}
    if not changed or changed != set(proof['context_reviews']) or any(
            not k.startswith('claims/') or cm.source.split_key(k) in protected for k in changed):
        raise ValueError('context reuse permits only reviewed direct neighbour changes')
    key = 'claims/' + proof['claim_id']
    peers = {}
    for neighbour, saved in proof['context_reviews'].items():
        rid = saved['round_id']
        if rid not in peers:
            row = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s', (rid,)).fetchone()
            if not row:
                raise ValueError('context reuse lost its peer review')
            peer = row[0]
            if peer['materials']['policy'].get('review_unit_contract') != 'claim_bounded_review_unit_v1':
                raise ValueError('context reuse needs an explicit role-scoped review')
            readback, review = evidence(peer)
            receipt = json.loads(Path(readback['path']).read_text())
            for name in ('readback', 'candidate', 'materials'):
                check_binding(receipt[name])
            request = cm.Request.model_validate(peer['materials']['unit_request'])
            candidate = cm.Candidate.model_validate(peer['candidate'])
            cm.validate_review(candidate, cm.Review.model_validate(review),
                cm.audit_keys(request, peer['materials']['before'], candidate),
                cm.operation_questions(peer['materials']['original_goal'], candidate),
                peer['materials']['unresolved'], peer['materials']['sources'],
                request=request, records=peer['materials']['before'])
            pending = cm.review_findings(request.action_id, peer['materials']['unresolved'], candidate)
            outstanding = list(review['findings']) + [cm.resolution_finding(
                cm.FindingResolution.model_validate(r), pending) for r in review['prior_findings']
                if r['disposition'] != 'resolved']
            fresh = decoded_pins(peer['audited_pins'])
            saved_records = json.loads(Path(receipt['readback']['path']).read_text())['actual_records']
            saved_pins = pins({cm.source.split_key(k):v for k,v in saved_records.items()})
            if any(saved_pins.get(k) != pin for k,pin in fresh.items()):
                raise ValueError('context peer pins do not match its saved readback')
            observed = store._assert_transaction_read_pins(cursor, fresh)
            writes = {d.record_key for d in candidate.dispositions if d.action != 'retain'}
            assert_current(store, cursor, request, observed, actual_write_keys=writes)
            if 'human_source_decisions' in peer['materials']:
                from backend.pipeline.claim_human_source import assert_current as assert_human_current
                assert_human_current(cursor, request.source_bindings, peer['materials']['human_source_decisions'])
            peers[rid] = (peer, readback, review, fresh, writes, outstanding,
                          scope(request, peer['materials']['before'], candidate))
        peer, readback, review, fresh, writes, outstanding, selected = peers[rid]
        if saved != {'round_id': rid, 'approval': peer['reviewed']['evidence']['approval'], 'readback': readback}:
            raise ValueError('context reuse peer approval identity drift')
        if (neighbour not in selected['content_claim_keys'] or neighbour not in writes
                or key not in selected['neighbour_claim_keys']):
            raise ValueError('peer did not review the changed neighbour and original reference role')
        retained = {d['record_key'] for d in peer['candidate']['dispositions'] if d['action'] == 'retain'}
        approved = {v['record_key'] for v in review['verdicts'] if v['approved'] and not v['problems']}
        shared = protected & set(fresh)
        needed = {cm.source.key(*k) for k in shared} | {key, neighbour}
        if (not needed <= approved or key not in retained
                or any(cm.source.key(*k) not in retained or original[k] != fresh[k] for k in shared)
                or fresh.get(cm.source.split_key(neighbour)) != current[cm.source.split_key(neighbour)]):
            raise ValueError('peer omitted or changed the retained original basis')
        # Even a context finding cannot silently negate the preserved Claim.
        if any(f['target_key'] in needed or key in (f.get('affected_target_keys') or [])
               or set(f.get('affected_write_keys') or []) & needed for f in outstanding):
            raise ValueError('peer has an unresolved impact on the original Claim')


def revalidate_context(store, cursor, claim_id, run_id, peer_id):
    """Caller holds the transaction lock; no model call or content write."""
    from backend.api.canonical_repository.viewpoint_foundation import canonical_json
    row = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s FOR UPDATE', (run_id,)).fetchone()
    if not row or claim_id not in row[0].get('readonly_claim_qualifications', {}):
        raise ValueError('context reuse requires an existing complete qualification')
    value = row[0]
    proof = deepcopy(value['readonly_claim_qualifications'][claim_id])
    if (proof.get('contract') not in {'claim_complete_review_reuse_v1', CONTEXT_CONTRACT}
            or proof.get('review_run_id') != run_id or proof.get('claim_id') != claim_id):
        raise ValueError('original reuse proof identity drift')
    if proof['proof_sha256'] != cm.sha256_json({k:v for k,v in proof.items() if k != 'proof_sha256'}):
        raise ValueError('original reuse proof seal drift')
    old = decoded_pins(proof['read_pins'])
    records = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s', (peer_id,)).fetchone()
    if not records:
        raise ValueError('missing peer review')
    peer = records[0]
    fresh = decoded_pins(peer['audited_pins'])
    changed = {k for k in old.keys() & fresh.keys() if old[k] != fresh[k]}
    if not changed:
        # An already restored proof may be replayed without another event.
        verify(store, cursor, claim_id, proof)
        return proof
    proof.setdefault('original_read_pins', deepcopy(proof['read_pins']))
    proof.setdefault('context_reviews', {})
    for k in changed:
        old[k] = fresh[k]
        proof['context_reviews'][cm.source.key(*k)] = {
            'round_id': peer_id, 'approval': peer['reviewed']['evidence']['approval'],
            'readback': peer['stage_results']['readback']}
    proof.update(contract=CONTEXT_CONTRACT, read_pins=pin_rows(old))
    proof['proof_sha256'] = cm.sha256_json({k:v for k,v in proof.items() if k != 'proof_sha256'})
    verify(store, cursor, claim_id, proof)
    value['readonly_claim_qualifications'][claim_id] = proof
    cursor.execute('UPDATE wang_knowledge.pipeline_runs SET metadata=%s::jsonb WHERE run_id=%s',
                   (canonical_json(value), run_id))
    return proof


def after_completion(store, cursor, peer_id, peer):
    """Reconcile only already admitted qualifications touched by this write set."""
    if peer['materials']['policy'].get('review_unit_contract') != 'claim_bounded_review_unit_v1':
        return
    writes = [d['record_key'] for d in peer['candidate']['dispositions'] if d['action'] != 'retain']
    if not writes:
        return
    cursor.execute("""SELECT r.run_id,q.key FROM wang_knowledge.pipeline_runs r
        CROSS JOIN LATERAL jsonb_each(r.metadata->'readonly_claim_qualifications') q
        WHERE r.metadata ? 'readonly_claim_qualifications'
          AND EXISTS (SELECT 1 FROM jsonb_array_elements(q.value->'read_pins') p
              WHERE (p->>'collection')||'/'||(p->>'object_id')=ANY(%s))
        ORDER BY r.run_id,q.key""", (writes,))
    from backend.api.canonical_repository.postgres_store import ChangeSetConflict
    report = []
    for rid, cid in cursor.fetchall():
        # Failure leaves its old proof stale, never masks the content commit.
        # The caller's final qualification checks expose focused follow-up.
        try:
            proof = revalidate_context(store, cursor, cid, rid, peer_id)
            report.append({'claim_id': cid, 'original_round_id': rid,
                           'status': 'current', 'proof_sha256': proof['proof_sha256']})
        except (ValueError, OSError, KeyError, ChangeSetConflict) as exc:
            report.append({'claim_id': cid, 'original_round_id': rid,
                           'status': 'deferred', 'reason': str(exc)})
    from backend.api.canonical_repository.viewpoint_foundation import canonical_json
    cursor.execute("""UPDATE wang_knowledge.pipeline_runs SET metadata=
        jsonb_set(metadata,'{reference_qualification_handoffs}',%s::jsonb,true) WHERE run_id=%s""",
        (canonical_json(report), peer_id))
