"""A commit landing mid-read must not tear read_claim_context (2026-10-23).

The Claim batch controller polled read_claim_context while its own worker
committed KCS-d60a81eb640868aa1302; the statements of one read saw two
versions of E016/E018 and the conflict check stopped the controller after the
action had completed. These tests commit between the statements of one read
and count the controller's reads.
"""
from contextlib import contextmanager
import json
from types import SimpleNamespace
import uuid

from backend.pipeline import claim_management as cm
from backend.tests.test_claim_management_postgres import action, postgres_store, _temporary_postgres  # noqa: F401


def seed(store):
    store.apply_plan(store.plan_package({'package_id': 'snapshot-' + uuid.uuid4().hex,
        'claims': [{'claim_id': 'C', 'statement': '甲是假设。', 'claim_type': 'exegesis',
                    'occurrences': [{'source_id': 'S', 'anchors': []}], 'evidence_step_ids': ['E']}],
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': '甲是假设。', 'produced_claim_ids': ['C']}]}))


def rewrite_step(store, statement):
    store.apply_plan(store.plan_package({'package_id': 'snapshot-' + uuid.uuid4().hex,
        'evidence_steps': [{'evidence_step_id': 'E', 'statement': statement, 'produced_claim_ids': ['C']}]}))


def test_commit_between_statements_reads_one_consistent_snapshot(action, monkeypatch):
    store = action[0]
    seed(store)
    before = store.get_record_state('evidence_steps', 'E')
    original = type(store)._affected_cvp_context
    fired = []

    def commit_then_read(self, conn, keys):
        if not fired:
            fired.append(True)
            rewrite_step(store, '甲是假设，不是断言。')  # separate connection, committed now
        return original(self, conn, keys)

    monkeypatch.setattr(type(store), '_affected_cvp_context', commit_then_read)
    records = store.read_claim_context([('claims', 'C')])
    assert fired
    assert records[('evidence_steps', 'E')]['revision'] == before['revision']
    assert records[('evidence_steps', 'E')]['payload']['statement'] == '甲是假设。'
    # The write really landed; only this read is pinned to its snapshot.
    after = store.get_record_state('evidence_steps', 'E')
    assert after['revision'] == before['revision'] + 1
    monkeypatch.setattr(type(store), '_affected_cvp_context', original)
    assert store.read_claim_context([('claims', 'C')])[('evidence_steps', 'E')]['revision'] == after['revision']


def test_claim_context_read_cannot_write(action, monkeypatch):
    store = action[0]
    seed(store)
    original = type(store)._affected_cvp_context

    def try_write(self, conn, keys):
        with conn.cursor() as cursor:
            cursor.execute("UPDATE wang_knowledge.objects SET updated_at=now() WHERE object_id='E'")
        return original(self, conn, keys)

    monkeypatch.setattr(type(store), '_affected_cvp_context', try_write)
    import psycopg
    import pytest
    with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        store.read_claim_context([('claims', 'C')])


def test_controller_reads_footprints_at_start_and_after_exits_not_every_poll(tmp_path, monkeypatch):
    from backend.pipeline import claim_management_batch as batch
    from backend.pipeline import source_reconciliation_runtime as runtime
    from backend.api.canonical_repository import postgres_store as pg, viewpoint_production_safety as safety

    @contextmanager
    def locked(*args, **kwargs):
        with (tmp_path / ('lock' + str(len(list(tmp_path.glob('lock*')))))).open('a') as fd:
            yield fd
    monkeypatch.setattr(runtime, 'maintenance_lock', locked)
    monkeypatch.setattr(safety, 'exclusive_cvp_run_lock', locked)
    monkeypatch.setattr(batch.time, 'sleep', lambda s: None)
    monkeypatch.setenv('DATA_BASE_DIR', str(tmp_path))
    # a and b share Claim X, so b waits for a; a polls running many times.
    requests = {k: (SimpleNamespace(target_keys=['claims/' + k], permissions=[cm.source.Permission(
        collection='claims', object_id='X', fields=['statement'])], source_bindings={}), tmp_path / k) for k in ['a', 'b']}
    monkeypatch.setattr(batch, 'load', lambda *a: ({'max_workers': 2}, requests))
    reads = []

    class Store:
        def read_claim_context(self, keys):
            reads.append(keys[0][1])
            return {('claims', 'X'): {}}

        def claim_management_qualification(self, key):
            return {'ready': True}
    monkeypatch.setattr(pg, 'PostgresKnowledgeStore', Store)
    # This test isolates scheduler reads; SQL completion is exercised by the
    # intake and paragraph tests with the real executor and disposable store.
    monkeypatch.setattr(batch, '_paragraph_results', lambda *args: {'all_actions_completed': True, 'created_keys': []})
    started = []

    class Process:
        def __init__(self, cmd, **kwargs):
            self.key = cmd[-1]
            self.pid = 1
            self.polls = 0
            started.append(self.key)
            (tmp_path / (cm.sha256_json(self.key) + '.result.json')).write_text(json.dumps({'result': {'status': 'completed'}}))

        def poll(self):
            self.polls += 1
            return 0 if self.polls > 20 else None
    monkeypatch.setattr(batch.subprocess, 'Popen', Process)
    batch.run(tmp_path / 'manifest', tmp_path)
    assert started == ['a', 'b']
    # Start: both pending. After a exits: b, still pending. Never during a's 20 polls
    # or while b runs.
    assert reads == ['a', 'b', 'b']
    report = json.loads((tmp_path / 'result.json').read_text())
    assert report['all_actions_completed'] is True
