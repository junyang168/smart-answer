"""One bounded reviewer-format repair, never a second semantic review round."""
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    CanonicalViewpointReviewResponse, canonicalize_review,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import call_model, write_immutable, read_artifact


def repair_coverage_pin(review, *, adapter, request, claims, proposal,
                        raw_path, packet_path, authorization_path, cache, call=call_model):
    """One explicitly owner-authorized metadata correction, not content review.

    No permission file means no call. Exact malformed trailing characters only;
    a valid but different version hash is NOT a format-only correction.
    """
    from backend.api.canonical_repository.viewpoint_production_safety import file_sha256
    from backend.api.canonical_repository.viewpoint_batch_resolution import validate_review
    authorization = read_artifact(authorization_path)
    original = read_artifact(raw_path)
    body = {k:v for k,v in authorization.items() if k!='artifact_sha256'}
    if (authorization.get('schema_version')!='cvp_owner_review_pin_format.v1'
            or authorization.get('artifact_sha256')!=sha256_json(body)
            or authorization.get('maximum_calls')!=1 or not authorization.get('owner_request')
            or authorization.get('batch_id')!=request['batch_id']
            or authorization.get('original_review')!={'path':str(raw_path.absolute()),'file_sha256':file_sha256(raw_path)}
            or authorization.get('packet')!={'path':str(packet_path.absolute()),'file_sha256':file_sha256(packet_path)}):
        raise ValueError('owner pin-format authorization/input binding differs')
    identity = {k:original[k] for k in ('model_id','backend','prompt_sha256','generation_config_sha256','request_payload_sha256')}
    if authorization.get('original_model_identity')!=identity:
        raise ValueError('owner pin-format model/config binding differs')
    if any(getattr(adapter,k)!=identity[k] for k in ('model_id','backend','prompt_sha256','generation_config_sha256')):
        raise ValueError('pin-format correction must use exact original reviewer')
    canonical, _ = canonicalize_review(original['response'])
    if review.model_dump(mode='json') != canonical:
        raise ValueError('pin-format original review was already changed')
    expected = {c.claim_id:c.claim_revision_sha256 for c in claims}
    fixes = []
    diagnostic = deepcopy(canonical)
    for index,row in enumerate(canonical['change_reviews']):
        new = expected.get(row['claim_id']); old = row.get('coverage_claim_sha256')
        if old==new:continue
        if (not new or len(new)!=64 or not isinstance(old,str) or len(old)<=64
                or not old.startswith(new) or row['decision']!='pass'
                or row.get('correction') is not None or row.get('finding_codes')):
            raise ValueError('review error is not an authorized trailing-SHA format fault')
        fixes.append({'index':index,'claim_id':row['claim_id'],'component_index':row['component_index'],
                      'old_sha256':old,'expected_sha256':new})
        diagnostic['change_reviews'][index]['coverage_claim_sha256']=new
    if not fixes or authorization.get('fixes')!=fixes:
        raise ValueError('owner pin-format exact editable fields differ')
    model = getattr(adapter,'_response_model',CanonicalViewpointReviewResponse)
    # A diagnostic copy proves SHA is the sole mechanical error. It is never
    # used as approval: approval must come from the new actual Opus response.
    checked = validate_review(review=model.model_validate(diagnostic),proposal=proposal,
                              proposal_sha256=request['proposal_sha256'],claims=claims)
    if checked['outcome']!='pass':raise ValueError('pin-format correction cannot remove content findings')
    repair_request = dict(request, review_mode='owner_authorized_pin_format_repair',
        invalid_review=canonical, invalid_review_sha256=sha256_json(canonical),
        owner_authorization_sha256=authorization['artifact_sha256'],
        format_repair_contract={'maximum_calls':1,'editable_fields':fixes,
            'instruction':'Return the COMPLETE original invalid_review exactly, replacing ONLY the listed coverage_claim_sha256 strings with their listed expected_sha256 values copied verbatim. Every decision, reason, correction, ID, novelty/structure/relation/revision field and all other strings must remain EXACTLY unchanged. Do not reconsider content, paraphrase, explain, or add findings. Return the same response schema.'})
    write_immutable(cache.with_name(cache.stem+'-request.json'),repair_request)
    raw, calls, seconds = call(adapter,repair_request,cache)
    fixed, changes = canonicalize_review(raw)
    if fixed != diagnostic or raw != fixed:
        raise ValueError('pin-format response changed another field or remains malformed; stop')
    parsed = model.model_validate(fixed)
    validate_review(review=parsed,proposal=proposal,proposal_sha256=request['proposal_sha256'],claims=claims)
    write_immutable(cache.with_name(cache.stem+'-validation.json'),{
        'owner_authorization_sha256':authorization['artifact_sha256'],
        'original_review_sha256':sha256_json(canonical),'corrected_review_sha256':sha256_json(fixed),
        'editable_fields':fixes,'all_other_fields_exactly_unchanged':True,'semantic_correction_rounds_added':0})
    return parsed, changes, calls, seconds


