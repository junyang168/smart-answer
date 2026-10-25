"""Explicit opt-in: authenticate saved production stages without calls/writes."""
import hashlib
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(os.getenv('WKP357_G113_READONLY') != '1', reason='explicit actual saved SQL read-only')
def test_saved_relation_reversal_reaches_unused_final_without_replaying(monkeypatch):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline import cvp_sql_flow as sql, viewpoint_resolution_runtime as rt
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    load_dotenv('.env')
    root = Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')
    run = 'CVPG-5c89052481d61e46258cf5779b8f3671bddfe808c152de22413fd559cfde01ef'
    out = root/run/'normal-review'
    store = PostgresKnowledgeStore()
    group = store.get_cvp_group(run)
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    calls = []
    class FinalBoundary(Exception):
        pass
    def saved(adapter, payload, cache):
        if cache.name == 'raw-final-review.json':
            assert not cache.exists()
            assert not cache.with_name('raw-final-review.call-intent.json').exists()
            raise FinalBoundary()
        assert cache.exists(), cache.name
        adapter = rt.response_adapter(adapter, rt.read_artifact(cache))
        response = rt.read_saved_model_response(adapter, payload, cache)
        calls.append(cache.name)
        return rt.bound_model_response(adapter, payload, response, cache, persist=False), 0, 0.0
    def immutable(path, value, **kwargs):
        if Path(path).exists():
            assert rt.read_artifact(path) == value, path
    monkeypatch.setattr(runner, '_call', saved)
    for obj, name in ((rt, 'write_immutable'), (runner, '_write_immutable'), (sql, 'write_immutable')):
        monkeypatch.setattr(obj, name, immutable)
    monkeypatch.setattr(runner, '_write_derived', lambda *a, **k: None)
    monkeypatch.setattr(runner, '_write_current_state', lambda *a, **k: None)
    monkeypatch.setattr(rt, 'validating_saved_artifacts', lambda: True)
    monkeypatch.setattr(store, 'apply_plan', lambda *a, **k: pytest.fail('Registry mutation'))
    integration = ENABLED.set(True)
    try:
        with store.connect() as conn, conn.cursor() as cursor:
            store._assert_transaction_read_pins(cursor, sql.decoded_pins(group['materials']['read_pins']))
        with pytest.raises(FinalBoundary):
            sql.reviewed_cvp_package(out, group['materials'], store)
        assert calls == ['raw-proposal.json', 'raw-review.json', 'raw-reconsideration.json']
        assert before == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
        assert store.get_cvp_group(run) == group
        print('Authenticated three saved responses, full physical sources and', len(group['materials']['read_pins']), 'SQL pins; unused final boundary reached; zero calls/writes.')
    finally:
        ENABLED.reset(integration)
