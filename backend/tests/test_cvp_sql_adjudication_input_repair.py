from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.postgres_store import ChangeSetConflict
from backend.pipeline import cvp_sql_adjudication_input_repair as repair
from backend.pipeline import viewpoint_audit_source_adjudication as adjudication
from backend.pipeline.viewpoint_adjudication_history import validated_history, target_identity
from backend.pipeline.viewpoint_audit_owner_resolution import binding
from backend.pipeline.viewpoint_resolution_runtime import read_artifact, write_immutable, subscription_client
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.tests.test_cvp_sql_transaction_postgres import task, apply
from backend.tests.test_claim_evidence_reciprocity_postgres_integration import _temporary_postgres, postgres_store


def seal(body):
    return {**body, 'artifact_sha256': sha256_json(body)}


def fixture(tmp_path, monkeypatch, *, campaign='campaign', run_id='run'):
    original = (tmp_path/'original').resolve()
    source = tmp_path/'source'; source.write_text('教授原話。')
    audit = tmp_path/'audit.json'; write_immutable(audit, {'layers': {}})
    old = seal({'schema_version': 'wang_audit_source_adjudication_packet_v1',
                'audit_binding': binding(audit), 'items': [{
                    'item_id': '4:v:c', 'viewpoint': {'viewpoint_id': 'v', 'current_revision_id': 'r'},
                    'claim': {'claim_id': 'c', 'revision': 1}, 'connections': [], 'audit_finding': {'reason': '範圍'},
                    'sources': [{'source_id': 's', **binding(source), 'paragraphs': [
                        {'paragraph_key': 'S0001', 'text': '教授原話。'}]}]}]})
    finding = {'item_id': '4:v:c', 'verdict': 'unresolved', 'source_id': 's',
               'paragraph_key': 'S0001', 'exact_quote': '教授原話。', 'reason': 'missing actual CVR scope'}
    adapter = StructuredJsonReviewerAdapter(client=subscription_client('codex', 'gpt-6.1-sol', 'high'),
        prompt=adjudication.PROMPT, response_model=adjudication.Findings,
        schema_name='wang_audit_source_findings_v1', compact_payload=True)
    response = {'findings': [finding]}
    raw = seal({'model_id': adapter.model_id, 'backend': adapter.backend,
                'prompt_sha256': adapter.prompt_sha256, 'generation_config_sha256': adapter.generation_config_sha256,
                'request_payload_sha256': sha256_json(old), 'response_sha256': sha256_json(response), 'response': response})
    handoff = seal({'packet_sha256': old['artifact_sha256'], 'maximum_model_calls': 1, 'master_data_mutations': 0,
                    'actions': [{'item_id': finding['item_id'], 'verdict': 'unresolved', 'gpt': finding}]})
    for name, value in [('packet.json', old), ('raw-gpt.json', raw), ('gpt-final-handoff.json', handoff)]:
        write_immutable(original/name, value)
    current = deepcopy(old); current.pop('artifact_sha256')
    current['schema_version'] = 'wang_audit_source_adjudication_packet_v2'
    current['items'][0]['viewpoint_revision'] = {'viewpoint_id': 'v', 'viewpoint_revision_id': 'r',
        'core_proposition': '現行命題。', 'scope': {}, 'proposition_signature': {}}
    current = seal(current)
    monkeypatch.setattr(adjudication, 'build_packet', lambda *args: current)
    direction = repair.make_direction(object(), campaign, run_id, original, '修復並重新測試，跑起來。')
    auth = tmp_path/'auth.json'; write_immutable(auth, direction)
    corrected = original/'input-repair-once'
    write_immutable(corrected/'packet.json', current)
    write_immutable(corrected/'owner-input-repair-proof.json', seal({'schema_version': repair.PROOF,
        'authorization': binding(auth), 'bindings': [binding(auth), *direction['original_bindings'], direction['audit_binding']]}))
    return original, corrected, auth, old, current


def test_explicit_complete_input_direction_keeps_original_unresolved(tmp_path, monkeypatch):
    original, corrected, auth, old, current = fixture(tmp_path, monkeypatch)
    assert repair.verify_proof(corrected/'owner-input-repair-proof.json')[0] == read_artifact(auth)
    assert validated_history(original)[1]['4:v:c']['verdict'] == 'unresolved'
    assert target_identity(old['items'][0]) == target_identity(current['items'][0])
    assert repair.effective_roots([original, corrected]) == [corrected]
    assert read_artifact(original/'raw-gpt.json')['response']['findings'][0]['verdict'] == 'unresolved'


@pytest.mark.parametrize('fault', ['claim', 'population', 'source', 'link', 'audit', 'foreign_cvr', 'missing_cvr', 'upheld'])
def test_no_content_drift_or_decided_retry_under_input_repair(fault, tmp_path, monkeypatch):
    _, _, _, old, current = fixture(tmp_path, monkeypatch)
    findings = {'4:v:c': {'verdict': 'unresolved'}}
    if fault == 'claim': current['items'][0]['claim']['revision'] = 2
    if fault == 'population': current['items'].append(deepcopy(current['items'][0]))
    if fault == 'source': current['items'][0]['sources'][0]['file_sha256'] = 'changed'
    if fault == 'link': current['items'][0]['connections'] = [{'new': 'link'}]
    if fault == 'audit': current['audit_binding'] = {'path': 'foreign'}
    if fault == 'foreign_cvr': current['items'][0]['viewpoint_revision']['viewpoint_id'] = 'foreign'
    if fault == 'missing_cvr': current['items'][0].pop('viewpoint_revision')
    if fault == 'upheld': findings['4:v:c']['verdict'] = 'audit_objection_upheld'
    with pytest.raises(ValueError): repair.verify_delta(old, current, findings)


