import importlib.util
import json
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('quality_history', Path(__file__).parents[2] / 'scripts/cvp-quality-history.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_blind_projection_removes_nested_old_decisions_and_keeps_source_roles():
    result = MODULE.blind({'claims': [{'statement': 'source judgment', 'attribution': 'professor',
         'occurrences': [{'speaker': 'professor', 'review_note': 'old reviewer reason'}],
         'provenance': {'approved_by': 'old model'}}], 'evidence': [{'support_eligibility': 'context_only',
         'rejected_highlights': ['old rejection'], 'review_status': 'approved'}]})
    assert result == {'claims': [{'statement': 'source judgment', 'attribution': 'professor',
                                 'occurrences': [{'speaker': 'professor'}]}],
                      'evidence': [{'support_eligibility': 'context_only'}]}


def test_same_audit_pins_are_collision_only_not_model_approval():
    audit = {'meta': {'registry_read_scope': {'complete_target_members': True,
           'read_pins': [{'collection': 'claims', 'object_id': 'CL-a', 'pin': [2, 'sha', False]}]}},
           'layers': {'4': {'results': [{'viewpoint_id': 'CV-a', 'verdict': 'same', 'revision_id': 'CVR-a'}]}}}
    objects = {('claims', 'CL-a'): {'revision': 2, 'content_sha256': 'sha', 'retired': False}}
    result = MODULE.match_audit('CV-a', audit, objects)
    assert result['same_complete_current_read_set']
    assert not result['new_forward_calls_allowed']
    assert not result['provider_raw_authenticated']
    assert not result['fresh_blind_evidence']
    objects['claims', 'CL-a']['revision'] = 3
    assert not MODULE.match_audit('CV-a', audit, objects)['same_complete_current_read_set']


def test_missing_read_pin_is_not_silently_dropped():
    audit = {'meta': {'registry_read_scope': {'complete_target_members': True,
           'read_pins': [{'collection': 'claims', 'object_id': 'CL-last', 'pin': [1, 'sha', False]}]}},
           'layers': {'4': {'results': [{'viewpoint_id': 'CV-a'}]}}}
    result = MODULE.match_audit('CV-a', audit, {})
    assert not result['same_complete_current_read_set']
    assert result['pin_differences'][0]['state'] == 'not_in_current_preparation_read_set'


def test_audit_binding_requires_exact_sha_and_never_replaces_raw(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'OLD_STAGING', tmp_path)
    path = tmp_path / 'audit.json'
    path.write_text(json.dumps({'layers': {}}))
    binding = {'path': str(path), 'file_sha256': MODULE.PF.digest(path)}
    assert MODULE.audit_binding(binding)[1] == 'saved_report_file_verified'
    path.write_text('{}')
    assert MODULE.audit_binding(binding)[1] == 'audit_file_sha_mismatch'
