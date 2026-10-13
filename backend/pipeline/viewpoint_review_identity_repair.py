"""Owner-bound, fixed-parent ID-only review transport repair.

Original provider answers are immutable. This is not semantic re-review and
does not infer a mapping from prose, numbering or similarities.
"""
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

from backend.api.canonical_repository.viewpoint_foundation import sha256_json

SCHEMA = 'wang_owner_review_claim_id_format_repair_v1'
BINDING = 'review-claim-id-format-repair-binding.json'
ACTIVE = ContextVar('review_claim_id_format_repair', default=None)


def enable(store, authorization, output_root, campaign):
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable
    from backend.pipeline.cvp_sql_flow import check_binding
    check_binding(authorization)
    direction = read_artifact(Path(authorization['path']))
    if (direction.get('schema_version') != SCHEMA
            or direction.get('artifact_sha256') != sha256_json({k:v for k,v in direction.items() if k != 'artifact_sha256'})
            or direction.get('campaign_sha256') != campaign
            or direction.get('authorized_by') != 'owner'
            or not direction.get('owner_request') or direction.get('maximum_format_calls') != 1
            or direction.get('additional_semantic_reviews') != 0):
        raise ValueError('review identity repair direction/seal/permission drift')
    group = store.get_cvp_group(direction['run_id'])
    root = Path(group['execution_roots']['group']) / 'normal-review'
    if (group['campaign_sha256'] != campaign or root.parent.parent.resolve() != Path(output_root).resolve()
            or direction['original_raw']['path'] != str((root/'raw-final-review.json').resolve())
            or sha256_json(group['materials']) != direction['material_sha256']):
        raise ValueError('review identity repair foreign group/material/root')
    path = Path(output_root)/BINDING
    if group['change_set_ids'] and not path.exists():
        raise ValueError('review identity repair cannot reopen committed approval')
    for proof in direction['original_answers']:
        check_binding(proof)
    check_binding(direction['original_raw'])
    write_immutable(path, {'authorization': authorization})
    ACTIVE.set(direction)


def diagnostic(direction, request, raw):
    """Exactly listed transport rows, complete component denominator and own evidence."""
    if (direction['request_payload_sha256'] != sha256_json(request)
            or direction['original_response_sha256'] != sha256_json(raw)
            or request.get('review_mode') != 'final_effective_proposal'
            or request.get('allows_further_correction') is not False):
        raise ValueError('review identity repair original request/response drift')
    fixed = deepcopy(raw)
    claims = {c['claim_id']: c for c in request['packet']['claims']}
    if len(claims) != len(request['packet']['claims']):
        raise ValueError('review identity repair duplicate Claims')
    edits = direction['edits']
    if not edits or len({e['row_index'] for e in edits}) != len(edits):
        raise ValueError('review identity repair empty/duplicate edits')
    for edit in edits:
        index = edit['row_index']
        if type(index) is not int or index < 0 or index >= len(fixed['change_reviews']):
            raise ValueError('review identity repair foreign row')
        row = fixed['change_reviews'][index]
        target = claims.get(edit['expected_claim_id'])
        if (row['claim_id'] != edit['original_claim_id'] or row['component_index'] != edit['component_index']
                or edit['original_claim_id'] == edit['expected_claim_id'] or target is None
                or row['decision'] != 'pass' or row['finding_codes'] or row['correction'] is not None):
            raise ValueError('review identity repair row/decision/target mismatch')
        eligible = {e['evidence_step_id'] for e in target['evidence'] if e.get('valid_for_identity_review') is True}
        if not row['coverage_evidence_step_ids'] or not set(row['coverage_evidence_step_ids']) <= eligible:
            raise ValueError('review identity repair foreign/ineligible own evidence')
        row['claim_id'] = edit['expected_claim_id']
    expected = {(d['claim_id'], i) for d in request['proposal']['claim_decisions'] for i,_ in enumerate(d['components'])}
    actual = [(r['claim_id'],r['component_index']) for r in fixed['change_reviews']]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError('review identity repair incomplete/duplicate/foreign component population')
    return fixed


def format_request(direction, request, raw):
    diagnostic(direction, request, raw)
    return {'review_mode': 'owner_claim_id_format_only',
        'proposal_sha256': request['proposal_sha256'],
        'packet': {'claims': [{'claim_id':c['claim_id'],'claim_revision_sha256':c['claim_revision_sha256']} for c in request['packet']['claims']]},
        'original_request_sha256': sha256_json(request),
        'owner_authorization_sha256': direction['artifact_sha256'],
        'invalid_review': raw, 'editable_claim_id_fields': direction['edits'],
        'format_repair_contract': 'Return the COMPLETE invalid_review exactly, replacing ONLY the listed change_reviews row claim_id values with expected_claim_id. Every decision, reason, evidence ID, component index, correction, novelty, structure, relation and other field MUST remain exactly unchanged. Do not reconsider content or add/remove rows. This is one ID-only format call, not a second semantic final review.'}


def validate_format_response(response, expected):
    if response != expected:
        raise ValueError('review identity format response changed another field or remains malformed')


def repair(adapter, request, raw, cache, *, persist=True):
    from backend.pipeline import viewpoint_resolution_runtime as rt
    from backend.api.canonical_repository.viewpoint_response_binding import bind_response
    from backend.pipeline.cvp_sql_flow import check_binding
    direction = ACTIVE.get()
    if direction is None or str(Path(cache).resolve()) != direction['original_raw']['path']:
        return raw, None
    check_binding(direction['original_raw'])
    original = rt.read_artifact(cache)
    keys = ('model_id','backend','prompt_sha256','generation_config_sha256','request_payload_sha256')
    if {k: original[k] for k in keys} != direction['original_model_identity']:
        raise ValueError('review identity repair original model identity drift')
    fixed = diagnostic(direction, request, raw)
    # Only formatting is requested. Semantic full source remains bound in the
    # authenticated original request, not reinterpreted in this transport call.
    payload = format_request(direction, request, raw)
    repaired_cache = Path(cache).with_name(Path(cache).stem+'-claim-id-format-repair.json')
    if persist and not rt.validating_saved_artifacts():
        rt.call_model(adapter, payload, repaired_cache)
    if not repaired_cache.exists():
        raise ValueError('review identity format repair lacks saved response; no readonly call')
    saved = rt.read_artifact(repaired_cache)
    selected = rt.response_adapter(adapter, saved)
    response = rt.read_saved_model_response(selected, payload, repaired_cache)
    validate_format_response(response, fixed)
    # Existing strict schema, proposal coverage and SQL checks still run later.
    bind_response(adapter._response_model, response, request)
    receipt = {'schema_version':SCHEMA, 'authorization_sha256':direction['artifact_sha256'],
        'original_raw_artifact_sha256':original['artifact_sha256'],
        'format_raw_artifact_sha256':saved['artifact_sha256'],
        'original_request_sha256':sha256_json(request), 'edits':direction['edits'],
        'all_other_fields_exactly_unchanged': True, 'additional_semantic_reviews':0}
    receipt['artifact_sha256'] = sha256_json(receipt)
    if persist and not rt.validating_saved_artifacts():
        rt.write_immutable(repaired_cache.with_name(repaired_cache.stem+'-validation.json'), receipt)
    return response, receipt
