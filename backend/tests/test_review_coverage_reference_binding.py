from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from backend.pipeline.viewpoint_review_format_repair import bind_coverage_evidence_references, parse_review
from backend.api.canonical_repository.viewpoint_batch_resolution import CoverageCanonicalViewpointReviewResponse


def fixture():
    request={'packet':{'claims':[{'claim_id':'C','evidence':[
        {'evidence_step_id':'E','source_fragment_id':'F1','valid_for_identity_review':True},
        {'evidence_step_id':'E','source_fragment_id':'F2','valid_for_identity_review':True}]}]}}
    response={'schema_version':'wang_canonical_viewpoint_review_v2','proposal_sha256':'p',
        'change_reviews':[{'claim_id':'C','component_index':0,'decision':'correct',
            'finding_codes':['missed_viewpoint'],'reason':'原理由不改','correction':'原纠正不改',
            'coverage_requirement':'cvp_required','coverage_claim_sha256':'sha',
            'coverage_evidence_step_ids':['F1','F2'],'coverage_reason':'完整原判读', 'cvp_covered':True}],
        'novelty_review':{'status':'pass','missed_claim_ids':[],'reason':'完整'},
        'structure_reviews':[],'relation_reviews':[],'revision_reviews':[]}
    return request,response


def test_exact_fragments_same_step_preserve_all_original_choices_and_verdict(tmp_path):
    request,response=fixture();original=deepcopy(response);oldrequest=deepcopy(request)
    bound,receipts=bind_coverage_evidence_references(response,request)
    assert bound['change_reviews'][0]['coverage_evidence_step_ids']==['E']
    comparable=deepcopy(bound);comparable['change_reviews'][0]['coverage_evidence_step_ids']=['F1','F2']
    assert comparable==original and response==original and request==oldrequest
    assert receipts[0]['original_references']==['F1','F2']
    adapter=SimpleNamespace(_response_model=CoverageCanonicalViewpointReviewResponse)
    def nocall(*a,**kw):raise AssertionError('unexpected model call')
    for _ in range(2):
        review,changes,calls,_=parse_review(response,adapter=adapter,request=request,cache=tmp_path/'raw-review-format-repair.json',call=nocall)
        assert calls==0 and review.change_reviews[0].decision=='correct'
        assert review.change_reviews[0].coverage_evidence_step_ids==['E']
    receipt=json.loads((tmp_path/'raw-review-format-repair-coverage-reference-binding.json').read_text())
    assert receipt['bindings']==receipts and receipt['content_decisions_changed'] is False


@pytest.mark.parametrize('fault',['foreign','ineligible','ambiguous','duplicate_fragment','duplicate_claim','duplicate_reference','spelling','namespace_collision'])
def test_alias_binding_refuses_unsafe_references(fault):
    request,response=fixture();e=request['packet']['claims'][0]['evidence']
    if fault=='foreign':response['change_reviews'][0]['coverage_evidence_step_ids']=['OTHER']
    elif fault=='ineligible':e[0]['valid_for_identity_review']=False
    elif fault=='ambiguous':e.append(dict(e[0],evidence_step_id='OTHER'))
    elif fault=='duplicate_fragment':e.append(deepcopy(e[0]))
    elif fault=='duplicate_claim':request['packet']['claims'].append(deepcopy(request['packet']['claims'][0]))
    elif fault=='duplicate_reference':response['change_reviews'][0]['coverage_evidence_step_ids']=['F1','F1']
    elif fault=='namespace_collision':
        e.append({'evidence_step_id':'OTHER','source_fragment_id':'E','valid_for_identity_review':True})
        response['change_reviews'][0]['coverage_evidence_step_ids']=['E','F1']
    else:response['change_reviews'][0]['coverage_evidence_step_ids']=['F1-typo']
    with pytest.raises(ValueError):bind_coverage_evidence_references(response,request)


def test_legacy_and_other_fields_remain_exact():
    request,response=fixture();response['change_reviews'][0]['coverage_evidence_step_ids']=['E','E']
    bound,receipt=bind_coverage_evidence_references(response,request)
    assert bound==response and receipt==[]  # Strict existing schema still rejects duplicates.


def test_actual_saved_common_compiler_reaches_only_unused_correction(tmp_path,monkeypatch):
    import os,hashlib
    from pathlib import Path
    run=os.environ.get('WKP_COVERAGE_REFERENCE_ACTUAL')
    if not run:pytest.skip('explicit saved current SQL scope; no calls/writes')
    from dotenv import load_dotenv
    from backend.tests.test_statement_impact_completion import ReadOnly
    from backend.pipeline import cvp_sql_flow as flow,viewpoint_batch_resolution_runner as runner
    from backend.pipeline import viewpoint_review_format_repair as fmt
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    from backend.pipeline import cvp_batch_identity_integration as integration
    from contextvars import ContextVar
    monkeypatch.setattr(integration,'ENABLED',ContextVar('actual_integration',default=True))
    load_dotenv('.env');store=ReadOnly();value=store.get_cvp_group(run)
    materials=value['materials']
    root=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059/execution')/run/'normal-review'
    before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
    with store.connect() as conn,conn.cursor() as cur:
        store._assert_transaction_read_pins(cur,flow.decoded_pins(materials['read_pins']))
    calls=[];original_call=runner._call
    class UnusedCorrection(Exception):pass
    def saved_only(adapter,payload,path):
        if not path.exists():
            assert path.name=='raw-reconsideration.json'
            calls.append((path.name,payload));raise UnusedCorrection
        return original_call(adapter,payload,path)
    monkeypatch.setattr(runner,'_call',saved_only)
    monkeypatch.setattr(runner,'_write_immutable',lambda *a,**kw:None)
    monkeypatch.setattr(runner,'_write_derived',lambda *a,**kw:None)
    monkeypatch.setattr(fmt,'write_immutable',lambda *a,**kw:None)
    monkeypatch.setattr(runtime,'write_immutable',lambda *a,**kw:None)
    with runtime.authorized_request_limit(root.parents[1],value['campaign_sha256'],root.parents[2]/'owner-request-limit-600k.json'):
        with pytest.raises(UnusedCorrection):flow.reviewed_cvp_package(root,materials,store)
    assert len(calls)==1
    review=calls[0][1]['review']
    assert sum(x['decision']=='correct' for x in review['change_reviews'])>=1
    assert all(not any(i.startswith('FR-') for i in x['coverage_evidence_step_ids']) for x in review['change_reviews'])
    assert store.get_cvp_group(run)==value
    assert before=={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
