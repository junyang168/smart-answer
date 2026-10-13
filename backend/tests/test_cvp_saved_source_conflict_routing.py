"""Actual saved input: source conflicts never enter retained-link separation."""
import hashlib
import os
from pathlib import Path

import pytest


@pytest.mark.skipif(os.getenv('WKP357_SAVED_SOURCE_CONFLICT_TEST')!='1',reason='actual saved SQL read-only opt-in')
@pytest.mark.parametrize('mode',['source-route','all-saved-authentication'])
def test_saved_source_conflict_routes_without_model_or_write(monkeypatch,mode):
    from dotenv import load_dotenv
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.viewpoint_request_encoding import canonical_json
    from backend.pipeline import cvp_sql_flow as flow, viewpoint_batch_resolution_runner as runner
    from backend.pipeline import claim_issue_management as management
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact,response_adapter,bound_model_response,read_saved_model_response
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            conn=super().connect();conn.execute('SET TRANSACTION READ ONLY');return conn
        def _assert_transaction_read_pins(self,cursor,pins):
            class Cursor:
                def execute(self,q,args):return cursor.execute(q.removesuffix(' FOR UPDATE'),args)
                def __getattr__(self,name):return getattr(cursor,name)
            return super()._assert_transaction_read_pins(Cursor(),pins)
    store=ReadOnly()
    rid='CVPG-b9bdf23b7ec4f63b20fa5d69818109697a8fb808138b18557dffe2e225369031'
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')/rid/'normal-review'
    saved=store.get_cvp_group(rid);materials=saved['materials']
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    with store.connect() as conn,conn.cursor() as cursor:
        store._assert_transaction_read_pins(cursor,flow.decoded_pins(materials['read_pins']))
    calls=[]
    def immutable(path,value,**kwargs):
        assert path.exists() and canonical_json(read_artifact(path))==canonical_json(value)
    def saved_only(model,packet,cache):
        assert cache.name in {'raw-proposal.json','raw-consolidation.json','raw-review.json','raw-reconsideration.json'}
        adapter=response_adapter(model,read_artifact(cache))
        raw=read_saved_model_response(adapter,packet,cache)
        calls.append(cache.name)
        return bound_model_response(adapter,packet,raw,cache,persist=False),0,0.0
    monkeypatch.setattr(runner,'_write_immutable',immutable)
    monkeypatch.setattr(runner,'_call',saved_only)
    if mode=='all-saved-authentication':
        # Diagnostic ONLY: reproduce the legacy missing route to authenticate
        # its already consumed correction. The unchanged source finding still
        # fails separation; there is no new final review, approval or write.
        monkeypatch.setattr(management,'review_findings',lambda *args:[])
    token=ENABLED.set(True)
    try:
        if mode=='source-route':
            with pytest.raises(management.IssuesDetected) as found:
                flow.reviewed_cvp_package(root,materials,store)
            assert len(found.value.findings)==1
            assert found.value.findings[0]['claim_id']=='DK-1004660290a2-P06-CL004'
            assert found.value.origin=='opus_initial'
        else:
            with pytest.raises(ValueError,match='new/source/unknown finding'):
                flow.reviewed_cvp_package(root,materials,store)
    finally:ENABLED.reset(token)
    assert len(calls)==(3 if mode=='source-route' else 4)
    assert store.get_cvp_group(rid)==saved
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
