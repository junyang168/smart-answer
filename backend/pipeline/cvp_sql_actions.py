"""Bounded revisions share the normal SQL execution/checkpoint boundary.

Content proposal/review contracts are reused; old prepare/run/apply/continuation
methods are not. No per-Claim wrapper, full Registry freeze, or ancestry replay.
"""
from __future__ import annotations

from pathlib import Path
import json
import time

from backend.api.canonical_repository.postgres_store import canonical_json,sha256_json
from backend.pipeline.cvp_sql_commit import binding, check_binding, pins, pin_rows, decoded_pins, commit_package, resume_reviewed_action
from backend.pipeline import source_reconciliation as content
from backend.pipeline.source_reconciliation_runner import Runner as ModelStages,body,read,write,validate_reviewed_records

SOURCE_SCHEMA='wang_source_sql_reconciliation_v2'
LINK_SCHEMA='wang_owner_sql_link_correction_v2'


def claim_management_review_packet(materials, candidate):
    from backend.pipeline.claim_management import audit_keys, Request, operation_questions, model_packet, review_findings
    from backend.pipeline.claim_model_output import review_packet
    packet = review_packet({**model_packet(materials), 'unresolved': review_findings(materials['request']['action_id'], materials['unresolved'], candidate),
            'candidate': candidate.model_dump(mode='json'), 'candidate_sha256': candidate.digest,
            'denominator': audit_keys(Request.model_validate(materials['unit_request']), materials['before'], candidate),
            'required_questions': operation_questions(materials['original_goal'], candidate)})
    from backend.pipeline import claim_direct_scope as direct
    request = Request.model_validate(materials['unit_request'])
    if direct.enabled(request):
        packet['claim_scope'] = direct.scope(request, materials['before'], candidate)
    if materials.get('program_metadata_projection'):
        packet['program_metadata_projection'] = {
            'sampling_status':'Only verified legacy AI sampling markers on updated targets are reset to candidate, never human approval.',
            'support_identities':'New EvidenceStep/SourceFragment identities and their typed references are assigned by the program. Review actual candidate identities and unchanged teaching, source quotes and links.'}
    from backend.pipeline.claim_evidence_review import add_packet
    return add_packet(packet, candidate)


def claim_source_transition_context(store, request, before):
    """Consume one committed source transition, never authorize source writes."""
    from backend.pipeline import claim_management as cm
    source_context = request.source_transition
    if not source_context:
        return None
    transition = cm.SourceTransition.model_validate_json(content.verify_file(source_context).read_text())
    payloads = {key: state['payload'] for key, state in before.items()}
    expected = {key for key, state in before.items() if key.startswith('claims/') and not state['retired']
                and cm.own_source_ids(state['payload'], payloads) & set(transition.before_sources)}
    if cm.is_source_impact(request) and set(request.target_keys) != expected:
        raise ValueError('source impact must account for the complete source/indirect owner population')
    if not cm.is_source_impact(request):
        predecessor = store.get_cvp_group('CMA-' + sha256_json(request.predecessor_action_id))
        if (not predecessor or predecessor['request'].get('source_transition') != source_context.model_dump(mode='json')
                or not set(request.target_keys) <= set(predecessor['original_goal']['target_keys'])):
            raise ValueError('source history does not belong to the explicitly linked source Review')
    histories = {}
    for sid, change_set_id in transition.source_change_set_ids.items():
        history = store.get_change_history(change_set_id)
        operations = [op for op in (history or {}).get('operations', [])
                      if op['collection'] == 'source_documents' and op['object_id'] == sid]
        state = before.get(content.key('source_documents', sid))
        if (not history or history['status'] != 'applied' or len(operations) != 1 or not state
                or operations[0]['operation'] != 'update'
                or any(operations[0]['before'].get(field) != operations[0]['after'].get(field)
                       for field in ('source_id', 'source_type', 'transcript_id'))
                or operations[0]['before'].get('source_file_sha256') != transition.before_sources[sid].file_sha256
                or operations[0]['after'].get('source_file_sha256') != request.source_bindings[sid].file_sha256
                or [operations[0]['after_revision'], operations[0]['after_sha256']]
                   != [state['revision'], state['content_sha256']]):
            raise ValueError('source impact is not the exact committed/current source version transition')
        histories[sid] = {'change_set_id': change_set_id, 'fingerprint_sha256': history['fingerprint_sha256'],
                          'source_operation': operations[0]}
    claim_keys = [key for key in cm.audit_keys(request, before) if key.startswith('claims/')]
    return {'schema_version': 'wang_claim_source_transition_v2', 'histories': histories,
            'population': sorted(expected), 'baselines': claim_source_baselines(store, request, before, claim_keys, transition)
                if cm.is_source_impact(request) else {}}


def claim_source_baselines(store, request, before, claim_keys, transition):
    """Authenticate direct prior content proof and exact mechanical transitions.

    Missing proof is recorded as missing; it never becomes an approval because
    the source impact reviewer calls an item unaffected. No ancestry replay.
    """
    from backend.pipeline import claim_management as cm
    from backend.pipeline.source_reconciliation_runner import body, read
    allowed_doc_fields = {'source_sha256','source_body_sha256','source_file_sha256','source_text_sha256',
        'anchor_binding_sha256','editorial_structure_sha256','editorial_topology_sha256','revision'}
    operations = {}
    for cid in set(transition.source_change_set_ids.values()):
        for op in store.get_change_history(cid)['operations']:
            operations[content.key(op['collection'], op['object_id'])] = op
    # Find each Claim's latest authority, then load/authenticate each distinct
    # review once. A batch review can cover many Claims; its full sources and
    # provider reply must not be reread and revalidated for every owner.
    claim_ids = [key.split('/', 1)[1] for key in claim_keys]
    with store.connect() as conn:
        rows = conn.execute("""SELECT requested.claim_id, latest.run_id
            FROM unnest(%s::text[]) AS requested(claim_id)
            CROSS JOIN LATERAL (
                SELECT run_id FROM wang_knowledge.pipeline_runs
                WHERE metadata->>'kind'='claim_management_round'
                  AND metadata->'claim_ids' ? requested.claim_id
                  AND ((metadata->'materials'->'request'->>'operation'<>'Review' OR metadata->'materials'->'request'->>'source_transition' IS NULL)
                       OR metadata->'materials'->'original_goal'->'target_keys' ? ('claims/' || requested.claim_id))
                  AND (metadata->>'checkpoint' IN ('applied','audited')
                       OR metadata->'blocked_current_claim_ids' ? requested.claim_id)
                ORDER BY started_at DESC,run_id DESC LIMIT 1
            ) latest""", (claim_ids,)).fetchall()
        latest = dict(rows)
        distinct_ids = sorted(set(latest.values()))
        metadata = dict(conn.execute("""SELECT run_id,metadata FROM wang_knowledge.pipeline_runs
            WHERE run_id=ANY(%s::text[])""", (distinct_ids,)).fetchall()) if distinct_ids else {}
    validated = {}
    result = {}
    for key in claim_keys:
        result[key] = None
        rid = latest.get(key.split('/', 1)[1])
        if rid is None:
            continue
        value = metadata[rid]
        if value.get('outcome') != 'completed' or value.get('pending_review_findings') or key.split('/',1)[1] in value.get('blocked_current_claim_ids', []):
            continue
        try:
            if rid not in validated:
                receipt = value['stage_results']['audit']; check_binding(receipt)
                saved = body(read(Path(receipt['path'])))
                check_binding(saved['audit'])
                report = json.loads(Path(saved['audit']['path']).read_text())
                material = value['materials']
                candidate = cm.Candidate.model_validate(value['candidate'])
                passed, _, _ = validate_claim_audit(report, material, candidate, historical=True)
                validated[rid] = (receipt, report, material, candidate) if passed else None
            if validated[rid] is None:
                continue
            receipt, report, material, candidate = validated[rid]
            old_pins = {content.key(r['collection'], r['object_id']): r['pin'] for r in value['audited_pins']}
            unit = cm.Request.model_validate(material['unit_request']).model_copy(update={'target_keys': [key], 'permissions': []})
            required = set(cm.audit_keys(unit, before))
            current = {k: v for k, v in before.items() if k in required}
            old_graph = {k: {'payload': v['payload'], 'retired': v['retired']} for k, v in report['packet']['actual_records'].items()}
            if set(current) != set(cm.audit_keys(unit, old_graph)) or not set(current) <= set(old_pins):
                continue
            valid = True
            for dep, state in current.items():
                actual = [state['revision'], state['content_sha256'], state['retired']]
                if actual == old_pins[dep]:
                    continue
                op = operations.get(dep)
                if (not op or op['operation'] != 'update' or old_pins[dep] != [op['before_revision'],op['before_sha256'],False]
                        or actual != [op['after_revision'],op['after_sha256'],False]):
                    valid = False; break
                changed = {f for f in set(op['before']) | set(op['after']) if op['before'].get(f) != op['after'].get(f)}
                permitted = allowed_doc_fields if dep.startswith('source_documents/') else {'source_sha256','revision'} if dep.startswith('source_fragments/') else {'revision'}
                if changed - permitted:
                    valid = False; break
            if valid:
                previous = value.get('source_revalidations', {}).get(key)
                if material['policy'].get('review_purpose') == 'source_impact' and not previous:
                    continue
                result[key] = {'run_id': rid, 'audit': receipt,
                    'content_audit': previous['content_audit'] if previous else receipt,
                    'question_scope': previous['question_scope'] if previous else cm.operation_questions(material['original_goal'], candidate),
                    'current_pins': pin_rows(pins({content.split_key(k): v for k, v in current.items()}))}
        except (KeyError, ValueError, RuntimeError):
            continue
    return result


def validate_claim_candidate_origin(root, materials, policy, sources, candidate):
    from backend.pipeline import claim_management as cm
    if 'gpt' in materials['roles']:
        raw = validate_claim_stage(root, 'gpt', policy, cm.model_packet(materials))
        from backend.pipeline.claim_model_output import candidate as expand_candidate
        expected, receipt = expand_candidate(raw['response'], materials)
    elif materials.get('continuation',{}).get('opus_api_transition'):
        original = materials['continuation']['previous_candidates'][materials['unit_id']]
        if original != materials['previous_candidate']:
            raise ValueError('API handoff altered the saved candidate')
        expected, receipt = cm.normalize_candidate(original, sources)
    else:
        expected = cm.existing_candidate(materials['before'], replacements=materials['replacements'])
        _, receipt = cm.normalize_candidate(expected.model_dump(mode='json'), sources)
    if candidate != expected or body(read(root / 'candidate-normalization.json')) != receipt:
        raise ValueError('candidate differs from exact raw and proved metadata-only projection')


def validate_claim_stage(root, role, policy, packet):
    from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter
    from backend.pipeline.source_reconciliation_runner import validate_bound_call
    return validate_bound_call(Path(root), role, packet, claim_management_model_adapter(role, policy))


def claim_management_package_body(round_id, writes):
    body = {'package_id': round_id}
    for key, write in sorted(writes.items()):
        if write['action'] == 'retire':
            continue
        body.setdefault(content.split_key(key)[0], []).append(write['payload'])
    return body


