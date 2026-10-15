"""Versioned ordinary joint Update checks on the existing Claim action boundary.

No executor, ledger, scheduler or additional approval. Legacy contracts do not
acquire these obligations when their saved replies are consumed.
"""
from __future__ import annotations

import json
from pathlib import Path

CONTRACT = 'wang_claim_joint_update_v1'


def enabled(request, policy=None):
    operation = request.get('operation') if isinstance(request, dict) else getattr(request, 'operation', None)
    targets = request.get('target_keys', []) if isinstance(request, dict) else getattr(request, 'target_keys', [])
    if operation != 'Update' or not targets:
        return False
    if policy is None:
        bound = request['policy'] if isinstance(request, dict) else request.policy.model_dump()
        # An unbound policy cannot enable a new contract. Action admission
        # and receipt boundaries independently require load_authority.
        if not Path(bound['path']).is_file():
            return False
        from backend.pipeline import claim_management as cm
        value = json.loads(cm.source.verify_file(cm.source.FileBinding.model_validate(bound)).read_text())
        return isinstance(value, dict) and value.get('schema_version') == cm.VERSION and value.get('joint_update_contract') == CONTRACT
    return policy.joint_update_contract == CONTRACT


def required_keys(request, *, strict=True):
    from backend.pipeline import claim_management as cm
    authority = json.loads(cm.source.verify_file(request.authorization).read_text())
    keys = authority.get('required_maintenance_keys')
    if not isinstance(keys, list) or any(not isinstance(k, str) for k in keys) or len(keys) != len(set(keys)):
        if strict:
            raise ValueError('joint Update requires an explicit exact required_maintenance_keys list, including []')
        return []
    return list(keys)


def admission_error(request, policy, before, goal):
    """For new actions only. The caller persists the refusal before any call."""
    if request.operation != 'Update':
        return None
    if len(request.target_keys) == 1 and not enabled(request, policy):
        return None
    if not enabled(request, policy):
        return 'ordinary multi-target Update requires the versioned joint Update policy (default capacity 1)'
    try:
        if len(request.target_keys) > (policy.joint_update_max_targets or 1):
            raise ValueError('joint Update exceeds joint_update_max_targets')
        if request.creation_permissions:
            raise ValueError('joint Update supports existing retain/update only; creation requires a separate capability')
        if not policy.all_findings_to_gpt:
            raise ValueError('joint Update must adjudicate all findings with GPT')
        required = required_keys(request)
        if (goal.get('joint_update_contract') != CONTRACT or goal.get('required_maintenance_keys') != required
                or list(goal.get('units', {}).values()) != [request.target_keys]):
            raise ValueError('joint Update must bind every target and required maintenance item to one unit')
        from backend.pipeline import claim_management as cm
        # Required keys must not seed their own reachability proof.
        from types import SimpleNamespace
        natural = SimpleNamespace(target_keys=request.target_keys, permissions=[])
        reachable = set(cm.audit_keys(natural, before))
        permissions = {p.key for p in request.permissions}
        if (permissions | set(required)) - reachable:
            raise ValueError('joint Update permission/required maintenance item is unreachable from its target closure')
        if (permissions | set(required)) & {k for k in before if k.startswith('claims/')} - set(request.target_keys):
            raise ValueError('joint Update cannot grant hidden Claim targets through maintenance or write permission')
        if any(k not in before or before[k]['retired'] for k in request.target_keys + required):
            raise ValueError('joint Update requires existing active targets and maintenance objects')
        if not set(goal['original_objects']) <= set(before):
            raise ValueError('joint Update original closure is incomplete')
    except ValueError as exc:
        return str(exc)
    return None


def validate_candidate_scope(request, before, candidate):
    from backend.pipeline import claim_management as cm
    proposed = cm.source.index(candidate.records)
    required = set(required_keys(request))
    dispositions = {d.record_key: d for d in candidate.dispositions}
    if not required <= set(dispositions) or not required <= set(before):
        raise ValueError('joint Update candidate omitted required maintenance disposition')
    if candidate.operation not in {None, 'Update'} or candidate.replacements or request.creation_permissions:
        raise ValueError('joint Update supports existing retain/update only')
    for disposition in candidate.dispositions:
        if disposition.action not in {'retain', 'update'}:
            raise ValueError('joint Update supports existing retain/update only')
        if disposition.action == 'update' and proposed.get(disposition.record_key) == before.get(disposition.record_key, {}).get('payload'):
            raise ValueError('joint Update unchanged objects require retain disposition')
        if disposition.action != 'retain' and disposition.record_key.startswith('claims/') and disposition.record_key not in request.target_keys:
            raise ValueError('joint Update cannot write a hidden Claim target')


