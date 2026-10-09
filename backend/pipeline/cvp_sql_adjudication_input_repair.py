"""Owner-directed single complete-input adjudication, preserving original budget.

Only an omitted CVR can be added to an exact unresolved v1 request. This is not
a general retry, semantic correction, content approval or replacement of history.
"""
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA = 'cvp_sql_adjudication_input_repair_authorization.v1'
PROOF = 'cvp_sql_adjudication_input_repair_proof.v1'


def make_direction(store, campaign, run_id, original_root, quote):
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    original_root = Path(original_root).resolve()
    old, findings = validated_history(original_root)
    current = build_packet(Path(old['audit_binding']['path']), store, True)
    verify_delta(old, current, findings)
    value = {'schema_version': SCHEMA, 'campaign_sha256': campaign, 'run_id': run_id,
             'authorized_by': 'owner', 'authorization_quote': quote,
             'maximum_additional_calls': 1, 'automatic_future_retries_authorized': False,
             'human_content_approval': False, 'allowed_added_field': 'viewpoint_revision',
             'original_root': str(original_root), 'corrected_packet_sha256': current['artifact_sha256'],
             'original_bindings': [binding(original_root/n) for n in
                 ('packet.json', 'raw-gpt.json', 'gpt-final-handoff.json')],
             'audit_binding': old['audit_binding']}
    return {**value, 'artifact_sha256': sha256_json(value)}


def verify_delta(old, current, findings):
    if (old.get('schema_version') != 'wang_audit_source_adjudication_packet_v1'
            or current.get('schema_version') != 'wang_audit_source_adjudication_packet_v2'
            or not findings or any(f['verdict'] != 'unresolved' for f in findings.values())):
        raise ValueError('input repair only permits unresolved incomplete v1 packet')
    projected = {k: v for k, v in current.items() if k != 'artifact_sha256'}
    projected['schema_version'] = old['schema_version']
    projected['items'] = [{k: v for k, v in i.items() if k != 'viewpoint_revision'} for i in current['items']]
    projected['artifact_sha256'] = sha256_json(projected)
    if projected != old:
        raise ValueError('input repair changed original Claim/source/audit/link/head or population')
    for item in current['items']:
        head, revision = item.get('viewpoint'), item.get('viewpoint_revision')
        if (not head or not revision or revision.get('viewpoint_id') != head['viewpoint_id']
                or revision.get('viewpoint_revision_id') != head['current_revision_id']
                or not revision.get('core_proposition') or not isinstance(revision.get('scope'), dict)
                or not isinstance(revision.get('proposition_signature'), dict)):
            raise ValueError('input repair lacks exact complete CVR')


def verify_proof(path):
    """Authenticate both requests and explicit direction, without recursive replay."""
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_owner_resolution import binding, verify_sha
    proof = read_artifact(Path(path)); verify_sha(proof)
    if proof.get('schema_version') != PROOF:
        raise ValueError('unknown input repair proof')
    for pin in proof['bindings']:
        if binding(pin['path']) != pin:
            raise ValueError('input repair original/authorization physical drift')
    direction = read_artifact(Path(proof['authorization']['path'])); verify_sha(direction)
    if (proof['authorization'] not in proof['bindings'] or direction.get('schema_version') != SCHEMA
            or direction.get('authorized_by') != 'owner' or not direction.get('authorization_quote', '').strip()
            or direction.get('maximum_additional_calls') != 1
            or direction.get('automatic_future_retries_authorized') is not False
            or direction.get('human_content_approval') is not False
            or direction.get('allowed_added_field') != 'viewpoint_revision'):
        raise ValueError('input repair lacks bounded owner call direction')
    original = Path(direction['original_root'])
    if Path(path).parent.resolve() != (original/'input-repair-once').resolve():
        raise ValueError('input repair must use the original fixed once output')
    expected = [binding(original/n) for n in ('packet.json', 'raw-gpt.json', 'gpt-final-handoff.json')]
    if direction['original_bindings'] != expected or any(p not in proof['bindings'] for p in expected):
        raise ValueError('input repair original response binding drift')
    old, findings = validated_history(original)
    current = read_artifact(Path(path).parent/'packet.json'); verify_sha(current)
    if (direction['audit_binding'] != old['audit_binding'] or old['audit_binding'] not in proof['bindings']
            or direction['corrected_packet_sha256'] != current['artifact_sha256']):
        raise ValueError('input repair audit/current packet drift')
    verify_delta(old, current, findings)
    return direction, proof


def execute(store, run_id, authorization_path):
    from backend.pipeline.viewpoint_adjudication_history import validated_history, target_identity
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, run
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    direction = read_artifact(Path(authorization_path))
    saved = store.get_cvp_group(run_id)
    if (direction.get('run_id') != run_id or direction.get('campaign_sha256') != saved['campaign_sha256']
            or saved['checkpoint'] not in {'applied', 'audited'}):
        raise ValueError('input repair direction group/campaign/checkpoint drift')
    original = Path(direction['original_root'])
    old, findings = validated_history(original)
    current = build_packet(Path(old['audit_binding']['path']), store, True)
    verify_delta(old, current, findings)
    corrected = original/'input-repair-once'
    write_immutable(corrected/'packet.json', current)
    auth = binding(authorization_path)
    body = {'schema_version': PROOF, 'authorization': auth,
            'bindings': [auth, *direction['original_bindings'], direction['audit_binding']]}
    write_immutable(corrected/'owner-input-repair-proof.json', {**body, 'artifact_sha256': sha256_json(body)})
    verify_proof(corrected/'owner-input-repair-proof.json')
    targets = [target_identity(i) for i in current['items']]
    store.register_adjudication_input_repair(run_id, targets=targets, original_root=original,
        original_packet=direction['original_bindings'][0], root=corrected,
        packet_binding=binding(corrected/'packet.json'), authorization=auth)
    if not (corrected/'raw-gpt.json').exists():
        import os
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        os.environ['CODEX_EXECUTABLE'] = canonical_runtime()['identity']['cli']['path']
    run(Path(old['audit_binding']['path']), corrected, store, final_gpt=True, history_roots=[])
    return original, corrected


def effective_roots(roots):
    """Use the authorized complete-input result, never erase the original reply."""
    superseded = set()
    for root in roots:
        proof_path = Path(root)/'owner-input-repair-proof.json'
        if proof_path.exists():
            direction, _ = verify_proof(proof_path)
            superseded.add(Path(direction['original_root']))
    return [Path(r) for r in roots if Path(r) not in superseded]