def test_durable_sql_once_appends_without_reset_and_rejects_second_root(postgres_store, tmp_path, monkeypatch):
    plan, context = task(postgres_store); apply(postgres_store, plan, context)
    saved = postgres_store.get_cvp_group(context.run_id)
    original, corrected, auth, old, current = fixture(tmp_path, monkeypatch,
        campaign=saved['campaign_sha256'], run_id=context.run_id)
    targets = [target_identity(i) for i in old['items']]
    original_pin = binding(original/'packet.json')
    postgres_store.register_adjudication(context.run_id, targets=targets, root=original, packet_binding=original_pin)
    args = dict(targets=targets, original_root=original, original_packet=original_pin,
                root=corrected, packet_binding=binding(corrected/'packet.json'), authorization=binding(auth))
    for _ in range(2): postgres_store.register_adjudication_input_repair(context.run_id, **args)
    prior = postgres_store.get_cvp_group(context.run_id)['semantic_adjudications'][targets[0]]
    assert prior['root'] == str(original) and prior['packet'] == original_pin
    assert prior['additional_roots'] == [str(corrected)]
    with pytest.raises((ValueError, FileNotFoundError)):
        postgres_store.register_adjudication_input_repair(context.run_id, **{**args, 'root': original/'another'})
    with pytest.raises(ChangeSetConflict):
        postgres_store.register_adjudication_input_repair(context.run_id, **{**args, 'targets': []})
    with pytest.raises(ChangeSetConflict):
        postgres_store.register_adjudication_input_repair(context.run_id, **{**args, 'authorization': {'path': 'foreign'}})
    assert len(postgres_store.get_change_history(plan.change_set_id)['operations']) == 1


def test_execute_calls_only_complete_packet_once_and_preserves_old_raw(tmp_path, monkeypatch):
    original, corrected, auth, old, current = fixture(tmp_path, monkeypatch)
    before = (original/'raw-gpt.json').read_bytes()
    from backend.tests.test_viewpoint_deferred_source_check import Adapter
    answer = {'findings': [{'item_id': '4:v:c', 'verdict': 'audit_objection_rejected', 'source_id': 's',
        'paragraph_key': 'S0001', 'exact_quote': '教授原話。', 'reason': 'actual complete scope judged'}]}
    adapter = Adapter(answer)
    actual_run = adjudication.run
    monkeypatch.setattr(adjudication, 'run', lambda *args, **kwargs: actual_run(*args, adapters=[adapter], **kwargs))
    monkeypatch.setattr('backend.pipeline.viewpoint_s201129_source_continuation.canonical_runtime',
        lambda: {'identity': {'cli': {'path': 'fixture-only-no-executable-call'}}})
    store = SimpleNamespace(get_cvp_group=lambda _: {'campaign_sha256': 'campaign', 'checkpoint': 'applied'},
                            register_adjudication_input_repair=lambda *args, **kwargs: None)
    for _ in range(2): repair.execute(store, 'run', auth)
    assert adapter.calls == 1 and adapter.payloads == [current]
    assert (original/'raw-gpt.json').read_bytes() == before
    assert read_artifact(corrected/'gpt-final-handoff.json')['actions'][0]['verdict'] == 'audit_objection_rejected'


def test_no_answer_intent_still_blocks_same_authorized_input(tmp_path, monkeypatch):
    original, corrected, auth, old, current = fixture(tmp_path, monkeypatch)
    write_immutable(corrected/'raw-gpt.call-intent.json', {'fixture': 'previous unknown call'})
    from backend.tests.test_viewpoint_deferred_source_check import Adapter
    adapter = Adapter({'findings': []})
    actual_run = adjudication.run
    monkeypatch.setattr(adjudication, 'run', lambda *args, **kwargs: actual_run(*args, adapters=[adapter], **kwargs))
    monkeypatch.setattr('backend.pipeline.viewpoint_s201129_source_continuation.canonical_runtime',
        lambda: {'identity': {'cli': {'path': 'fixture-no-call'}}})
    store = SimpleNamespace(get_cvp_group=lambda _: {'campaign_sha256': 'campaign', 'checkpoint': 'applied'},
                            register_adjudication_input_repair=lambda *args, **kwargs: None)
    with pytest.raises(ValueError): repair.execute(store, 'run', auth)
    assert adapter.calls == 0 and not (corrected/'raw-gpt.json').exists()


@pytest.mark.parametrize('fault', ['source', 'raw', 'auth', 'second_budget', 'approval'])
def test_proof_keeps_original_physical_bindings_and_no_expanded_authority(fault, tmp_path, monkeypatch):
    original, corrected, auth, _, _ = fixture(tmp_path, monkeypatch)
    if fault == 'source': (tmp_path/'source').write_text('changed')
    elif fault == 'raw': (original/'raw-gpt.json').write_text('{}')
    else:
        value = read_artifact(auth)
        if fault == 'second_budget': value['maximum_additional_calls'] = 2
        elif fault == 'approval': value['human_content_approval'] = True
        else: value['authorization_quote'] = ''
        auth.write_text(__import__('json').dumps(seal({k: v for k, v in value.items() if k != 'artifact_sha256'})))
    with pytest.raises(ValueError): repair.verify_proof(corrected/'owner-input-repair-proof.json')
