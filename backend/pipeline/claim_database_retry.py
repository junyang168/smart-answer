"""A mid-unit retry starts from current SQL, never a rejected model answer.

Only the runtime projection is compacted. Frozen goals, rejected replies,
verdicts and call accounting stay intact in the existing run artifacts.
"""
from copy import deepcopy

CONTRACT = 'claim_database_retry_v1'


def enabled(policy):
    return policy.get('retry_context_contract') == CONTRACT


def active(materials):
    return enabled(materials['policy']) and bool(
        materials.get('round_number', 0) or materials.get('continuation'))


def unresolved_for_retry(policy, current, previous):
    """Discarding an unwritten draft also discards its proposed fixes.

    A reviewer resolution on unchanged SQL remains resolved. When a rejected
    candidate contains writes, its input obligations must be reassessed from
    SQL too; otherwise the next fresh proposal silently loses valid repairs.
    """
    if (enabled(policy) and previous and not previous.get('change_set_ids')
            and any(d['action'] != 'retain'
                    for d in (previous.get('candidate') or {}).get('dispositions', []))):
        return {**deepcopy(previous['materials']['unresolved']), **deepcopy(current)}
    return deepcopy(current)


def project(packet, materials):
    if not active(materials):
        return packet
    from backend.pipeline import claim_management as cm
    result = deepcopy(packet)

    def goal_view(goal):
        # Goals/questions remain obligations; prior record versions are history,
        # not another current database. Preserve changed target judgments as
        # constraints so a concurrent content change is never hidden.
        out = {k: deepcopy(v) for k, v in goal.items()
               if k not in {'original_objects', 'unaccepted_model_inputs'}}
        out['original_objects_archive_sha256'] = cm.sha256_json(goal.get('original_objects', {}))
        constraints = {}
        for key in goal.get('target_keys', []):
            old = goal.get('original_objects', {}).get(key, {}).get('payload', {})
            now = materials['before'].get(key, {}).get('payload', {})
            changed = {f: old.get(f) for f in ('statement', 'attribution', 'limitations')
                       if old.get(f) != now.get(f)}
            if changed:
                constraints[key] = changed
        if constraints:
            out['original_target_judgments'] = constraints
        return out

    result['original_goal'] = goal_view(materials['original_goal'])
    continuation = materials.get('continuation', {})
    if continuation:
        # Keep actual owner source answers and operation constraints. Raw
        # proposals, old candidates, old record tables and transport receipts
        # are already retained server-side, not sent back for imitation.
        keep = {'predecessor_action_id', 'human_source_verification',
                'human_verified_finding_ids', 'completed_context_cleanup',
                'qualification_refresh', 'read_only_refresh',
                'related_owner_target_extension', 'source_support_creation'}
        result['continuation'] = {k: deepcopy(v) for k, v in continuation.items() if k in keep}
        if continuation.get('original_goal'):
            result['continuation']['original_goal'] = goal_view(continuation['original_goal'])
            # Only the current request's SQL-reserved identities are writable.
            result['continuation']['original_goal'].pop('claim_creation_allocations', None)

    entries = list(materials['original_goal'].get('unaccepted_model_inputs', []))
    entries += list(continuation.get('unaccepted_returned_proposals', {}).values())
    latest = entries[-1] if entries else {}
    error = latest.get('rejection_detail')
    if error is None and latest.get('invalid_citations'):
        from backend.pipeline.claim_review_recovery import CITATION_ERROR
        error = CITATION_ERROR
    keys = set(latest.get('repeated_change_keys', []))
    keys.update(row['record_key'] for row in latest.get('invalid_fragment_bindings', []))
    checks = {}
    for key in sorted(keys):
        state = materials['before'].get(key)
        if not state:
            continue
        payload = state['payload']
        owners = []
        if key.startswith('source_fragments/'):
            fid = key.split('/', 1)[1]
            for ek, row in materials['before'].items():
                e = row['payload']
                if ek.startswith('evidence_steps/') and (
                        fid in (e.get('source_fragment_ids') or []) or e.get('source_fragment_id') == fid):
                    owners.append({'evidence_key': ek, 'claim_ids': e.get('produced_claim_ids', [])})
        checks[key] = {k: payload[k] for k in
                       ('source_id', 'paragraph_key', 'verbatim_excerpt') if k in payload}
        checks[key]['current_owners'] = owners
    result.pop('previous_candidate', None)
    result['retry_context'] = {
        'contract': CONTRACT,
        'basis': 'Fresh database before and bound current sources; discard all unwritten drafts.',
        'last_validation_error': error,
        'current_record_checks': checks,
        # Transaction-only consumers are not model facts. A lawful read-pin
        # refresh must not invalidate an otherwise byte-identical model input.
        'current_records_sha256': cm.sha256_json(materials['before']),
        'discarded_candidate_archive_sha256': cm.sha256_json(materials.get('previous_candidate')),
        'rejected_history_archive_sha256': cm.sha256_json(entries),
    }
    result['record_reference_contract'] = (
        'before is the sole current database record table. Sources are complete and current. '
        'Historical record versions and rejected answers are archived under their digests, '
        'not current evidence or a draft to patch. Keep the original goals and every unresolved '
        'obligation, including fixes discarded with an unwritten draft. '
        'Each {"__claim_request_ref__":"request"} denotes the complete packet.request. '
        'In permissions, {"__claim_fields_ref__":id} denotes packet.permission_fields[id].')
    return result
