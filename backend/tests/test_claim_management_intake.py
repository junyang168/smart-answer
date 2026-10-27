"""ID-list intake exercises the real Update executor on disposable SQL."""
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.pipeline import claim_management_intake as intake
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


@pytest.fixture
def claims(action):
    store, _, _, _, tmp, _ = action
    store.apply_plan(store.plan_package({'package_id': 'list-case',
        'claims': [{'claim_id': cid, 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E' + cid]}
                   for cid in ['C', 'D']],
        'evidence_steps': [{'evidence_step_id': 'E' + cid, 'statement': '甲是假设。',
            'produced_claim_ids': [cid], 'source_fragment_ids': ['F' + cid],
            'support_eligibility': 'eligible_candidate', 'speaker': 'professor', 'stance': 'asserted'} for cid in ['C', 'D']],
        'source_fragments': [{'fragment_id': 'F' + cid, 'source_id': 'S', 'paragraph_key': 'S0001',
                             'verbatim_excerpt': '甲是假设，不是断言。'} for cid in ['C', 'D']]}))
    return store, tmp / 'list-update'


def pass_review(packet):
    return {'complete_review': True, 'findings': [], 'prior_findings': [], 'binding_assessments': [
        {'claim_key': key, 'claim_use': 'professor_judgment', 'sufficient': True,
         'evidence_keys': list(evidence), 'reason': 'synthetic own bound evidence judgment'}
        for key, evidence in packet['own_evidence_bindings'].items()]}


def test_ids_become_complete_updates_and_resume_without_model_replay(claims, monkeypatch):
    store, root = claims
    ports, starts = {}, []
    def execute(manifest, output):
        plan, jobs = batch.load(manifest, output, store=store)
        assert plan['max_workers'] == 2
        for key, (request, execution) in jobs.items():
            starts.append(key)
            port = ports.setdefault(key, SyntheticPorts(store, execution, opus=pass_review))
            assert execute_claim_management(store, request, root=execution, ports=port)['status'] == 'completed'
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C', 'D', 'C'], root, instruction='Update these Claims', max_workers=2, store=store)
    assert result['status'] == 'completed' and list(result['claims']) == ['C', 'D']
    assert all(row['qualification']['ready'] for row in result['claims'].values())
    assert all(port.calls == ['opus'] for port in ports.values())
    assert intake.run(['C', 'D'], root, instruction='Update these Claims', max_workers=2, store=store) == result
    assert len(starts) == 2


@pytest.mark.parametrize('ids', [[], [''], [' C'], ['claims/C'], ['C\n'], 'C', ['C', None]])
def test_invalid_list_never_prepares_action(claims, ids):
    store, root = claims
    with pytest.raises(ValueError, match='claim_ids'):
        intake.prepare(ids, root, instruction='Update', store=store)
    assert not (root / 'manifest.json').exists()


def test_complete_list_validated_before_any_dispatch(claims):
    store, root = claims
    prepared = intake.prepare(['C', 'MISSING'], root, instruction='Update', store=store)
    assert prepared['excluded'] == {'MISSING': {'status': 'paused', 'reason': 'missing_claim'}}
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    assert [r.target_keys for r, _ in jobs.values()] == [['claims/C']]


@pytest.mark.parametrize('change', ['ids', 'instruction', 'workers', 'source', 'request', 'policy'])
def test_recovery_cannot_change_authority_or_bound_inputs(claims, change):
    store, root = claims
    prepared = intake.prepare(['C'], root, instruction='Update', store=store)
    kwargs = {'instruction': 'Update', 'store': store}
    ids = ['C']
    if change == 'ids': ids = ['C', 'D']
    if change == 'instruction': kwargs['instruction'] = 'different direction'
    if change == 'workers': kwargs['max_workers'] = 2
    if change == 'policy': kwargs['policy'] = cm.DEFAULT_POLICY
    if change == 'source':
        Path(store.get_record_state('source_documents', 'S')['payload']['source_path']).write_text('{"script":[{"text":"different"}]}')
    if change == 'request':
        item = json.loads(Path(prepared['manifest']['path']).read_text())['jobs'][0]
        Path(item['request']['path']).write_text('{}')
    with pytest.raises(ValueError):
        intake.prepare(ids, root, **kwargs)


def test_permissions_do_not_turn_related_claims_into_hidden_targets(claims):
    store, root = claims
    store.apply_plan(store.plan_package({'package_id': 'related-list-case', 'claim_relations': [
        {'claim_relation_id': 'R', 'from_id': 'C', 'to_id': 'D', 'relation_type': 'supports'}]}))
    prepared = intake.prepare(['C'], root, instruction='Update', store=store)
    _, jobs = batch.load(prepared['manifest']['path'], root / 'batch', store=store)
    request = next(iter(jobs.values()))[0]
    assert request.target_keys == ['claims/C'] and not request.creation_permissions
    assert not any(p.key == 'claims/D' for p in request.permissions)
    assert all(not set(p.fields) & (cm.source.PROVENANCE | {'visibility', 'claim_id', 'source_id'})
               for p in request.permissions)


def test_review_revision_and_commit_run_through_the_list_entry(claims, monkeypatch):
    store, root = claims
    ports = []
    citation = {'source_id': 'S', 'paragraph_key': 'S0001', 'exact_quote': '甲是假设，不是断言。'}
    def resolution(fid):
        return {'finding_id': fid, 'disposition': 'resolved', 'reason': 'synthetic source correction',
                'citations': [citation], 'affected_target_keys': ['claims/C'],
                'affected_write_keys': ['claims/C'], 'impact_reason': 'target wording corrected'}
    def review(packet):
        assert packet['original_goal']['review_questions'] == ['Cleanup this Claim']
        result = pass_review(packet)
        if not packet['unresolved']:
            result['findings'] = [{'target_key': 'claims/C', 'component': 'statement', 'category': 'content',
                'reason': 'synthetic missing qualification', 'citations': [citation],
                'acceptance_condition': 'preserve the stated qualification',
                'affected_target_keys': ['claims/C'], 'affected_write_keys': [], 'impact_reason': 'target wording'}]
        else:
            result['prior_findings'] = [resolution(fid) for fid in packet['unresolved']]
        return result
    def propose(packet):
        assert packet['original_goal']['review_questions'] == ['Cleanup this Claim']
        return {'changes': [{'record_key': 'claims/C', 'fields_json': json.dumps({'statement': '甲是假设，不是断言。'}),
                             'reason': 'synthetic complete qualification', 'citations': [citation]}],
                'findings': [], 'finding_resolutions': [resolution(fid) for fid in packet['unresolved']]}
    def execute(manifest, output):
        for request, execution in batch.load(manifest, output, store=store)[1].values():
            port = SyntheticPorts(store, execution, proposal=propose, opus=review)
            ports.append(port)
            result = execute_claim_management(store, request, root=execution, ports=port)
            assert result['status'] == 'completed', result
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C'], root, instruction='Cleanup this Claim', store=store)
    assert result['all_actions_completed']
    assert ports[0].calls == ['opus', 'gpt', 'opus']
    assert store.get_record_state('claims', 'C')['payload']['statement'] == '甲是假设，不是断言。'


def test_unknown_reply_keeps_its_reservation_and_other_claim_finishes(claims, monkeypatch):
    store, root = claims
    ports = {}
    def unknown(packet):
        raise RuntimeError('synthetic unknown transport result')
    def execute(manifest, output):
        for key, (request, execution) in batch.load(manifest, output, store=store)[1].items():
            port = ports.setdefault(key, SyntheticPorts(store, execution,
                opus=unknown if request.target_keys == ['claims/C'] else pass_review))
            execute_claim_management(store, request, root=execution, ports=port)
    monkeypatch.setattr(batch, 'run', execute)
    result = intake.run(['C', 'D'], root, instruction='Update', store=store)
    assert result['status'] == 'paused' and result['claims']['D']['qualification']['ready']
    assert not (result['claims']['C']['qualification'] or {}).get('ready')
    action_id = result['claims']['C']['action_id']
    saved = store.get_cvp_group('CMA-' + cm.sha256_json(action_id))['call_reservations']
    again = intake.run(['C', 'D'], root, instruction='Update', store=store)
    assert again['claims']['C']['result']['reason'] == 'call_uncertain'
    assert store.get_cvp_group('CMA-' + cm.sha256_json(action_id))['call_reservations'] == saved
    assert all(port.calls == ['opus'] for port in ports.values())


def test_all_unavailable_ids_return_explicit_results_without_batch_dispatch(claims, monkeypatch):
    store, root = claims
    payload = store.get_record_state('claims', 'C')['payload']
    payload['review_status'] = 'human_review_required'
    # Seed a protected status only in this disposable test database.
    from backend.api.canonical_repository.postgres_store import record_content_sha
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.objects SET payload=%s::jsonb,content_sha256=%s WHERE collection='claims' AND object_id='C'",
                     (json.dumps(payload), record_content_sha(payload)))
    def unexpected(*args, **kwargs):
        raise AssertionError('unavailable Claim must not dispatch')
    monkeypatch.setattr(batch, 'run', unexpected)
    result = intake.run(['C', 'MISSING'], root, instruction='Update', store=store)
    assert not result['all_actions_completed'] and result['status'] == 'paused'
    assert {key: row['result']['reason'] for key, row in result['claims'].items()} == {
        'C': 'human_hold', 'MISSING': 'missing_claim'}


