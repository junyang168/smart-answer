"""Actual coordinator admission: only different own sources may overlap."""
from contextlib import contextmanager
import json
from types import SimpleNamespace

import pytest
from backend.pipeline import claim_management as cm, claim_management_batch as batch


@pytest.mark.parametrize('same_source', [True, False])
def test_coordinator_serializes_same_source_and_overlaps_different_sources(tmp_path, monkeypatch, same_source):
    from backend.api.canonical_repository import postgres_store as pg, viewpoint_production_safety as safety
    from backend.pipeline import source_reconciliation_runtime as runtime
    @contextmanager
    def locked(*args, **kwargs):
        with (tmp_path / ('lock-' + str(len(list(tmp_path.glob('lock-*')))))).open('a') as fd:
            yield fd
    monkeypatch.setattr(runtime, 'maintenance_lock', locked)
    monkeypatch.setattr(safety, 'exclusive_cvp_run_lock', locked)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    jobs = {key: (SimpleNamespace(target_keys=['claims/' + key], source_bindings={
        'S' if same_source else 'S-' + key: {}}, permissions=[
        cm.source.Permission(collection='claims', object_id=key, fields=['statement'])]), tmp_path / key)
        for key in ['a', 'b']}
    monkeypatch.setattr(batch, 'load', lambda *args: ({'max_workers': 2}, jobs))
    monkeypatch.setattr(batch, '_paragraph_results', lambda *args: {'all_actions_completed': True, 'created_keys': []})
    class Store:
        def read_claim_context(self, keys): return {keys[0]: {}}
        def claim_management_qualification(self, cid): return {'ready': True}
    monkeypatch.setattr(pg, 'PostgresKnowledgeStore', Store)
    processes, overlaps = [], []
    class Process:
        def __init__(self, command, **kwargs):
            self.key, self.pid, self.polls, self.live = command[-1], 1, 0, True
            processes.append(self)
            overlaps.append(sum(p.live for p in processes))
            (tmp_path / (cm.sha256_json(self.key) + '.result.json')).write_text(json.dumps({
                'result': {'status': 'completed'}}))
        def poll(self):
            self.polls += 1
            if self.polls <= 3: return None
            self.live = False
            return 0
    monkeypatch.setattr(batch.subprocess, 'Popen', Process)
    batch.run(tmp_path / 'manifest.json', tmp_path)
    assert len(processes) == 2
    assert max(overlaps) == (1 if same_source else 2)
