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


def human_evidence_snapshot(cursor, source_ids, frozen, records):
    """An unrelated paragraph confirmation does not undo an unchanged review.

    Bind the entire current decision snapshot afterwards: a later change must
    be explicitly reconciled again, never silently inherited by this proof.
    """
    from backend.pipeline.claim_human_source import snapshot
    current = snapshot(cursor, source_ids)
    if current == frozen:
        return current
    positions = {sid: set() for sid in source_ids}

    def visit(value, inherited):
        if isinstance(value, dict):
            sid = value.get('source_id')
            sources = {sid} if sid in positions else inherited
            if value.get('paragraph_key'):
                for source_id in sources:
                    positions[source_id].add(value['paragraph_key'])
            for child in value.values():
                visit(child, sources)
        elif isinstance(value, list):
            for child in value:
                visit(child, inherited)

    for key, row in records.items():
        if not key.startswith('source_documents/'):
            visit(row['payload'], set(source_ids))
    before = {v['decision_id']: v for v in frozen}
    after = {v['decision_id']: v for v in current}
    for ident in before.keys() | after.keys():
        if before.get(ident) == after.get(ident):
            continue
        for value in (before.get(ident), after.get(ident)):
            if value is None:
                continue
            located = positions.get(value['source_id'], set())
            if not located or located & set(value['paragraph_sha256']):
                raise ValueError('review reuse human source evidence changed in the Claim scope')
    return current


def owner_extension(before, after, *, field="produced_claim_ids"):
    """Only additive Claim references; all other content stays exact."""
    ignored = {'revision', field}
    if {k:v for k,v in before.items() if k not in ignored} != {k:v for k,v in after.items() if k not in ignored}:
        return set()
    old, new = before.get(field, []), after.get(field, [])
    added = set(new) - set(old)
    return added if added and len(set(new)) == len(new) and [v for v in new if v not in added] == old else set()


def answer_extension(before, after):
    """A reviewed additional answer cannot change the question or old answers."""
    return owner_extension(before, after, field="answer_claim_ids")


def retired_incident_relation(key, before, after, claim_key):
    """Only the approved retirement of an existing Claim incident edge."""
    fields = ('from_id', 'to_id', 'source_id', 'target_id', 'from_claim_id', 'to_claim_id')
    return (key.startswith('claim_relations/') and not before['retired'] and after['retired']
            and claim_key.split('/', 1)[1] in {before['payload'].get(f) for f in fields}
            and {k:v for k,v in before['payload'].items() if k != 'revision'} ==
                {k:v for k,v in after['payload'].items() if k != 'revision'})


