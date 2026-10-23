from copy import deepcopy
import json

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha, _normalize_records
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline import viewpoint_historical_review_bridge as bridge
from backend.pipeline.legacy_candidate_reconciliation import scoped_applied_adjudication
from backend.tests.test_viewpoint_historical_review_bridge import inputs, write_binding


def fixture(tmp_path, monkeypatch):
    args = inputs(tmp_path)
    package = json.loads((tmp_path / 'input.json').read_text())
    final = deepcopy(package)
    final['claims'][0]['topic_terms'] = ['merged topic']
    final['consensus_application'] = {'merged_claim_ids': {'C2': 'C1'}, 'applied_claim_ids': ['C2']}
    args['legacy_reviewed_candidate_binding'] = write_binding(tmp_path / 'final.json', final)
    for row in args['versions']:
        row['payload']['topic_terms'] = ['merged topic']
        row['content_sha256'] = record_content_sha(row['payload'])
    args['versions'][0]['source_sha256'] = sha256_json(final)
    plan = json.loads((tmp_path / 'plan.json').read_text())
    plan['changed_records'][0].update(before_sha256=args['versions'][0]['content_sha256'],
                                      after_sha256=args['versions'][1]['content_sha256'])
    plan.pop('artifact_sha256'); plan['artifact_sha256'] = sha256_json(plan)
    binding = write_binding(tmp_path / 'plan.json', plan)
    args['versions'][1]['metadata']['source_plan_file_sha256'] = binding['file_sha256']
    bundle = {'reason': None, 'scoped_adjudication_structurally_validated': True,
              'validated_adjudication_claim_ids': ['C2'],
              'review_sha256': args['review_binding']['file_sha256'],
              'package_sha256': args['review_input_binding']['file_sha256'],
              'original_claims': _normalize_records(package)[0]['claims'],
              'review_rows': {'C2': {'decision': 'changes_suggested'}},
              'adjudication_results': {'C2': {'status': 'auto_applied',
                 'openai': {'patch': {'superseded_by_claim_id': 'C1'}}}},
              'adjudication_path': '/proof/adjudication.json', 'adjudication_sha256': 'sha'}
    from backend.pipeline import legacy_candidate_reconciliation as inspector
    from backend.pipeline import legacy_auto_applied_review_reconciliation as replay
    monkeypatch.setattr(inspector, 'inspect_legacy_bundle', lambda p, **kw: bundle)
    monkeypatch.setattr(replay, 'verify_historical_replay', lambda p, **kw: {'historical_replay_sha256': sha256_json(final)})
    return args, bundle


def test_passing_merge_recipient_then_coordinate_repair(tmp_path, monkeypatch):
    args, _ = fixture(tmp_path, monkeypatch)
    proof = bridge.verify_historical_review_bridge(**args)
    assert proof['status'] == 'verified'
    assert proof['legacy_merge_authority']['merged_claim_ids'] == ['C2']
    assert proof['legacy_merge_authority']['unrelated_human_findings_released'] is False
    assert len(proof['coordinate_repairs']) == 1
    assert proof['master_data_mutations'] == proof['model_calls_executed'] == 0


@pytest.mark.parametrize('case', ['source', 'replay_scope', 'human_donor', 'sample_donor', 'pending',
                                 'wrong_target', 'import_sha', 'statement', 'no_merge', 'current_drift',
                                 'evidence_drift', 'quote_drift'])
def test_no_authority_or_content_drift_can_hide_as_merge(tmp_path, monkeypatch, case):
    args, bundle = fixture(tmp_path, monkeypatch)
    if case == 'source': bundle['package_sha256'] = 'wrong'
    elif case == 'replay_scope': bundle['validated_adjudication_claim_ids'] = ['other']
    elif case == 'human_donor': bundle['review_rows']['C2']['human_review_reason'] = 'hold'
    elif case == 'sample_donor': bundle['review_rows']['C2']['spot_check_selected'] = True
    elif case == 'pending': bundle['adjudication_results']['C2']['status'] = 'pending'
    elif case == 'wrong_target': bundle['adjudication_results']['C2']['openai']['patch']['superseded_by_claim_id'] = 'other'
    elif case == 'import_sha': args['versions'][0]['source_sha256'] = 'wrong'
    elif case == 'statement': bundle['original_claims']['C1']['statement'] = 'different words'
    elif case == 'no_merge': args.pop('legacy_reviewed_candidate_binding')
    elif case == 'current_drift':
        args['current_claim']['statement'] = 'unreviewed'
        args['versions'][-1]['content_sha256'] = record_content_sha(args['current_claim'])
    elif case == 'evidence_drift': args['current_evidence']['E1']['statement'] = 'unreviewed'
    else: args['current_fragments']['F1']['verbatim_excerpt'] = 'other'
    with pytest.raises(CvpProductionBlocked): bridge.verify_historical_review_bridge(**args)


def test_scoped_applied_selection_preserves_unrelated_hold():
    response = {'scope_confirmation': 'source_fidelity_only_no_theological_critique',
                'adjudications': [{'claim_id': 'merge'}, {'claim_id': 'held', 'patch': {'bad': 'anchor'}}]}
    adjudication = {'openai_adjudication': response, 'results': [
        {'claim_id': 'merge', 'status': 'auto_applied', 'openai': {'decision': 'accept'}},
        {'claim_id': 'held', 'status': 'human_confirmation_required', 'openai': {'decision': 'accept'}}]}
    selected, reviews = scoped_applied_adjudication(adjudication, [{'claim_id': 'merge'}, {'claim_id': 'held'}], {'merge'})
    assert selected['adjudications'] == [{'claim_id': 'merge'}]
    assert reviews == [{'claim_id': 'merge'}]
    assert len(response['adjudications']) == 2
    for ids in [set(), {'held'}, {'missing'}, {'merge', 'held'}]:
        with pytest.raises(ValueError): scoped_applied_adjudication(adjudication, [], ids)


def test_pinned_source_reader_preserves_old_coordinates(tmp_path):
    from backend.pipeline import legacy_auto_applied_review_reconciliation as replay
    source = tmp_path / 'source.md'
    source.write_text('# heading\n\nfirst\n\nsecond')
    payload, raw, path = replay.historical_markdown_source({'source_id': 'S', 'source_path': str(source)})
    assert [r['text'] for r in payload['script']] == ['# heading', 'first', 'second']
    assert [r['index'] for r in payload['script']] == [1, 2, 3]
    assert raw == source.read_bytes() and path == source
    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as patch:
        patch.setattr(replay, 'HISTORICAL_SOURCE_CODE_SHA256', 'wrong')
        with pytest.raises(ValueError, match='code SHA'): replay.historical_markdown_source({'source_id': 'S', 'source_path': str(source)})
