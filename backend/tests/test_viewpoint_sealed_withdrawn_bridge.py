"""A historical withdrawal is authenticated authority, not a pass waiver."""
from copy import deepcopy

import pytest

from backend.pipeline import viewpoint_historical_review_bridge as bridge
from backend.pipeline import legacy_candidate_reconciliation as legacy
from backend.pipeline import legacy_auto_applied_review_reconciliation as replay_module
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


@pytest.mark.parametrize('mutation', [None, 'missing_replay', 'wrong_contract', 'replay_sha',
    'bundle_failure', 'unvalidated', 'review_sha', 'input_sha', 'suggestion', 'human_target',
    'pass', 'hold', 'sample', 'human_routing', 'result_hold', 'no_reject', 'no_withdraw',
    'applied', 'claim_drift', 'graph_drift', 'missing_graph', 'initial_sha', 'initial_kind',
    'initial_revision', 'history_gap', 'history_semantic_drift', 'physical_source',
    'source_coverage', 'source_pin'])
def test_withdrawn_bridge_requires_full_replay_unchanged_graph_and_initial_apply(tmp_path, monkeypatch, mutation):
    cid = 'C'
    claim = {'claim_id': cid, 'statement': '原主張', 'revision': 1, 'evidence_step_ids': ['E']}
    evidence = {'evidence_step_id': 'E', 'source_fragment_id': 'F', 'produced_claim_ids': [cid]}
    fragment = {'fragment_id': 'F', 'verbatim_excerpt': '原文', 'source_id': 'S'}
    final = {'claims': {cid: deepcopy(claim)}, 'evidence_steps': {'E': deepcopy(evidence)},
             'source_fragments': {'F': deepcopy(fragment)}}
    original = deepcopy(final)
    package = {'final': True}
    resolution = {'target_review_status': 'ai_consensus_reviewed',
                  'independent_review_decision': 'changes_suggested', 'adjudication_status': 'withdrawn'}
    row = {'decision': 'changes_suggested'}
    result = {'status': 'withdrawn', 'openai': {'decision': 'reject'},
              'claude_reconsideration': {'decision': 'withdraw'}}
    bundle = {'reason': None, 'adjudication_structurally_validated': True,
              'review_sha256': 'review', 'package_sha256': 'input',
              'review_rows': {cid: row}, 'adjudication_results': {cid: result},
              'review_transcript_paths': {'S': '/source'}, 'review_transcript_sha256': {'S': 'rendered'},
              'review_source_binding_modes': {'S': 'rendered_json_projection'},
              'adjudication_path': '/adjudication', 'adjudication_sha256': 'adjudication'}
    replay = {'replay_contract': 'sealed_batched_exact_full_package_v1',
              'historical_replay_sha256': sha256_json(package)}
    history = [{'revision': 1, 'payload': deepcopy(claim), 'source_kind': 'knowledge_package',
                'source_sha256': replay['historical_replay_sha256']}]
    item = {'path': str(tmp_path / 'final.json'), 'payload': package,
            'review_input_payload': {'input': True, 'source_documents': [
                {'source_id': 'S', 'source_file_sha256': 'physical'}]},
            'application': {'applied_claim_ids': []},
            'review_payload': {'source': {}}}
    if mutation == 'missing_replay': replay = None
    if mutation == 'wrong_contract': replay['replay_contract'] = 'unsealed'
    if mutation == 'replay_sha': replay['historical_replay_sha256'] = 'wrong'
    if mutation == 'bundle_failure': bundle['reason'] = 'invalid'
    if mutation == 'unvalidated': bundle['adjudication_structurally_validated'] = False
    if mutation == 'review_sha': bundle['review_sha256'] = 'wrong'
    if mutation == 'input_sha': bundle['package_sha256'] = 'wrong'
    if mutation == 'suggestion': resolution['adjudication_status'] = 'unresolved'
    if mutation == 'human_target': resolution['target_review_status'] = 'human_review_required'
    if mutation == 'pass': resolution['independent_review_decision'] = 'pass'
    if mutation == 'hold': row['human_review_reason'] = 'human required'
    if mutation == 'sample': row['spot_check_selected'] = True
    if mutation == 'human_routing': row['routing_status'] = 'human_spot_check'
    if mutation == 'result_hold': result['human_review_reason'] = 'human required'
    if mutation == 'no_reject': result['openai']['decision'] = 'accept'
    if mutation == 'no_withdraw': result['claude_reconsideration']['decision'] = 'maintain'
    if mutation == 'applied': item['application']['applied_claim_ids'] = [cid]
    if mutation == 'claim_drift': original['claims'][cid]['statement'] = '另一主張'
    if mutation == 'graph_drift': original['evidence_steps']['E']['produced_claim_ids'].append('OTHER')
    if mutation == 'missing_graph': del original['source_fragments']['F']
    if mutation == 'initial_sha': history[0]['source_sha256'] = 'wrong'
    if mutation == 'initial_kind': history[0]['source_kind'] = 'unrelated'
    if mutation == 'initial_revision': history[0]['revision'] = 2
    if mutation == 'history_gap':
        claim['revision'] = 3
        history.append(history[0] | {'revision': 3})
    if mutation == 'history_semantic_drift': history[0]['payload']['statement'] = '另一主張'
    if mutation == 'source_coverage': bundle['review_transcript_paths']['OTHER'] = '/other'
    if mutation == 'source_pin': item['review_input_payload']['source_documents'][0]['source_file_sha256'] = None
    monkeypatch.setattr(legacy, 'inspect_legacy_bundle', lambda *a, **k: bundle)
    monkeypatch.setattr(replay_module, 'verify_historical_replay', lambda *a, **k: replay)
    monkeypatch.setattr(bridge, '_normalize_records', lambda p: (final if p is package else original, {}))
    sources = []
    def own_source(f, c, r):
        sources.append(r)
        if mutation == 'physical_source': raise ValueError('physical SHA drift')
    monkeypatch.setattr(bridge, 'verify_fragment_own_source', own_source)
    invoked = []
    def modern(**kwargs):
        invoked.append(kwargs)
        return {'artifact_sha256': 'old', 'independent_review_decision': 'changes_suggested',
                'adjudication_status': 'withdrawn', 'master_data_mutations': 0, 'model_calls_executed': 0}
    monkeypatch.setattr(bridge, '_sealed_adjudicated_bridge', modern)
    kwargs = dict(claim_id=cid, current_claim=claim, item=item, resolution=resolution, versions=history,
        current_evidence={'E': evidence}, current_fragments={'F': fragment},
        review_binding={'file_sha256': 'review'}, review_input_binding={'file_sha256': 'input'},
        sealed_lineage_binding={'file_sha256': 'lineage'})
    if mutation:
        with pytest.raises(bridge.CvpProductionBlocked): bridge._sealed_withdrawn_bridge(**kwargs)
        assert invoked == []
    else:
        proof = bridge._sealed_withdrawn_bridge(**kwargs)
        assert len(invoked) == 1
        assert proof['authority_kind'] == 'exact_replayed_applied_withdrawal_not_initial_review_pass'
        assert proof['independent_review_decision'] == 'changes_suggested'
        assert proof['adjudication_status'] == 'withdrawn'
        assert proof['master_data_mutations'] == proof['model_calls_executed'] == 0
        assert proof['artifact_sha256'] == sha256_json({k:v for k,v in proof.items() if k != 'artifact_sha256'})
        assert sources[0]['source']['transcript_paths'] == {'S': '/source'}
        assert sources[0]['source']['transcript_sha256'] == {'S': 'physical'}
        assert proof['reviewed_source_bindings']['S']['review_projection_sha256'] == 'rendered'
        assert item['review_payload']['source'] == {}  # original aggregate never modified


