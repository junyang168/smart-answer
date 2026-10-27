"""Paragraph ordering reuses real Update/SQL; model answers are synthetic only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import binding
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import (
    SyntheticPorts, corrected, operation_request, passing, rejected, seed_claims,
)
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.fixture
def paragraphs(action):
    store, original, _, _, tmp, _ = action
    source = Path(original.source_bindings['S'].path)
    # The real S 210613 case has 61 paragraphs; slides supplement the same lecture.
    rows = [{'text': '甲是假设，不是断言。'} for _ in range(61)]
    rows[48]['text'] = '聖經裡面講摩西是中保。你們再看提摩太前書第2章第6節。'
    rows[49]['text'] = '這個就是Accusative Absolute的文法。\n\n```\nτὸ μαρτύριον καιροῖς ἰδίοις 自己時候的見證\n```\n\n到時候這事被證明出來？錯！'
    source.write_text(json.dumps({'script': [{'type': 'subtitle', 'text': '中保'}] + rows}, ensure_ascii=False))
    payload = store.get_record_state('source_documents', 'S')['payload']
    payload['source_file_sha256'] = binding(source)['file_sha256']
    store.apply_plan(store.plan_package({'package_id': 'registered-new-source', 'source_documents': [payload]}))
    seed_claims(store, ['C', 'D'])
    store.apply_plan(store.plan_package({'package_id': 'direct-claim-neighbours', 'claim_relations': [
        {'claim_relation_id': 'CR', 'source_id': 'C', 'target_id': 'D', 'relation_type': 'supports'}]}))
    policy = cm.Policy.model_validate_json((Path(cm.__file__).parent / 'policies/wang_claim_management_opus_only_v2.json').read_text())

    def job(ids):
        request, root = operation_request(action, 'Update', ids)
        request.source_bindings = {'S': cm.source.FileBinding.model_validate(binding(source))}
        authorize(request, policy, root.with_suffix('.policy.json'))
        path = root.with_suffix('.request.json')
        path.write_text(request.model_dump_json())
        return {'request': binding(path), 'root': str(root)}, request

    first, first_request = job(['C'])
    second, second_request = job(['D'])

    def sequence(groups=None):
        groups = groups or [('S0050', [second]), ('S0049', [first])]
        entries = []
        for i, (paragraph, jobs) in enumerate(groups):
            path = tmp / f'batch-{i}.json'
            path.write_text(json.dumps({'jobs': jobs, 'max_workers': 2}))
            entries.append({'paragraph_key': paragraph, 'batch': binding(path), 'root': str(tmp / f'batch-root-{i}')})
        path = tmp / 'paragraphs.json'
        path.write_text(json.dumps({'source_id': 'S', 'source': binding(source), 'paragraphs': entries}))
        return path, tmp / 'sequence'

    return store, source, job, sequence, first, second, first_request, second_request


def execute_batches(monkeypatch, store, *, after=None, revise_first=False):
    calls, ports_by_action, observed = [], {}, []

    def run(manifest, root):
        _, jobs = batch.load(manifest, root, store=store)
        assert json.loads(Path(manifest).read_text())['max_workers'] == 2
        calls.append(list(jobs))
        for action_id, (request, action_root) in jobs.items():
            def propose(packet):
                candidate = corrected(packet)
                candidate['finding_resolutions'] = [
                    {'finding_id': fid, 'disposition': 'resolved', 'reason': 'synthetic actual correction',
                     'citations': [{'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}]}
                    for fid in packet['unresolved']]
                return candidate
            def review(packet):
                assert 'S0049' in packet['sources']['S']['paragraphs']
                assert 'τὸ μαρτύριον' in packet['sources']['S']['paragraphs']['S0050']['text']
                observed.append(deepcopy(packet['before']))
                if revise_first and request.target_keys == ['claims/C'] and not packet['unresolved']:
                    return rejected(packet)
                return passing(packet)
            ports = ports_by_action.setdefault(action_id, SyntheticPorts(store, action_root, proposal=propose, opus=review))
            result = execute_claim_management(store, request, root=action_root, ports=ports)
            assert result['status'] == 'completed', result
            if after:
                after(request)
        # A misleading process result is deliberately irrelevant to sequence completion.
        batch.progress(Path(root) / 'result.json', {'all_actions_completed': True})
    monkeypatch.setattr(batch, 'run', run)
    return calls, ports_by_action, observed


def test_orders_actual_paragraphs_revises_then_reads_latest_context_and_resumes_without_calls(paragraphs, monkeypatch):
    store, _, _, sequence, _, _, first, second = paragraphs
    manifest, root = sequence()
    calls, ports, observed = execute_batches(monkeypatch, store, revise_first=True)
    result = batch.run_paragraphs(manifest, root, store=store)
    assert calls == [[first.action_id], [second.action_id]]
    assert ports[first.action_id].calls == ['opus', 'gpt', 'opus']
    assert ports[second.action_id].calls == ['opus']
    assert observed[-1]['claims/C']['payload']['statement'].endswith('（保留假设条件）')
    assert result['all_actions_completed'] and result['completed_paragraphs'] == ['S0049', 'S0050']
    assert result['downstream_handoff'] == {'claim_ids': ['C', 'D'], 'action_ids': [first.action_id, second.action_id], 'status': 'not_executed'}
    assert batch.run_paragraphs(manifest, root, store=store) == result
    assert len(calls) == 2 and ports[first.action_id].calls == ['opus', 'gpt', 'opus']


def test_identical_cross_paragraph_action_runs_once_without_changing_request(paragraphs, monkeypatch):
    store, _, _, sequence, first, second, first_request, _ = paragraphs
    original = Path(first['request']['path']).read_bytes()
    manifest, root = sequence([('S0050', [first, second]), ('S0049', [first])])
    calls, _, _ = execute_batches(monkeypatch, store)
    result = batch.run_paragraphs(manifest, root, store=store)
    assert result['all_actions_completed'] and len(calls) == 2
    assert sum(first_request.action_id in group for group in calls) == 1
    assert Path(first['request']['path']).read_bytes() == original


def test_rejects_partial_joint_overlap_without_trimming_authority(paragraphs):
    store, _, job, sequence, first, _, _, _ = paragraphs
    joint, _ = job(['C', 'D'])
    original = Path(joint['request']['path']).read_bytes()
    manifest, root = sequence([('S0049', [first]), ('S0050', [joint])])
    with pytest.raises(ValueError, match='deduplication before authorization'):
        batch.load_paragraphs(manifest, root, store=store)
    assert Path(joint['request']['path']).read_bytes() == original


def test_unknown_call_stops_before_later_paragraph_and_keeps_original_reservation(paragraphs, monkeypatch):
    store, _, _, sequence, _, _, first, second = paragraphs
    manifest, root = sequence()
    started, ports = [], []

    def run(path, batch_root):
        _, jobs = batch.load(path, batch_root, store=store)
        for action_id, (request, action_root) in jobs.items():
            started.append(action_id)
            def unknown(packet):
                raise RuntimeError('synthetic lost response')
            port = SyntheticPorts(store, action_root, opus=unknown)
            ports.append(port)
            execute_claim_management(store, request, root=action_root, ports=port)
        # Exit success/old saved completion must not allow p2.
        batch.progress(batch_root / 'result.json', {'all_actions_completed': True})
    monkeypatch.setattr(batch, 'run', run)
    result = batch.run_paragraphs(manifest, root, store=store)
    assert not result['all_actions_completed'] and result['paragraph_key'] == 'S0049'
    assert started == [first.action_id] and ports[0].calls == ['opus']
    assert store.get_cvp_group('CMA-' + cm.sha256_json(second.action_id)) is None
    batch.run_paragraphs(manifest, root, store=store)
    assert started == [first.action_id, first.action_id] and ports[1].calls == []


@pytest.mark.parametrize('drift', ['file', 'registration', 'input'])
def test_source_or_input_drift_between_batches_stops_before_p2(paragraphs, monkeypatch, drift):
    store, source, _, sequence, _, _, first, second = paragraphs
    manifest, root = sequence()
    def after(request):
        assert request.action_id == first.action_id
        if drift == 'file':
            source.write_text(source.read_text() + ' ')
        elif drift == 'registration':
            p = store.get_record_state('source_documents', 'S')['payload'];p['title'] = 'new source registration'
            store.apply_plan(store.plan_package({'package_id': 'other-source-update', 'source_documents': [p]}))
        else:
            manifest.write_text(manifest.read_text() + ' ')
    calls, _, _ = execute_batches(monkeypatch, store, after=after)
    with pytest.raises(ValueError):
        batch.run_paragraphs(manifest, root, store=store)
    assert calls == [[first.action_id]]
    assert store.get_cvp_group('CMA-' + cm.sha256_json(second.action_id)) is None
    assert not json.loads((root / 'result.json').read_text())['all_actions_completed']


def test_final_report_rechecks_earlier_qualification_after_later_batch(paragraphs, monkeypatch):
    store, _, _, sequence, _, _, first, second = paragraphs
    # D has no dependency on C in this case: only the final aggregate detects C's drift.
    store.apply_plan(store.plan_retirement([('claim_relations', 'CR')], reason='independent fixture stages', package_id='independent-stages'))
    manifest, root = sequence()
    def after(request):
        if request.action_id == second.action_id:
            p = store.get_record_state('claims', 'C')['payload'];p['statement'] += '（未經本輪審閱的後續變更）'
            store.apply_plan(store.plan_package({'package_id': 'later-shared-impact', 'claims': [p]}))
    calls, _, _ = execute_batches(monkeypatch, store, after=after)
    result = batch.run_paragraphs(manifest, root, store=store)
    assert len(calls) == 2 and result['completed_paragraphs'] == ['S0049', 'S0050']
    assert not result['all_actions_completed'] and not result['current_qualifications']['claims/C']['ready']


def test_new_claims_also_need_current_qualification_before_next_paragraph(paragraphs, monkeypatch):
    store, _, _, sequence, _, _, first, second = paragraphs
    manifest, root = sequence()
    committed = set()
    execute_batches(monkeypatch, store, after=lambda request: committed.add(request.action_id))
    get = store.get_cvp_group
    def include_new(key):
        parent = get(key)
        if key == 'CMA-' + cm.sha256_json(first.action_id) and parent and first.action_id in committed:
            parent = deepcopy(parent);parent['created_keys'] = ['claims/NEW-NEEDS-REVIEW']
        return parent
    monkeypatch.setattr(store, 'get_cvp_group', include_new)
    result = batch.run_paragraphs(manifest, root, store=store)
    assert result['paragraph_key'] == 'S0049' and not result['all_actions_completed']
    assert 'claims/NEW-NEEDS-REVIEW' in result['batch']['current_qualifications']
    assert get('CMA-' + cm.sha256_json(second.action_id)) is None


@pytest.mark.parametrize('fault', ['unknown_paragraph', 'duplicate_paragraph', 'source_mismatch', 'root_reuse'])
def test_preflight_rejects_invalid_sequence_without_starting_actions(paragraphs, fault):
    store, _, _, sequence, _, _, first, second = paragraphs
    manifest, root = sequence()
    data = json.loads(manifest.read_text())
    if fault == 'unknown_paragraph':
        data['paragraphs'][0]['paragraph_key'] = 'S9999'
    elif fault == 'duplicate_paragraph':
        data['paragraphs'][0]['paragraph_key'] = 'S0049'
    elif fault == 'source_mismatch':
        data['source_id'] = 'UNREGISTERED'
    else:
        data['paragraphs'][0]['root'] = str(root)
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        batch.load_paragraphs(manifest, root, store=store)
    assert store.get_cvp_group('CMA-' + cm.sha256_json(first.action_id)) is None
    assert store.get_cvp_group('CMA-' + cm.sha256_json(second.action_id)) is None


def test_same_paragraph_passes_multiple_jobs_to_existing_parallel_batch(paragraphs, monkeypatch):
    store, _, job, sequence, first, second, first_request, second_request = paragraphs
    seed_claims(store, ['E'])
    extra, extra_request = job(['E'])
    manifest, root = sequence([('S0050', [second]), ('S0049', [first, extra])])
    calls, _, _ = execute_batches(monkeypatch, store)
    result = batch.run_paragraphs(manifest, root, store=store)
    assert calls == [[first_request.action_id, extra_request.action_id], [second_request.action_id]]
    assert result['all_actions_completed']


def test_completed_action_cannot_be_reused_with_another_execution_root(paragraphs, monkeypatch):
    store, _, _, sequence, first, second, _, _ = paragraphs
    manifest, root = sequence()
    calls, _, _ = execute_batches(monkeypatch, store)
    assert batch.run_paragraphs(manifest, root, store=store)['all_actions_completed']
    changed = deepcopy(first);changed['root'] += '-different-root'
    next_manifest, _ = sequence([('S0049', [changed]), ('S0050', [second])])
    value = json.loads(next_manifest.read_text())
    for entry in value['paragraphs']:
        entry['root'] += '-new-sequence'
    next_manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError, match='original action/root/budget'):
        batch.run_paragraphs(next_manifest, root.with_name('other-sequence'), store=store)
    assert len(calls) == 2


def test_changed_file_cannot_skip_registered_source_version_handoff(paragraphs):
    store, source, job, sequence, _, _, _, _ = paragraphs
    source.write_text(source.read_text().replace('摩西是中保', '摩西是約的中保'))
    # Even if a caller freshly binds the changed file, the source row is still old.
    first_job, first = job(['C']);second_job, second = job(['D'])
    manifest, root = sequence([('S0049', [first_job]), ('S0050', [second_job])])
    with pytest.raises(ValueError, match='registered source physical version drift'):
        batch.load_paragraphs(manifest, root, store=store)
    assert store.get_cvp_group('CMA-' + cm.sha256_json(first.action_id)) is None
    assert store.get_cvp_group('CMA-' + cm.sha256_json(second.action_id)) is None
