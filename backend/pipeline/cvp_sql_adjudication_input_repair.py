"""Owner-directed single complete-input adjudication, preserving original budget.

The legacy direction adds an omitted CVR to an unresolved v1 request. A distinct
standing-engineering direction discloses an omitted relation-role contract while
preserving the entire actual packet. Neither is general semantic retry/approval.
"""
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable

SCHEMA = 'cvp_sql_adjudication_input_repair_authorization.v1'
RELATION_SCHEMA = 'cvp_sql_adjudication_relation_contract_repair_authorization.v1'
PROOF = 'cvp_sql_adjudication_input_repair_proof.v1'
EXTRA_RELATIONS = {'qualifies', 'extends', 'applies', 'tension_evidence', 'superseding_evidence'}


def verify_relation_delta(old, current, findings, direction):
    """Complete immutable input, neutral rules only; never interpret finding prose."""
    from backend.pipeline.viewpoint_semantic_contract import RELATION_PATH, relation_rules
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    from backend.pipeline.viewpoint_audit_source_adjudication import source_review_adapter
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    if (old != current or old.get('schema_version') != 'wang_audit_source_adjudication_packet_v2'
            or not findings or any(f['verdict'] != 'audit_objection_upheld' for f in findings.values())
            or direction.get('allowed_added_field') != 'relation_role_contract'
            or direction.get('relation_contract') != binding(RELATION_PATH)
            or direction.get('standing_engineering_authority') is not True):
        raise ValueError('relation input repair changed material or lacks exact omitted-contract direction')
    root = Path(direction['original_root'])
    raw = read_artifact(root/'raw-gpt.json')
    intent = read_artifact(root/'raw-gpt.call-intent.json')
    from backend.pipeline.viewpoint_audit_owner_resolution import verify_sha
    verify_sha(intent)
    if (intent.get('schema_version') != 'wang_model_call_intent_v1'
            or intent.get('automatic_retry') is not False
            or intent.get('cache_path') != str(root/'raw-gpt.json')
            or any(intent.get(k) != raw.get(k) for k in ('model_id','backend','prompt_sha256',
                'generation_config_sha256','request_payload_sha256'))
            or direction.get('original_request_bindings') != [binding(root/n) for n in
                ('raw-gpt.call-intent.json','raw-gpt.request-budget.json','raw-gpt.request-encoding.json')]):
        raise ValueError('relation input repair lacks exact answered original intent/request')
    audit = read_artifact(Path(old['audit_binding']['path']))
    from backend.pipeline.viewpoint_semantic_contract import COMPONENT_ROLES,CONTEXTUAL_SEMANTICS
    if audit.get('meta',{}).get('semantic_contract_version') not in {COMPONENT_ROLES,CONTEXTUAL_SEMANTICS}:
        raise ValueError('independent audit already disclosed full roles or unknown contract')
    adapter = source_review_adapter(subscription_client('codex', 'gpt-6.1-sol', 'high'), root/'raw-gpt.json')
    if (direction.get('original_prompt_sha256') != adapter.prompt_sha256
            or relation_rules() in adapter._base_prompt):
        raise ValueError('relation role contract already supplied or original prompt drift')
    links = []
    for item in old['items']:
        head, revision = item.get('viewpoint'), item.get('viewpoint_revision')
        if (not item['item_id'].startswith('4:') or not head or not revision
                or revision.get('viewpoint_revision_id') != head['current_revision_id']
                or revision.get('viewpoint_id') != head['viewpoint_id']
                or revision.get('review_status') not in {'approved','system_approved','human_approved'}):
            raise ValueError('relation input repair lacks exact approved current CVR')
        eligible = [l for l in item['connections'] if l.get('link_type') in EXTRA_RELATIONS]
        if not eligible or any(l.get('effective_state') != 'active'
                or l.get('review_status') not in {'approved','system_approved','human_approved'}
                or l.get('pinned_claim_revision') != item['claim']['revision']
                or l.get('validated_against_viewpoint_revision_id') != head['current_revision_id']
                for l in eligible):
            raise ValueError('relation input repair lacks exact approved nonmember connection')
        links.extend(l['viewpoint_claim_link_id'] for l in eligible)
    if direction.get('affected_link_ids') != sorted(links) or len(links) != len(set(links)):
        raise ValueError('relation input repair foreign/duplicate affected links')


