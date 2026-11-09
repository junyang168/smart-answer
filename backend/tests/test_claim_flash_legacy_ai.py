import json
import pytest
from backend.pipeline import claim_flash_statements as statements, claim_flash_arguments as arguments
from backend.pipeline.claim_legacy_ai_reopen import marker
from backend.api.canonical_repository.postgres_store import ChangeSetConflict
from backend.tests.test_claim_flash_arguments import ready, Ports
from backend.tests.test_claim_legacy_ai_reopen import seed, NOTE
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def authorize(store,ports):
    state=store.get_record_state('claims','C')
    with store.connect() as conn,conn.cursor() as cursor:
        cursor.execute('SET TRANSACTION READ ONLY');proof=marker(cursor,'C',state)
    ports.legacy_ai_findings_refresh={'authorization_quote':'所有 human review required 都要 refresh',
                                    'claims':{'claims/C':proof}}
    ports.legacy_ai_findings_notes={'claims/C':state['payload']['review_note']}
    return ports


@pytest.mark.parametrize('fault',[None,'human','earlier_human','source_uncertain'])
def test_exact_legacy_AI_hold_waits_for_normal_argument_review(ready,fault):
    store,root,plan,eroot,_=ready;seed(store,fault);before=store.get_record_state('claims','C')
    ports=authorize(store,Ports())
    sroot=root/'held-statements'
    if fault:
        with pytest.raises(ValueError):statements.run(store,'S',['C'],eroot,sroot,ports,apply=True)
        assert store.get_record_state('claims','C')==before and not ports.calls
        return
    result=statements.run(store,'S',['C'],eroot,sroot,ports,apply=True)
    assert result['deferred_legacy_ai_commit'] and not result['applied']
    assert store.get_record_state('claims','C')==before
    result=arguments.run(store,'S',plan,'G',eroot,sroot,root/'held-arguments',ports,apply=True)
    assert result['status']=='applied' and result['claim_qualification']=='not_granted'
    after=store.get_record_state('claims','C')['payload']
    assert after['review_status']=='candidate' and after['statement']==before['payload']['statement']
    req=next(req for role,req in ports.calls if role=='gpt' and req['input']['task']=='argument_completeness')
    assert req['input']['jobs'][0]['legacy_ai_finding']==NOTE
    assert json.loads((root/'held-arguments'/'before.json').read_text())['records']['claims/C']['payload']['review_note']==NOTE


def test_human_event_without_record_change_still_blocks_legacy_AI_commit(ready,monkeypatch):
    store,root,plan,eroot,_=ready;seed(store);ports=authorize(store,Ports())
    sroot=root/'held-statements';statements.run(store,'S',['C'],eroot,sroot,ports,apply=True)
    original=store.apply_plan;before=store.get_record_state('claims','C')
    def raced(plan,**kwargs):
        if plan.source_kind==arguments.VERSION:
            with store.connect() as conn:
                conn.execute("""INSERT INTO wang_knowledge.review_events
                    (review_event_id,collection,object_id,object_revision,reviewer_kind,reviewer_id,decision,reason,artifact)
                    VALUES ('test-later-human','claims','C',%s,'human','owner','human_review_required','待人工確認','{}'::jsonb)""",(before['revision'],))
        return original(plan,**kwargs)
    monkeypatch.setattr(store,'apply_plan',raced)
    with pytest.raises(ChangeSetConflict,match='human review history changed'):
        arguments.run(store,'S',plan,'G',eroot,sroot,root/'held-arguments',ports,apply=True)
    assert store.get_record_state('claims','C')==before
