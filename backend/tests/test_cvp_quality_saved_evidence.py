import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('quality_saved_evidence', Path(__file__).parents[2] / 'scripts/cvp-quality-saved-evidence.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_all_historical_pins_are_checked_and_missing_objects_are_not_matches():
    pins = [{'collection': 'claims', 'object_id': 'A', 'pin': [1, 'sha', False]},
            {'collection': 'claims', 'object_id': 'B', 'pin': [2, 'sha2', False]}]
    current = {('claims', 'A'): {'revision': 1, 'content_sha256': 'sha', 'retired': False}}
    result = MODULE.compare_pins(pins, current)
    assert result['read_pin_count'] == 2 and not result['all_current_pins_match']
    assert result['differences'][0]['state'] == 'missing_current_object'
    current['claims', 'B'] = {'revision': 2, 'content_sha256': 'sha2', 'retired': True}
    assert MODULE.compare_pins(pins, current)['differences'][0]['state'] == 'current_pin_differs'


def test_matching_versions_do_not_authenticate_raw_or_six_dimensional_quality():
    pins = [{'collection': 'claims', 'object_id': 'A', 'pin': [1, 'sha', False]}]
    current = {('claims', 'A'): {'revision': 1, 'content_sha256': 'sha', 'retired': False}}
    result = MODULE.compare_pins(pins, current)
    assert result['all_current_pins_match']
    assert result['provider_raw_authenticated'] is False
    assert result['six_dimension_pass'] is None
    assert result['new_calls_authorized'] is False
    assert not MODULE.compare_pins([], {})['all_current_pins_match']


def test_exact_query_is_read_only_scoped_and_recomputes_payload_hash(monkeypatch):
    observed = {}
    row = {'kind': 'object', 'collection': 'claims', 'object_id': "A'quoted", 'revision': 2,
           'content_sha256': MODULE.PF.sha({'statement': 'fixture'}), 'retired': False,
           'payload': {'statement': 'fixture', 'revision': 2}}
    class Result:
        returncode = 0
        stdout = json.dumps(row)
    def run(command, **kwargs):
        observed['sql'] = kwargs['input']
        return Result()
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    output = MODULE.query_exact('fixture', {('claims', "A'quoted")})
    assert 'payload' not in output[0]
    assert 'REPEATABLE READ READ ONLY' in observed['sql'] and "A''quoted" in observed['sql']
    assert 'JOIN requested' in observed['sql']
    row['payload']['statement'] = 'changed'
    Result.stdout = json.dumps(row)
    with pytest.raises(ValueError, match='SHA mismatches'):
        MODULE.query_exact('fixture', {('claims', "A'quoted")})