def finding_blocks(finding, request, candidate):
    targets = set(request.target_keys)
    writes = {d.record_key for d in candidate.dispositions if d.action != 'retain'} if candidate else set()
    affected = finding.get('affected_target_keys')
    affected_writes = finding.get('affected_write_keys')
    for values, allowed in ((affected, targets), (affected_writes, writes)):
        if not isinstance(values, list) or len(values) != len(set(values)) or not set(values) <= allowed:
            raise ValueError('joint finding requires exact target impact and affected_write_keys from actual candidate writes')
    if not (finding.get('impact_reason') or '').strip():
        raise ValueError('joint finding requires impact_reason')
    key = finding['target_key']
    if key in targets and key not in affected:
        raise ValueError('joint finding on a target must include its target impact')
    if key in writes and key not in affected_writes:
        raise ValueError('joint finding on an actual write must include its write impact')
    return bool(affected or affected_writes or key in set(required_keys(request)))


def validate_scope_agreement(candidate, review, previous, request):
    """No program inference of semantic causation: require model scope agreement."""
    decisions = {r.finding_id: r for r in candidate.finding_resolutions or []}
    for resolution in review.prior_findings:
        if resolution.disposition == 'resolved':
            continue
        # An unresolved resolution must explicitly declare its own scope,
        # rather than inherit a missing field from a previous occurrence.
        finding_blocks({**previous[resolution.finding_id], 'affected_target_keys': resolution.affected_target_keys,
            'affected_write_keys': resolution.affected_write_keys, 'impact_reason': resolution.impact_reason}, request, candidate)
        proposed = decisions.get(resolution.finding_id)
        if proposed is not None and proposed.disposition != 'resolved' and (
                set(proposed.affected_target_keys or []) != set(resolution.affected_target_keys or [])
                or set(proposed.affected_write_keys or []) != set(resolution.affected_write_keys or [])):
            raise ValueError('joint Update GPT/Opus impact scope disagreement')


def verify_completion(goal, child, observed):
    if goal.get('joint_update_contract') != CONTRACT:
        return
    from backend.pipeline import claim_management as cm
    from backend.pipeline.source_reconciliation_runner import read, body
    for bound in goal['source_bindings'].values():
        cm.source.verify_file(cm.source.FileBinding.model_validate(bound))
    required = goal.get('required_maintenance_keys')
    if not isinstance(required, list):
        raise ValueError('joint Update completion omitted required maintenance list')
    candidate = cm.Candidate.model_validate(child['candidate'])
    dispositions = {d.record_key: d for d in candidate.dispositions}
    from backend.pipeline.cvp_sql_flow import check_binding
    check_binding(child['stage_results']['opus'])
    receipt = body(read(Path(child['stage_results']['opus']['path'])))
    check_binding(receipt['candidate'])
    raw = body(read(Path(receipt['materials']['path']).parent / 'opus.raw.json'))
    review = cm.Review.model_validate(raw['response'])
    if review.proposal_sha256 != candidate.digest:
        raise ValueError('joint Update completion candidate/review binding drift')
    approved = {v.record_key for v in review.verdicts if v.approved and not v.problems}
    obligations = set(goal['target_keys']) | set(required)
    if (not obligations <= set(dispositions) or not obligations <= approved or not obligations <= set(observed)
            or any(observed[k].get('retired') for k in obligations)
            or any(dispositions[k].action not in {'retain', 'update'} for k in obligations)):
        raise ValueError('joint Update completion lacks final dispositions, per-object pass or current readback')


