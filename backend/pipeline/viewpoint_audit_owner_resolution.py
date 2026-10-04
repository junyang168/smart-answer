"""Exact, owner-accepted source adjudication; never rewrites an audit verdict.

This is D4's final exception disposition, not a passing Gemini response and
not a blanket waiver. It accepts only the explicitly named rejected findings.
"""
from pathlib import Path
import hashlib

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, validate_findings, run


def binding(path):
    path = Path(path).resolve()
    return {'path': str(path), 'file_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def verify_sha(payload):
    if payload.get('artifact_sha256') != sha256_json({k: v for k, v in payload.items() if k != 'artifact_sha256'}):
        raise ValueError('resolution artifact SHA drift')


def audit_semantic_sha(audit):
    return sha256_json({'meta': audit['meta'], 'layers': {k: audit['layers'][k] for k in ('3', '4')}})


def create_resolution(audit_path, adjudication_root, output, store, *, authorized_by, authorization_quotes):
    if not authorized_by.strip() or not authorization_quotes or any(not q.strip() for q in authorization_quotes):
        raise ValueError('owner acceptance must be explicit and recorded')
    packet_path = adjudication_root / 'packet.json'
    packet = read_artifact(packet_path)
    verify_sha(packet)
    original_path = Path(packet['audit_binding']['path'])
    if binding(original_path) != packet['audit_binding'] or build_packet(original_path, store) != packet:
        raise ValueError('source adjudication no longer matches current originals/Registry')
    handoff_path = adjudication_root / 'handoff.json'
    handoff = read_artifact(handoff_path)
    verify_sha(handoff)
    if handoff['packet_sha256'] != packet['artifact_sha256']:
        raise ValueError('handoff packet drift')
    reviews = []
    raw_paths = []
    for label, model, backend in [('gpt', 'gpt-6.1-sol', 'codex_subscription'),
                                  ('opus', 'claude-opus-5-5', 'claude_subscription')]:
        path = adjudication_root / f'raw-{label}.json'
        raw = read_artifact(path)
        verify_sha(raw)
        if (raw['model_id'] != model or raw['backend'] != backend or
                raw['request_payload_sha256'] != sha256_json(packet) or
                raw['response_sha256'] != sha256_json(raw['response'])):
            raise ValueError('adjudication raw identity/input/answer drift')
        reviews.append(validate_findings(raw['response'], packet))
        raw_paths.append(path)
    if len(handoff['actions']) != len(packet['items']):
        raise ValueError('handoff population drift')
    for action in handoff['actions']:
        identity = action['item_id']
        if (action['verdict'] != 'audit_objection_rejected' or
                action['gpt'] != reviews[0][identity] or action['opus'] != reviews[1][identity] or
                any(r[identity]['verdict'] != 'audit_objection_rejected' for r in reviews)):
            raise ValueError('unresolved/upheld or fabricated adjudication cannot be accepted')
    # Only cached calls: both files must exist above, and runtime validates
    # exact current prompt/schema/config/model pins without model execution.
    if run(original_path, adjudication_root, store) != handoff:
        raise ValueError('adjudication configuration or handoff drift')
    audit = read_artifact(audit_path)
    rows = [r for r in audit['layers']['4']['results'] if r['verdict'] == 'disputed']
    questions = {(r['viewpoint_id'], cid) for r in rows for cid in r.get('claim_ids_in_question', [])}
    pinned = {(i['viewpoint']['viewpoint_id'], i['claim']['claim_id']) for i in packet['items']}
    if not rows or questions != pinned or any(not r.get('claim_ids_in_question') for r in rows):
        raise ValueError('owner resolution must match the exact current disputed population')
    body = {'schema_version': 'wang_owner_accepted_audit_source_resolution_v1',
            'approval_basis': 'owner_accepted_source_adjudication',
            'authorized_by': authorized_by, 'authorization_quotes': authorization_quotes,
            'audit_semantic_sha256': audit_semantic_sha(audit), 'packet_sha256': packet['artifact_sha256'],
            'resolved_findings': rows, 'master_data_mutations': 0,
            'independent_audit_verdicts_unchanged': True,
            'bindings': [binding(p) for p in [audit_path, original_path, packet_path, handoff_path, *raw_paths]]}
    result = body | {'artifact_sha256': sha256_json(body)}
    write_immutable(output, result)
    validate_resolution(audit, output)
    return result


def validate_resolution(audit, path):
    resolution = read_artifact(Path(path))
    verify_sha(resolution)
    if (resolution.get('schema_version') != 'wang_owner_accepted_audit_source_resolution_v1' or
            resolution.get('approval_basis') != 'owner_accepted_source_adjudication' or
            not resolution.get('authorized_by', '').strip() or not resolution.get('authorization_quotes') or
            resolution.get('master_data_mutations') != 0 or
            resolution.get('independent_audit_verdicts_unchanged') is not True or
            resolution['audit_semantic_sha256'] != audit_semantic_sha(audit)):
        raise ValueError('invalid or foreign owner audit resolution')
    for b in resolution['bindings']:
        if binding(b['path']) != b:
            raise ValueError('owner resolution physical proof drift')
    actual = [r for r in audit['layers']['4']['results'] if r['verdict'] == 'disputed']
    if not actual or resolution['resolved_findings'] != actual:
        raise ValueError('owner resolution does not exactly cover current objections')
    return {r['viewpoint_id'] for r in actual}