def make_relation_direction(store, campaign, run_id, original_root, quote):
    """Operator-bound proven input defect, under standing engineering authority.

    No automatic trigger or natural-language verdict/reason matching. The real
    corrected reader still decides; no original verdict or content is changed.
    """
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_source_adjudication import build_packet, source_review_adapter
    from backend.pipeline.viewpoint_audit_owner_resolution import binding
    from backend.pipeline.viewpoint_semantic_contract import RELATION_PATH
    from backend.pipeline.viewpoint_resolution_runtime import subscription_client
    original = Path(original_root).resolve()
    old, findings = validated_history(original)
    current = build_packet(Path(old['audit_binding']['path']), store, True)
    adapter = source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'), original/'raw-gpt.json')
    value = {'schema_version': RELATION_SCHEMA, 'campaign_sha256': campaign, 'run_id': run_id,
        'authorized_by':'owner', 'authorization_quote':quote, 'standing_engineering_authority':True,
        'maximum_additional_calls':1, 'automatic_future_retries_authorized':False,
        'human_content_approval':False, 'allowed_added_field':'relation_role_contract',
        'relation_contract':binding(RELATION_PATH), 'original_prompt_sha256':adapter.prompt_sha256,
        'affected_link_ids':sorted(l['viewpoint_claim_link_id'] for i in old['items']
            for l in i['connections'] if l.get('link_type') in EXTRA_RELATIONS),
        'original_root':str(original), 'corrected_packet_sha256':current['artifact_sha256'],
        'original_bindings':[binding(original/n) for n in
            ('packet.json','raw-gpt.json','gpt-final-handoff.json')], 'audit_binding':old['audit_binding']}
    value['original_request_bindings']=[binding(original/n) for n in
        ('raw-gpt.call-intent.json','raw-gpt.request-budget.json','raw-gpt.request-encoding.json')]
    verify_relation_delta(old,current,findings,value)
    return {**value,'artifact_sha256':sha256_json(value)}


def verify_direction_delta(old, current, findings, direction):
    if direction.get('schema_version') == RELATION_SCHEMA:
        verify_relation_delta(old,current,findings,direction)
    else:
        verify_delta(old,current,findings)


def applies_to_packet(old, current):
    from backend.pipeline.viewpoint_adjudication_history import target_identity
    old_targets={target_identity(i) for i in old['items']}
    current_targets={target_identity(i) for i in current['items']}
    matched=old_targets & current_targets
    if matched and matched != old_targets:
        raise ValueError('partially changed input-repair target set; no automatic replay')
    return bool(matched)


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
    relation = direction.get('schema_version') == RELATION_SCHEMA
    if (proof['authorization'] not in proof['bindings'] or direction.get('schema_version') not in {SCHEMA,RELATION_SCHEMA}
            or direction.get('authorized_by') != 'owner' or not direction.get('authorization_quote', '').strip()
            or direction.get('maximum_additional_calls') != 1
            or direction.get('automatic_future_retries_authorized') is not False
            or direction.get('human_content_approval') is not False
            or direction.get('allowed_added_field') != ('relation_role_contract' if relation else 'viewpoint_revision')):
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
    if relation and any(p not in proof['bindings'] for p in
            [direction['relation_contract'],*direction['original_request_bindings']]):
        raise ValueError('input repair missing physical relation contract binding')
    verify_direction_delta(old, current, findings, direction)
    if relation:
        # A copied old answer cannot masquerade as a complete-contract review.
        from backend.pipeline.viewpoint_audit_source_adjudication import source_review_adapter
        from backend.pipeline.viewpoint_resolution_runtime import subscription_client
        from backend.pipeline.viewpoint_semantic_contract import relation_rules
        for name in ('raw-gpt.json','raw-gpt.call-intent.json'):
            candidate = Path(path).parent/name
            if not candidate.exists():continue
            saved = read_artifact(candidate)
            adapter = source_review_adapter(subscription_client('codex','gpt-6.1-sol','high'),
                Path(path).parent/'raw-gpt.json')
            if (relation_rules() not in adapter._base_prompt
                    or any(saved.get(k) != getattr(adapter,k) for k in
                        ('model_id','backend','prompt_sha256','generation_config_sha256'))):
                raise ValueError('corrected relation request did not receive complete role contract')
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
    verify_direction_delta(old, current, findings, direction)
    corrected = original/'input-repair-once'
    write_immutable(corrected/'packet.json', current)
    auth = binding(authorization_path)
    body = {'schema_version': PROOF, 'authorization': auth,
            'bindings': [auth, *direction['original_bindings'], direction['audit_binding'],
                *([direction['relation_contract'],*direction['original_request_bindings']]
                  if direction.get('schema_version') == RELATION_SCHEMA else [])]}
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
        from backend.pipeline.cvp_sql_quota_resume import PROOF,verify_proof as verify_quota
        if (Path(root)/PROOF).exists():
            direction,_=verify_quota(Path(root)/PROOF)
            superseded.add(Path(direction['original_root']))
        proof_path = Path(root)/'owner-input-repair-proof.json'
        if proof_path.exists():
            direction, _ = verify_proof(proof_path)
            superseded.add(Path(direction['original_root']))
    return [Path(r) for r in roots if Path(r) not in superseded]
