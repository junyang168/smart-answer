import json
from copy import deepcopy

import pytest

from backend.pipeline.viewpoint_cvp_campaign_runner import (
    binding, build_selection, discover_selection, lineage_preflight,
)


def inventory(tmp_path):
    candidate = tmp_path / 'batch/candidates/c.reviewed-candidate.json'
    review = tmp_path / 'batch/reviews/c.independent-review.json'
    package = tmp_path / 'input.json'
    for path, body in ((candidate, {}), (package, {'original': '完整原文'}),
                       (review, {'source': {'package_path': str(package)}})):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))
    return {'good': {'variants': [{'path': str(candidate),
        'file_sha256': binding(candidate)['file_sha256'],
        'changed_substantive_fields': ['evidence_step_ids'],
        'contract': 'requires_historical_proof_adapter'}]},
        'bad': {'variants': [{'changed_substantive_fields': ['statement']}]},
        'ambiguous': {'variants': []}}


def test_all_pending_claims_reported_without_models_or_writes(tmp_path):
    index = inventory(tmp_path)
    before = deepcopy(index)
    schedule = {'schedule': [
        {'sequence': 1, 'group_key': 'old', 'state': 'already_complete'},
        {'sequence': 2, 'group_key': 'next', 'state': 'pending'},
        {'sequence': 3, 'group_key': 'later', 'state': 'pending'}]}
    groups = {'next': {'claim_ids': ['bad', 'good']},
              'later': {'claim_ids': ['ambiguous', 'missing']}}
    paths = set(tmp_path.rglob('*'))
    report = lineage_preflight(schedule, groups, index)
    assert report['claims_checked'] == 4 and report['blocked_claims'] == 3
    assert report['eligibility_approved'] is False
    assert report['model_calls'] == report['registry_writes'] == 0
    assert [r['sequence'] for r in report['results']] == [2, 2, 3, 3]
    assert index == before and set(tmp_path.rglob('*')) == paths
    assert report == lineage_preflight(schedule, groups, index)


@pytest.mark.parametrize('mutation', ['statement', 'ambiguous', 'sha', 'missing_input'])
def test_preflight_and_live_entrance_reject_same_failure(tmp_path, mutation):
    index = inventory(tmp_path)
    variant = index['good']['variants'][0]
    if mutation == 'statement':
        variant['changed_substantive_fields'] = ['statement']
    elif mutation == 'ambiguous':
        index['good']['variants'].append(deepcopy(variant))
    elif mutation == 'sha':
        variant['file_sha256'] = '0' * 64
    else:
        (tmp_path / 'input.json').unlink()
    schedule = {'schedule': [{'sequence': 1, 'group_key': 'g', 'state': 'pending'}]}
    group = {'claim_ids': ['good']}
    result = lineage_preflight(schedule, {'g': group}, index)['results'][0]
    assert result['status'] == 'blocked'
    with pytest.raises((ValueError, OSError)) as exc:
        build_selection(group, index, tmp_path / 'never-produced')
    assert str(exc.value) == result['error']
    assert not (tmp_path / 'never-produced').exists()


def test_live_selection_preserves_discovered_bindings(tmp_path):
    index = inventory(tmp_path)
    group = {'claim_ids': ['good']}
    selected, sealed = discover_selection(group, index)
    assert sealed == []
    build_selection(group, index, tmp_path / 'out')
    assert json.loads((tmp_path / 'out/review-selection.json').read_text()) == {'claims': selected}
