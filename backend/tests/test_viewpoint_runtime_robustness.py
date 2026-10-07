"""Fault-injected runtime tests: fake adapters only, no provider/Registry calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import viewpoint_resolution_runtime as r
from backend.pipeline.viewpoint_validation_context import (
    ValidationContext, validated_history, validation_runtime,
)


class Adapter:
    model_id = 'fake-model'
    backend = 'fake-subscription'
    prompt_sha256 = 'prompt'
    generation_config_sha256 = 'config'
    max_request_bytes = 500000

    def __init__(self):
        self.calls = 0

    def request_bytes(self, payload):
        return len(json.dumps(payload).encode())

    def request_encoding_proof(self, payload):
        return {'logical_sha256': sha256_json(payload)}

    def generate(self, payload):
        self.calls += 1
        return {'approved': True}


def test_saved_cache_is_pure_no_phase_closure_no_file_writes(tmp_path, monkeypatch):
    a = Adapter(); cache = tmp_path / 'raw.json'
    r.call_model(a, {'full': 'input'}, cache)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(r, 'write_immutable', lambda *a, **k: pytest.fail('cache authentication writes'))
    with ValidationContext('cache') as ctx:
        assert r.call_model(a, {'full': 'input'}, cache) == ({'approved': True}, 0, 0.0)
        assert not ctx.closed
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert a.calls == 1


def test_historical_validation_cannot_fallback_to_model(tmp_path):
    a = Adapter()
    @validated_history
    def validate():
        return r.call_model(a, {}, tmp_path / 'missing.json')
    with pytest.raises(ValueError, match='read-only historical validation'):
        with ValidationContext('history'):
            validate()
    assert a.calls == 0 and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('fault', ['exception', 'keyboard_interrupt', 'encoding_only', 'intent', 'raw_write'])
def test_unfinished_call_cannot_automatically_retry(tmp_path, monkeypatch, fault):
    a = Adapter(); cache = tmp_path / 'raw.json'
    if fault == 'encoding_only':
        (tmp_path / 'raw.request-encoding.json').write_text('{}')
    elif fault == 'intent':
        (tmp_path / 'raw.call-intent.json').write_text('{}')
    else:
        if fault == 'raw_write':
            actual_write = r.write_immutable
            def fail_response(path, payload, **kw):
                if path == cache:
                    raise OSError('injected response publication failure')
                return actual_write(path, payload, **kw)
            monkeypatch.setattr(r, 'write_immutable', fail_response)
        else:
            def fail(payload):
                a.calls += 1
                raise KeyboardInterrupt() if fault == 'keyboard_interrupt' else RuntimeError('injected error')
            a.generate = fail
        with pytest.raises((RuntimeError, KeyboardInterrupt, OSError)):
            r.call_model(a, {}, cache)
    calls = a.calls
    with pytest.raises(ValueError, match='outcome unknown, no automatic retry'):
        r.call_model(a, {}, cache)
    assert a.calls == calls
    counts = r.recorded_model_executions(tmp_path, raw_artifacts={'proposal': cache.name})
    assert counts['unknown_execution_stages'] == ['proposal']


def test_response_survives_and_is_reusable_after_later_failure(tmp_path):
    a = Adapter(); cache = tmp_path / 'raw.json'
    r.call_model(a, {}, cache)
    # A consumer crashing after the raw is published need not call the provider again.
    (tmp_path / 'unrelated-consumer-failure.json').write_text('{}')
    assert r.call_model(a, {}, cache)[1] == 0 and a.calls == 1


@pytest.mark.parametrize('fault', ['request', 'model', 'response', 'intent', 'encoding', 'missing_intent'])
def test_saved_response_identity_and_new_invocation_cannot_drift(tmp_path, fault):
    a = Adapter(); cache = tmp_path / 'raw.json'; r.call_model(a, {}, cache)
    payload = {'different': True} if fault == 'request' else {}
    if fault == 'model':
        a.model_id = 'other'
    elif fault in {'response', 'encoding', 'intent'}:
        path = {'response': cache, 'encoding': tmp_path/'raw.request-encoding.json',
                'intent': tmp_path/'raw.call-intent.json'}[fault]
        value = json.loads(path.read_text()); value['tampered'] = True
        path.write_text(json.dumps(value))
    elif fault == 'missing_intent':
        (tmp_path/'raw.call-intent.json').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        r.call_model(a, payload, cache)
    assert a.calls == 1


def test_concurrent_callers_cannot_both_spend_same_stage(tmp_path):
    a = Adapter(); cache = tmp_path/'raw.json'; entered = threading.Event(); release = threading.Event()
    def generate(payload):
        a.calls += 1; entered.set()
        assert release.wait(5)
        return {'approved': True}
    a.generate = generate
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(r.call_model, a, {}, cache)
        try:
            assert entered.wait(5)
            with pytest.raises(ValueError, match='outcome unknown|already reserved'):
                r.call_model(a, {}, cache)
        finally:
            release.set()
        assert first.result()[1] == 1
    assert a.calls == 1


def test_immutable_publication_is_no_clobber_even_for_identical_reservations(tmp_path):
    p = tmp_path/'reservation.json'
    r.write_immutable(p, {'once': True}, require_new=True)
    original = p.read_bytes()
    with pytest.raises(ValueError, match='already reserved'):
        r.write_immutable(p, {'once': True}, require_new=True)
    with pytest.raises(ValueError, match='differs'):
        r.write_immutable(p, {'once': False})
    r.write_immutable(p, {'once': True})
    assert p.read_bytes() == original


def test_actual_process_death_preserves_unknown_call_and_prevents_reexecution(tmp_path):
    code = '''
import os,sys
from pathlib import Path
from backend.pipeline.viewpoint_resolution_runtime import call_model
class Fake:
    model_id='fake-model';backend='fake-subscription';prompt_sha256='prompt';generation_config_sha256='config'
    def generate(self,payload): os._exit(37)
call_model(Fake(),{},Path(sys.argv[1]))
'''
    cache = tmp_path/'raw.json'
    process = subprocess.run([sys.executable, '-c', code, str(cache)], timeout=10)
    assert process.returncode == 37
    assert (tmp_path/'raw.call-intent.json').exists() and not cache.exists()
    a = Adapter()
    with pytest.raises(ValueError, match='outcome unknown'):
        r.call_model(a, {}, cache)
    assert a.calls == 0


def test_explicit_bound_file_proofs_ignore_logs_but_recheck_all_actual_inputs(tmp_path):
    path = tmp_path/'bound.json'; path.write_text('{"revision":1}')
    calls = []
    @validated_history(bound_files_only=True)
    def validate():
        calls.append(1); return r.read_artifact(path)
    with validation_runtime():
        with ValidationContext('first'): result = validate()
        (tmp_path/'repair-record.txt').write_text('runtime note, not approval')
        with ValidationContext('second'): assert validate() is result
        path.write_text('{"revision":2}')
        with pytest.raises(ValueError, match='dependency drift'):
            with ValidationContext('third'): validate()
    assert calls == [1]


def test_explicit_proof_cannot_disable_nested_legacy_negative_receipt_guard(tmp_path):
    p = tmp_path/'proof.json'; p.write_text('{}')
    @validated_history
    def legacy(): return r.read_artifact(p)
    @validated_history(bound_files_only=True)
    def outer(): return legacy()
    with validation_runtime():
        with ValidationContext('first'): outer()
        (tmp_path/'raw-opus.json').write_text('{}')
        with pytest.raises(ValueError, match='receipt population drift'):
            with ValidationContext('second'): outer()


@pytest.mark.parametrize('change', ['none', 'new_receipt', 'delete_receipt', 'late_receipt'])
def test_bound_proof_reauthenticates_inherited_directory_in_new_phase(tmp_path, change):
    """A prior legacy check can pin the same parent before a bound-only proof."""
    p = tmp_path/'proof.json'; p.write_text('{}')
    receipt = tmp_path/'existing-receipt.json'; receipt.write_text('{}')
    @validated_history
    def legacy(): return r.read_artifact(p)
    calls = []
    @validated_history(bound_files_only=True)
    def bound():
        calls.append(1)
        return r.read_artifact(p)
    with validation_runtime():
        with ValidationContext('first'):
            legacy()
            result = bound()
        if change == 'new_receipt': (tmp_path/'new-receipt.json').write_text('{}')
        if change == 'delete_receipt': receipt.unlink()
        if change in ('new_receipt', 'delete_receipt'):
            with pytest.raises(ValueError, match='receipt population drift'):
                with ValidationContext('second'): bound()
        elif change == 'late_receipt':
            with pytest.raises(ValueError, match='receipt set drift'):
                with ValidationContext('second'):
                    assert bound() is result
                    (tmp_path/'late-receipt.json').write_text('{}')
        else:
            with ValidationContext('second'):
                assert bound() is result
    assert calls == [1]


@pytest.mark.parametrize('when', ['during_phase', 'next_phase'])
def test_bound_file_proof_still_authenticates_explicit_negative_inputs(tmp_path, when):
    from backend.pipeline.viewpoint_validation_context import optional_file_exists
    p = tmp_path/'proof.json'; p.write_text('{}'); absent = tmp_path/'required-absent.json'
    @validated_history(bound_files_only=True)
    def validate():
        if optional_file_exists(absent):
            raise ValueError('new conflicting receipt')
        return r.read_artifact(p)
    with validation_runtime():
        if when == 'during_phase':
            with pytest.raises(ValueError, match='optional receipt appeared'):
                with ValidationContext('first'):
                    validate(); absent.write_text('{}')
        else:
            with ValidationContext('first'): validate()
            absent.write_text('{}')
            with pytest.raises(ValueError, match='optional receipt appeared'):
                with ValidationContext('next'): validate()


def test_sigterm_is_cooperative_and_restores_handler(tmp_path):
    from backend.pipeline.viewpoint_campaign_control import graceful_campaign_stop, stop_requested
    prior = signal.getsignal(signal.SIGTERM)
    with graceful_campaign_stop(tmp_path):
        assert not stop_requested(tmp_path)
        os.kill(os.getpid(), signal.SIGTERM)
        assert stop_requested(tmp_path)  # no exception; an in-flight fake call can finish
        assert not stop_requested(tmp_path/'other')
    assert signal.getsignal(signal.SIGTERM) == prior and not stop_requested(tmp_path)


def test_sigterm_during_fake_model_preserves_the_actual_answer(tmp_path):
    from backend.pipeline.viewpoint_campaign_control import graceful_campaign_stop
    from backend.pipeline.viewpoint_cvp_campaign_runner import pause_at_safe_boundary
    a = Adapter(); cache = tmp_path/'raw.json'
    def generate(payload):
        a.calls += 1
        os.kill(os.getpid(), signal.SIGTERM)
        return {'approved': True}
    a.generate = generate
    with graceful_campaign_stop(tmp_path):
        assert r.call_model(a, {}, cache)[1] == 1
        assert pause_at_safe_boundary(tmp_path, {'sequence': 1}, group_complete=True)
    assert json.loads(cache.read_text())['response'] == {'approved': True}
    assert r.call_model(a, {}, cache)[1] == 0 and a.calls == 1


def test_stop_during_real_campaign_preflight_never_enters_generation(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from backend.pipeline import viewpoint_cvp_campaign_runner as campaign
    from backend.pipeline.viewpoint_campaign_control import graceful_campaign_stop
    manifest = {'artifact_sha256': 'manifest', 'groups': [{'group_key': 'group', 'claim_ids': ['C']} ]}
    schedule = {'upstream_manifest_path': str(tmp_path/'manifest.json'), 'completed_groups': [],
                'schedule': [{'group_key': 'group', 'sequence': 1, 'state': 'pending'}]}
    for name, value in [('manifest', manifest), ('schedule', schedule), ('inventory', {'claims': []}),
                        ('seed', {'groups': []}), ('entrance', {})]:
        (tmp_path/(name+'.json')).write_text(json.dumps(value))
    args = SimpleNamespace(schedule=tmp_path/'schedule.json', accepted_grouping_sha='manifest',
        schedule_sha256=campaign.binding(tmp_path/'schedule.json')['file_sha256'],
        lineage_inventory=tmp_path/'inventory.json', entrance_audit=tmp_path/'entrance.json',
        completed_proofs=tmp_path/'seed.json', grouping_repository=tmp_path/'grouping',
        wang_repository=tmp_path/'wang', global_lock=tmp_path/'registry.lock', authorized_by='synthetic',
        output_root=tmp_path/'out', validation_runtime=False, dry_run=False)
    args.output_root.mkdir()
    monkeypatch.setattr(campaign, 'validate_schedule', lambda *a: {'group': manifest['groups'][0]})
    monkeypatch.setattr(campaign, 'campaign_policy', lambda *a: ({}, {}))
    monkeypatch.setattr(campaign, 'lineage_preflight', lambda *a: {})
    monkeypatch.setattr(campaign.subprocess, 'check_output', lambda *a, **k: 'fake-commit')
    monkeypatch.setattr(campaign, 'qualify', lambda *a: None)
    monkeypatch.setattr(campaign, 'runner_args', lambda *a: ['fake-runner'])
    calls = []
    def command(argv):
        calls.append(argv)
        assert argv == ['fake-runner', '--dry-run']
        os.kill(os.getpid(), signal.SIGTERM)
    monkeypatch.setattr(campaign, 'command', command)
    with graceful_campaign_stop(args.output_root):
        assert campaign.execute(args) == 0
    assert calls == [['fake-runner', '--dry-run']]
    state = json.loads((args.output_root/'progress.json').read_text())
    assert state['status'] == 'paused_at_safe_boundary' and not state['current_group_completed']
    assert not (args.output_root/'g001/completion.json').exists()


def test_revision_storage_authority_survives_unrelated_source_line_shifts():
    from backend.pipeline.viewpoint_g033_identity_repair import revision_writer_excerpt
    source = '''
def compile_cvp_batch_package():
    return ViewpointRevisionRecord(revision_number=1, supersedes_revision_id=target_key)
'''
    assert revision_writer_excerpt(source) == revision_writer_excerpt('# unrelated comment\n'*100 + source)


@pytest.mark.parametrize('fault', ['counter', 'parent', 'ambiguous', 'comment_only', 'wrong_function'])
def test_revision_storage_authority_still_rejects_semantic_or_ambiguous_change(fault):
    from backend.pipeline.viewpoint_g033_identity_repair import revision_writer_excerpt
    call = 'ViewpointRevisionRecord(revision_number=1, supersedes_revision_id=target_key)'
    source = 'def compile_cvp_batch_package():\n    return ' + call
    if fault == 'counter': source = source.replace('revision_number=1', 'revision_number=2')
    elif fault == 'parent': source = source.replace('target_key', 'foreign_key')
    elif fault == 'ambiguous': source = 'def compile_cvp_batch_package():\n    ' + call + '\n    return ' + call
    elif fault == 'comment_only': source = 'def compile_cvp_batch_package():\n    # ' + call + '\n    return None'
    else: source = source.replace('compile_cvp_batch_package', 'unrelated')
    with pytest.raises(ValueError, match='mechanical source authority'):
        revision_writer_excerpt(source)


@pytest.mark.parametrize('group_complete', [False, True])
def test_campaign_stop_progress_is_not_blocked_or_completion(tmp_path, group_complete):
    from backend.pipeline.viewpoint_campaign_control import main, graceful_campaign_stop
    from backend.pipeline.viewpoint_cvp_campaign_runner import pause_at_safe_boundary
    (tmp_path/'campaign.json').write_text('{}')
    main(['--campaign-root', str(tmp_path)])
    with graceful_campaign_stop(tmp_path):
        assert pause_at_safe_boundary(tmp_path, {'sequence': 1}, group_complete=group_complete)
    state = json.loads((tmp_path/'progress.json').read_text())
    assert state['status'] == 'paused_at_safe_boundary'
    assert state['current_group_completed'] is group_complete
    assert state['no_inflight_model_or_write']
