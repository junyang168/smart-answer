"""Reuse an actual complete prior review without manufacturing a new model vote.

The proof is additive metadata on its original round. Raw answers, targets,
policies, budgets and content remain unchanged. Every consumer checks current
records and adjacency; newly rejected content cannot hide behind an old pass.
"""
import json
from pathlib import Path

from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_commit import check_binding, pins, pin_rows, decoded_pins


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
        metadata->>'outcome',metadata->'opus_approved',metadata->'program_readback_complete'
        FROM wang_knowledge.pipeline_runs
        WHERE metadata->>'kind'='claim_management_round' AND metadata->'claim_ids' ? %s
          AND started_at >= (SELECT started_at FROM wang_knowledge.pipeline_runs WHERE run_id=%s)
        ORDER BY started_at DESC,run_id DESC""", (claim_id, run_id))
    key = 'claims/' + claim_id
    for findings, context, blocked, targets, outcome, approved, readback in cursor.fetchall():
        if (claim_id in (blocked or [])
                or any(f.get('target_key') in protected or key in (f.get('affected_target_keys') or [])
                       for f in list((findings or {}).values()) + list((context or {}).values()))):
            raise ValueError('review reuse has a newer unresolved Claim objection')
        # A newer completed target pass supersedes earlier rejected rounds.
        if (key in (targets or []) and outcome == 'completed' and approved and readback):
            return


def verify(store, cursor, claim_id, proof):
    from backend.pipeline.source_editorial_transition import verify_current_source_binding
    if proof.get('contract') != 'claim_complete_review_reuse_v1' or proof.get('claim_id') != claim_id:
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
    store._assert_claim_management_scope(cursor, decoded_pins(proof['read_pins']), records=states,
                                         request=proof['scope_request'])
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
