"""Legacy human_review_required is metadata, not a Flash admission gate."""
import json
import pytest
from backend.pipeline import claim_flash_statements as statements, claim_flash_arguments as arguments
from backend.tests.test_claim_flash_arguments import ready, Ports
from backend.tests.test_claim_legacy_ai_reopen import seed, NOTE
from backend.tests.test_evidence_refresh import sample
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


@pytest.mark.parametrize('fault',[None,'human','earlier_human','source_uncertain'])
def test_legacy_status_needs_no_grant_or_special_review(ready,fault):
    store,root,plan,eroot,_=ready;seed(store,fault)
    before=store.get_record_state('claims','C');ports=Ports()
    # Stale runner attributes must not turn obsolete flags into fresh work.
    ports.legacy_ai_findings_notes={'claims/C':NOTE}
    sroot=root/'legacy-statements'
    result=statements.run(store,'S',['C'],eroot,sroot,ports,apply=True)
    assert result['status']=='verified' and not result.get('deferred_legacy_ai_commit')
    result=arguments.run(store,'S',plan,'G',eroot,sroot,root/'legacy-arguments',ports,apply=True)
    assert result['status'] in {'verified','applied'} and not result['pending']
    assert not result.get('legacy_ai_findings_markers')
    request=next(req for role,req in ports.calls if role=='gpt' and req['input']['task']=='argument_completeness')
    assert 'legacy_ai_finding' not in request['input']['jobs'][0]
    # No bulk migration or invented approval for unchanged content.
    assert store.get_record_state('claims','C')['payload']==before['payload']
    replay=Ports()
    assert arguments.run(store,'S',plan,'G',eroot,sroot,root/'legacy-arguments',replay,apply=True)['status']==result['status']
    assert not replay.calls


@pytest.mark.parametrize('status',['human_hold','human_approved','approved','rejected','superseded'])
def test_actual_protected_status_still_blocks_before_models(ready,status):
    store,root,_,eroot,_=ready
    store.record_review('claims','C',decision=status,reviewer_kind='human')
    before=store.get_record_state('claims','C');ports=Ports()
    with pytest.raises(ValueError,match='protected target'):
        statements.run(store,'S',['C'],eroot,root/'protected',ports,apply=True)
    assert not ports.calls and store.get_record_state('claims','C')==before