def build(store, claim_id, run_id, *, peer_run_id=None):
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
    if key not in current or current[key]['retired']:
        raise ValueError('review reuse cannot release retired or held content')
    if current[key]['payload'].get('review_status') in cm.HELD:
        from backend.pipeline.claim_qualification_reuse import legacy_spot_check_replaced
        with store.connect() as conn, conn.cursor() as cursor:
            if not legacy_spot_check_replaced(cursor, claim_id, current[key], value):
                raise ValueError('review reuse cannot release retired or held content')
    # Capabilities and other target identities from the old task do not seed
    # a new review. Both old and current direct scope are compared exactly.
    own = request.model_copy(deep=True, update={'target_keys': [key], 'permissions': []})
    left, right = (scope(own, rows, include_write_context=False) for rows in (old, current))
    required = set(left['record_keys']) | set(right['record_keys'])
    neighbours = set(left['neighbour_claim_keys']) | set(right['neighbour_claim_keys'])
    changed_context = set()
    sources = {}
    for sid, bound in materials['request']['source_bindings'].items():
        sources[sid] = verify_current_source_binding(bound)
    # Current graph reads omit tombstones and their now-disconnected endpoints.
    # Load only old incident-edge tombstones and old leaf Claim identities;
    # the completed peer must still prove the exact retirement below.
    for record in required:
        if record in old and record not in current and record.startswith('claim_relations/'):
            state = store.get_record_state(*cm.source.split_key(record))
            if state and state.get('retired'):
                current_states[cm.source.split_key(record)] = state
                current[record] = cm.model_state(state)
    for record in required:
        if (record in neighbours and record in old and record not in current
                and any(retired_incident_relation(edge, old[edge], current[edge], key)
                        for edge in required & old.keys() & current.keys()
                        if edge.startswith('claim_relations/')
                        and record.split('/',1)[1] in {old[edge]['payload'].get('from_id'), old[edge]['payload'].get('to_id')})):
            state = store.get_record_state(*cm.source.split_key(record))
            if state:
                current_states[cm.source.split_key(record)] = state
                current[record] = cm.model_state(state)
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
            extension = (record.startswith('evidence_steps/') and owner_extension(pa, pb)
                         or record.startswith('questions/') and answer_extension(pa, pb)
                         or retired_incident_relation(record, a, b, key))
            if (not peer_run_id or not (record in neighbours and record.startswith('claims/') or extension)
                    or a['retired'] or b['retired'] and not retired_incident_relation(record, a, b, key)):
                raise ValueError('review reuse argument/context changed: ' + record)
            changed_context.add(record)
    if required - neighbours - approved:
        raise ValueError('review reuse cannot conceal an unapproved required object')
    proof = {'contract': 'claim_complete_review_reuse_v1', 'claim_id': claim_id,
             'review_run_id': run_id, 'approval': approval, 'readback': receipt['readback'],
             'source_bindings': sources, 'scope_request': own.model_dump(mode='json'),
             'protected_record_keys': sorted(required - neighbours),
             'read_pins': pin_rows(pins({k:v for k,v in current_states.items() if cm.source.key(*k) in required}))}
    if changed_context:
        # A fresh target approval and an admitted bystander approval have the
        # same own-Claim proof. Join the already reviewed neighbour delta
        # atomically; never admit an intermediate stale qualification.
        peer = store.get_cvp_group(peer_run_id)
        if not peer or not peer.get('reviewed'):
            raise ValueError('missing completed peer review for changed neighbour')
        original = decoded_pins(proof['read_pins'])
        for record in changed_context:
            row=old[record]
            original[cm.source.split_key(record)]=(row['revision'],row['content_sha256'],row['retired'])
        proof.update(contract=CONTEXT_CONTRACT, original_read_pins=pin_rows(original),
            context_reviews={record:{'round_id':peer_run_id,
                'approval':peer['reviewed']['evidence']['approval'],
                'readback':peer['stage_results']['readback']} for record in changed_context})
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY')
        if not changed_context:
            assert_no_newer_rejection(cursor, claim_id, run_id, proof['protected_record_keys'])
        proof['human_source_decisions'] = human_evidence_snapshot(
            cursor, request.source_bindings, materials.get('human_source_decisions', []),
            {k: current[k] for k in required})
    proof['proof_sha256'] = cm.sha256_json(proof)
    if changed_context:
        with store.connect() as conn, conn.cursor() as cursor:
            verify(store,cursor,claim_id,proof)
    return proof


