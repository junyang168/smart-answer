from copy import deepcopy
import json
from pathlib import Path

import pytest
from backend.api.canonical_repository.knowledge_models import EvidenceStepRecord
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_evidence_hold_trace import trace_evidence, run_trace


def fixture(tmp_path):
    step = EvidenceStepRecord.model_validate({'evidence_step_id': 'e', 'statement': '祷告',
        'step_type': 'qualification', 'speaker': 'professor', 'stance': 'asserted',
        'support_eligibility': 'withheld_unreviewed', 'produced_claim_ids': ['c']}).model_dump(mode='json')
    package = {'package_id': 'p', 'evidence_steps': [step]}
    path = tmp_path / 'reviewed' / 'p.reviewed-candidate.json'
    path.parent.mkdir(); path.write_text(json.dumps(package))
    current = {'payload': step, 'revision': 1, 'content_sha256': record_content_sha(step), 'retired': False}
    version = {'revision': 1, 'payload': step, 'content_sha256': current['content_sha256'],
        'change_set_id': 'k', 'source_kind': 'knowledge_package', 'source_sha256': sha256_json(package),
        'status': 'applied', 'metadata': {'input_path': str(path)}}
    return current, [version], []


def test_applied_hold_traced_not_approved(tmp_path):
    trace = trace_evidence('e', 'c', *fixture(tmp_path))
    assert trace['status'] == 'held_in_applied_package_no_evidence_review_event'
    assert not trace['automatic_release_allowed']
    assert trace['applied_inputs'][0]['support_eligibility'] == 'withheld_unreviewed'


@pytest.mark.parametrize('damage', ['retired', 'foreign', 'gap', 'unapplied', 'altered', 'current', 'package', 'missing'])
def test_false_provenance_stops(tmp_path, damage):
    current, versions, events = deepcopy(fixture(tmp_path))
    if damage == 'retired': current['retired'] = True
    elif damage == 'foreign': current['payload']['evidence_step_id'] = 'foreign'
    elif damage == 'gap': versions[0]['revision'] = 2
    elif damage == 'unapplied': versions[0]['status'] = 'dry_run'
    elif damage == 'altered': versions[0]['content_sha256'] = 'forged'
    elif damage == 'current': current['content_sha256'] = 'forged'
    elif damage == 'package': versions[0]['source_sha256'] = 'forged'
    elif damage == 'missing': versions.clear()
    with pytest.raises(ValueError): trace_evidence('e', 'c', current, versions, events)


def test_claim_pass_does_not_release_evidence(tmp_path):
    current, versions, events = fixture(tmp_path)
    input_path = Path(versions[0]['metadata']['input_path'])
    import hashlib
    review = {'source': {'package_path': str(input_path),
        'package_sha256': hashlib.sha256(input_path.read_bytes()).hexdigest()},
        'claim_reviews': [{'claim_id': 'c', 'decision': 'pass', 'issues': []}]}
    root = tmp_path / 'reviews'; root.mkdir()
    (root / 'p.independent-review.json').write_text(json.dumps(review))
    trace = trace_evidence('e', 'c', current, versions, events)
    assert trace['related_claim_reviews'][0]['claim_review']['decision'] == 'pass'
    assert not trace['related_claim_reviews'][0]['evidence_eligibility_release_proven']
    assert not trace['automatic_release_allowed']
    review['source']['package_sha256'] = 'forged'
    (root / 'p.independent-review.json').write_text(json.dumps(review))
    trace = trace_evidence('e', 'c', current, versions, events)
    assert trace['related_claim_reviews'][0]['historical_claim_review_qualification'] == 'unverified_input_physical_sha_mismatch'
    assert not trace['automatic_release_allowed']


def test_human_event_preserved_not_reinterpreted(tmp_path):
    current, versions, _ = fixture(tmp_path)
    events = [{'review_event_id': 'hold', 'reviewer_kind': 'human', 'decision': 'rejected'}]
    trace = trace_evidence('e', 'c', current, versions, events)
    assert trace['evidence_review_events'] == events
    assert not trace['automatic_release_allowed']


def test_run_is_immutable_read_only_and_repeatable(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_evidence_hold_trace as module
    history = fixture(tmp_path)
    monkeypatch.setattr(module, 'read_history', lambda *args: history)
    monkeypatch.setattr(module, 'build_packet', lambda *args: {'artifact_sha256': 'source', 'items': []})
    packet = {'claims': [{'claim_id': 'c', 'evidence': [{'evidence_step_id': 'e',
        'evidence_statement': '祷告', 'support_eligibility': 'withheld_unreviewed'}]}]}
    disposition = {'claims': [{'claim_id': 'c', 'components': [{'disposition': 'deferred'}]}]}
    for name, body in [('packet', packet), ('disposition', disposition)]:
        (tmp_path / (name + '.json')).write_text(json.dumps(body))
    args = (tmp_path/'packet.json', tmp_path/'disposition.json', tmp_path/'out', object())
    result = run_trace(*args)
    assert result == run_trace(*args)
    assert result['model_calls'] == result['master_data_mutations'] == 0
    assert result['eligibility_attestation_created'] is result['group_complete'] is False


def test_campaign_traces_before_investigating(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_cvp_campaign_runner as campaign
    root = tmp_path/'production/run/batches/b'; root.mkdir(parents=True)
    (root/'batch-disposition-applied.json').write_text(json.dumps(
        {'claims': [{'components': [{'disposition': 'deferred'}]}]}))
    out = tmp_path/'deferred-source-check'; out.mkdir()
    (out/'handoff.json').write_text('{}')
    calls = []; monkeypatch.setattr(campaign, 'command', lambda args: calls.append(args))
    campaign.investigate_deferred(tmp_path)
    assert calls[0][2] == 'backend.pipeline.viewpoint_evidence_hold_trace'
    assert calls[1][2] == 'backend.pipeline.viewpoint_deferred_source_check'