def preflight(store, request):
    """Read-only grouping of an exact authorized batch; same reader as prepare.

    Returns proposed groups, complete row pins and permission/obligation
    assignment. It never rewrites authorizations or prepares/runs an action.
    """
    from backend.pipeline import claim_management as cm
    from backend.pipeline.cvp_sql_flow import pins, pin_rows
    request = cm.Request.model_validate(request)
    policy, _ = cm.load_authority(request)
    if request.operation != 'Update':
        raise ValueError('joint Update preflight requires an Update batch')
    authority = json.loads(cm.source.verify_file(request.authorization).read_text())
    required = required_keys(request) if enabled(request, policy) else authority.get('required_maintenance_keys', [])
    if not isinstance(required, list) or any(not isinstance(k, str) for k in required) or len(set(required)) != len(required):
        raise ValueError('invalid required maintenance list')
    targets = request.target_keys
    reads, closures, states = {}, {}, {}
    for key in targets:
        records = store.read_claim_context([cm.source.split_key(key), *[('source_documents', sid) for sid in request.source_bindings]])
        reads[key] = {cm.source.key(*k) for k in records}
        from types import SimpleNamespace
        closures[key] = set(cm.audit_keys(SimpleNamespace(target_keys=[key], permissions=[]),
            {cm.source.key(*k): cm.model_state(v) for k, v in records.items()}))
        for k, state in records.items():
            if k in states and cm.model_state(states[k]) != cm.model_state(state):
                raise ValueError('joint Update preflight dependency drift')
            states[k] = state
    allowed = {p.key for p in request.permissions}
    writes = {key: allowed & closures[key] for key in targets}
    parent = {k: k for k in targets}
    def root(k):
        while parent[k] != k:
            k = parent[k]
        return k
    for i, a in enumerate(targets):
        for b in targets[i + 1:]:
            if writes[a] & reads[b] or writes[b] & reads[a]:
                parent[root(b)] = root(a)
    groups = {}
    for key in targets:
        groups.setdefault(root(key), []).append(key)
    result = []
    assigned = set().union(*closures.values()) if closures else set()
    unassigned = sorted((allowed | set(required)) - assigned)
    hidden = sorted((allowed | set(required)) & {k for k in assigned if k.startswith('claims/')} - set(targets))
    required_owner = {}
    for group_id, keys in groups.items():
        natural = set().union(*(closures[k] for k in keys))
        for key in required:
            if key in natural:
                required_owner.setdefault(key, group_id)
    for group_id, keys in groups.items():
        scope = set().union(*(reads[k] for k in keys))
        reason = ('unassigned permission/maintenance objects: ' + ', '.join(unassigned) if unassigned else
                  'hidden Claim target: ' + ', '.join(sorted(set(hidden) & scope)) if set(hidden) & scope else
                  'joint Update forbids creation_permissions' if request.creation_permissions else
                  'joint capacity/policy unavailable' if len(keys) > 1 and (
                      not enabled(request, policy) or len(keys) > (policy.joint_update_max_targets or 1)) else None)
        result.append({'target_keys': keys, 'permission_keys': sorted(allowed & scope),
            'required_maintenance_keys': [k for k in required if required_owner.get(k) == group_id],
            'status': 'engineering_required' if reason else 'prepared_scope_only', 'reason': reason})
    with store.connect() as conn, conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor, pins(states))
    return {'schema_version': CONTRACT, 'groups': result, 'unassigned_keys': unassigned,
            'read_pins': pin_rows(pins(states)), 'model_calls': 0, 'content_writes': 0}


def current_batch_results(store, action_ids):
    """Recompute qualifications for the original bounded plan at report time."""
    from backend.pipeline import claim_management as cm
    results = []
    for action_id in action_ids:
        parent = store.get_cvp_group('CMA-' + cm.sha256_json(action_id))
        if not parent:
            raise ValueError('batch summary requires an existing exact action')
        targets = parent['original_goal']['target_keys']
        peers = {k.split('/', 1)[1] for k in parent['original_goal']['original_objects'] if k.startswith('claims/')} - {k.split('/', 1)[1] for k in targets}
        results.append({'action_id': action_id, 'historical_result': parent['result'],
            'targets': {k: store.claim_management_qualification(k.split('/', 1)[1]) for k in targets},
            'affected_consumers': {cid: store.claim_management_qualification(cid) for cid in sorted(peers)}})
    return results
