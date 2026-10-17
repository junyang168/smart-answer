"""Batch preflight uses registered source formats, exactly as the operation does."""
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm,claim_management_batch as batch
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import operation_request,seed_claims
from backend.tests.test_claim_owner_call_extension import authorize


@pytest.mark.parametrize('kind',['json','notes','missing','drift'])
def test_batch_authenticates_registered_source_without_guessing_format(action,kind):
    store,_,_,_,tmp,_=action;seed_claims(store,['C']);request,root=operation_request(action,'Update',['C'])
    if kind in {'notes','drift'}:
        source=tmp/'notes.md';source.write_text('# Notes\n\n甲是假设，不是断言。\n')
        request.source_bindings={'N':cm.source.FileBinding.model_validate(binding(source))}
        state=store.get_record_state('source_documents','S');payload=state['payload']
        payload.update(source_id='N',transcript_id='N',source_type='notes_manuscript',source_file_sha256=binding(source)['file_sha256'] if kind=='notes' else '0'*64)
        store.apply_plan(store.plan_package({'package_id':'registered-notes','source_documents':[payload]}))
        claim=store.get_record_state('claims','C')['payload'];claim['occurrences']=[{'source_id':'N','anchors':[]}]
        store.apply_plan(store.plan_package({'package_id':'notes-claim','claims':[claim]}))
    elif kind=='missing':
        with store.connect() as conn:conn.execute("DELETE FROM wang_knowledge.objects WHERE collection='source_documents' AND object_id='S'")
    policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text());authorize(request,policy,tmp/'policy.json')
    req=tmp/'request.json';req.write_text(request.model_dump_json());manifest=tmp/'manifest.json';manifest.write_text(json.dumps({'max_workers':1,'jobs':[{'request':binding(req),'root':str(root)}]}))
    if kind in {'missing','drift'}:
        with pytest.raises(ValueError):batch.load(manifest,tmp/'batch',store=store)
    else:
        plan,jobs=batch.load(manifest,tmp/'batch',store=store)
        assert jobs[request.action_id][0]==request
    assert store.get_cvp_group('CMA-'+cm.sha256_json(request.action_id)) is None