@pytest.mark.parametrize('human_hold', [False, True])
def test_sealed_withdrawal_dispatch_never_uses_initial_pass_or_overrides_hold(monkeypatch, human_hold):
    from backend.pipeline import viewpoint_source_attestation_runner as runner
    review = {'source': {'package_sha256': 'input'}, 'reviewed_claims': [],
              'reviewer': {'provider': 'anthropic_batched'}}
    package = {'original': True}
    item = {'review_payload': review, 'review_input_payload': package,
            'application': {'review_resolutions': [{'claim_id': 'C', 'adjudication_status': 'withdrawn'}]}}
    lineage = {'claims': [{'claim_id': 'C'}]}
    monkeypatch.setattr(bridge, '_read_binding', lambda b: {
        'review': review, 'input': package, 'lineage': lineage}[b['file_sha256']])
    monkeypatch.setattr(bridge, '_normalize_claim_layer', lambda p: {'candidate_claims': []})
    monkeypatch.setattr(runner, '_validated_lineage_inputs', lambda **kw: {'C': item})
    calls = []
    def withdrawn(**kwargs):
        calls.append(kwargs)
        return {'withdrawn_proof': True}
    monkeypatch.setattr(bridge, '_sealed_withdrawn_bridge', withdrawn)
    kwargs = dict(claim_id='C', current_claim={}, versions=[], current_evidence={}, current_fragments={},
        review_binding={'file_sha256': 'review'}, review_input_binding={'file_sha256': 'input'},
        sealed_lineage_binding={'path': '/lineage.json', 'file_sha256': 'lineage'},
        human_closure_binding={'hold': True} if human_hold else None)
    if human_hold:
        with pytest.raises(bridge.CvpProductionBlocked, match='human hold'):
            bridge.verify_historical_review_bridge(**kwargs)
        assert not calls
    else:
        assert bridge.verify_historical_review_bridge(**kwargs) == {'withdrawn_proof': True}
        assert len(calls) == 1