def test_status_detects_inherited_inner_worker_lock(claims):
    import fcntl
    store, root = claims
    intake.prepare(['C'], root, instruction='Update', store=store)
    with (root / 'batch/controller.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert batch.status(root)['live_lock_holder']
    assert not batch.status(root)['live_lock_holder']


def test_policy_is_saved_with_task_and_can_resume_from_another_checkout(claims, tmp_path):
    store, root = claims
    prepared = intake.prepare(['C'], root, instruction='Update', store=store)
    alternate = tmp_path / 'same-policy-another-checkout.json'
    alternate.write_bytes(intake.DEFAULT_POLICY.read_bytes())
    assert intake.prepare(['C'], root, instruction='Update', store=store, policy=alternate) == prepared
    for request, _ in batch.load(prepared['manifest']['path'], root / 'batch', store=store)[1].values():
        assert Path(request.policy.path) == root / 'policy.json'


def test_failed_current_source_check_replaces_stale_completed_report(claims):
    store, root = claims
    intake.prepare(['C'], root, instruction='Update', store=store)
    batch.progress(root / 'result.json', {'status': 'completed', 'all_actions_completed': True})
    Path(store.get_record_state('source_documents', 'S')['payload']['source_path']).write_text('{"script":[]}')
    with pytest.raises(ValueError):
        intake.run(['C'], root, instruction='Update', store=store)
    assert batch.status(root)['result']['all_actions_completed'] is False


def test_detached_cli_freezes_input_ids_and_uses_the_same_entry(claims, monkeypatch, tmp_path):
    store, root = claims
    ids_path = tmp_path / 'provided.json'
    ids_path.write_text('["C", "D", "C"]')
    original = intake.prepare
    monkeypatch.setattr(intake, 'prepare', lambda values, output, **kwargs:
                        original(values, output, store=store, **kwargs))
    commands = []
    monkeypatch.setattr(batch, 'detach', lambda command, output: commands.append((command, output)))
    monkeypatch.setattr('sys.argv', ['batch', '--claim-ids', str(ids_path), '--root', str(root),
                                   '--instruction', 'Update', '--max-workers', '2', '--detach'])
    batch.main()
    ids_path.write_text('["MISSING"]')
    command, output = commands[0]
    assert output == root
    assert json.loads(Path(command[command.index('--claim-ids') + 1]).read_text()) == ['C', 'D']
