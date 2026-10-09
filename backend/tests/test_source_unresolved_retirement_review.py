"""Unresolved input is reviewable, never approval or a new retry budget."""
from copy import deepcopy

import pytest

from backend.pipeline import source_reconciliation as c
from backend.pipeline import source_reconciliation_runner as r
from backend.pipeline import claim_issue_management as m
from backend.tests.test_source_reconciliation import scenario
from backend.tests.test_claim_issue_management import issue_setup, materials, retain_proposal


@pytest.fixture
def pending(issue_setup, tmp_path):
    _, frozen, issue = materials(issue_setup, tmp_path)
    frozen['claim_issue_context']['scope_amendment'] = {'fixture': 'exact owner direction'}
    frozen['sql_scope'] = {'read_pins': [
        {'collection': 'claims', 'object_id': 'OLD', 'pin': [2, 'saved-history', True]}]}
    cv = 'canonical_viewpoints/CV-test'
    vr = 'viewpoint_revisions/CVR-test'
    frozen['before_records'] += c.rows({cv: {'viewpoint_id': 'CV-test', 'current_revision_id': 'CVR-test'},
                                       vr: {'viewpoint_revision_id': 'CVR-test', 'viewpoint_id': 'CV-test'}})
    frozen['inventory']['affected_keys'] += [cv, vr]
    proposal = retain_proposal(frozen, issue)
    for d in proposal.dispositions:
        if d.record_key in {cv, vr}: d.action = 'unresolved'
    for i in proposal.cvp_impacts: i.action = 'needs_direction'
    return frozen, proposal


def test_review_preserves_unresolved_and_exposes_retirement_without_identity_mapping(pending):
    frozen, proposal = pending
    packet = r.initial_review_base(frozen, proposal)
    assert packet['proposal'] == proposal.model_dump()
    receipt = packet['unresolved_proposal_review']
    assert not receipt['write_authority']
    assert receipt['retired_current_sql_pins'] == frozen['sql_scope']['read_pins']
    assert 'OLD' not in {row['object_id'] for row in packet['before_records']}
    # Even an erroneous all-approved reviewer cannot commit an unresolved draft.
    answer = {'proposal_sha256': packet['proposal_sha256'], 'verdicts': [
        {'record_key': d.record_key, 'approved': True, 'problems': [], 'reason': 'fixture'}
        for d in proposal.dispositions]}
    assert not c.validate_review(packet, answer)
    with pytest.raises(ValueError, match='unresolved'):
        c.validate_proposal(frozen, proposal)


@pytest.mark.parametrize('change', ['no_scope', 'no_retired_pin', 'claim_unresolved', 'unknown', 'write_unresolved'])
def test_retirement_review_cannot_expand_scope_or_bypass_other_gates(pending, change):
    frozen, proposal = deepcopy(pending)
    if change == 'no_scope': frozen['claim_issue_context'].pop('scope_amendment')
    elif change == 'no_retired_pin': frozen['sql_scope']['read_pins'][0]['pin'][2] = False
    elif change == 'claim_unresolved':
        next(d for d in proposal.dispositions if d.record_key.startswith('claims/')).action = 'unresolved'
    elif change == 'unknown': frozen['inventory']['unknown_keys'] = ['foreign/X']
    else:
        proposal.records.append(c.Row(collection='canonical_viewpoints', object_id='CV-test',
                                      payload={'viewpoint_id': 'CV-test', 'current_revision_id': 'foreign'}))
    with pytest.raises(ValueError): r.initial_review_base(frozen, proposal)


def test_review_only_does_not_skip_graph_or_exact_own_source_checks(pending):
    frozen, proposal = pending
    before = c.index(frozen['before_records'])
    before['claims/CL-test']['evidence_step_ids'] = ['missing']
    frozen['before_records'] = c.rows(before)
    with pytest.raises(ValueError, match='chain'):
        r.initial_review_base(frozen, proposal)


def test_final_review_has_no_review_only_escape(pending):
    frozen, proposal = pending
    stage = object.__new__(r.Runner)
    with pytest.raises(ValueError, match='unresolved'):
        stage.review_input(frozen, proposal.model_dump(), proposal, 'final_review')


def test_saved_unresolved_proposal_uses_existing_single_correction_and_no_extra_review(pending, tmp_path, monkeypatch):
    frozen, proposal = pending
    fixed = deepcopy(proposal)
    for d in fixed.dispositions: d.action = 'retain'
    for i in fixed.cvp_impacts: i.action = 'retain'
    cache = {'proposal': {'response': proposal.model_dump()}}
    calls = []
    class Runner(r.Runner):
        def guard(self, *args, **kwargs): pass
        def call(self, stage, packet):
            if stage in cache: return cache[stage]
            calls.append(stage)
            if stage == 'correction':
                assert not packet['another_correction_allowed']
                assert packet['unresolved_proposal_review']['retired_current_sql_pins']
                response = fixed.model_dump()
            else:
                response = {'proposal_sha256': packet['proposal_sha256'], 'verdicts': [
                    {'record_key': d['record_key'], 'approved': stage == 'final_review',
                     'problems': [] if stage == 'final_review' else ['initial unresolved'], 'reason': 'fixture'}
                    for d in packet['proposal']['dispositions']]}
            cache[stage] = {'response': response}
            return cache[stage]
    runner = Runner(tmp_path, None)
    runner.root = tmp_path
    # Physical/model identity is covered by the existing real transport tests;
    # this unit checks only the orchestration budget and strict final boundary.
    monkeypatch.setattr(r, 'effective_proposal', lambda f, response: m.Proposal.model_validate(response))
    result, raw = runner.reviewed(frozen)
    assert result == fixed
    assert calls == ['initial_review', 'correction', 'final_review']
    result, raw = runner.reviewed(frozen)
    assert calls == ['initial_review', 'correction', 'final_review']
