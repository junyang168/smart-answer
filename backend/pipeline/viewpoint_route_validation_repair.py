"""Repair only failed deltas, preserving stage inputs and complete audit data."""
from copy import deepcopy
from pydantic import ValidationError
from backend.api.canonical_repository.viewpoint_batch_resolution import BatchResolutionError
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import call_model, read_saved_model_response, write_immutable, read_artifact


EDIT_REPAIR_INSTRUCTION = 'Fix the failed edits against the SAME baseline; return the complete corrected edits only. Preserve all still-valid intended edits and current finding dispositions. Do not rewrite the full proposal or change scope.'
REVIEW_REPAIR_INSTRUCTION = ('Return a complete corrected review of the SAME proposal and all review_targets. '
    'Resolve the program validation findings, including missing or duplicate target decisions. '
    'Preserve valid substantive findings; do not edit the proposal, infer approval or omit targets.')

HISTORY = 'route_program_repair_history'


def with_validation_history(packet, baseline, failures, findings):
    previous = {finding for attempt in failures for finding in attempt['findings']}
    return packet | {HISTORY: {
        'schema_version': 'wang_route_program_repair_history_v1',
        'baseline_sha256': sha256_json(baseline),
        'prior_attempts': deepcopy(failures),
        'repeated_findings': sorted(set(findings) & previous),
        'instruction': ('只修正当前阶段的程序错误；先对照同一基线及此前失败，不猜测 ID。'
            '保留仍有效的修改意图和证据，不用删除有效内容来绕过校验。'
            '这些记录不是 Opus 内容异议或批准，不改变审核标准，也不增加调用额度。'),
    }}


def validate_changes(raw, *, validate, adapter, packet, cache, state, max_repairs,
                     repair_instruction=EDIT_REPAIR_INSTRUCTION, contextual=False):
    seen = set()
    failures = []
    active_packet=packet
    while True:
        try:
            result = validate(raw,active_packet) if contextual else validate(raw)
            write_immutable(cache.with_name(cache.stem+'-validated.json'), {
                'response_sha256': sha256_json(raw), 'failures': failures})
            return result, raw
        except (ValidationError, BatchResolutionError, ValueError, KeyError, TypeError) as exc:
            findings = ([f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors(include_input=False)]
                if isinstance(exc, ValidationError) else exc.findings if isinstance(exc, BatchResolutionError) else [str(exc)])
            fingerprint = sha256_json({'response': raw, 'findings': findings})
            if fingerprint in seen or state['used'] >= max_repairs:
                raise ValueError(f'Route program validation remains unresolved: {findings}') from exc
            seen.add(fingerprint)
        repair = packet | {'prior_invalid_response': raw, 'validation_findings': findings,
            'validation_instruction': repair_instruction}
        response = cache.with_name(cache.stem+f'-validation-{len(failures):03d}.json')
        request = response.with_name(response.stem+'-packet.json')
        dispatched_findings = read_artifact(request)['validation_findings'] if request.exists() else findings
        if not request.exists() or HISTORY in read_artifact(request):
            repair=with_validation_history(repair, packet, failures, dispatched_findings)
        from backend.pipeline.viewpoint_route_decision_rubric import with_decision_rubric, MARKER as RUBRIC
        if not request.exists() or RUBRIC in read_artifact(request):
            repair=with_decision_rubric(repair)
        from backend.pipeline.viewpoint_route_review_continuity import with_observation_identities, OBSERVATION_IDENTITIES
        if not request.exists() or OBSERVATION_IDENTITIES in read_artifact(request):
            repair=with_observation_identities(repair)
        from backend.pipeline.viewpoint_route_correction_changes import with_creation_shapes, CREATION_SHAPES
        if not request.exists() or CREATION_SHAPES in read_artifact(request):
            repair=with_creation_shapes(repair)
        from backend.pipeline.viewpoint_route_observation_restore import with_restore_contract, MARKER as RESTORE
        if not request.exists() or RESTORE in read_artifact(request):
            repair=with_restore_contract(repair)
        if request.exists():
            saved = read_artifact(request)
            if ({k:v for k,v in saved.items() if k != 'validation_findings'} !=
                    {k:v for k,v in repair.items() if k != 'validation_findings'}
                    or not saved.get('validation_findings')):
                raise ValueError('saved Route validation repair baseline changed')
            # A clearer diagnostic may be shipped after a saved failure.
            # Replay the exact dispatched input; improve only future requests.
            repair, findings = saved, saved['validation_findings']
        write_immutable(request, repair)
        failures.append({'response_sha256': sha256_json(raw), 'findings': findings,
            'repair_response': response.name})
        state['used'] += 1
        state['attempts'].append({'stage': cache.name, 'repair_response': response.name, 'findings': findings})
        raw, _, _ = call_model(adapter, repair, response)
        active_packet=repair


