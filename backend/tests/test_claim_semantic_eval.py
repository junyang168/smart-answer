"""Probe integrity checks; these are not semantic model accuracy tests."""
import json
import pytest
from backend.tests.claim_semantic_eval import FIXTURE, inputs, score
from backend.pipeline import claim_management as cm
from backend.pipeline.source_reconciliation_runtime import claim_management_model_adapter


def test_blind_inputs_exclude_expected_results_and_provenance():
    cases = json.loads(FIXTURE.read_text())['cases']
    assert all(set(row) == {'case_id', 'source', 'statement'} for row in inputs(cases))
    assert {c['expected_supported'] for c in cases} == {True, False}
    assert '其中一段' in cases[0]['source']
    assert cases[0]['source'] == cases[1]['source']


def test_scoring_distinguishes_false_acceptance_from_false_rejection():
    cases = json.loads(FIXTURE.read_text())['cases']
    rows = [{'case_id':c['case_id'], 'supported':not c['expected_supported'], 'reason':'test'} for c in cases]
    result = score(cases, {'judgments':rows})
    assert result['correct'] == 0
    assert len(result['false_accepts']) == len(result['false_rejects']) == 5
    rows[-1] = rows[0]
    with pytest.raises(ValueError, match='duplicate'):
        score(cases, {'judgments':rows})


@pytest.mark.parametrize('policy_name', [
    'wang_claim_management_joint_update_v6.json',
    'wang_claim_management_joint_update_v3.json',
])
def test_composed_review_keeps_schema_and_budget_and_includes_scope_judgment(policy_name):
    policy = cm.Policy.model_validate_json((cm.DEFAULT_POLICY.parent/policy_name).read_text())
    before = policy.model_dump()
    adapter = claim_management_model_adapter('opus', policy)
    assert '审阅概括的语义力度' in adapter._base_prompt
    assert '不能仅因出现' in adapter._base_prompt
    assert '圣经证据是教授的立论原则' in adapter._base_prompt
    schema = adapter._response_model.model_json_schema()
    assert ('binding_assessments' in schema['properties']) == bool(policy.evidence_review_contract)
    assert policy.model_dump() == before
