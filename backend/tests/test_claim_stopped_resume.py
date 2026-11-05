"""Owner stop is a known spent call, not unknown, free retry or approval."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_management as cm
from backend.pipeline import claim_stopped_resume as resume
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.source_reconciliation_runner import body, read, write
from backend.pipeline.cvp_sql_commit import binding
from backend.pipeline.subscription_process import digest_file
from backend.tests.test_claim_subscription_output_limit import truncated
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres
from backend.tests.test_claim_owner_call_extension import authorize
from backend.tests.test_claim_management_execution_postgres import SyntheticPorts


@pytest.fixture
def stopped(truncated):
    store, old, root, previous, new, next_root, policy, _ = truncated
    key, slot = next((k, s) for k, s in previous['call_reservations'].items() if s['state'] == 'started')
    stage = root / slot['round_id']; transport = stage / 'gpt.transport'
    (transport / 'stdout.log').write_text('{"type":"turn.started"}\n')
    receipt = json.loads((transport / 'exit.json').read_text())
    receipt.update(returncode=-15, files={n: digest_file(transport / n) for n in ('stdout.log', 'stderr.log')})
    (transport / 'exit.json').write_text(json.dumps(receipt))
    failure = body(read(stage / 'gpt.failure.json'))
    failure['provider_raw']['stdout'] = (transport / 'stdout.log').read_text()
    path = stage / 'gpt.failure.json'; path.unlink(); write(path, failure)
    stop = root / 'owner-stop.json'
    stop.write_text(json.dumps({'action_id': old.action_id, 'gpt_terminated_by_owner_stop': True,
        'gpt_answer_received': False, 'gpt_started_consumption_preserved': True,
        'gpt_exit': binding(transport / 'exit.json')}))
    policy.initial_candidate = 'existing'
    policy.budget.gpt_calls = previous['policy']['budget']['gpt_calls'] - 1
    policy.budget.opus_calls = previous['policy']['budget']['opus_calls']
    policy.budget.max_revisions = previous['policy']['budget']['max_revisions']
    authorize(new, policy, next_root.with_suffix('.policy.json'))
    auth_path = Path(new.authorization.path); auth = json.loads(auth_path.read_text())
    auth.pop('owner_call_extension')
    current = {cm.source.key(*k): cm.model_state(v) for k, v in store.read_claim_context([('claims', 'C')]).items()}
    proof = {'predecessor_action_id': old.action_id, 'prior_metadata_sha256': cm.sha256_json(previous),
        'authorization_quote': '重新跑這組，看看審核結果', 'slot': key, 'stop_receipt': binding(stop),
        'current_objects_sha256': cm.sha256_json(current), 'migrations': {},
        'saved_files': {n: binding(stage / n) for n in (
            'materials.json', 'gpt.packet.json', 'gpt.reservation.json', 'gpt.failure.json',
            'gpt.transport/invocation.json', 'gpt.transport/exit.json',
            'gpt.transport/stdout.log', 'gpt.transport/stderr.log', 'gpt.transport/response-schema.json')}}
    auth['owner_stopped_resume'] = proof
    auth_path.write_text(json.dumps(auth)); new.authorization = cm.source.FileBinding.model_validate(binding(auth_path))
    return store, old, root, previous, new, next_root, policy, proof


@pytest.mark.parametrize('refused_first', [False, True])
def test_owner_stop_uses_remaining_budget_and_current_existing_review(stopped, monkeypatch, refused_first):
    store, old, root, previous, new, next_root, policy, proof = stopped
    accounting = cm.continuation_budget(previous, new, policy)
    assert accounting['spent_generations']['gpt'] == 1
    ports = SyntheticPorts(store, next_root, opus=lambda _: {'complete_review': True, 'findings': [], 'prior_findings': []})
    if refused_first:
        with monkeypatch.context() as patch:
            patch.setattr('backend.pipeline.claim_joint_update.admission_error', lambda *args: 'old reachability guard')
            assert execute_claim_management(store, new, root=next_root, ports=ports)['reason'] == 'engineering_required'
        assert ports.calls == []
        from backend.pipeline.claim_review_recovery import recover_refused_admission, VERSION
        parent = store.get_cvp_group('CMA-' + cm.sha256_json(new.action_id))
        metadata = {k: v for k, v in parent.items() if k not in {'run_id', 'status', 'error'}}
        path = next_root.parent / 'recover-admission.json'
        path.write_text(json.dumps({'schema_version': VERSION, 'action_id': new.action_id,
            'authorized_by': 'owner', 'authorization_quote': 'fix engineering and resume',
            'human_content_approval': False, 'prior_metadata_sha256': cm.sha256_json(metadata)}))
        recover_refused_admission(store, new.action_id, authorization=binding(path))
    result = execute_claim_management(store, new, root=next_root, ports=ports)
    assert result['status'] == 'completed', result
    assert ports.calls == ['opus']  # No GPT restatement of mechanically repaired input.
    prior = store.get_cvp_group('CMA-' + cm.sha256_json(old.action_id))
    assert prior['call_reservations'][proof['slot']] == previous['call_reservations'][proof['slot']]
    assert prior['continued_by']


def test_historical_grant_is_reachable_only_through_its_actual_replacement_slices(stopped, tmp_path):
    store, _, _, _, new, _, _, _ = stopped
    before = {cm.source.key(*k): cm.model_state(v) for k, v in store.read_claim_context([('claims', 'C')]).items()}
    # The base stopped fixture has Claim-only grants. Add a fragment to a
    # separate synthetic historical frame to exercise the admission projection.
    key = 'source_fragments/F'
    before[key] = {'revision': 1, 'content_sha256': 'a' * 64, 'retired': False, 'payload': {'fragment_id': 'F'}}
    new = new.model_copy(deep=True)
    new.permissions.append(cm.source.Permission(collection='source_fragments', object_id='F', fields=['paragraph_key']))
    auth = json.loads(Path(new.authorization.path).read_text())
    material = body(read(Path(auth['owner_stopped_resume']['saved_files']['materials.json']['path'])))
    material['unit_request']['permissions'] = new.model_dump(mode='json')['permissions']
    material['before'][key] = deepcopy(before[key])
    path = tmp_path / 'historical-material.json'; write(path, material)
    auth['owner_stopped_resume']['saved_files']['materials.json'] = binding(path)
    auth_path = tmp_path / 'projection-authority.json'; auth_path.write_text(json.dumps(auth))
    new.authorization = cm.source.FileBinding.model_validate(binding(auth_path))
    before[key]['payload'].update(anchor_state='replaced_by_precise_fragments', replacement_fragment_ids=['NEW1', 'NEW2'])
    reachable = {'source_fragments/NEW1', 'source_fragments/NEW2'}
    assert resume.historical_replacement_keys(new, before, reachable) == {key}
    assert resume.historical_replacement_keys(new, before, {'source_fragments/NEW1'}) == set()
    before[key]['payload']['anchor_state'] = 'source_version_bound'
    assert resume.historical_replacement_keys(new, before, reachable) == set()


@pytest.mark.parametrize('defect', ['missing_exit', 'changed_stdout', 'returned_answer', 'wrong_stop', 'missing_authority', 'more_budget'])
def test_owner_stop_does_not_waive_receipts_or_budget(stopped, defect):
    _, _, root, previous, new, _, policy, proof = stopped
    stage = root / previous['call_reservations'][proof['slot']]['round_id']
    if defect == 'missing_exit': (stage / 'gpt.transport/exit.json').unlink()
    elif defect == 'changed_stdout': (stage / 'gpt.transport/stdout.log').write_text('changed')
    elif defect == 'returned_answer': (stage / 'gpt.raw.json').write_text('{}')
    elif defect == 'wrong_stop':
        p = Path(proof['stop_receipt']['path']); p.write_text('{}')
    elif defect == 'missing_authority':
        p = Path(new.authorization.path); d = json.loads(p.read_text()); d.pop('owner_stopped_resume')
        p.write_text(json.dumps(d)); new.authorization = cm.source.FileBinding.model_validate(binding(p))
    else: policy.budget.gpt_calls += 1
    with pytest.raises((ValueError, FileNotFoundError)):
        cm.continuation_budget(previous, new, policy)


def test_only_pinned_migration_chain_can_change_original_target(stopped):
    store, _, _, previous, _, _, _, proof = stopped
    from backend.pipeline.transcript_claim_anchor_sync import VERSION
    payload = store.get_record_state('claims', 'C')['payload']
    payload['occurrences'][0]['anchors'] = [{'paragraph_key': 'S0001', 'media_time': 1}]
    # Build a real before/after history, then exercise the replay validator
    # with a synthetic migration label. Production apply still independently
    # requires the sealed migration plan and full read pins (tested separately).
    plan = store.plan_package({'claims': [payload]}, source_kind='test_fixture')
    store.apply_plan(plan)
    history = store.get_change_history(plan.change_set_id)
    history['source_kind'] = VERSION
    current = {cm.source.key(*k): cm.model_state(v) for k, v in store.read_claim_context([('claims', 'C')]).items()}
    proof.update(current_objects_sha256=cm.sha256_json(current), migrations={plan.change_set_id: cm.sha256_json(history)})
    resume.verify_migrations(previous, current, proof, {plan.change_set_id: history})
    bad = deepcopy(current); bad['claims/C']['payload']['statement'] = 'unreviewed semantic change'
    proof['current_objects_sha256'] = cm.sha256_json(bad)
    with pytest.raises(ValueError, match='outside the pinned'):
        resume.verify_migrations(previous, bad, proof, {plan.change_set_id: history})
