import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('quality_evidence_report', Path(__file__).parents[2] / 'scripts/cvp-quality-report.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_group_intent_subset_and_changed_versions_are_explicit():
    old = [{'collection': 'claims', 'object_id': 'A', 'pin': [1, 'a', False]},
           {'collection': 'claims', 'object_id': 'B', 'pin': [1, 'b', False]}]
    now = [{'collection': 'claims', 'object_id': 'A', 'pin': [2, 'a2', False]},
           {'collection': 'claims', 'object_id': 'C', 'pin': [1, 'c', False]}]
    result = MODULE.read_set_delta(old, now)
    assert result == {'intent_only': [['claims', 'B']], 'report_only': [['claims', 'C']],
                      'changed_shared_pins': [['claims', 'A']], 'same_read_set': False}
    assert MODULE.read_set_delta(old, list(reversed(old)))['same_read_set']


def test_duplicate_historical_pin_cannot_silently_overwrite_a_version():
    row = {'collection': 'claims', 'object_id': 'A', 'pin': [1, 'a', False]}
    with pytest.raises(ValueError, match='duplicate'):
        MODULE.read_set_delta([row, row], [row])


def test_unassessable_fixed_samples_are_neither_zero_defects_nor_a_population_estimate():
    metrics = MODULE.dimension_metrics(60, 20, 78, 12)
    assert len(metrics) == 6
    for row in metrics.values():
        assert row['independent_semantic_units_evaluated'] == 0
        assert row['confirmed_content_defects'] is None
        assert row['population_defect_rate'] is None and row['quality_pass'] is None
    assert metrics['identity']['not_assessable_units'] == 78
    assert metrics['source_to_viewpoint_coverage']['not_assessable_units'] == 12


def test_saved_provider_refusal_cannot_be_promoted_to_semantic_approval(monkeypatch):
    artifact = {'model': 'gemini-3.8-flash', 'prompt': 'fixture', 'schema': {},
                'provider_response': {'promptFeedback': {'blockReason': 'PROHIBITED_CONTENT'}}}
    key = MODULE.PF.hashlib.sha256(b'gemini-3.8-flash\nfixture').hexdigest()
    binding = {'path': '/fixture/' + key + '.provider-block.json', 'file_sha256': 'fixture'}
    monkeypatch.setattr(MODULE.H, 'audit_binding', lambda b: (artifact, 'saved_report_file_verified'))
    proof = MODULE.refusal_proof(binding)
    assert proof['provider_response_sha_bound']
    assert proof['independent_semantic_answer'] is False
    assert proof['new_call_permission'] is False and proof['quality_pass'] is None
    assert proof['actual_http_request_bytes_authenticated'] is False


def test_wrong_prompt_identity_and_nonrefusal_raw_are_rejected(monkeypatch):
    artifact = {'model': 'gemini-3.8-flash', 'prompt': 'fixture', 'schema': {},
                'provider_response': {'candidates': [{'finishReason': 'STOP'}]}}
    monkeypatch.setattr(MODULE.H, 'audit_binding', lambda b: (artifact, 'saved_report_file_verified'))
    with pytest.raises(ValueError, match='prompt identity'):
        MODULE.refusal_proof({'path': '/fixture/wrong.provider-block.json'})
    key = MODULE.PF.hashlib.sha256(b'gemini-3.8-flash\nfixture').hexdigest()
    with pytest.raises(ValueError, match='confirmed provider refusal'):
        MODULE.refusal_proof({'path': '/fixture/' + key + '.provider-block.json'})
