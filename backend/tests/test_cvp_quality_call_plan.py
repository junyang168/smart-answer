import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location('quality_call_plan', Path(__file__).parents[2] / 'scripts/cvp-quality-call-plan.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_protection_is_based_on_physical_aliases_not_root_name():
    snapshot = [{'kind': 'campaign', 'metadata': {'sequence': 35, 'revisit_required': True, 'claim_ids': ['CL-a']}}]
    rows = [{'collection': 'claims', 'object_id': 'CL-a', 'payload': {'occurrences': [{'source_id': 'lecture-title'}]}}]
    sources = [{'source_id': 'SRC-new-alias', 'transcript_id': 'lecture-title', 'path': '/physical-original'}]
    paths, ids = MODULE.protection_paths(snapshot, sources, rows)
    assert paths == {'/physical-original'}
    assert ids == {'CL-a'}
    with pytest.raises(ValueError, match='source unresolved'):
        MODULE.protection_paths(snapshot, sources, [])


def test_request_freezes_model_configuration_and_distinct_tasks_without_credentials():
    material = {'keys': {}, 'data': {}}
    request = MODULE.request('observe', material)
    assert request['generationConfig']['temperature'] == 0
    assert request['generationConfig']['maxOutputTokens'] == 16384
    assert 'observations' in request['generationConfig']['responseSchema']['properties']
    assert 'coverage' in MODULE.request('compare', material)['generationConfig']['responseSchema']['properties']
    assert 'key=' not in MODULE.PF.encoded(request).decode()
