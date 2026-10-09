"""Retirement transfers to routine management, never a fake pass/retry."""
from copy import deepcopy
import pytest
from backend.pipeline import cvp_membership_management as m
from backend.pipeline import source_reconciliation as c
from backend.tests.test_source_unresolved_retirement_review import pending
from backend.tests.test_source_reconciliation import scenario
from backend.tests.test_claim_issue_management import issue_setup


@pytest.fixture
def retired(pending):
    frozen, proposal = deepcopy(pending)
    states = c.index(frozen['before_records'])
    states['canonical_viewpoints/CV-test'].update(
        identity_status='active',review_status='system_approved',created_from_candidate_id='VIC-old')
    states['viewpoint_revisions/CVR-test']['review_status']='system_approved'
    frozen['before_records']=c.rows(states)
    frozen['sql_scope']['read_pins'].append(
        {'collection':'viewpoint_identity_candidates','object_id':'VIC-old','pin':[2,'retired-proof',True]})
    raw={'artifact_sha256':'saved-correction','response':proposal.model_dump()}
    return frozen,proposal,raw


def test_routine_intake_preserves_unresolved_and_budget(retired):
    frozen,proposal,raw=retired
    before=deepcopy((frozen,proposal,raw))
    handoff=m.retirement_handoff(frozen,proposal,raw)
    assert handoff['waiting_for']=='system' and not handoff['write_authority']
    assert handoff['original_correction_budget_exhausted']
    assert handoff['targets'][0]['retired_creation_candidate_id']=='VIC-old'
    assert (frozen,proposal,raw)==before
    with pytest.raises(ValueError,match='unresolved'):c.validate_proposal(frozen,proposal)


@pytest.mark.parametrize('change',['no_retirement','foreign_retirement','head_hold','revision_hold','wrong_revision','claim_unresolved','unknown','write_unresolved'])
def test_not_a_missing_proof_or_content_escape(retired,change):
    frozen,proposal,raw=retired
    states=c.index(frozen['before_records'])
    if change=='no_retirement':frozen['sql_scope']['read_pins']=[]
    elif change=='foreign_retirement':frozen['sql_scope']['read_pins'][-1]['object_id']='another'
    elif change=='head_hold':states['canonical_viewpoints/CV-test']['review_status']='withheld'
    elif change=='revision_hold':states['viewpoint_revisions/CVR-test']['review_status']='rejected'
    elif change=='wrong_revision':states['viewpoint_revisions/CVR-test']['viewpoint_id']='other'
    elif change=='claim_unresolved':next(d for d in proposal.dispositions if d.record_key.startswith('claims/')).action='unresolved'
    elif change=='unknown':frozen['inventory']['unknown_keys']=['foreign/X']
    else:proposal.records.append(c.Row(collection='canonical_viewpoints',object_id='CV-test',payload=states['canonical_viewpoints/CV-test']))
    frozen['before_records']=c.rows(states)
    try: result=m.retirement_handoff(frozen,proposal,raw)
    except ValueError:return
    assert result is None


def test_no_second_management_identity_for_reason_or_raw_wording(retired):
    frozen,proposal,raw=retired
    first=m.retirement_handoff(frozen,proposal,raw)
    raw['artifact_sha256']='different-report'
    for d in proposal.dispositions:d.reason+=' more words'
    assert m.retirement_handoff(frozen,proposal,raw)['handoff_id']==first['handoff_id']