def read_validated_changes(adapter, packet, cache, *, repair_instruction=EDIT_REPAIR_INSTRUCTION):
    """Authenticate every saved repair without invoking a model or trusting an alias."""
    raw = read_saved_model_response(adapter, packet, cache)
    names = [cache.name]
    summary_path = cache.with_name(cache.stem+'-validated.json')
    if not summary_path.exists():
        return raw, names
    summary = read_artifact(summary_path)
    for index, failure in enumerate(summary['failures']):
        name = cache.stem+f'-validation-{index:03d}.json'
        if failure['repair_response'] != name or failure['response_sha256'] != sha256_json(raw) or not failure['findings']:
            raise ValueError('saved Route validation repair chain changed')
        response = cache.with_name(name)
        actual = read_artifact(response.with_name(response.stem+'-packet.json'))
        expected = packet | {'prior_invalid_response': raw, 'validation_findings': failure['findings'],
            'validation_instruction': repair_instruction}
        if HISTORY in actual:
            expected=with_validation_history(expected, packet, summary['failures'][:index], failure['findings'])
        from backend.pipeline.viewpoint_route_decision_rubric import with_decision_rubric, MARKER as RUBRIC
        if RUBRIC in actual:
            expected=with_decision_rubric(expected)
        from backend.pipeline.viewpoint_route_review_continuity import with_observation_identities, OBSERVATION_IDENTITIES
        if OBSERVATION_IDENTITIES in actual:
            expected=with_observation_identities(expected)
        from backend.pipeline.viewpoint_route_correction_changes import with_creation_shapes, CREATION_SHAPES
        if CREATION_SHAPES in actual:
            expected=with_creation_shapes(expected)
        from backend.pipeline.viewpoint_route_observation_restore import with_restore_contract, MARKER as RESTORE
        if RESTORE in actual:
            expected=with_restore_contract(expected)
        if actual != expected:
            raise ValueError('saved Route validation repair input changed')
        raw = read_saved_model_response(adapter, actual, response)
        names.append(name)
    if summary['response_sha256'] != sha256_json(raw):
        raise ValueError('saved Route validated response changed')
    return raw, names


def read_initial_saved_proposal(adapter, packet, root):
    from backend.pipeline.viewpoint_route_correction_changes import (
        proposal_adapter, ProposalChanges, PROPOSAL_MARKER, PROPOSAL_VERSION)
    from backend.pipeline.viewpoint_route_update_projection import update_baseline
    from backend.pipeline.viewpoint_route_review_continuity import continuity_packet
    from backend.pipeline.viewpoint_route_cvp_resolution import parse_proposal_changes
    path, response = root/'route-proposal-input.json', root/'raw-route-proposal-000.json'
    if not response.exists():
        raise ValueError('saved Route initial response is missing; no generation fallback')
    active = proposal_adapter(adapter, path, response)
    compact = getattr(active, '_response_model', None) is ProposalChanges
    payload = packet
    if compact:
        baseline = update_baseline(packet)
        payload = packet | {PROPOSAL_MARKER: PROPOSAL_VERSION, 'route_proposal_baseline': baseline}
    payload = continuity_packet(payload, [], packet_path=path, response_path=response)
    raw, names = read_validated_changes(active, payload, response)
    if compact:
        raw = parse_proposal_changes(raw, baseline, packet)[0].model_dump(mode='json')
    return raw, names
