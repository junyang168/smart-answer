"""Agent-prepared input may arrive between worker exits, without a new scheduler."""
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from backend.pipeline import claim_management as cm, claim_management_batch as batch
from backend.tests.test_claim_batch_source_lanes import POLICY, completed_batch


@pytest.mark.parametrize('late_input', [True, False])
def test_poll_prepared_input_while_busy_but_finish_when_idle(tmp_path, monkeypatch, late_input):
    from backend.api.canonical_repository import postgres_store as pg, viewpoint_production_safety as safety
    from backend.pipeline import source_reconciliation_runtime as runtime

    @contextmanager
    def locked(*args, **kwargs):
        with (tmp_path / ('lock-' + str(len(list(tmp_path.glob('lock-*')))))).open('a') as fd:
            yield fd

    monkeypatch.setattr(runtime, 'maintenance_lock', locked)
    monkeypatch.setattr(safety, 'exclusive_cvp_run_lock', locked)
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    clock = [0]
    monkeypatch.setattr(batch.time, 'monotonic', lambda: clock[0])

    def sleep(seconds):
        clock[0] += seconds
        assert clock[0] <= 30, 'an empty batch must not wait indefinitely for future input'

    monkeypatch.setattr(batch.time, 'sleep', sleep)

    class Request(SimpleNamespace):
        def model_dump(self, **kwargs):
            return {'target_keys': self.target_keys}

    def job(key):
        return (Request(policy=POLICY, target_keys=['claims/' + key],
                        source_bindings={'S-' + key: {}}, permissions=[]), tmp_path / key)

    original = tmp_path / 'manifest.json'
    extra = tmp_path / 'extra' / 'manifest.json'
    extra.parent.mkdir()
    original.write_text('{}')
    extra.write_text('{}')
    monkeypatch.setattr(batch, 'load', lambda path, *a, **kw: (
        {'max_workers': 2}, {'a': job('a')} if Path(path) == original else {'b': job('b')}))
    monkeypatch.setattr(batch, '_paragraph_results', completed_batch)

    class Store:
        def get_cvp_group(self, key):
            return None

        def claim_management_qualification(self, cid):
            return {'ready': True}

    monkeypatch.setattr(pg, 'PostgresKnowledgeStore', Store)
    processes = {}

    class Process:
        def __init__(self, command, **kwargs):
            self.key, self.pid = command[-1], len(processes) + 1
            self.started = clock[0]
            self.ends = self.started + (15 if late_input else 2) if self.key == 'a' else self.started + 3
            processes[self.key] = self
            (tmp_path / (cm.sha256_json(self.key) + '.result.json')).write_text(
                json.dumps({'result': {'status': 'completed'}}))

        def poll(self):
            return None if clock[0] < self.ends else 0

    monkeypatch.setattr(batch.subprocess, 'Popen', Process)
    callbacks = []

    def refill(occupied, room, jobs):
        callbacks.append(clock[0])
        if late_input and clock[0] >= 3 and 'b' not in jobs:
            assert 'S-b' not in occupied and room > 0
            return [batch.binding(extra)]
        return []

    batch.run(original, tmp_path, model_capacity=dict(
        root=str(tmp_path / 'capacity'), action_window=4, spacing_seconds=0, cooldown_seconds=1), refill=refill)
    if late_input:
        assert processes['b'].started < processes['a'].ends
        assert len(callbacks) <= 6, 'empty callbacks must not run on every one-second worker poll'
        assert len(list((tmp_path / 'dispatch-inputs').glob('*.json'))) == 1
    else:
        assert set(processes) == {'a'} and clock[0] <= 3