def assert_no_newer_rejection(cursor, claim_id, run_id, protected, *, resolved=None):
    resolved = resolved or {}
    cursor.execute("""SELECT metadata->'findings',metadata->'context_findings',
        metadata->'blocked_current_claim_ids',metadata->'materials'->'original_goal'->'target_keys',
        metadata->>'outcome',metadata->'opus_approved',metadata->'program_readback_complete',
        metadata->'materials'->'policy'->>'review_unit_contract',metadata->>'parent_run_id',started_at
        FROM wang_knowledge.pipeline_runs
        WHERE metadata->>'kind'='claim_management_round' AND metadata->'claim_ids' ? %s
          AND started_at >= (SELECT started_at FROM wang_knowledge.pipeline_runs WHERE run_id=%s)
        ORDER BY started_at DESC,run_id DESC""", (claim_id, run_id))
    key = 'claims/' + claim_id
    for findings, context, blocked, targets, outcome, approved, readback, unit, parent, started in cursor.fetchall():
        if unit == 'claim_bounded_review_unit_v1':
            # Reference leads stay unadjudicated. An issue on this target's
            # own data, wrongly deferred by an older classifier, still means
            # its complete Update is unfinished; do not reuse that old green.
            context = {fid:f for fid,f in (context or {}).items()
                       if key in (targets or []) and f.get('target_key') in protected
                       and not f.get('target_key','').startswith('claims/')}
        relevant = any(f.get('target_key') in protected or key in (f.get('affected_target_keys') or [])
                       for f in (findings or {}).values())
        def was_resolved(fid, finding):
            entry = resolved.get((parent, fid))
            return entry and entry['finding'] == finding and started < entry['resolved_at']
        fully_resolved = relevant and all(was_resolved(fid, f) for fid, f in findings.items())
        findings = {fid:f for fid,f in (findings or {}).items() if not was_resolved(fid, f)}
        if (claim_id in (blocked or []) and not fully_resolved
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
    from backend.pipeline.claim_human_source import assert_current as assert_human_current
    if 'human_source_decisions' in proof:
        assert_human_current(cursor, proof['source_bindings'], proof['human_source_decisions'])
    else:
        # Historical proofs did not pin this separate evidence registry.
        # They can only keep their original exact decision snapshot.
        material = cursor.execute('SELECT metadata->\'materials\' FROM wang_knowledge.pipeline_runs WHERE run_id=%s',
                                  (proof['review_run_id'],)).fetchone()[0]
        assert_human_current(cursor, proof['source_bindings'], material.get('human_source_decisions', []))
    states = store._assert_transaction_read_pins(cursor, decoded_pins(proof['read_pins']))
    resolved = {}
    if proof['contract'] == CONTEXT_CONTRACT:
        resolved = verify_context_reviews(store, cursor, proof)
    assert_no_newer_rejection(cursor, claim_id, proof['review_run_id'], proof['protected_record_keys'], resolved=resolved)
    # The reused proof explicitly protects a direct Claim basis, even when
    # its original review used the legacy transitive scope. Dispatching by
    # that old policy would demand neighbours' private Evidence again.
    from backend.pipeline.claim_direct_scope import assert_current
    assert_current(store, cursor, cm.Request.model_validate(proof['scope_request']), states,
                   include_write_context=False)
    from backend.pipeline.claim_evidence_review import enabled as own_evidence_enabled, assert_current_fragments
    if own_evidence_enabled(cm.Request.model_validate(proof['scope_request'])):
        material = cursor.execute('SELECT metadata->\'materials\' FROM wang_knowledge.pipeline_runs WHERE run_id=%s',
                                  (proof['review_run_id'],)).fetchone()[0]
        assert_current_fragments({col+'/'+oid:s['payload'] for (col,oid),s in states.items()
                                  if s['retired_at'] is None}, material['sources'], ['claims/'+claim_id])
    state = states[('claims', claim_id)]
    if state['retired_at'] is not None:
        raise ValueError('review reuse cannot release retired or held content')
    if state['payload'].get('review_status') in cm.HELD:
        from backend.pipeline.claim_qualification_reuse import legacy_spot_check_replaced
        reviewed = cursor.execute('SELECT metadata FROM wang_knowledge.pipeline_runs WHERE run_id=%s',
                                  (proof['review_run_id'],)).fetchone()[0]
        if not legacy_spot_check_replaced(cursor, claim_id, state, reviewed):
            raise ValueError('review reuse cannot release retired or held content')
    return {'ready': True, 'run_id': proof['review_run_id'], 'revision': state['revision'],
            'content_sha256': state['content_sha256'], 'audit': proof['readback'],
            'validation_kind': 'reused_complete_review_and_current_program_check',
            'independent_audit_complete': False}


def admit(store, claim_id, run_id, *, peer_run_id=None):
    proof = build(store, claim_id, run_id, peer_run_id=peer_run_id)
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

    Own Claim, evidence content, source and incident edges stay unchanged.
    Additive Evidence consumers and Question answers require a full peer review
    of each added Claim and the shared update, preserving the original role.
    This is not permission to promote a reference into a new full approval.
    One latest review per changed neighbour keeps the proof flat.
    """
    from backend.pipeline.claim_qualification_reuse import evidence
    from backend.pipeline.claim_direct_scope import scope, assert_current
    original = decoded_pins(proof['original_read_pins'])
    current = decoded_pins(proof['read_pins'])
    protected = {cm.source.split_key(k) for k in proof['protected_record_keys']}
    if set(original) != set(current) or any(original[k] != current[k] for k in protected if k[0] not in {'evidence_steps', 'questions', 'claim_relations'}):
        raise ValueError('context reuse changed the original Claim or its own basis')
    changed = {cm.source.key(*k) for k in original if original[k] != current[k]}
    if not changed or changed != set(proof['context_reviews']) or any(
            not k.startswith(('claims/', 'evidence_steps/', 'questions/', 'claim_relations/')) or
            k.startswith('claims/') and cm.source.split_key(k) in protected for k in changed):
        raise ValueError('context reuse permits only reviewed direct neighbour changes')
    key = 'claims/' + proof['claim_id']
    peers, resolved = {}, {}
    for neighbour, saved in proof['context_reviews'].items():
        rid = saved['round_id']
        if rid not in peers:
            row = cursor.execute('SELECT metadata,started_at FROM wang_knowledge.pipeline_runs WHERE run_id=%s', (rid,)).fetchone()
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
            writes = {d.record_key for d in candidate.dispositions if d.action != 'retain'}
            peers[rid] = (peer, readback, review, fresh, writes, outstanding,
                          scope(request, peer['materials']['before'], candidate), saved_records, request, row[1])
        peer, readback, review, fresh, writes, outstanding, selected, saved_records, request, started = peers[rid]
        if saved != {'round_id': rid, 'approval': peer['reviewed']['evidence']['approval'], 'readback': readback}:
            raise ValueError('context reuse peer approval identity drift')
        extension = set()
        retirement = False
        old_receipt = json.loads(Path(proof['readback']['path']).read_text())
        if neighbour.startswith(('evidence_steps/', 'questions/')):
            old_payload = old_receipt['actual_records'][neighbour]['payload']
            is_evidence = neighbour.startswith('evidence_steps/')
            extension = (owner_extension if is_evidence else answer_extension)(
                old_payload, saved_records[neighbour]['payload'])
            if (not extension or any('claims/' + cid not in selected['content_claim_keys'] or
                    is_evidence and neighbour.split('/',1)[1] not in saved_records['claims/'+cid]['payload'].get('evidence_step_ids', [])
                    for cid in extension)):
                raise ValueError('context reuse changed shared content beyond reviewed additive Claims')
        roots = {'claims/'+cid for cid in extension} or {neighbour}
        if neighbour.startswith('claim_relations/'):
            retirement = retired_incident_relation(neighbour, old_receipt['actual_records'][neighbour],
                saved_records[neighbour], key)
            if (not retirement or not any(d['record_key']==neighbour and d['action']=='retire'
                                         for d in peer['candidate']['dispositions'])):
                raise ValueError('context reuse requires an exact reviewed incident-edge retirement')
            payloads = {k:v['payload'] for k,v in saved_records.items()}
            roots = set(cm.domain_dependencies('claim_relations', payloads[neighbour], payloads)) & set(selected['content_claim_keys'])
        if ((not extension and not retirement and neighbour not in selected['content_claim_keys']) or neighbour not in writes
                or key not in selected['neighbour_claim_keys']):
            raise ValueError('peer did not review the changed neighbour and original reference role')
        if not roots:
            raise ValueError('context peer has no fully reviewed relevant Claim')
        scoped = request.model_copy(deep=True, update={'target_keys':sorted(roots), 'permissions':[]})
        wanted = set(scope(scoped, saved_records, include_write_context=False)['record_keys'])
        shared = protected & set(fresh)
        wanted.update(cm.source.key(*k) for k in shared)
        wanted.update({key, neighbour})
        if not {cm.source.split_key(k) for k in wanted} <= set(fresh):
            raise ValueError('context peer omitted the complete relevant Claim basis')
        relevant = {cm.source.split_key(k):fresh[cm.source.split_key(k)] for k in wanted}
        if 'human_source_decisions' in peer['materials']:
            # The outer proof already binds the complete current decision
            # snapshot. Reconcile the peer against this delta's complete
            # relevant Claim/evidence basis, just as for the original review;
            # an unrelated paragraph is not a reason to repeat its audit.
            human_evidence_snapshot(cursor, request.source_bindings,
                peer['materials']['human_source_decisions'],
                {cm.source.key(*k):saved_records[cm.source.key(*k)] for k in relevant})
        # Different records can be certified by different completed peers.
        # Consume only this delta's own basis, while retaining exact current
        # pins for another delta independently checked in this flat proof.
        for record, saved_review in proof['context_reviews'].items():
            pair = cm.source.split_key(record)
            if record != neighbour and saved_review['round_id'] != rid and pair in relevant:
                relevant[pair] = current[pair]
        observed = store._assert_transaction_read_pins(cursor, relevant)
        assert_current(store, cursor, scoped, observed, include_write_context=False)
        retained = {d['record_key'] for d in peer['candidate']['dispositions'] if d['action'] == 'retain'}
        approved = {v['record_key'] for v in review['verdicts'] if v['approved'] and not v['problems']}
        needed = {cm.source.key(*k) for k in shared} | {key, neighbour} | {'claims/' + cid for cid in extension}
        if (not needed <= approved or key not in retained
                or any((cm.source.key(*k) not in retained or original[k] != fresh[k])
                       and not (k[0] in {'evidence_steps', 'questions', 'claim_relations'} and cm.source.key(*k) in changed) for k in shared)
                or fresh.get(cm.source.split_key(neighbour)) != current[cm.source.split_key(neighbour)]):
            raise ValueError('peer omitted or changed the retained original basis')
        # Even a context finding cannot silently negate the preserved Claim.
        if any(f['target_key'] in needed or key in (f.get('affected_target_keys') or [])
               or set(f.get('affected_write_keys') or []) & needed for f in outstanding):
            raise ValueError('peer has an unresolved impact on the original Claim')
        for resolution in review['prior_findings']:
            if resolution['disposition'] == 'resolved':
                fid = resolution['finding_id']
                resolved[peer['parent_run_id'], fid] = {
                    'finding':peer['materials']['unresolved'][fid], 'resolved_at':started}
    return resolved


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
