"""Mechanical, unapproved draft of existing Route records for delta updates."""
from copy import deepcopy


def update_baseline(packet):
    observations = bool(packet.get('observation_support_contract'))
    result = {'schema_version': 'wang_argument_route_proposal_v3' if observations else 'wang_argument_route_proposal_v2',
        'scope_label': packet['scope_label'],
        'approved_viewpoint_revision_ids': list(packet['approved_viewpoint_revision_ids']),
        'argument_route_candidates': [], 'source_route_attestations': [],
        'viewpoints_with_no_route': [], 'unattested_members': [], 'retired_route_revision_ids': []}
    heads = {v['viewpoint_id']: v['viewpoint_revision_id'] for v in packet['approved_viewpoints']}
    route_keys = {}
    for existing in packet['existing_routes']:
        revision = existing['revision']
        key = existing['argument_route_id']
        route_keys[existing['route_revision_id']] = key
        head = heads[existing['route']['conclusion_viewpoint_id']]
        changed = existing['conclusion_viewpoint_revision_id'] != head
        nodes = []
        for node in revision['ordered_inference_nodes']:
            nodes.append({k: deepcopy(node[k]) for k in (
                'route_step_key', 'role', 'normalized_proposition', 'required_for_full_attestation')})
            nodes[-1]['conclusion_ref'] = {'target_viewpoint_revision_id': head} if node['role'] == 'conclusion' else None
        signature = revision['route_signature']
        result['argument_route_candidates'].append({'local_route_key': key,
            'conclusion_ref': {'target_viewpoint_revision_id': head},
            'proposed_action': 'revise_existing' if changed else 'match_existing',
            'target_argument_route_revision_id': existing['route_revision_id'],
            'route_label': revision['route_label'],
            'inference_method_codes': deepcopy(signature['inference_method_codes']),
            'inference_method_note': signature.get('inference_method_note'),
            'ordered_inference_nodes': nodes,
            'identity_comparison': '旧路线的机械投影；全部语义与证据仍待本轮核对。',
            'revision_reason': '结论版本更换，旧骨架投影到当前 CVP 供完整复核。' if changed else None})
    components = packet['claim_components']
    for old in packet.get('existing_attestations', []):
        terminals = [c['claim_component_key'] for c in components
            if old.get('terminal_claim_link_id') is not None
            and c.get('viewpoint_claim_link_id') == old['terminal_claim_link_id']]
        result['source_route_attestations'].append({
            'local_attestation_key': old['argument_route_attestation_id'],
            'route_ref': {'local_route_key': route_keys.get(old['validated_against_route_revision_id']),
                          'target_argument_route_revision_id': None},
            'source_id': old['source_id'],
            'source_revision_sha256': packet['source_revisions'].get(old['source_id'], old['source_revision_sha256']),
            'claim_ids': deepcopy(old['claim_ids']), 'step_bindings': deepcopy(old['step_bindings']),
            'terminal_claim_component_key': terminals[0] if len(terminals) == 1 else None,
            'completeness': old['completeness'],
            'reason': '旧实例投影；当前来源、连接、完整度及全部绑定仍须逐项复核。'})
    if observations:
        result.update(observation_supports=[], sources_without_observation=[], retired_observation_support_ids=[])
        for old in packet.get('existing_observation_supports', []):
            item = {k: deepcopy(old[k]) for k in ('source_id', 'observation_summary', 'supports', 'support_scope_note')}
            item.update(local_observation_key=old['observation_support_id'],
                viewpoint_revision_id=heads[old['viewpoint_id']], evidence_bindings=[])
            for binding in old['evidence_bindings']:
                choices = [c['claim_component_key'] for c in components
                    if c['claim_id'] == binding['claim_id'] and c['source_id'] == old['source_id']]
                item['evidence_bindings'].append({
                    # Ambiguity remains explicit and invalid until the model
                    # chooses an actual component. Never invent a reference.
                    'claim_component_key': choices[0] if len(choices) == 1 else None,
                    'evidence_step_id': binding['evidence_step_id'],
                    'source_fragment_id': binding['source_fragment_id']})
            result['observation_supports'].append(item)
    return result
