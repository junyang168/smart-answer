"""Exact owner disposition of a saved structural duplicate finding, no replay.

This is NOT a GPT rejection or a passing independent audit. Only explicitly
named existing upheld findings can be accepted; all others must be rejected.
"""
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.canonical_repository.viewpoint_component_roles import component_role_findings
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA = 'wang_owner_component_role_resolution_v1'
AUTH_SCHEMA = 'wang_owner_component_role_authorization_v1'


def verify(audit, resolution):
    from backend.pipeline.viewpoint_audit_owner_resolution import binding, verify_sha, audit_semantic_sha
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_source_adjudication import disputes, validate_findings
    verify_sha(resolution)
    if (resolution.get('schema_version') != SCHEMA
            or resolution.get('audit_semantic_sha256') != audit_semantic_sha(audit)
            or resolution.get('new_model_calls') != 0 or resolution.get('master_data_mutations') != 0
            or resolution.get('independent_audit_verdicts_unchanged') is not True):
        raise ValueError('invalid component role disposition identity')
    bound = {b['path']: b for b in resolution['bindings']}
    for b in resolution['bindings']:
        if binding(b['path']) != b:
            raise ValueError('component disposition physical proof drift')
    def read(key):
        path = resolution[key]
        if path not in bound:
            raise ValueError('component disposition omitted binding')
        return read_artifact(Path(path))
    auth = read('authorization_path')
    if (auth.get('schema_version') != AUTH_SCHEMA or auth.get('authorized_by') != 'owner'
            or not auth.get('authorization_quotes') or not all(auth['authorization_quotes'])
            or auth.get('accept_structural_duplicate_finding_only') is not True
            or auth.get('audit_binding') not in resolution['bindings']):
        raise ValueError('missing exact owner component rule direction')
    old = read('packet_path'); raw = read('raw_path'); handoff = read('handoff_path')
    if auth.get('packet_binding') != bound[resolution['packet_path']]:
        raise ValueError('owner component direction packet binding drift')
    for value in (old, raw, handoff):
        verify_sha(value)
    actual, _ = validated_history(Path(resolution['packet_path']).parent)
    if actual != old:
        raise ValueError('saved component adjudication authentication drift')
    findings = validate_findings(raw['response'], old)
    actions = handoff['actions']
    if (handoff['packet_sha256'] != old['artifact_sha256']
            or handoff.get('maximum_model_calls') != 1
            or len(actions) != len(findings) or {a['item_id'] for a in actions} != set(findings)
            or any(a['gpt'] != findings[a['item_id']] or a['verdict'] != a['gpt']['verdict'] for a in actions)):
        raise ValueError('saved component adjudication decisions changed')
    expected = {f'{layer}:{row[key]}:{cid}' for layer, key, row in disputes(audit)
                for cid in ([row[key]] if layer == '3' else row.get('claim_ids_in_question', []))}
    items = {i['item_id']: i for i in old['items']}
    if set(items) != expected:
        raise ValueError('component disposition omitted or added actual findings')
    if old['audit_binding'] != auth['audit_binding']:
        raise ValueError('component direction names a different audit')
    targets = auth.get('targets') or []
    accepted = {t['item_id']: t for t in targets}
    if not targets or len(accepted) != len(targets):
        raise ValueError('empty or duplicate component directions')
    upheld = {k for k, v in findings.items() if v['verdict'] != 'audit_objection_rejected'}
    if upheld != set(accepted):
        raise ValueError('unresolved or unrelated finding lacks owner direction')
    for identity, target in accepted.items():
        item = items[identity]
        if (not identity.startswith('4:') or findings[identity]['verdict'] != 'audit_objection_upheld'
                or sha256_json(item) != target['item_sha256']):
            raise ValueError('owner component direction is not this exact upheld finding')
        links = item['connections']
        if (len(links) != 2 or sorted(l['viewpoint_claim_link_id'] for l in links) != target['link_ids']
                or {l['link_type'] for l in links} != {'equivalent_component', 'supports'}):
            raise ValueError('owner component direction changed endpoints/roles/population')
        claim = item['claim']
        steps = {e['step']['evidence_step_id']: e['step'] for e in item['evidence']}
        fragments = {e['fragment']['fragment_id']: e['fragment'] for e in item['evidence']}
        errors = component_role_findings(links, {claim['claim_id']: claim}, steps, fragments,
                                        claim_sha=semantic_record_sha)
        if errors:
            raise ValueError('owner direction cannot waive component gates: ' + '; '.join(errors))
    for item in old['items']:
        layer, _, _ = item['item_id'].split(':', 2)
        if item['audit_finding'] not in audit['layers'][layer]['results']:
            raise ValueError('component disposition finding changed')
        for source in item['sources']:
            if bound.get(source['path']) != {'path': source['path'], 'file_sha256': source['file_sha256']}:
                raise ValueError('component disposition omitted own source')
    return {layer: {i['item_id'].split(':')[1] for i in old['items']
                    if i['item_id'].startswith(layer + ':')} for layer in ('3', '4')}


def create(audit_path, prior_root, authorization_path, output, store, *, campaign_sha256, run_id):
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet_for_saved
    from backend.pipeline.viewpoint_audit_owner_resolution import binding, audit_semantic_sha
    auth = read_artifact(authorization_path)
    if auth.get('campaign_sha256') != campaign_sha256 or auth.get('run_id') != run_id:
        raise ValueError('component owner direction campaign/group mismatch')
    packet = read_artifact(prior_root / 'packet.json')
    if build_packet_for_saved(audit_path, store, packet) != packet:
        raise ValueError('component current SQL/Claim/source/connection drift')
    paths = {'authorization_path': authorization_path, 'packet_path': prior_root / 'packet.json',
             'raw_path': prior_root / 'raw-gpt.json', 'handoff_path': prior_root / 'gpt-final-handoff.json'}
    sources = {s['path']: {'path': s['path'], 'file_sha256': s['file_sha256']}
               for i in packet['items'] for s in i['sources']}
    body = {'schema_version': SCHEMA, 'audit_semantic_sha256': audit_semantic_sha(read_artifact(audit_path)),
            'new_model_calls': 0, 'master_data_mutations': 0, 'independent_audit_verdicts_unchanged': True,
            'bindings': [binding(p) for p in [audit_path, *paths.values()]] + list(sources.values()),
            **{k: str(Path(v).resolve()) for k, v in paths.items()}}
    result = {**body, 'artifact_sha256': sha256_json(body)}
    verify(read_artifact(audit_path), result)
    if build_packet_for_saved(audit_path, store, packet) != packet:
        raise ValueError('component current state changed during disposition')
    write_immutable(output, result)
    return result