def bind_coverage_evidence_references(raw, request):
    """Resolve exact own-fragment references, never infer IDs from spelling.

    Coverage is an EvidenceStep set. Reviewers sometimes name its actual bound
    fragments instead. Only a unique eligible owner in the authenticated Claim
    packet permits that transport conversion. The raw fragment choices remain
    in the normalization receipt, including multiple fragments of one step.
    """
    result = deepcopy(raw)
    receipts = []
    claims = request.get('packet', request).get('claims', [])
    index = {c['claim_id']: c for c in claims}
    if len(index) != len(claims):
        raise ValueError('coverage reference binding has duplicate Claims')
    for row in result.get('change_reviews', []):
        ids = row.get('coverage_evidence_step_ids')
        claim = index.get(row.get('claim_id'))
        if not isinstance(ids, list) or claim is None:
            continue  # Existing strict schema/Claim gate remains authoritative.
        evidence = claim.get('evidence', [])
        own = {e['evidence_step_id'] for e in evidence}
        if all(i in own for i in ids):
            continue  # Legacy IDs/multiplicity are deliberately unchanged.
        if len(ids) != len(set(ids)):
            raise ValueError('coverage reference binding has duplicate references')
        mapped = []
        for ref in ids:
            fragment_matches = [e for e in evidence if e.get('source_fragment_id') == ref]
            if ref in own:
                if any(e['evidence_step_id'] != ref for e in fragment_matches):
                    raise ValueError('coverage reference collides across ID namespaces')
                matches = [e for e in evidence if e['evidence_step_id'] == ref]
            else:
                matches = fragment_matches
                if len(matches) != 1:
                    raise ValueError('coverage fragment does not have one exact own binding')
            owners = {e['evidence_step_id'] for e in matches}
            if len(owners) != 1 or not all(e.get('valid_for_identity_review') is True for e in matches):
                raise ValueError('coverage reference is foreign, ambiguous or ineligible')
            owner = next(iter(owners))
            mapped.append({'original_reference': ref, 'evidence_step_id': owner})
        bound = sorted({x['evidence_step_id'] for x in mapped})
        receipts.append({'claim_id': row['claim_id'], 'component_index': row['component_index'],
                         'original_references': list(ids), 'exact_own_bindings': mapped,
                         'bound_evidence_step_ids': bound})
        row['coverage_evidence_step_ids'] = bound
    return result, receipts


def parse_review(raw: dict[str, Any], *, adapter: Any, request: dict[str, Any],
                 cache: Path, call=call_model):
    canonical, changes = canonicalize_review(raw)
    canonical, bindings = bind_coverage_evidence_references(canonical, request)
    if bindings:
        write_immutable(cache.with_name(cache.stem+'-coverage-reference-binding.json'), {
            'schema_version': 'wang_exact_own_coverage_reference_binding_v1',
            'request_sha256': sha256_json(request), 'original_response_sha256': sha256_json(raw),
            'bound_response_sha256': sha256_json(canonical), 'bindings': bindings,
            'model_calls_added': 0, 'content_decisions_changed': False})
        changes += [f"/change_reviews/{i}/coverage_evidence_step_ids"
                    for i, row in enumerate(canonical.get('change_reviews', []))
                    if any(b['claim_id']==row['claim_id'] and b['component_index']==row['component_index'] for b in bindings)]
    response_model = getattr(adapter, '_response_model', CanonicalViewpointReviewResponse)
    try:
        return response_model.model_validate(canonical), changes, 0, 0.0
    except ValidationError as error:
        errors = error.errors(include_url=False, include_context=False, include_input=False)
        for item in errors:
            item['loc'] = list(item['loc'])
        # Only the known contradictory pass/issue-code shape is format-only.
        # Missing fields, bad IDs, correction text, and other schema errors stop.
        indices = []
        for item in errors:
            loc = item['loc']
            if (len(loc) != 2 or loc[0] != 'change_reviews'
                    or not isinstance(loc[1], int)
                    or item['msg'] != 'Value error, a passing change carries no finding or correction'):
                raise
            row = canonical['change_reviews'][loc[1]]
            if row['decision'] != 'pass' or row.get('correction') is not None or not row['finding_codes']:
                raise
            indices.append(loc[1])
        repair_request = dict(request, review_mode='schema_format_repair',
            invalid_review=canonical, invalid_review_sha256=sha256_json(canonical),
            schema_errors=errors,
            format_repair_contract={
                'maximum_format_repair_calls': 1,
                'editable_change_review_indices': indices,
                'instruction': 'Return the complete same review. Only classify the misplaced finding_codes on the listed pass rows: they may be cleared ONLY if informational, not actual objections. Keep every decision, reason, correction, identity and all other rows/fields exactly unchanged. If the code is a real objection, do not clear it: stop with the original invalid response. Do not reconsider content or introduce a new finding.',
            })
        write_immutable(cache.with_name(cache.stem + '-request.json'), repair_request)
        fixed, calls, seconds = call(adapter, repair_request, cache)
        fixed_canonical, fixed_changes = canonicalize_review(fixed)
        # Validate once; a second malformed answer never causes another call.
        parsed = response_model.model_validate(fixed_canonical)
        comparable = deepcopy(fixed_canonical)
        for index in indices:
            if fixed_canonical['change_reviews'][index]['finding_codes'] != []:
                raise ValueError('format repair did not resolve contradictory pass codes')
            comparable['change_reviews'][index]['finding_codes'] = canonical['change_reviews'][index]['finding_codes']
        if comparable != canonical:
            raise ValueError('format repair changed a substantive review field; stop')
        write_immutable(cache.with_name(cache.stem + '-validation.json'), {
            'original_review_sha256': sha256_json(canonical),
            'corrected_review_sha256': sha256_json(fixed_canonical),
            'editable_indices': indices, 'removed_informational_codes': [
                canonical['change_reviews'][i]['finding_codes'] for i in indices],
            'all_decisions_reasons_and_corrections_unchanged': True,
            'semantic_correction_rounds_added': 0, 'max_format_repairs': 1,
        })
        return parsed, changes + fixed_changes, calls, seconds
