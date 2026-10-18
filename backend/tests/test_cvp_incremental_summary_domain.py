"""CVP 2.0 summaries follow reviewed members, not a detached index card."""
from copy import deepcopy

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.cvp_claim_input import compile_input
from backend.api.canonical_repository.cvp_recall import STATEMENT_SCOPE_PROFILE, seal
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
from test_cvp_management import materials, maintained, request, proposal, passing, provenance, change


def incremental_input():
    req = request()
    req = req.model_copy(update={'input': {**req.input, 'recall_profile': STATEMENT_SCOPE_PROFILE}})
    bundle = materials()
    rows = [r for r in bundle['records'] if r['collection'] == 'source_fragments']
    assert rows[0]['payload']['source_id'] == 'S1'
    source = {'source_id': 'S1', 'source_type': 'sermon_transcript', 'revision': 1,
              'source_sha256': 'a' * 64}
    bundle = seal({**{k: deepcopy(v) for k, v in bundle.items() if k != 'artifact_sha256'},
        'records': [*bundle['records'], {'collection': 'source_documents', 'object_id': 'S1',
            'payload': source, 'revision': 1, 'content_sha256': semantic_record_sha(source),
            'retired': False}],
        'material': {'claims': [{'claim_ref': 'c1', 'target': True, 'support_loaded': True,
            'content': bundle['claim_input']['claims'][0]['content']}]}})
    return req, bundle


def test_new_viewpoint_persists_500_char_summary_and_exact_basis():
    req, bundle = incremental_input()
    raw = proposal()
    raw['summaries'][0]['summary'] = '人子称号的含义与这里的神性判断。' + '相关论证。' * 35
    assert 149 < len(raw['summaries'][0]['summary']) <= 500
    candidate = domain.compile_candidate(req, bundle, raw)
    assert candidate['summary_material_pending'] == []
    view = next(row['payload'] for row in candidate['writes'] if row['collection'] == 'canonical_viewpoints')
    assert view['summary'] == raw['summaries'][0]['summary']
    assert view['summary_basis']['member_link_ids'] == [next(
        row['object_id'] for row in candidate['writes'] if row['collection'] == 'viewpoint_claim_links')]
    approval = domain.validate_review(req, bundle, candidate, passing(candidate))
    assert approval['approved']
    package = domain.finalize_package(req, bundle, candidate, approval, provenance())
    domain.validate_summary_afterimage(req, bundle, candidate, package)
    forged = deepcopy(package)
    forged['canonical_viewpoints'][0]['summary_basis']['dependency_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='basis differs'):
        domain.validate_summary_afterimage(req, bundle, candidate, forged)


def test_missing_summary_keeps_proposal_pending_and_cannot_reach_review():
    req, bundle = incremental_input()
    raw = proposal()
    raw.pop('summaries')
    candidate = domain.compile_candidate(req, bundle, raw)
    assert candidate['summary_material_pending'] == ['nv1']
    with pytest.raises(ValueError, match='summary material'):
        domain.validate_review(req, bundle, candidate, passing(candidate))


def test_historical_summary_limit_remains_149_characters():
    raw = proposal()
    raw['summaries'][0]['summary'] = '甲' * 150
    with pytest.raises(ValueError, match='149 characters'):
        domain.compile_candidate(request(), materials(), raw)


def test_new_link_rebinds_unchanged_existing_summary_without_identity_permission():
    bundle, ids = maintained()
    records = deepcopy(bundle['records'])
    source = {'source_id': 'S1', 'source_type': 'sermon_transcript', 'revision': 1,
              'source_sha256': 'a' * 64}
    claim = {'claim_id': 'C2', 'statement': '人子是神，此處補充其身份。',
             'claim_type': 'exegesis', 'revision': 1, 'evidence_step_ids': ['E2']}
    evidence = {'evidence_step_id': 'E2', 'source_fragment_id': 'F2'}
    fragment = {'fragment_id': 'F2', 'source_id': 'S1'}
    for col, oid, payload in [('source_documents', 'S1', source), ('claims', 'C2', claim),
                              ('evidence_steps', 'E2', evidence), ('source_fragments', 'F2', fragment)]:
        records.append({'collection': col, 'object_id': oid, 'payload': payload,
            'revision': payload.get('revision', 1), 'content_sha256': semantic_record_sha(payload),
            'retired': False})
    payloads = {(r['collection'], r['object_id']): r['payload'] for r in records}
    view_record = next(r for r in records if (r['collection'], r['object_id']) ==
                       ('canonical_viewpoints', ids['canonical_viewpoints']))
    view_record['payload']['summary'] = '人子稱號指向神性。'
    view_record['payload']['summary_basis'] = domain.summary_basis_for_records(payloads, ids['canonical_viewpoints'])
    view_record['content_sha256'] = semantic_record_sha(view_record['payload'])
    refs = deepcopy(bundle['references'])
    refs['claims']['C2'] = 'c2'
    refs['evidence_steps']['E2'] = 'e2'
    refs['source_fragments']['F2'] = 'f2'
    refs['source_documents'] = {'S1': 's1'}
    policy = bundle['claim_input']['policy']
    target_input = compile_input([claim], policy, claim_refs={'C2': 'c2'})
    complete = seal({**{k: deepcopy(v) for k, v in bundle.items() if k != 'artifact_sha256'},
        'records': records, 'references': refs, 'claim_input': target_input,
        'material': {'claims': [
            {'claim_ref': 'c1', 'target': False, 'support_loaded': True},
            {'claim_ref': 'c2', 'target': True, 'support_loaded': True}]}})
    req = request(action_id='incremental-C2', creation_slots={'viewpoint_claim_links': 1})
    req = req.model_copy(update={'input': {**req.input, 'recall_profile': STATEMENT_SCOPE_PROFILE}})
    raw = {'component_dispositions': [{'component_ref': 'k1', 'disposition': 'connected',
        'target_ref': 'v1', 'link_ref': 'nl1', 'reason': '同一觀點的新證據。'}],
        'changes': [change('nl1', viewpoint_id='v1', claim_id='c2', link_type='extends')]}
    candidate = domain.compile_candidate(req, complete, raw)
    assert candidate['summary_material_pending'] == []
    view_write = next(row for row in candidate['writes'] if row['collection'] == 'canonical_viewpoints')
    assert view_write['payload']['summary'] == view_record['payload']['summary']
    assert len(view_write['payload']['summary_basis']['member_link_ids']) == 2
    assert 'v1' in candidate['review_sets']['objects']
    approval = domain.validate_review(req, complete, candidate, passing(candidate))
    assert approval['approved']
    package = domain.finalize_package(req, complete, candidate, approval, provenance())
    domain.validate_summary_afterimage(req, complete, candidate, package)
    withdrawn = domain.compile_candidate(req, complete, {
        'discard_refs': ['nl1'],
        'component_dispositions': [{'component_ref': 'k1', 'disposition': 'background',
            'reason': '撤回本輪擬新增連接。'}]}, previous=candidate)
    assert withdrawn['writes'] == []
    assert withdrawn['summaries'] == []
