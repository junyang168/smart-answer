"""Fresh v2 review replaces AI sampling, never a substantive or human hold."""
import json
from pathlib import Path
import pytest
from backend.pipeline import claim_management as cm
from backend.pipeline.cvp_sql_actions import execute_claim_management
from backend.pipeline.cvp_sql_flow import binding
from backend.tests.test_claim_management_postgres import action,postgres_store,_temporary_postgres
from backend.tests.test_claim_management_execution_postgres import seed_claims,operation_request,SyntheticPorts
from backend.tests.test_claim_management_human_source_follow_up import authorize


@pytest.mark.parametrize('fault',[None,'human','source_uncertain','failed_old_review','wrong_revision','wrong_claim','ordinary_hold'])
def test_only_fresh_explicit_review_replaces_exact_legacy_ai_sampling_marker(action,fault):
    store,_,_,_,root,_=action;seed_claims(store,['C'])
    store.record_review('claims','C',decision='human_review_required',reviewer_kind='ai',reviewer_id='legacy-model')
    state=store.get_record_state('claims','C')
    resolution={'schema_version':'wang_legacy_ai_review_reconciliation_v1','claim_id':'C',
        'approval_status':'not_human_approved','independent_review_decision':'pass',
        'adjudication_status':'human_spot_check','target_review_status':'human_review_required'}
    if fault=='source_uncertain':resolution['adjudication_status']='human_confirmation'
    if fault=='failed_old_review':resolution['independent_review_decision']='changes_suggested'
    if fault=='wrong_claim':resolution['claim_id']='OTHER'
    if fault=='ordinary_hold':resolution={}
    with store.connect() as conn:
        conn.execute("UPDATE wang_knowledge.review_events SET artifact=%s::jsonb, reviewer_kind=%s, object_revision=%s WHERE collection='claims' AND object_id='C'",
            (json.dumps({'resolution':resolution}),'human' if fault=='human' else 'ai',state['revision']-1 if fault=='wrong_revision' else state['revision']))
    before=store.get_record_state('claims','C')
    assert store.claim_management_qualification('C') is None
    request,output=operation_request(action,'Update',['C']);request.permissions=[]
    policy=cm.Policy.model_validate_json(Path(request.policy.path).read_text());policy.independent_audit=False;policy.budget.gemini_calls=0
    pp=root/'fresh-review-policy.json';pp.write_text(policy.model_dump_json());request.policy=cm.source.FileBinding.model_validate(binding(pp));authorize(request)
    result=execute_claim_management(store,request,root=output,ports=SyntheticPorts(store,root))
    assert result['status']=='completed',result
    qualification=store.claim_management_qualification('C')
    assert bool(qualification and qualification.get('ready')) == (fault is None)
    if fault is None:
        assert qualification['legacy_ai_spot_check_replaced_by_current_review']
    assert store.get_record_state('claims','C')==before
    # Read-only review does not grant permission to edit the held payload.
    candidate=cm.existing_candidate({'claims/C':cm.model_state(before)})
    candidate.operation='Update';candidate.dispositions[0].action='update'
    candidate.dispositions[0].citations=[cm.Citation(source_id='S',paragraph_key='S0001',exact_quote='甲是假设，不是断言。')]
    post=dict(before['payload'],statement='different')
    candidate.records=[cm.source.Row(collection='claims',object_id='C',payload=post)]
    _,sources=cm.load_authority(request)
    with pytest.raises(ValueError,match='protected review hold'):
        cm.validate_candidate(request,{'claims/C':cm.model_state(before)},candidate,sources)
