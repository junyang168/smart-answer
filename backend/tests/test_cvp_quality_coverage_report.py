import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('quality_coverage_report', Path(__file__).parents[2] / 'scripts/cvp-quality-coverage-report.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_old_claim_adjudication_is_detected_even_without_cvp_review_fields():
    leaks = MODULE.R.H.blind_leaks({'claims': [{'ai_adjudication': {'approval_status': 'approved', 'status': 'upheld'}}],
                                  'evidence': [{'stance': 'endorsed'}]})
    assert '$.claims[0].ai_adjudication' in leaks
    assert '$.claims[0].ai_adjudication.approval_status' in leaks
    assert not MODULE.R.H.blind_leaks({'claims': [{'ai_adjudication': None}], 'evidence': [{'stance': 'endorsed'}]})


def test_claim_coverage_and_support_are_not_reported_as_cvp_identity():
    packet = {'source_claims': [{'claim_id': 'C1'}, {'claim_id': 'C2'}, {'claim_id': 'C3'}],
              'connected_complete_viewpoints': [{'viewpoint': {'viewpoint_id': 'V1', 'viewpoint_revision_id': 'VR1'},
                'links': [{'claim_id': 'C2', 'viewpoint_claim_link_id': 'L2', 'link_type': 'supports'},
                          {'claim_id': 'C3', 'viewpoint_claim_link_id': 'L3', 'link_type': 'equivalent_component'}]}]}
    expected = {'C1': 'claim_only', 'C2': 'related_role_only', 'C3': 'identity_destination_reference_resolved',
                'VR1': 'identity_destination_reference_resolved', 'E1': 'no_destination_cited'}
    for rid, kind in expected.items():
        r = MODULE.destination_references({'record_ids': [rid]}, packet)
        assert r['kind'] == kind
        assert r['semantic_identity_validated'] is False
