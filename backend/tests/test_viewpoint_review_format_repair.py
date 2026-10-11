from copy import deepcopy
from types import SimpleNamespace
import json

import pytest
from pydantic import ValidationError
from backend.pipeline.viewpoint_review_format_repair import parse_review
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


@pytest.mark.parametrize('duplicate', [False, True])
def test_coverage_id_order_is_zero_call_but_duplicates_remain_invalid(tmp_path, duplicate):
    from backend.api.canonical_repository.viewpoint_batch_resolution import CoverageCanonicalViewpointReviewResponse
    original = review()
    original['schema_version'] = 'wang_canonical_viewpoint_review_v2'
    original['change_reviews'][0].update(finding_codes=[])
    for row in original['change_reviews']:
        row.update(coverage_requirement='cvp_required', coverage_claim_sha256='sha',
                   coverage_evidence_step_ids=['E2', 'E1'] + (['E1'] if duplicate else []),
                   coverage_reason='完整自身證據', cvp_covered=True)
    before = deepcopy(original)
    a, calls = adapter(original)
    a._response_model = CoverageCanonicalViewpointReviewResponse
    if duplicate:
        with pytest.raises(ValidationError, match='sorted and unique'):
            parse_review(original, adapter=a, request={}, cache=tmp_path/'unused.json')
    else:
        parsed, changes, count, _ = parse_review(original, adapter=a, request={}, cache=tmp_path/'unused.json')
        assert count == 0
        assert len(changes) == 2
        for raw_row, parsed_row in zip(original['change_reviews'], parsed.change_reviews):
            actual = parsed_row.model_dump()
            actual['coverage_evidence_step_ids'] = raw_row['coverage_evidence_step_ids']
            # Base schema defaults are allowed; every supplied semantic field is exact.
            assert all(actual[key] == value for key, value in raw_row.items())
    assert original == before and calls == []
    assert not (tmp_path/'unused.json').exists()


def review():
    return {'schema_version': 'wang_canonical_viewpoint_review_v1',
        'proposal_sha256': 'p', 'change_reviews': [
            {'claim_id': 'C1', 'component_index': 0, 'decision': 'pass',
             'finding_codes': ['scope_note'], 'reason': '同意，範圍附註不是異議', 'correction': None},
            {'claim_id': 'C2', 'component_index': 0, 'decision': 'correct',
             'finding_codes': ['missed_viewpoint'], 'reason': '保留全部限定', 'correction': '新增獨立觀點'}],
        'novelty_review': {'status': 'pass', 'missed_claim_ids': [], 'reason': '完整'},
        'structure_reviews': [], 'relation_reviews': [], 'revision_reviews': []}


def adapter(response):
    calls = []
    def generate(payload):
        calls.append(deepcopy(payload))
        return deepcopy(response)
    return SimpleNamespace(model_id='claude-opus-5-5', backend='claude_subscription',
        prompt_sha256='prompt', generation_config_sha256='config', generate=generate,
        max_request_bytes=500000), calls


def test_once_preserves_actual_findings_and_reuses_saved_format_response(tmp_path):
    original = review(); fixed = deepcopy(original)
    fixed['change_reviews'][0]['finding_codes'] = []
    a, calls = adapter(fixed)
    request = {'batch_id': 'b', 'proposal_sha256': 'p', 'packet': {'source': '完整原文'}}
    cache = tmp_path/'raw-review-format-repair.json'
    parsed, _, count, _ = parse_review(original, adapter=a, request=request, cache=cache)
    assert count == 1 and len(calls) == 1 and parsed.outcome() == 'findings'
    assert parsed.change_reviews[1].model_dump() == original['change_reviews'][1]
    assert original['change_reviews'][0]['finding_codes'] == ['scope_note']
    assert calls[0]['invalid_review_sha256'] == sha256_json(original)
    assert calls[0]['packet'] == request['packet']
    assert json.loads(cache.read_text())['response'] == fixed
    assert parse_review(original, adapter=a, request=request, cache=cache)[2] == 0
    assert len(calls) == 1


@pytest.mark.parametrize('drift', ['reason', 'correct_reason', 'correction', 'decision',
                                  'identity', 'proposal', 'novelty', 'remove_row'])
def test_rejects_every_substantive_change(tmp_path, drift):
    original = review(); fixed = deepcopy(original)
    fixed['change_reviews'][0]['finding_codes'] = []
    if drift == 'reason': fixed['change_reviews'][0]['reason'] = '另作解釋'
    elif drift == 'correct_reason': fixed['change_reviews'][1]['reason'] = '另作解釋'
    elif drift == 'correction': fixed['change_reviews'][1]['correction'] = '其他修改'
    elif drift == 'decision':
        fixed['change_reviews'][1].update(decision='pass', finding_codes=[], correction=None)
    elif drift == 'identity': fixed['change_reviews'][0]['claim_id'] = 'C3'
    elif drift == 'proposal': fixed['proposal_sha256'] = 'changed'
    elif drift == 'novelty': fixed['novelty_review']['reason'] = 'changed'
    else: fixed['change_reviews'].pop()
    a, calls = adapter(fixed)
    with pytest.raises((ValueError, IndexError)):
        parse_review(original, adapter=a, request={}, cache=tmp_path/'raw-review-format-repair.json')
    assert len(calls) == 1


def test_second_invalid_response_is_saved_and_never_called_again(tmp_path):
    original = review(); a, calls = adapter(original)
    cache = tmp_path/'raw-review-format-repair.json'
    for _ in range(2):
        with pytest.raises(ValidationError):
            parse_review(original, adapter=a, request={}, cache=cache)
    assert len(calls) == 1 and cache.exists()


@pytest.mark.parametrize('fault', ['missing_reason', 'pass_with_correction', 'duplicate_id'])
def test_other_schema_failures_do_not_trigger_repair(tmp_path, fault):
    original = review()
    if fault == 'missing_reason': original['change_reviews'][0].pop('reason')
    elif fault == 'pass_with_correction': original['change_reviews'][0]['correction'] = '實際異議'
    else:
        original['change_reviews'][0]['finding_codes'] = []
        original['change_reviews'][1]['claim_id'] = 'C1'
    a, calls = adapter({})
    with pytest.raises(ValidationError):
        parse_review(original, adapter=a, request={}, cache=tmp_path/'repair.json')
    assert not calls


def test_valid_review_has_zero_extra_calls(tmp_path):
    original = review(); original['change_reviews'][0]['finding_codes'] = []
    a, calls = adapter({})
    assert parse_review(original, adapter=a, request={}, cache=tmp_path/'repair.json')[2] == 0
    assert not calls