def claim_pending_consumers(before, writes):
    """Named downstream references become pending; payloads remain history."""
    from backend.api.canonical_repository.postgres_store import SEMANTIC_EXTRACTION_REFERENCE_FIELDS
    from backend.pipeline.claim_management import CLAIM_COLLECTIONS
    retired = {content.split_key(k)[1] for k, v in writes.items() if v['action'] == 'retire'}
    def referenced(value, fields):
        if isinstance(value, dict):
            return any((bool(set(content.strings(child)) & retired) if key in fields else referenced(child, fields))
                       for key, child in value.items())
        if isinstance(value, list):
            return any(referenced(child, fields) for child in value)
        return False
    return sorted(key for key, state in before.items() if not state['retired']
        and content.split_key(key)[0] not in CLAIM_COLLECTIONS
        and referenced(state['payload'], SEMANTIC_EXTRACTION_REFERENCE_FIELDS.get(content.split_key(key)[0], set())))


def pending_consumer_validation_payload(collection, payload, retired_ids):
    """Validation-only view of exact named pending references, never a write.

    Unknown fields remain in this picture and still block retirement. Historical
    IDs stay physically resolvable; the SQL pending obligation blocks current use.
    """
    from backend.api.canonical_repository.postgres_store import SEMANTIC_EXTRACTION_REFERENCE_FIELDS
    fields = SEMANTIC_EXTRACTION_REFERENCE_FIELDS.get(collection, set())
    def remove(value):
        if isinstance(value, str):
            return None if value in retired_ids else value
        if isinstance(value, list):
            return [filtered for child in value if (filtered := remove(child)) is not None]
        if isinstance(value, dict):
            return {k: filtered for k, child in value.items() if (filtered := remove(child)) is not None}
        return value
    def visit(value):
        if isinstance(value, dict):
            return {k: remove(child) if k in fields else visit(child) for k, child in value.items()}
        if isinstance(value, list):
            return [visit(child) for child in value]
        return value
    return visit(payload)


