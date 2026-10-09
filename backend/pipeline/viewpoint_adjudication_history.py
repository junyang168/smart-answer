"""Read-only anti-retry guard for unchanged own-source semantic targets.

Finding prose is not a new semantic target. This guard grants *no approval*:
exact rejected findings belong in the existing SHA-bound reuse consumer;
reworded/new concerns or contradictory prior decisions require an explicit
resolution instead of another automatic model call. Changed targets are not
approved by this guard either; they remain subject to ordinary new-scope gates.
"""
import hashlib
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact


class AdjudicationAlreadyRecorded(ValueError):
    pass


def target_identity(item):
    # Adding the omitted CVR to diagnostic material cannot reset the original
    # target's once budget. The head and links already pin its exact CVR ID.
    return sha256_json({k: v for k, v in item.items() if k not in {'audit_finding', 'viewpoint_revision'}})


def validated_history(root):
    from backend.pipeline.viewpoint_audit_source_adjudication import (
        source_review_adapter, validate_findings,
    )
    packet, raw, handoff = (read_artifact(root / n) for n in
                           ('packet.json', 'raw-gpt.json', 'gpt-final-handoff.json'))
    for value in (packet, raw, handoff):
        if value.get('artifact_sha256') != sha256_json({k:v for k,v in value.items() if k!='artifact_sha256'}):
            raise ValueError('saved adjudication history artifact drift')
    audit = packet['audit_binding']
    if hashlib.sha256(Path(audit['path']).read_bytes()).hexdigest() != audit['file_sha256']:
        raise ValueError('saved adjudication history audit drift')
    # Construct the real profile without executing it. Omitting its default
    # output ceiling changed the configuration SHA and misclassified saved
    # compact responses as unknown contextual requests.
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    client = subscription_client('codex', 'gpt-6.1-sol', 'high')
    adapter = source_review_adapter(client, root / 'raw-gpt.json')
    if (raw.get('model_id') != adapter.model_id or raw.get('backend') != adapter.backend
            or raw.get('prompt_sha256') != adapter.prompt_sha256
            or raw.get('generation_config_sha256') != adapter.generation_config_sha256
            or raw.get('request_payload_sha256') != sha256_json(packet)
            or raw.get('response_sha256') != sha256_json(raw['response'])
            or handoff.get('packet_sha256') != packet['artifact_sha256']
            or handoff.get('maximum_model_calls') != 1 or handoff.get('master_data_mutations') != 0):
        raise ValueError('saved adjudication history backend/input drift')
    findings = validate_findings(raw['response'], packet)
    actions = handoff['actions']
    if (len(actions)!=len(findings) or {a['item_id'] for a in actions}!=set(findings)
            or any(a['gpt']!=findings[a['item_id']] or a['verdict']!=a['gpt']['verdict'] for a in actions)):
        raise ValueError('saved adjudication history response/handoff drift')
    for item in packet['items']:
        for source in item['sources']:
            if hashlib.sha256(Path(source['path']).read_bytes()).hexdigest() != source['file_sha256']:
                raise ValueError('saved adjudication history physical source drift')
    proof = root/'owner-input-repair-proof.json'
    if proof.exists():
        from backend.pipeline.cvp_sql_adjudication_input_repair import verify_proof
        verify_proof(proof)
    return packet, findings


def assert_no_readjudication(packet, roots):
    current = {target_identity(i):i for i in packet['items']}
    matches = []
    for root in roots:
        # Cheap exact target match first; unrelated old versions need no replay.
        prior = read_artifact(root / 'packet.json')
        overlapping = [i for i in prior['items'] if target_identity(i) in current]
        if not overlapping:
            continue
        prior, findings = validated_history(root)
        for item in overlapping:
            finding = findings[item['item_id']]
            matches.append({'item_id':item['item_id'], 'prior_root':str(root),
                            'verdict':finding['verdict'],
                            'exact_finding':item==current[target_identity(item)]})
    if matches:
        raise AdjudicationAlreadyRecorded(
            'unchanged semantic target already adjudicated; do not call model again. '
            'Consume an exact validated rejection via the reuse gate, or resolve '
            'a changed concern/contradiction without automatic retry: ' + str(matches))


def campaign_history(audit_path, output):
    # Scope is explicit from the audit's production tree; never scan primary,
    # other issue worktrees or an unbounded home directory.
    base = next((p for p in audit_path.resolve().parents if p.name=='cvp-production-357'), None)
    if base is None:
        return []
    return sorted(p.parent for p in base.rglob('gpt-final-handoff.json')
                  if p.parent.resolve()!=output.resolve())
