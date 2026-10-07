"""One bounded reviewer-format repair, never a second semantic review round."""
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    CanonicalViewpointReviewResponse, canonicalize_review,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_resolution_runtime import call_model, write_immutable


def parse_review(raw: dict[str, Any], *, adapter: Any, request: dict[str, Any],
                 cache: Path, call=call_model):
    canonical, changes = canonicalize_review(raw)
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