def validate_claim_management_package(package):
    """Exact new direction, frozen candidates and independent Opus raw.

    This validator never grants Gemini qualification. The short transaction
    admits only this write set; post-commit audit has its own result boundary.
    """
    from backend.pipeline import claim_management as cm
    app = package['source_repair_application']
    if app['schema_version'] != cm.VERSION:
        raise ValueError('wrong Claim management content authority')
    if set(app) != {'schema_version','run_id','materials','candidate','opus_raw','pending_consumers'}:
        raise ValueError('Claim package cannot acquire an unversioned authority extension')
    for key in ('materials', 'candidate', 'opus_raw'):
        check_binding(app[key])
    materials = body(read(Path(app['materials']['path'])))
    root = Path(app['materials']['path']).parent
    if (app['opus_raw'] != binding(root / 'opus.raw.json') or app['candidate'] != binding(root / 'candidate.json')
            or materials['round_id'] != app['run_id']):
        raise ValueError('Claim package stage/round identity drift')
    request = cm.Request.model_validate(materials['request'])
    unit_request = cm.Request.model_validate(materials['unit_request'])
    # A unit narrows only existing targets and permissions; it cannot invent
    # source, operation, field or creation authority by partitioning a batch.
    narrowed = unit_request.model_dump(mode='json')
    original = request.model_dump(mode='json')
    if (any(narrowed[k] != original[k] for k in original if k not in {'target_keys', 'permissions', 'source_bindings', 'creation_permissions'})
            or not set(unit_request.target_keys) <= set(request.target_keys)
            or any(p not in request.permissions for p in unit_request.permissions)
            or any(p not in request.creation_permissions for p in unit_request.creation_permissions)
            or any(bound != request.source_bindings.get(sid) for sid, bound in unit_request.source_bindings.items())):
        raise ValueError('unit expands original Claim authority')
    policy, sources = cm.load_authority(request, source_documents=cm.source_documents(materials['before']),
                                       selected_source_ids=unit_request.source_bindings, human_source_decisions=materials.get('human_source_decisions'))
    if policy.model_dump(mode='json') != materials['policy'] or sources != materials['sources']:
        raise ValueError('Claim policy/complete physical own source drift')
    from backend.pipeline.claim_joint_update import enabled, admission_error
    if enabled(request, policy):
        if unit_request.model_dump(mode='json') != original:
            raise ValueError('joint Update package cannot narrow its complete original unit')
        if refusal := admission_error(request, policy, materials['before'], {**materials['original_goal'],
                'units': {materials['unit_id']: request.target_keys}}):
            raise ValueError('joint Update package admission: ' + refusal)
    candidate = cm.Candidate.model_validate(body(read(root / 'candidate.json')))
    _, writes = cm.validate_candidate(unit_request, materials['before'], candidate, sources,
        created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
    validate_claim_candidate_origin(root, materials, policy, sources, candidate)
    packet = claim_management_review_packet(materials, candidate)
    raw = validate_claim_stage(root, 'opus', policy, packet)
    from backend.pipeline.claim_model_output import review as expand_review
    review = expand_review(raw['response'], materials, candidate)
    passed = cm.validate_review(candidate, review, packet['denominator'], packet['required_questions'],
                                materials['unresolved'], sources, request=unit_request, records=materials['before'])
    if not passed:
        raise ValueError('Claim package lacks passing complete independent Opus review')
    expected = claim_management_package_body(app['run_id'], writes)
    if {k: v for k, v in package.items() if k != 'source_repair_application'} != expected:
        raise ValueError('Claim package exceeds exact reviewed complete postimages')
    if app['pending_consumers'] != claim_pending_consumers(materials.get('transaction_before', materials['before']), writes):
        raise ValueError('Claim package omitted or invented a pending historical consumer')
    return {content.split_key(k): v for k, v in writes.items()}


def validate_claim_audit(report, materials, candidate, *, historical=False):
    """Consume independent HTTP raw, exact SQL pins and physical own sources."""
    from backend.pipeline import claim_management as cm
    from hashlib import sha256
    audit_model = cm.Policy.model_validate(materials['policy']).models['gemini']['model_id']
    if (report.get('schema_version') != cm.VERSION or report.get('model') != audit_model
            or report.get('layer') != 3 or report.get('round_id') != materials['round_id']
            or report.get('candidate_sha256') != candidate.digest
            or report.get('source_bindings') != materials['unit_request']['source_bindings']):
        raise ValueError('independent Claim audit identity drift')
    if not historical:
        cm.require_current_implementation(materials['audit_contract'])
    bindings = report['bindings']
    if set(bindings) != {'intent', 'provider', 'response'}:
        raise ValueError('independent Claim audit has no complete original HTTP response')
    for bound in bindings.values():
        check_binding(bound)
    intent = json.loads(Path(bindings['intent']['path']).read_text())
    provider = json.loads(Path(bindings['provider']['path']).read_text())
    response = json.loads(Path(bindings['response']['path']).read_text())
    raw = provider['provider_response']
    if (intent['model'] != audit_model or intent['prompt'] != report['prompt']
            or intent['schema'] != report['schema'] or intent.get('automatic_retry') is not False
            or intent.get('maximum_calls') != 1 or provider['request'] != intent
            or raw.get('promptFeedback', {}).get('blockReason')
            or len(raw.get('candidates', [])) != 1 or raw['candidates'][0].get('finishReason') not in {'STOP', None}
            or json.loads(raw['candidates'][0]['content']['parts'][0]['text']) != response
            or response != report['response']):
        raise ValueError('independent Gemini raw/cache/block binding drift')
    budget = materials['policy']['budget']
    wire = json.dumps({'contents': [{'parts': [{'text': report['prompt']}]}], 'generationConfig': {
        'temperature': 0, 'responseMimeType': 'application/json', 'responseJsonSchema': report['schema'],
        'maxOutputTokens': budget['max_output_tokens']}}).encode()
    if (len(wire) > budget['max_request_bytes'] or intent['request_bytes'] != len(wire)
            or intent['request_sha256'] != sha256(wire).hexdigest()):
        raise ValueError('independent complete request bytes/configuration drift')
    packet = report['packet']
    if (packet['original_goal'] != cm.goal_packet(materials['original_goal']) or packet['unresolved'] != cm.review_findings(materials['request']['action_id'], materials['unresolved'], candidate)
            or packet['replacements'] != candidate.replacements or packet['candidate_sha256'] != candidate.digest
            or report['schema'] != materials['audit_contract']['response_schema']):
        raise ValueError('independent audit omitted original task or prior findings')
    instructions = content.verify_file(cm.source.FileBinding.model_validate(
        materials['audit_contract']['instructions'])).read_text().strip()
    if report['prompt'] != instructions + '\n' + json.dumps(packet, ensure_ascii=False, sort_keys=True):
        raise ValueError('independent raw prompt differs from exact instructions and complete actual material')
    states = {k: {'payload': r['payload'], 'retired': r['retired']} for k, r in packet['actual_records'].items()}
    request = cm.Request.model_validate(materials['unit_request'])
    if packet.get('required_questions') != cm.operation_questions(materials['original_goal'], candidate):
        raise ValueError('independent audit changed required operation questions')
    denominator = cm.audit_keys(request, states, candidate)
    from backend.pipeline.claim_joint_update import enabled
    if enabled(request) and packet.get('joint_update_writes') != sorted(d.record_key for d in candidate.dispositions if d.action != 'retain'):
        raise ValueError('joint independent audit omitted actual write impact scope')
    if set(denominator) != set(packet['denominator']) or set(packet['actual_records']) != set(denominator):
        raise ValueError('independent Claim audit incomplete current dependency denominator')
    actual_pins = [{'collection': content.split_key(k)[0], 'object_id': content.split_key(k)[1],
                   'pin': [v['revision'], v['sha'], v['retired']]} for k, v in sorted(packet['actual_records'].items())]
    if actual_pins != report['read_pins']:
        raise ValueError('independent audit actual current SQL readset drift')
    policy, sources = cm.load_authority(cm.Request.model_validate(materials['request']), source_documents=cm.source_documents(materials['before']),
        archived_sources=materials.get('source_archives') if historical else None,
        selected_source_ids=materials['unit_request']['source_bindings'], human_source_decisions=materials.get('human_source_decisions'))
    if policy.model_dump(mode='json') != materials['policy'] or sources != materials['sources']:
        raise ValueError('Claim stage source/policy drift')
    cm.validate_candidate(request, materials['before'], candidate, sources,
                          created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
    full_sources = {sid: {'paragraphs': paragraphs} for sid, paragraphs in packet['full_sources'].items()}
    if ({sid: {k: {'text': p['text']} for k, p in v['paragraphs'].items()} for sid, v in sources.items()}
            != packet['full_sources']):
        raise ValueError('independent audit full physical own source projection differs')
    expected_before = {sid: {k: {'text': p['text']} for k, p in v['before']['paragraphs'].items()}
                       for sid, v in sources.items() if v.get('before')}
    from backend.pipeline.claim_direct_scope import enabled as direct_scope_enabled, scope as direct_scope
    scope_request = cm.Request.model_validate(materials['unit_request'])
    if direct_scope_enabled(scope_request) and packet.get('claim_scope') != direct_scope(scope_request, materials['before'], candidate):
        raise ValueError('independent audit direct-neighbour scope drift')
    if 'human_source_decisions' in materials:
        expected_human = {sid: v.get('human_source_decisions', []) for sid, v in sources.items()}
        if packet.get('human_source_decisions') != expected_human:
            raise ValueError('independent audit omitted or changed human source facts')
    if packet.get('before_sources', {}) != expected_before:
        raise ValueError('independent source impact omitted/changed the complete old physical source')
    for sid in full_sources:
        full_sources[sid]['body_sha256'] = sources[sid]['body_sha256']
    for sid, paragraphs in expected_before.items():
        full_sources[sid]['before'] = {'paragraphs': paragraphs}
    review = cm.Review.model_validate(response)
    approved = cm.validate_review(candidate, review, denominator, cm.operation_questions(materials['original_goal'], candidate),
                                   materials['unresolved'], full_sources, request=request, records=states)
    if approved:
        cm.goal_satisfied(materials['original_goal'], states, candidate.replacements)
    return approved, review, actual_pins


def validate_claim_stage_receipt(receipt):
    """One authenticated content result for existing SQL run metadata."""
    from backend.pipeline import claim_management as cm
    check_binding(receipt['materials'])
    root = Path(receipt['materials']['path']).parent
    materials = body(read(root / 'materials.json'))
    if receipt['round_id'] != materials['round_id']:
        raise ValueError('Claim stage receipt belongs to another round')
    check_binding(receipt['candidate'])
    candidate = cm.Candidate.model_validate(body(read(root / 'candidate.json')))
    stage = receipt['stage']
    if stage == 'audit':
        check_binding(receipt['audit'])
        report = json.loads(Path(receipt['audit']['path']).read_text())
        passed, review, _ = validate_claim_audit(report, materials, candidate)
        return materials, candidate, review, passed
    cm.require_current_implementation(materials['audit_contract'])
    policy, sources = cm.load_authority(cm.Request.model_validate(materials['request']), source_documents=cm.source_documents(materials['before']),
                                       selected_source_ids=materials['unit_request']['source_bindings'], human_source_decisions=materials.get('human_source_decisions'))
    if sources != materials['sources'] or policy.model_dump(mode='json') != materials['policy']:
        raise ValueError('Claim stage source/policy drift')
    cm.validate_candidate(cm.Request.model_validate(materials['unit_request']), materials['before'], candidate, sources,
                          created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
    if stage == 'candidate':
        validate_claim_candidate_origin(root, materials, policy, sources, candidate)
        if policy.all_findings_to_gpt and 'gpt' in materials['roles']:
            cm.validate_proposer_resolutions(candidate, materials['unresolved'], sources, request=cm.Request.model_validate(materials['unit_request']))
        return materials, candidate, None, None
    if stage == 'opus':
        packet = claim_management_review_packet(materials, candidate)
        raw = validate_claim_stage(root, 'opus', policy, packet)
        from backend.pipeline.claim_model_output import review as expand_review
        review = expand_review(raw['response'], materials, candidate)
        passed = cm.validate_review(candidate, review, packet['denominator'], packet['required_questions'],
                                     materials['unresolved'], sources,
                                     request=cm.Request.model_validate(materials['unit_request']), records=materials['before'])
        return materials, candidate, review, passed
    if stage == 'readback':
        if policy.independent_audit_required:
            raise ValueError('program readback cannot replace a required independent audit')
        packet = claim_management_review_packet(materials, candidate)
        raw = validate_claim_stage(root, 'opus', policy, packet)
        from backend.pipeline.claim_model_output import review as expand_review
        review = expand_review(raw['response'], materials, candidate)
        passed = cm.validate_review(candidate, review, packet['denominator'], packet['required_questions'],
            materials['unresolved'], sources, request=cm.Request.model_validate(materials['unit_request']), records=materials['before'])
        check_binding(receipt['readback'])
        if receipt['readback'] != binding(root / 'readback.json'):
            raise ValueError('program readback belongs to a different round')
        report = body(read(root / 'readback.json'))
        if (report['round_id'] != materials['round_id'] or report['candidate_sha256'] != candidate.digest
                or report['validation_kind'] != 'opus_and_program_readback'):
            raise ValueError('program readback identity drift')
        states = report['actual_records']
        denominator = cm.audit_keys(cm.Request.model_validate(materials['unit_request']), states, candidate)
        if set(states) != set(denominator) or pin_rows(pins({content.split_key(k):v for k,v in states.items()})) != report['read_pins']:
            raise ValueError('program readback omitted or changed its current scope')
        if passed and not cm.is_source_impact(materials['request']):
            cm.goal_satisfied(materials['original_goal'], states, candidate.replacements)
        return materials, candidate, review, passed
    raise ValueError('unknown Claim content stage')


def execute_claim_management(store, request, *, root, ports):
    """Claim operations on the existing call/SQL/audit execution boundaries.

    No historical contract is upgraded. Read never allocates a model slot;
    Update starts with the actual existing candidate, then revises if needed.
    Every applied round is retained even when its independent audit rejects.
    """
    from types import SimpleNamespace
    from backend.pipeline import claim_management as cm
    from backend.pipeline.claim_joint_update import enabled
    from backend.pipeline.source_reconciliation_runner import exclusive, Stop
    request = cm.Request.model_validate(request)
    read_context = store.read_affected_context if cm.is_source_impact(request) or request.operation == 'Create' else store.read_claim_context
    root = Path(root).resolve()
    if request.operation == 'Read':
        return {'status': 'completed', 'operation': 'Read', 'records': {
            key: store.get_record_state(*content.split_key(key)) for key in request.target_keys}}
    with exclusive(root):
        parent_id = 'CMA-' + sha256_json(request.action_id)
        parent = store.get_cvp_group(parent_id)
        if parent:
            if parent['request'] != request.model_dump(mode='json') or parent['execution_root'] != str(root):
                raise ValueError('Claim recovery cannot reset original input/root/budget')
        else:
            keys = {content.split_key(k) for k in request.target_keys}
            if not enabled(request):
                keys.update((p.collection, p.object_id) for p in request.permissions)
            keys.update(('source_documents', sid) for sid in request.source_bindings)
            records = read_context(sorted(keys))
            before = {content.key(*k): cm.model_state(v) for k, v in records.items()}
            goal = cm.original_goal(request, before)
            parent = store.prepare_claim_management_action(request, goal=goal, read_pins=pins(records), root=root)
        if parent.get('admission_refusal'):
            return parent['result']
        policy, sources = cm.load_authority(request, source_documents=cm.source_documents(parent['original_goal']['original_objects']),
            human_source_decisions=parent.get('human_source_decisions'))
        if parent.get('continued_by'):
            if parent['result'].get('status') == 'completed':
                return {'status':'completed', 'historical_result':parent['result'], 'context_follow_up':parent['continued_by']}
            return {'status':'paused', 'reason':'continued_in_linked_action', 'run_id':parent['continued_by']}
        if parent['result'].get('status') == 'completed':
            return store.finish_claim_management_action(parent_id)
        if parent['result'].get('returned_rejected_call'):
            from backend.pipeline.claim_review_recovery import resume_rejected_output
            rejected = parent['result']['returned_rejected_call']
            if not isinstance(parent['result'].get('detail'),str) or request.operation != 'Update' or not policy.all_findings_to_gpt:
                return parent['result']
            try:
                rejected_round, role = rejected.rsplit(':',1)
                resume_rejected_output(store,rejected_round,role=role,error=parent['result']['detail'])
                parent = store.get_cvp_group(parent_id)
            except (ValueError,RuntimeError,OSError) as exc:
                return store.pause_claim_management_action(parent_id,
                    reason='budget_exhausted' if 'budget' in str(exc) else 'engineering_required',detail=str(exc))
        if parent['result'].get('reason') in {'human_required','no_progress','disagreement','permission_required','budget_exhausted'}:
            return parent['result']
        try:
            cm.require_current_implementation(parent['audit_contract'])
        except ValueError as exc:
            return store.pause_claim_management_action(parent_id, reason='engineering_required', detail=str(exc))
        initialize_subscription_runtime(ports)
        try:
            for unit_id, targets in parent['original_goal']['units'].items():
                while True:
                    parent = store.get_cvp_group(parent_id)
                    prior_id = parent['latest_rounds'].get(unit_id)
                    prior = store.get_cvp_group(prior_id) if prior_id else None
                    if prior and prior.get('outcome') in {'completed','reported'}:
                        break
                    if prior and prior.get('outcome') in {'human_required','engineering_required','permission_required'}:
                        return store.pause_claim_management_action(parent_id, reason=prior['outcome'], detail='Original finding requires its stated resolution')
                    new_round = prior is None or prior.get('outcome') == 'revision_required'
                    if new_round:
                        ordinal = prior['round_number'] + 1 if prior else 0
                        keys = {content.split_key(k) for k in targets}
                        keys.update(content.split_key(k) for k in parent['created_keys'])
                        if request.operation == 'Create':
                            keys.update(('source_documents', sid) for sid in request.source_bindings)
                        if len(parent['original_goal']['units']) == 1:
                            keys.update((p.collection,p.object_id) for p in request.permissions)
                        # Root authority is fixed; only permissions reachable
                        # from this unit are presented as writable.
                        records = read_context(sorted(keys))
                        for sid in {p.source_id for p in request.creation_permissions}:
                            if ('source_documents',sid) not in records:
                                records['source_documents',sid] = store.get_record_state('source_documents',sid)
                        transaction_before = {content.key(*k): cm.model_state(v) for k, v in records.items()}
                        before = transaction_before
                        if enabled(request):
                            frozen = set(parent['original_goal']['original_objects'])
                            natural = request.model_copy(deep=True, update={'permissions': []})
                            current_scope = set(cm.audit_keys(natural, before))
                            if current_scope - frozen:
                                raise ValueError('joint Update dependency scope expanded outside frozen original objects')
                        unit_request = request.model_copy(deep=True, update={'target_keys': targets, 'permissions': []})
                        unit_keys = set(cm.audit_keys(unit_request, before))
                        unit_request.permissions = [p for p in request.permissions if p.key in unit_keys]
                        if len(parent['original_goal']['units']) == 1:
                            unit_request.permissions = list(request.permissions)
                            unit_keys.update(cm.audit_keys(unit_request, before))
                        if prior:
                            if prior.get('candidate'):
                                unit_keys.update(k for k in cm.audit_keys(unit_request, before, cm.Candidate.model_validate(prior['candidate'])))
                            elif not (prior.get('citation_input_recovery') or prior.get('model_input_recovery')):
                                raise ValueError('missing prior candidate requires authenticated rejected-input recovery')
                        if request.operation == 'Create':
                            unit_keys.update(k for k in before if content.split_key(k)[0] in cm.CLAIM_COLLECTIONS)
                        source_ids = {content.split_key(k)[1] for k in unit_keys if k.startswith('source_documents/')}
                        from backend.pipeline.claim_direct_scope import enabled as direct_scope_enabled, scope as direct_scope
                        if direct_scope_enabled(request):
                            source_ids = set(direct_scope(unit_request, before)['source_ids'])
                        source_ids.update(p.source_id for p in request.creation_permissions)
                        unit_keys.update(content.key('source_documents',sid) for sid in source_ids)
                        unit_keys = {k for k in unit_keys if not k.startswith('source_documents/') or content.split_key(k)[1] in source_ids}
                        before = {k: v for k, v in before.items() if k in unit_keys}
                        unit_request.source_bindings = {sid: bound for sid, bound in request.source_bindings.items() if sid in source_ids}
                        unit_request.creation_permissions = [p for p in request.creation_permissions if p.source_id in source_ids]
                        unit_decisions = [d for d in parent.get('human_source_decisions', []) if d['source_id'] in source_ids]
                        _, unit_sources = cm.load_authority(request, source_documents=cm.source_documents(before), selected_source_ids=source_ids,
                            human_source_decisions=unit_decisions if 'human_source_decisions' in parent else None)
                        unit_goal = cm.unit_goal(parent['original_goal'], unit_request)
                        roles = cm.required_roles(request, ordinal, policy)
                        rid = 'CMR-' + sha256_json({'action': parent_id, 'unit': unit_id, 'round': ordinal})
                        materials = {'schema_version': cm.VERSION, 'round_id': rid, 'unit_id': unit_id,
                            'packet_profile':'claim_single_record_table_v2',
                            'round_number': ordinal, 'request': request.model_dump(mode='json'),
                            'unit_request': unit_request.model_dump(mode='json'), 'policy': policy.model_dump(mode='json'),
                            'audit_contract': parent['audit_contract'],
                            'sources': unit_sources, 'source_archives': {sid: bound for sid, bound in parent['source_archives'].items() if sid in source_ids},
                            'before': before, 'transaction_before': transaction_before, 'original_goal': unit_goal,
                            'roles': roles,
                            'program_owned_fields': {'source_fragments': sorted(cm.FRAGMENT_HASH_FIELDS),
                                'review_provenance': sorted(cm.source.PROVENANCE), 'reader_visible_text_changes': False},
                            'created_keys': parent['created_keys'], 'replacements': parent.get('replacements', {}),
                            'unresolved': {k: v for k, v in {**parent.get('context_findings', {}), **parent['findings']}.items()
                                if v['target_key'] in unit_keys},
                            'previous_candidate': (prior.get('candidate') if prior else
                                parent.get('continuation', {}).get('previous_candidates', {}).get(unit_id)),
                            'claim_ids': [k.split('/',1)[1] for k in unit_keys if k.startswith('claims/')],
                            'writable_record_shapes': {col: cm.KNOWLEDGE_COLLECTIONS[col][0].model_json_schema()
                                for col in sorted({p.collection for p in unit_request.permissions} | {p.collection for p in request.creation_permissions})}}
                        if 'human_source_decisions' in parent:
                            materials['human_source_decisions'] = unit_decisions
                        if parent.get('continuation'):
                            materials['continuation'] = parent['continuation']
                        if policy.legacy_ai_supersession_restore:
                            materials['program_owned_fields']['legacy_ai_supersession_restore'] = (
                                'Explicit Restore may restore an old AI-superseded Claim even when its SQL retired flag is false. '
                                'For each selected restore, preserve raw review provenance; the program projects review_status=candidate '
                                'and superseded_by=null before Opus review. This is not human approval. '
                                'Review retirement_cause_resolved and not_duplicate against the full current source. '
                                'All named active targets and permitted Evidence/relations may be maintained in the same transaction.')
                        from backend.pipeline.claim_metadata_projection import compile_projection, FIELD
                        with store.connect() as conn, conn.cursor() as cursor:
                            projection = compile_projection(cursor, unit_request, before, policy)
                        if projection:
                            materials[FIELD] = projection
                            if projection['support_identities']:
                                materials['program_owned_fields']['support_creation_ids'] = 'New EvidenceStep/SourceFragment object_id values are local aliases for typed references. The program assigns final action-scoped identities. Claim IDs must still use the reserved SQL slots.'
                            if projection['sampling_markers']:
                                materials['program_owned_fields']['legacy_ai_sampling'] = 'These target markers are verified historical AI sampling only, not human source holds. Do not edit review provenance; the program resets an updated target to candidate before independent review, never human-approved.'
                        rid = store.prepare_claim_management_round(parent_id, unit_id=unit_id, round_number=ordinal,
                                                                  materials=materials, roles=roles)
                        prior = store.get_cvp_group(rid)
                    else:
                        rid = prior['run_id']
                        materials = prior['materials']
                        records = {content.split_key(k): v for k, v in materials.get('transaction_before', materials['before']).items()}
                    round_root = Path(prior.get('recovery_root', root / rid))
                    write(round_root / 'materials.json', materials)
                    def guard():
                        cm.load_authority(request, source_documents=cm.source_documents(materials['before']),
                                          selected_source_ids=materials['unit_request']['source_bindings'], human_source_decisions=materials.get('human_source_decisions'))
                        with store.connect() as conn, conn.cursor() as cursor:
                            if 'human_source_decisions' in materials:
                                from backend.pipeline.claim_human_source import assert_current
                                assert_current(cursor, materials['unit_request']['source_bindings'], materials['human_source_decisions'])
                            if 'program_metadata_projection' in materials:
                                from backend.pipeline.claim_metadata_projection import assert_current
                                assert_current(cursor, cm.Request.model_validate(materials['unit_request']), materials['before'], policy, materials['program_metadata_projection'])
                            observed = store._assert_transaction_read_pins(cursor, pins(records))
                            from backend.pipeline.claim_direct_scope import enabled as direct_scope_enabled
                            if enabled(request) or direct_scope_enabled(request):
                                # Keep every transaction pin. Only actual
                                # content roots expand the semantic audit.
                                audited = {content.split_key(k) for k in materials['before']}
                                scope_pins = {k: v for k, v in pins(records).items() if k in audited}
                                store._assert_claim_management_scope(cursor, scope_pins,
                                    records={k: v for k, v in observed.items() if k in audited}, request=materials['unit_request'])
                    before_call = getattr(ports, 'before_claim_model_call', getattr(ports, 'before_model_call', lambda stage: None))
                    transport = SimpleNamespace(before_model_call=before_call,
                        model_call_capacity=getattr(ports, 'model_call_capacity', None),
                        model_call_reserved=lambda role, intent: store.begin_claim_management_call(
                            parent_id, round_id=rid, role=role, intent=intent))
                    stages = ModelStages(root, transport)
                    stages.root = round_root
                    def call(role, packet):
                        guard()
                        return stages.call_bound(role, packet, ports.claim_management_adapter(role, policy), expected=(policy.models[role]['model_id'], policy.models[role]['backend']))
                    def record(stage, **extra):
                        receipt = {'round_id': rid, 'stage': stage, 'materials': binding(round_root / 'materials.json'),
                                   'candidate': binding(round_root / 'candidate.json'), **extra}
                        path = round_root / (stage + '-result.json')
                        write(path, receipt)
                        return store.record_claim_management_stage(rid, binding(path))
                    if prior['checkpoint'] == 'prepared':
                        raw_candidate = (call('gpt', cm.model_packet(materials))['response'] if 'gpt' in materials['roles'] else
                            materials['previous_candidate'] if materials.get('continuation',{}).get('opus_api_transition') else
                            cm.existing_candidate(materials['before'], replacements=materials['replacements']).model_dump(mode='json'))
                        from backend.pipeline.claim_model_output import candidate as expand_candidate
                        candidate, normalization = (expand_candidate(raw_candidate, materials) if 'gpt' in materials['roles']
                            else cm.normalize_candidate(raw_candidate, materials['sources']))
                        cm.validate_candidate(cm.Request.model_validate(materials['unit_request']), materials['before'], candidate, materials['sources'],
                            created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
                        write(round_root / 'candidate.json', candidate.model_dump(mode='json'))
                        write(round_root / 'candidate-normalization.json', normalization)
                        record('candidate')
                        packet = claim_management_review_packet(materials, candidate)
                        cm.require_full_sources(cm.Request.model_validate(materials['unit_request']), materials['before'], candidate)
                        call('opus', packet)
                        prior = record('opus')
                        if prior.get('outcome'):
                            if prior['outcome'] == 'revision_required' and (request.operation != 'Review' or policy.all_findings_to_gpt):
                                continue
                            break
                        _, writes = cm.validate_candidate(cm.Request.model_validate(materials['unit_request']), materials['before'], candidate, materials['sources'],
                            created_keys=materials['created_keys'], original_replacements=materials['replacements'], metadata_projection=materials.get('program_metadata_projection'))
                        package = claim_management_package_body(rid, writes)
                        package['source_repair_application'] = {'schema_version': cm.VERSION, 'run_id': rid,
                            'materials': binding(round_root / 'materials.json'), 'candidate': binding(round_root / 'candidate.json'),
                            'opus_raw': binding(round_root / 'opus.raw.json'),
                            'pending_consumers': claim_pending_consumers(materials['transaction_before'], writes)}
                        write(round_root / 'package.json', package)
                        guard()
                        commit_package(store, rid, package=package, approval=binding(round_root / 'opus.raw.json'),
                            authorization=request.authorization.model_dump(), backup={},
                            sources=[b for b in materials['unit_request']['source_bindings'].values()], read_pins=pins(records),
                            reason='Claim ' + request.operation + ' independently reviewed current candidate', actor='claim-management-v2',
                            source_kind=cm.VERSION, pending_cvp_claim_ids=prior['claim_ids'])
                        prior = store.get_cvp_group(rid)
                    if prior['checkpoint'] == 'reviewed':
                        resume_reviewed_action(store, rid)
                        prior = store.get_cvp_group(rid)
                    if prior['checkpoint'] == 'applied':
                        candidate = cm.Candidate.model_validate(prior['candidate'])
                        roots = {content.split_key(k) for k in materials['before']}
                        if request.operation != 'Create' and not cm.is_source_impact(request):
                            unit = cm.Request.model_validate(materials['unit_request'])
                            roots = {content.split_key(k) for k in unit.target_keys}
                            roots.update((p.collection,p.object_id) for p in unit.permissions)
                            roots.update(content.split_key(k) for k in parent['created_keys'])
                        roots.update(content.split_key(r.key) for r in candidate.records)
                        actual = read_context(sorted(roots))
                        states = {content.key(*k): v for k, v in actual.items()}
                        denominator = cm.audit_keys(cm.Request.model_validate(materials['unit_request']), states, candidate)
                        cm.require_full_sources(cm.Request.model_validate(materials['unit_request']), states, candidate,
                                                qualification=not cm.is_source_impact(request))
                        if not policy.independent_audit_required:
                            write(round_root / 'readback.json', {'round_id':rid, 'candidate_sha256':candidate.digest,
                                'validation_kind':'opus_and_program_readback',
                                'actual_records':{k:cm.model_state(states[k]) for k in denominator},
                                'read_pins':pin_rows(pins({content.split_key(k):states[k] for k in denominator}))})
                            prior = record('readback', readback=binding(round_root / 'readback.json'))
                            break
                        audit_input = {'schema_version': cm.VERSION, 'round_id': rid, 'original_goal': cm.goal_packet(materials['original_goal']),
                            'model': policy.models['gemini']['model_id'],
                            'denominator': denominator, 'read_pins': pin_rows(pins({content.split_key(k): states[k] for k in denominator})),
                            'source_bindings': materials['unit_request']['source_bindings'], 'replacements': candidate.replacements,
                            'candidate_sha256': candidate.digest, 'unresolved': cm.review_findings(request.action_id, materials['unresolved'], candidate),
                            'required_questions': cm.operation_questions(materials['original_goal'], candidate),
                            'response_schema': materials['audit_contract']['response_schema'],
                            'audit_contract': materials['audit_contract'], 'budget': materials['policy']['budget']}
                        from backend.pipeline.claim_direct_scope import enabled as direct_scope_enabled, scope as direct_scope
                        if direct_scope_enabled(request):
                            audit_input['claim_scope'] = direct_scope(cm.Request.model_validate(materials['unit_request']), materials['before'], candidate)
                        if 'human_source_decisions' in materials:
                            audit_input['human_source_decisions'] = materials['human_source_decisions']
                        if enabled(request):
                            audit_input['joint_update_writes'] = sorted(d.record_key for d in candidate.dispositions if d.action != 'retain')
                        if request.source_transition:
                            transition = cm.SourceTransition.model_validate_json(content.verify_file(request.source_transition).read_text())
                            selected = set(materials['unit_request']['source_bindings'])
                            audit_input['source_transition'] = transition.model_copy(update={
                                'before_sources': {sid: b for sid, b in transition.before_sources.items() if sid in selected},
                                'source_change_set_ids': {sid: cid for sid, cid in transition.source_change_set_ids.items() if sid in selected}}).model_dump(mode='json')
                        input_path = round_root / 'audit-input.json'
                        audit_path = round_root / 'independent-audit/audit.json'
                        write(input_path, audit_input)
                        if not audit_path.exists():
                            slot = store.get_cvp_group(parent_id)['call_reservations'][rid + ':gemini']
                            if slot['state'] != 'planned':
                                raise Stop('independent call has no complete saved reply; do not repeat', status='call_uncertain')
                            before_call('gemini')
                            store.begin_claim_management_call(parent_id, round_id=rid, role='gemini', intent={'input': binding(input_path)})
                            ports.claim_management_audit(round_root, audit_input)
                        prior = record('audit', audit=binding(audit_path))
                        if prior['outcome'] == 'revision_required':
                            continue
                    break
            parent = store.get_cvp_group(parent_id)
            outcome = parent['result']
            latest = parent['latest_rounds'].values()
            if outcome.get('status') == 'paused' and not all(
                    store.get_cvp_group(rid).get('outcome') in {'completed', 'reported'} for rid in latest):
                return outcome
            return store.finish_claim_management_action(parent_id)
        except (ValueError, RuntimeError) as exc:
            text = str(exc)
            reason = (exc.status if isinstance(exc, Stop) and exc.status in {'model_failed','call_uncertain'} else
                      'no_progress' if 'semantic progress' in text else
                      'budget_exhausted' if 'budget' in text or 'complete next round' in text else
                      'source_drift' if 'source' in text and 'drift' in text else 'engineering_required')
            # Preserve known returned failures as distinct from unknown calls.
            # Authentication failure must never clear a started reservation.
            parent = store.get_cvp_group(parent_id)
            # Runtime/transport interruption is not a schema rejection. A valid
            # saved reply must still be resumable without another model call.
            for slot in parent['call_reservations'].values() if isinstance(exc, ValueError) and not isinstance(exc, Stop) else []:
                if (slot['state'] != 'started' or slot['role'] not in {'gpt', 'opus'}
                        or slot['round_id'] not in parent['latest_rounds'].values()
                        or slot.get('rejection')):
                    continue
                child = store.get_cvp_group(slot['round_id'])
                stage_root = Path(child.get('recovery_root', root / slot['round_id']))
                if (stage_root / (slot['role'] + '.raw.json')).is_file():
                    try:
                        store.record_claim_management_returned_rejection(slot['round_id'], role=slot['role'], detail=str(exc))
                    except (ValueError, RuntimeError) as recording_error:
                        text += '; returned-answer authentication failed: ' + str(recording_error)
                    else:
                        return store.get_cvp_group(parent_id)['result']
            return store.pause_claim_management_action(parent_id, reason=reason, detail=text)


def read_source_manifest(path):
    """Accept plain manifests or verify the existing immutable artifact envelope."""
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    value=read_artifact(path)
    return body(read(Path(path))) if 'artifact_sha256' in value else value


def initialize_subscription_runtime(ports):
    # Restoring an applied SQL group can bypass normal proposal startup. All
    # live bounded branches still use the same local canonical identity proof.
    from backend.pipeline.source_reconciliation_runtime import ProductionPorts
    if isinstance(ports,ProductionPorts):
        import os
        from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
        os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']


def resume_bound_action(store,run_id,action_id):
    """All special cases consume the same SQL checkpoint/once authority."""
    saved=store.get_cvp_group(run_id)
    active=saved.get('reviewed',{}).get('evidence',{}).get('action',{})
    if active.get('action_id')==action_id:
        if saved['checkpoint']=='reviewed':return resume_reviewed_action(store,run_id)
        if saved['checkpoint'] in {'applied','audited'}:
            return {'status':'already_applied','action_id':action_id}
    if store.cvp_action_committed(run_id,action_id):
        return {'status':'already_applied','action_id':action_id}
    return None


def action_sources(materials):
    manifest=content.Manifest.model_validate(materials['manifest'])
    result = [b.model_dump() for b in (manifest.before_source,*manifest.current_sources,manifest.authorization,
                                    manifest.accepted_membership,*manifest.prior_updates)] + [s['file'] for s in materials.get('context_sources',[])]
    if manifest.viewpoint_scope_repair:
        context=materials['viewpoint_scope_context']
        result += [manifest.viewpoint_scope_repair.model_dump(),context['policy'],*context['reports']]
    elif manifest.claim_issue:
        context=materials['claim_issue_context']
        result += [manifest.claim_issue.model_dump(),context['policy'],*(i['report'] for i in context['issues'])]
        if context.get('scope_amendment'):result.append(context['scope_amendment'])
    if manifest.anchor_qualification:result.append(manifest.anchor_qualification.model_dump())
    if manifest.evidence_binding_repair:result.append(manifest.evidence_binding_repair.model_dump())
    return result


def validate_source_package(package):
    """Exact actual proposal/Opus answer, scoped materials and permissions.

    This validates the material packet itself, not a prior/whole SQL snapshot.
    Current rows and consumer closure are checked inside apply_plan.
    """
    application=package['source_repair_application']
    if application.get('source_write_slice'):
        from backend.pipeline.claim_source_write_slice import validate_package
        return validate_package(package)
    material_binding=application['materials'];check_binding(material_binding)
    materials=body(read(Path(material_binding['path'])))
    if sha256_json(materials)!=application['materials_sha256']:
        raise ValueError('source action material identity drift')
    content.guard_files(materials)
    scope=materials.get('sql_scope')
    if not scope or scope['contract']!='wang_cvp_sql_transaction_v2' or scope['run_id']!=application['run_id']:
        raise ValueError('source action lacks exact SQL group/readset')
    # Shared pure content validator authenticates original model/prompt/once
    # reservation and the complete reviewed records. It does not rebuild freeze.
    templates={}
    proof=application.get('identity_template_read_pins')
    if proof:
        from backend.api.canonical_repository.postgres_store import record_content_sha
        check_binding(proof);receipt=body(read(Path(proof['path'])))
        if (not materials['manifest'].get('viewpoint_scope_repair')
                or receipt.get('schema_version')!='wang_cvp_scope_identity_template_read_pins_v1'
                or receipt.get('materials_sha256')!=application['materials_sha256']
                or receipt.get('terminal_review_sha256')!=application['review_sha256']):
            raise ValueError('scope template authority drift')
        rp=decoded_pins(receipt['read_pins'])
        for row in receipt['templates']:
            k=(row['collection'],row['object_id']);p=row['payload']
            if (content.key(*k) in templates or k[0]!='viewpoint_identity_candidates' or k not in rp
                    or p.get('identity_candidate_id')!=k[1] or record_content_sha(p)!=rp[k][1]
                    or p.get('review_status') in {'human_hold','human_review_required','withheld_unreviewed','rejected','superseded'}):
                raise ValueError('scope template payload/pin conflict')
            templates[content.key(*k)]=p
        if len(templates)!=len(rp):raise ValueError('scope template denominator drift')
    validate_reviewed_records(package,materials,frozen_sha256=application['materials_sha256'],provenance_templates=templates)
    from backend.pipeline.claim_evidence_completion import package_withdrawals
    return package_withdrawals(package,materials)


def prepare_source_action(store,run_id,manifest,*,root):
    manifest=content.Manifest.model_validate(manifest)
    action_id=source_action_id(manifest)
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    saved=store.get_cvp_group(run_id)
    if not saved:raise ValueError('source action requires admitted SQL group')
    previous=store.get_cvp_action(run_id,action_id)
    if previous:
        check_binding(binding(root/'scoped-materials.json'))
        if body(read(root/'scoped-materials.json'))!=previous['materials']:
            raise ValueError('saved SQL source action materials differ')
        if bool(manifest.source_paragraph_profile) != bool(previous['materials'].get('source_paragraph_execution')):
            raise ValueError('saved source action cannot acquire/drop a new paragraph review profile')
        return previous
    started=time.perf_counter()
    amendment=None
    if manifest.evidence_binding_repair:
        d=json.loads(content.bound_bytes(manifest.evidence_binding_repair))
        keys=[('evidence_steps',d['evidence_id'])]
    elif manifest.anchor_qualification:
        d=json.loads(content.bound_bytes(manifest.anchor_qualification))
        keys=[('claims',d['claim_id']),('evidence_steps',d['evidence_id'])]
    elif manifest.viewpoint_scope_repair:
        from backend.pipeline.cvp_scope_management import direction
        target=direction(manifest)
        keys=[('canonical_viewpoints',target['viewpoint_id']),('viewpoint_revisions',target['before_revision_id'])]
    elif manifest.claim_issue:
        context=json.loads(content.bound_bytes(manifest.claim_issue))
        # Source is unchanged. Starting reverse closure at its document would
        # incorrectly load every unrelated Claim in the same sermon.
        keys=[('claims',cid) for cid in context['claim_ids']]
        keys.extend((r['historical_source_pin']['collection'], r['historical_source_pin']['object_id'])
                    for r in context.get('retained_occurrence_context', []))
        if context.get('scope_amendment'):
            from backend.pipeline.claim_issue_rescope import validate_direction,amendment_records
            direction=json.loads(content.bound_bytes(context['scope_amendment']))
            # Indexed current pins/consumer checks below establish this
            # material generation; the once reservation validates the SQL
            # parent/action before any model call. No duplicate SQL coldstart.
            original,_=validate_direction(direction)
            amendment=amendment_records(store,direction,original)
    else:
        keys=[('source_documents',manifest.source_id)]
    keys.extend((p.collection,p.object_id) for p in manifest.permissions if not p.create)
    records=amendment if amendment is not None else store.read_affected_context(keys)
    states={content.key(col,oid):row['payload'] for (col,oid),row in records.items() if not row.get('retired')}
    # The existing content compiler gets only the complete affected current
    # graph. Its before_records are scoped model evidence, not a Registry dump.
    materials=content.freeze(manifest,states)
    if manifest.source_paragraph_profile:
        from backend.pipeline.source_paragraph_impact import attach
        attach(materials, store, run_id=run_id)
    from backend.pipeline.claim_owner_revision import attach_material
    attach_material(store,saved,materials,root)
    materials['sql_scope']={'contract':'wang_cvp_sql_transaction_v2','run_id':run_id,
                           'read_pins':pin_rows(pins(records)),'consumer_roots':[list(k) for k in keys]}
    if len(canonical_json(materials).encode())>64*1024**2 or time.perf_counter()-started>5:
        raise ValueError('bounded source materials exceed mechanical guard; no whole-store fallback')
    if manifest.claim_issue:
        from backend.pipeline.source_permission_recovery import program_contract
        materials['program_provenance_contract']=program_contract()
    write(root/'scoped-materials.json',materials)
    return store.prepare_cvp_action(run_id,action_id=action_id,
        kind='cvp_scope_revision' if manifest.viewpoint_scope_repair else 'source_claim_revision',
        authorization=manifest.authorization.model_dump(),materials=materials)


def source_action_id(manifest):
    if manifest.evidence_binding_repair:
        value=json.loads(content.bound_bytes(manifest.evidence_binding_repair))
        return 'CVPA-'+sha256_json({'kind':'eligible_evidence_binding','evidence_id':value['evidence_id'],
                                    'before_pin':value['before_pin'],'source_id':manifest.source_id})
    if manifest.viewpoint_scope_repair:
        from backend.pipeline.cvp_scope_management import direction
        value = direction(manifest)
        return 'CVPA-'+sha256_json({'kind':'viewpoint_scope_revision','viewpoint_id':value['viewpoint_id'],
                                  'before_revision_id':value['before_revision_id']})
    if manifest.claim_issue:
        from backend.pipeline.claim_issue_management import Issue
        context=__import__('json').loads(content.bound_bytes(manifest.claim_issue))
        if context.get('scope_amendment'):
            direction=json.loads(content.bound_bytes(context['scope_amendment']))
            return 'CVPA-'+sha256_json({'kind':'claim_issue_scope_amendment_once','parent':direction['old_action_id']})
        return 'CVPA-'+sha256_json({'claim_issues':sorted(Issue.model_validate(i).issue_id for i in context['issues'])})
    identity = manifest.model_dump(mode='json')
    identity.pop('source_paragraph_profile', None)  # A profile cannot create another action/budget.
    return 'CVPA-'+sha256_json(identity)


class ScopedSourceModelStages(ModelStages):
    """Only the existing four model stages; no legacy run/prepare/SQL ports."""
    def __init__(self,root,ports,store,read_pins):
        self.root=Path(root);self.ports=ports;self.store=store;self.read_pins=read_pins

    def guard(self,materials,*,committed=False):
        content.guard_files(materials)
        with self.store.connect() as conn,conn.cursor() as cursor:
            self.store._assert_transaction_read_pins(cursor,self.read_pins)

    def call(self,stage,packet):
        if stage in {'proposal','correction','permission_input_recovery_proposal'} and not (self.root/(stage+'.raw.json')).exists():
            from backend.pipeline.source_reconciliation_runtime import ProductionPorts
            if isinstance(self.ports,ProductionPorts):
                from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
                import os
                os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
        return super().call(stage,packet)

    def prepare(self,*args,**kwargs):raise RuntimeError('legacy source prepare is retired')
    def run(self,*args,**kwargs):raise RuntimeError('legacy source execution is retired')


def execute_source_action(store,run_id,manifest,*,root,ports,claim_first=False):
    manifest=content.Manifest.model_validate(manifest).model_dump(mode='json')
    root=root/source_action_id(content.Manifest.model_validate(manifest))
    action=prepare_source_action(store,run_id,manifest,root=root)
    materials=action['materials'];read_pins=decoded_pins(materials['sql_scope']['read_pins'])
    saved=store.get_cvp_group(run_id)
    resumed=resume_bound_action(store,run_id,action['action_id'])
    if resumed:return resumed
    issue_context=materials.get('claim_issue_context')
    if issue_context:
        from backend.pipeline.claim_issue_management import Issue
        if issue_context.get('scope_amendment'):
            store.bind_claim_issue_scope_amendment([Issue.model_validate(i).issue_id for i in issue_context['issues']],
                run_id=run_id,action_id=action['action_id'],authorization=issue_context['scope_amendment'])
        else:
            store.bind_claim_issue_action([Issue.model_validate(i).issue_id for i in issue_context['issues']],
                                         run_id=run_id,action_id=action['action_id'])
    # Resume a committed source action directly from SQL. Neither content
    # stages nor apply are replayed. Audit/coverage continue in the group flow.
    initialize_subscription_runtime(ports)
    stages=ScopedSourceModelStages(root,ports,store,read_pins)
    stages.claim_first=claim_first
    proposal,raw=stages.reviewed(materials)
    if manifest.get('viewpoint_scope_repair') and not proposal.records:
        raise ValueError('scope assessment retained existing scope; unresolved original finding needs direction, no empty commit/retry loop')
    templates={}
    template_proof=None
    if manifest.get('viewpoint_scope_repair'):
        from backend.pipeline.viewpoint_resolution_runtime import write_immutable
        before=content.index(materials['before_records'])
        records={tuple(k.split('/',1)):{'payload':p,'revision':read_pins[tuple(k.split('/',1))][0],
                 'content_sha256':read_pins[tuple(k.split('/',1))][1],
                 'retired':read_pins[tuple(k.split('/',1))][2]} for k,p in before.items()}
        selected=[]
        for k in content.index(proposal.records):
            if not k.startswith('viewpoint_claim_links/'):continue
            old=before[k]
            decision=before[content.key('viewpoint_identity_decisions',old['decision_id'])]
            if content.key('viewpoint_identity_candidates',decision['identity_candidate_id']) not in before:
                selected.append({'before_link':old})
        extra=supports_identity_dependencies(store,records,selected)
        templates={content.key(*k):r['payload'] for k,r in extra.items()}
        for k,pin in pins(extra).items():
            if k in read_pins and tuple(read_pins[k])!=tuple(pin):
                raise ValueError('scope forward template readpin drift')
            read_pins[k]=pin
        receipt={
            'schema_version':'wang_cvp_scope_identity_template_read_pins_v1',
            'action_id':action['action_id'],'materials_sha256':action['materials_sha256'],
            'terminal_review_sha256':raw['artifact_sha256'],
            'read_pins':pin_rows(pins(extra)),
            'templates':[{'collection':k[0],'object_id':k[1],'payload':r['payload']} for k,r in sorted(extra.items())],
            'semantic_review_input_changed':False}
        write_immutable(root/'identity-template-read-pins.json',{**receipt,'artifact_sha256':sha256_json(receipt)})
        template_proof=binding(root/'identity-template-read-pins.json')
    package=content.compile_package(materials,proposal,raw,frozen_sha256=action['materials_sha256'],claim_first=claim_first,
                                    provenance_templates=templates)
    from backend.pipeline.claim_owner_revision import validate_joint
    joint_owner_claim=validate_joint(materials,proposal,package)
    if (root/'membership-resolution.json').exists():
        from backend.pipeline.cvp_membership_management import append_joint_package
        from backend.pipeline.source_reconciliation_runner import effective_proposal, validate_raw
        correction_packet=body(read(root/'correction.packet.json'))
        correction=validate_raw(root,'correction',correction_packet)
        package=append_joint_package(package,root,materials,effective_proposal(materials,correction['response']),correction)
    package['source_repair_application']={'schema_version':SOURCE_SCHEMA,'run_root':str(Path(root).resolve()),
        'run_id':run_id,'materials':binding(root/'scoped-materials.json'),
        'materials_sha256':action['materials_sha256'],'terminal_stage':raw['stage'],
        'review_sha256':raw['artifact_sha256'],'proposal_sha256':sha256_json(proposal.model_dump())}
    from backend.pipeline.claim_evidence_completion import direction_for
    completion=direction_for(materials.get('claim_issue_context',{}))
    if completion:
        package['source_repair_application']['knowledge_relation_withdrawals']=completion['withdraw_relation_ids']
    if template_proof:package['source_repair_application']['identity_template_read_pins']=template_proof
    validate_source_package(package)
    from backend.pipeline.cvp_sql_commit import verified_group_backup
    backup=verified_group_backup(root,package,ports) if issue_context or manifest.get('viewpoint_scope_repair') or manifest.get('anchor_qualification') or manifest.get('evidence_binding_repair') else ports.backup(root)
    original=store.get_cvp_group(run_id).get('reviewed',{}).get('evidence',{})
    approval={**(original.get('approval',{}) if joint_owner_claim else {}),
              'terminal_review':binding(root/(raw['stage']+'.raw.json'))}
    if template_proof:approval['scope_identity_template_read_pins']=template_proof
    if joint_owner_claim:
        approval['owner_claim_completion']=materials['owner_claim_completion']
        if materials.get('prior_cvp_management'):
            approval['prior_cvp_management']=materials['prior_cvp_management']
    if (root/'membership-resolution.json').exists():
        approval['membership_management']=binding(root/'membership-resolution.json')
    if raw['stage']=='normalization_recovery_review':
        from backend.pipeline.source_normalization_recovery import NAME
        approval['normalization_engineering_recovery']=binding(root/NAME)
    for stage in ('initial_review','final_review','final_review_input_repair','normalization_recovery_review'):
        proof=root/(stage+'.raw.program-binding.json')
        if proof.exists():approval[stage+'_program_binding']=binding(proof)
    views=set(original.get('viewpoint_ids',[]))
    if issue_context or manifest.get('viewpoint_scope_repair') or manifest.get('anchor_qualification') or manifest.get('evidence_binding_repair') or manifest.get('source_paragraph_profile'):
        views.update(p['viewpoint_id'] for p in content.index(materials['before_records']).values() if 'viewpoint_id' in p)
    pending=content.Manifest.model_validate(materials['manifest']).deferred_repair.claim_ids if materials['manifest'].get('deferred_repair') else []
    if manifest.get('anchor_qualification'):
        d=json.loads(content.bound_bytes(manifest['anchor_qualification']))
        pending=sorted(set(saved['claim_ids'])|set(original.get('pending_cvp_claim_ids',[]))|{d['claim_id']})
    if manifest.get('evidence_binding_repair'):
        d=json.loads(content.bound_bytes(manifest['evidence_binding_repair']))
        pending=sorted(set(saved['claim_ids'])|set(original.get('pending_cvp_claim_ids',[]))|set(d['claim_ids']))
    if issue_context:
        # Uncommitted original model materials include the changed Claim. They
        # are retained as history, never re-labelled approved for the new cut.
        pending=list(saved['claim_ids']) if not original else sorted(set(original.get('pending_cvp_claim_ids',[]))|set(issue_context['claim_ids']))
        from backend.pipeline.claim_evidence_intake import enabled as intake_enabled
        if intake_enabled(materials):
            pending=sorted(set(original.get('pending_cvp_claim_ids',[]))|set(issue_context['claim_ids']))
    if joint_owner_claim:
        # Actual complete joint proposal/Opus reviewed every existing active
        # connection against the new Claim. This is not create-only coverage.
        # Other already-pending Claims are never cleared by this repair.
        pending=sorted(set(original.get('pending_cvp_claim_ids',[]))-set(issue_context['claim_ids']))
    pending_keys = list(original.get('pending_cvp_management_record_keys', []))
    if manifest.get('source_paragraph_profile'):
        from backend.pipeline.source_paragraph_impact import pending_candidates
        # The complete original roster awaits current-source Claim/CVP audits.
        # Internal extraction ambiguity is preserved separately, never approved.
        if set(original.get('pending_cvp_claim_ids', [])) - set(saved['claim_ids']):
            raise ValueError('paragraph source cannot erase a separate dependency coverage obligation')
        pending = list(saved['claim_ids'])
        pending_keys = sorted(set(pending_keys) | set(pending_candidates(materials, proposal)))
    return commit_package(store,run_id,package=package,
        approval=approval,
        authorization=action['authorization'],backup=backup,sources=action_sources(materials),
        read_pins=read_pins,reason='approved CVP management scope enrichment' if manifest.get('viewpoint_scope_repair') else 'approved bounded source/Claim revision',actor='owner-authorized-pipeline',
        source_kind=SOURCE_SCHEMA,viewpoint_ids=sorted(views),
        disposition=original.get('disposition'),action=action,
        pending_cvp_claim_ids=pending,
        pending_cvp_management_record_keys=pending_keys)


def merge_dispositions(old,new):
    """Keep every original Claim; do not pretend two approvals are one raw."""
    if not old:return new
    claims={r['claim_id']:r for r in old['claims']}
    claims.update({r['claim_id']:r for r in new['claims']})
    return {'schema_version':'cvp_sql_component_disposition.v2','claims':[claims[k] for k in sorted(claims)],
            'source_disposition_sha256s':[sha256_json(old),sha256_json(new)]}


def validated_sufficient_deferred(root):
    """A source diagnosis is not CVP approval; it only selects once-only work."""
    from backend.pipeline.viewpoint_deferred_source_check import Findings,PROMPT,validate_findings
    from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,read_saved_model_response,subscription_client
    packet=read_artifact(root/'packet.json');handoff=read_artifact(root/'handoff.json')
    if handoff['packet_sha256']!=packet['artifact_sha256']:raise ValueError('source investigation identity drift')
    findings=[]
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        adapter=StructuredJsonReviewerAdapter(client=subscription_client(provider,model,'high'),prompt=PROMPT,
            response_model=Findings,schema_name='wang_deferred_source_findings_v1')
        findings.append(validate_findings(read_saved_model_response(adapter,packet,root/f'raw-{label}.json'),packet))
    ids=[]
    for item in packet['items']:
        left,right=(f[item['item_id']] for f in findings)
        actions=[a for a in handoff['actions'] if a['item_id']==item['item_id']]
        if (len(actions)!=1 or actions[0]['gpt']!=left or actions[0]['opus']!=right
                or left['diagnosis']!='existing_evidence_sufficient' or right['diagnosis']!='existing_evidence_sufficient'
                or actions[0]['next_action']!='reviewed_cvp_repair_required'):
            raise ValueError('true source/Claim ambiguity or missing bound evidence requires bounded owner direction')
        ids.append(item['claim_id'])
    return sorted(set(ids)),binding(root/'handoff.json')


def execute_coverage_action(store,run_id,definition,group,*,root,ports,investigation_root=None):
    from backend.pipeline.cvp_sql_commit import NORMAL_COLLECTIONS
    from backend.pipeline.cvp_sql_flow import prepare_group, reviewed_cvp_package
    saved=store.get_cvp_group(run_id);old=saved['reviewed']['evidence']
    from backend.pipeline.claim_evidence_binding import coverage_context as evidence_coverage_context
    sequential=evidence_coverage_context(store,saved,group) if investigation_root is None else None
    if investigation_root is not None:
        ids,investigation=validated_sufficient_deferred(investigation_root)
        check_binding(definition['cvp_policy'])
        from backend.pipeline.viewpoint_resolution_runtime import read_artifact
        if read_artifact(definition['cvp_policy']['path'])['coverage_contract_version']!='wang_cvp_required_coverage_v1':
            raise ValueError('normal flow lacks standing required-coverage authority')
    else:
        ids=old['pending_cvp_claim_ids'];investigation=None
        if old.get('action',{}).get('kind')!='source_claim_revision':
            raise ValueError('required coverage must follow the approved bounded Claim revision')
    if sequential:ids=sequential['coverage_claim_ids']
    allowed=set(sequential['coverage_claim_ids']) if sequential else set(saved['claim_ids'])
    if not ids or (set(ids)!=allowed if sequential else not set(ids)<=allowed):
        raise ValueError('coverage changes original membership or audited dependency scope')
    action_id='CVPA-'+sha256_json({'prior':saved['reviewed_sha256'],'kind':'required_cvp_coverage','claims':ids})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    previous=store.get_cvp_action(run_id,action_id)
    materials=previous['materials'] if previous else prepare_group(store,definition,{**group,'claim_ids':ids})
    if not previous and sequential:
        materials['claim_management_resolution']=sequential
    elif not previous and saved.get('claim_layer_audit'):
        from backend.pipeline.claim_first_management import coverage_context
        materials['claim_management_resolution']=coverage_context(store,saved,group)
    elif not previous and old.get('action',{}).get('materials',{}).get('claim_issue_context'):
        # A retained Claim is not a newly approved normal CVP proposal. Its
        # joint management review is explicit input to this new coverage stage;
        # never silently repeat/relabel the former deferred request.
        materials['claim_management_resolution']={
            'prior_reviewed_sha256':saved['reviewed_sha256'],
            'management_action_id':old['action']['action_id'],
            'approved_package_sha256':old['package']['sha256'],
            'terminal_review':old['approval']['terminal_review'],
            'issues':old['action']['materials']['claim_issue_context']['issues']}
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='required_cvp_coverage',
                                    authorization={**old['authorization'],**({'source_investigation':investigation} if investigation else {})},materials=materials)
    if saved.get('claim_layer_audit'):
        from backend.pipeline.claim_first_management import REPROPOSAL_VERSION
        if materials.get('claim_management_resolution',{}).get('schema_version')!=REPROPOSAL_VERSION:
            raise ValueError('saved coverage predates Claim-first handoff; no cache reinterpretation or new budget')
    if saved['checkpoint']=='reviewed':return resume_reviewed_action(store,run_id)
    initialize_subscription_runtime(ports)
    package,approval,disposition=reviewed_cvp_package(root/'model-stages',materials,store,require_resolved=True)
    plan=store.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    if any(op.operation!='create' or op.collection not in NORMAL_COLLECTIONS for op in plan.operations):
        raise ValueError('required coverage may create CVPs/links only; no existing content revision')
    if any(link['claim_id'] not in ids for link in package.get('viewpoint_claim_links',[])):
        raise ValueError('required coverage added a foreign Claim')
    views=sorted(set(old['viewpoint_ids'])|{l['viewpoint_id'] for l in package.get('viewpoint_claim_links',[])})
    from backend.pipeline.cvp_sql_commit import verified_group_backup
    return commit_package(store,run_id,package=package,approval=approval,authorization=old['authorization'],
        backup=verified_group_backup(root,package,ports),
        sources=materials['sources'],read_pins=decoded_pins(materials['read_pins']),
        reason='required CVP coverage after bounded Claim revision',actor=definition['authorized_by'],
        viewpoint_ids=views,disposition=merge_dispositions(old.get('disposition'),disposition),action=action,
        pending_cvp_management_record_keys=materials.get('claim_management_resolution',{}).get('pending_existing_cvp_record_keys',[]))


def restore_exact_supports_packet_order(saved, current):
    """Preserve the actual request order after exact indexed graph rebuild.

    Only graph context arrays are unordered SQL sets. Source paragraphs,
    evidence, spans, targets, findings and all content retain exact ordering.
    """
    from copy import deepcopy
    projected=deepcopy(current)
    if len(saved.get('targets',[]))!=len(projected.get('targets',[])):
        raise ValueError('saved supports target population drift')
    for old,new in zip(saved['targets'],projected['targets']):
        for field in ('adjacent_links','adjacent_claims','dependent_records'):
            if (field not in old or field not in new
                    or sorted(map(canonical_json,old[field]))!=sorted(map(canonical_json,new[field]))):
                raise ValueError('saved supports context field or record drift')
            new[field]=old[field]
    if projected!=saved:raise ValueError('saved supports content/input drift')
    return saved


def supports_identity_dependencies(store, records, selected):
    """Read exact forward candidate references omitted by consumer closure.

    Candidates are provenance templates, not semantic review input. Keep the
    saved packet and calls unchanged; pin these rows in the actual transaction.
    """
    from backend.api.canonical_repository.postgres_store import record_content_sha
    keys=set()
    for item in selected:
        old=item['before_link']
        decision=records.get(('viewpoint_identity_decisions',old['decision_id']))
        if not decision or decision.get('retired'):
            raise ValueError('bounded supports decision missing or retired')
        value=decision['payload']
        if value.get('review_status') not in {'approved','system_approved','human_approved','ai_consensus_reviewed'}:
            raise ValueError('bounded supports decision not approved')
        cid=value.get('identity_candidate_id')
        if not cid:raise ValueError('bounded supports candidate reference missing')
        keys.add(('viewpoint_identity_candidates',cid))
    extra={}
    with store.connect() as conn:
        for key in sorted(keys):
            row=conn.execute('SELECT revision,content_sha256,payload,retired_at IS NOT NULL FROM wang_knowledge.objects WHERE collection=%s AND object_id=%s',key).fetchone()
            if not row:raise ValueError('bounded supports candidate missing')
            history=conn.execute('''SELECT v.payload,v.content_sha256,o.after_sha256,c.status,o.operation
                FROM wang_knowledge.object_versions v JOIN wang_knowledge.change_sets c
                ON c.change_set_id=v.change_set_id JOIN wang_knowledge.change_operations o
                ON o.change_set_id=v.change_set_id AND o.collection=v.collection AND o.object_id=v.object_id
                AND o.after_revision=v.revision WHERE v.collection=%s AND v.object_id=%s AND v.revision=%s''',
                (*key,row[0])).fetchall()
            if (len(history)!=1 or history[0][1:4]!=(row[1],row[1],'applied')
                    or history[0][0].get('revision')!=row[0]
                    or {k:v for k,v in history[0][0].items() if k!='revision'}!={k:v for k,v in row[2].items() if k!='revision'}
                    or (history[0][4]=='retire')!=row[3]):
                raise ValueError('bounded supports candidate lacks exact committed history')
            # Retired candidate is historical provenance only. Copy to a new ID;
            # never revive it, reinterpret it as a current member or edit it.
            extra[key]={'revision':row[0],'content_sha256':row[1],'payload':row[2],'retired':row[3]}
    for key,row in extra.items():
        value=row['payload']
        if (value.get('identity_candidate_id')!=key[1]
                or record_content_sha(value)!=row['content_sha256']
                or value.get('review_status') in {'human_hold','human_review_required','withheld_unreviewed','rejected','superseded'}):
            raise ValueError('bounded supports candidate identity, version or hold conflict')
        if key in records and records[key]!=row:
            # _existing omits the false retired field; compare actual pins/payload.
            if pins({key:records[key]})!=pins({key:row}) or records[key]['payload']!=value:
                raise ValueError('bounded supports candidate changed within readset')
    for item in selected:
        old=item['before_link'];decision=records['viewpoint_identity_decisions',old['decision_id']]['payload']
        candidate=extra['viewpoint_identity_candidates',decision['identity_candidate_id']]['payload']
        if (old['claim_id'] not in candidate.get('candidate_claim_ids',[])
                or decision.get('resolved_viewpoint_id')!=old['viewpoint_id']
                or not any(d.get('claim_id')==old['claim_id'] and d.get('link_type')==old['link_type']
                           for d in decision.get('claim_link_decisions',[]))):
            raise ValueError('bounded supports candidate has foreign Claim or viewpoint')
    return extra


def execute_supports_action(store,run_id,adjudication_root,*,root,ports):
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_audit_link_repair import select_repairs,link_review_adapter,package_for,validate_supports_review,POLICY,PROMPT
    from backend.pipeline.viewpoint_authorized_source_repair_357 import Review,review_call
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,subscription_client,write_immutable
    packet,_=validated_history(adjudication_root)
    report=read_artifact(adjudication_root/'gpt-final-handoff.json')
    selected=select_repairs(packet,report)  # rejects l3/unresolved/non-equivalent/new scope
    from backend.pipeline.cvp_retained_impact_resolution import protected_link_ids
    protected=protected_link_ids(store.get_cvp_group(run_id),store)
    if any(i['before_link']['viewpoint_claim_link_id'] in protected for i in selected):
        raise ValueError('human NO-CHANGE disposition forbids another automatic demotion review')
    policy=read_artifact(POLICY)
    if (policy['allowed_after_type']!='supports' or not policy['require_dual_model_postimage_review']
            or policy['maximum_corrections_per_adjudication']!=1):raise ValueError('standing narrow policy drift')
    ids=sorted({i['claim']['claim_id'] for i in selected})
    action_id='CVPA-'+sha256_json({'adjudication':packet['artifact_sha256'],'handoff':report['artifact_sha256'],
                                 'policy':binding(POLICY),'links':[i['before_link']['viewpoint_claim_link_id'] for i in selected]})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    resumed=resume_bound_action(store,run_id,action_id)
    if resumed:return resumed
    records=store.read_affected_context([('viewpoint_claim_links',i['before_link']['viewpoint_claim_link_id']) for i in selected])
    states={k:r['payload'] for k,r in records.items() if not r['retired']}
    for item in selected:
        old=item['before_link'];cv=old['viewpoint_id']
        if states['viewpoint_claim_links',old['viewpoint_claim_link_id']]!=old or states['claims',old['claim_id']]!=item['claim']:
            raise ValueError('actual upheld link/Claim changed before bounded review')
        item['viewpoint_revision']=states['viewpoint_revisions',old['validated_against_viewpoint_revision_id']]
        item['adjacent_links']=[p for (c,_),p in states.items() if c=='viewpoint_claim_links'
                                and p.get('effective_state')=='active' and p['viewpoint_id']==cv]
        item['adjacent_claims']=[states['claims',cid] for cid in sorted({p['claim_id'] for p in item['adjacent_links']})]
        if any(p['claim_id']==old['claim_id'] and p['link_type']=='supports' for p in item['adjacent_links']):
            raise ValueError('existing supports edge requires separately approved merge')
        item['dependent_records']=[p for (c,_),p in states.items() if c in
            {'viewpoint_structures','argument_routes','argument_route_attestations','viewpoint_relations'}
            and (cv in canonical_json(p) or old['viewpoint_claim_link_id'] in canonical_json(p))]
    repair={'schema_version':'wang_audit_link_repair_packet_v1','targets':selected,
            'adjudication_packet_sha256':packet['artifact_sha256'],'adjudication_handoff':report,
            'policy':policy,'claim_source_viewpoint_text_writes':0}
    if (root/'packet.json').exists():
        repair=restore_exact_supports_packet_order(read_artifact(root/'packet.json'),repair)
        selected=repair['targets']
    write_immutable(root/'packet.json',repair)
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='audit_supports_correction',
        authorization=binding(POLICY),materials={'packet':repair,'read_pins':pin_rows(pins(records))})
    reviews={}
    for label,provider,model in [('gpt','codex','gpt-6.1-sol'),('opus','claude','claude-opus-5-5')]:
        if provider=='codex' and not (root/f'raw-{label}.json').exists():
            import os
            from backend.pipeline.viewpoint_s201129_source_continuation import canonical_runtime
            os.environ['CODEX_EXECUTABLE']=canonical_runtime()['identity']['cli']['path']
        adapter=link_review_adapter(subscription_client(provider,model,'high'),PROMPT,contextual_semantics=True,
                                    multiple_targets_per_claim=len(ids)!=len(selected))
        adapter.max_request_bytes=500000
        response,_,_=review_call(adapter,repair,root,label)
        result=Review.model_validate(response)
        from backend.pipeline.claim_issue_management import declined_supports_findings,IssuesDetected
        findings=declined_supports_findings(selected,result)
        if findings:
            # This preserves the link refusal and hands its actual full reason
            # to the already-authorized source classifier. It does not approve
            # a Claim change or repeat the failed supports proposal.
            raise IssuesDetected(findings,origin='opus_initial' if label=='opus' else 'gpt_proposal',
                                 report=binding(root/f'raw-{label}.json'))
        validate_supports_review(result,selected)
        reviews[label]=result.model_dump(mode='json')
    extra=supports_identity_dependencies(store,records,selected)
    write_immutable(root/'identity-template-read-pins.json',{
        'schema_version':'wang_cvp_supports_identity_template_read_pins_v1',
        'action_id':action_id,'packet_sha256':sha256_json(repair),
        'read_pins':pin_rows(pins(extra)),'semantic_review_input_changed':False})
    records.update(extra)
    states.update({k:r['payload'] for k,r in extra.items()})
    package=package_for(states,repair,reviews,root)
    plan=store.plan_package(package,source_kind='cvp_batch_resolution',transactional=True)
    expected={i['before_link']['viewpoint_claim_link_id'] for i in selected}
    if any(op.collection not in {'viewpoint_claim_links','viewpoint_identity_candidates','viewpoint_identity_decisions'}
           or (op.collection=='viewpoint_claim_links' and (op.operation!='update' or op.object_id not in expected))
           or (op.collection!='viewpoint_claim_links' and op.operation!='create') for op in plan.operations):
        raise ValueError('bounded supports package exceeds exact authorized delta')
    saved=store.get_cvp_group(run_id)['reviewed']['evidence']
    sources={s['path']:{'path':s['path'],'file_sha256':s['file_sha256']} for i in selected for s in i['sources']}
    return commit_package(store,run_id,package=package,
        approval={label:binding(root/f'raw-{label}.json') for label in reviews},authorization=binding(POLICY),
        backup=ports.backup(root),sources=list(sources.values()),read_pins=pins(records),
        reason='standing once own-source upheld equivalent-to-supports correction',actor='owner-authorized-pipeline',
        viewpoint_ids=saved['viewpoint_ids'],disposition=saved.get('disposition'),action=action)


def validate_owner_link_direction(direction,packet,run_id):
    wanted={'schema_version':'cvp_sql_owner_link_direction.v2','run_id':run_id,
            'link_id':packet['before_link']['viewpoint_claim_link_id'],
            'adjudication_packet_sha256':packet['adjudication_packet_sha256'],
            'before_link_sha256':sha256_json(packet['before_link']),
            'after_link_sha256':sha256_json(packet['after_link'])}
    if any(direction.get(k)!=v for k,v in wanted.items()) or not direction.get('authorization_quote'):
        raise ValueError('owner direction does not bind exact SQL action/postimage')
    if direction.get('human_content_approval') is not False or direction.get('automatic_future_repairs_authorized') is not False:
        raise ValueError('execution direction is not future content approval')


def validate_owner_link_package(package):
    """Exact bound direction and saved single Opus answer; no parent rebuild."""
    from backend.pipeline.owner_approved_link_correction import package_for,review_raw
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    app=package['source_repair_application']
    if app['schema_version']!=LINK_SCHEMA:raise ValueError('SQL link contract differs')
    check_binding(app['packet']);check_binding(app['authorization'])
    packet=read_artifact(app['packet']['path'])
    raw=review_raw(Path(app['run_root']),packet)
    expected=package_for(packet,raw,Path(app['run_root']))
    expected['viewpoint_identity_decisions'][0]['policy_version']=LINK_SCHEMA
    expected['source_repair_application']=app
    direction=read_artifact(app['authorization']['path'])
    validate_owner_link_direction(direction,packet,app['run_id'])
    if package!=expected:raise ValueError('SQL link package differs from actual reviewed three-record delta')


def execute_owner_link_action(store,run_id,adjudication_root,*,link_id,authorization,root,ports):
    from backend.pipeline.owner_approved_link_correction import target_postimage,validate_target,package_for,review_raw,VERSION
    from backend.pipeline.viewpoint_adjudication_history import validated_history
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,write_immutable
    check_binding(authorization)
    direction=read_artifact(authorization['path'])
    action_id='CVPA-'+sha256_json({'kind':'owner_link_correction','authorization':authorization})
    root=root/action_id
    store.bind_cvp_execution_root(run_id,identity=action_id,root=root)
    resumed=resume_bound_action(store,run_id,action_id)
    if resumed:return resumed
    saved=store.get_cvp_group(run_id);previous=store.get_cvp_action(run_id,action_id)
    if previous:
        material=previous['materials'];packet=material['packet'];read_pins=decoded_pins(material['read_pins'])
    else:
        adjudication,findings=validated_history(Path(adjudication_root))
        selected=[i for i in adjudication['items'] if any(l.get('viewpoint_claim_link_id')==link_id for l in i.get('connections',[]))]
        if len(selected)!=1 or findings[selected[0]['item_id']]['verdict']!='audit_objection_upheld':
            raise ValueError('owner link direction requires a unique actual upheld target')
        item=selected[0]
        connections=[l for l in item['connections'] if l['viewpoint_claim_link_id']==link_id]
        if len(connections)!=1:raise ValueError('ambiguous owner link target')
        original_link=connections[0]
        records=store.read_cvp_context([item['claim']['claim_id']],viewpoint_ids=[original_link['viewpoint_id']])
        states={content.key(*k):r['payload'] for k,r in records.items() if not r['retired']}
        old=states[content.key('viewpoint_claim_links',link_id)]
        claim=states[content.key('claims',old['claim_id'])]
        if old!=original_link or claim!=item['claim']:
            raise ValueError('current link/Claim differs from owner-approved target')
        after=target_postimage(old,claim,states,'equivalent_full')
        packet={'before_link':old,'after_link':after,'item':item,
                'context_records':[{'collection':content.split_key(k)[0],'object_id':content.split_key(k)[1],'payload':p} for k,p in sorted(states.items())],
                'sources':item['sources'],'adjudication_packet_sha256':adjudication['artifact_sha256'],
                'authorization':{**direction,'schema_version':VERSION}}
        validate_target(packet)
        material={'packet':packet,'read_pins':pin_rows(pins(records))};read_pins=pins(records)
    packet=json.loads(canonical_json(packet))
    validate_owner_link_direction(direction,packet,run_id)
    action=store.prepare_cvp_action(run_id,action_id=action_id,kind='owner_link_correction',authorization=authorization,materials=material)
    write_immutable(root/'packet.json',packet)
    stages=ScopedSourceModelStages(root,ports,store,read_pins)
    for source in packet['sources']:check_binding({'path':source['path'],'file_sha256':source['file_sha256']})
    with store.connect() as conn,conn.cursor() as cursor:store._assert_transaction_read_pins(cursor,read_pins)
    stages.call('initial_review',packet)
    raw=review_raw(root,packet)
    package=package_for(packet,raw,root)
    package['viewpoint_identity_decisions'][0]['policy_version']=LINK_SCHEMA
    package['source_repair_application']={'schema_version':LINK_SCHEMA,'run_id':run_id,'run_root':str(root.resolve()),
        'packet':binding(root/'packet.json'),'authorization':authorization}
    validate_owner_link_package(package)
    original=saved.get('reviewed',{}).get('evidence') or saved.get('import_certificate',{})
    return commit_package(store,run_id,package=package,approval={'opus':binding(root/'initial_review.raw.json')},
        authorization=authorization,backup=ports.backup(root),sources=[{'path':s['path'],'file_sha256':s['file_sha256']} for s in packet['sources']],
        read_pins=read_pins,reason='owner-authorized exact full-Claim link classification',actor='owner-authorized-pipeline',
        source_kind=LINK_SCHEMA,viewpoint_ids=original['viewpoint_ids'],disposition=original.get('disposition'),action=action)
