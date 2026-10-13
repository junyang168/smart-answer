"""Mandatory coverage is a review finding, not an extra model-review loop."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from backend.api.canonical_repository.viewpoint_batch_resolution import (
    BatchResolutionError, CanonicalViewpointReviewResponse,
    CoverageCanonicalViewpointReviewResponse, CanonicalViewpointProposalResponse,
    CanonicalViewpointReconsiderationResponse, validate_review,
    apply_reconsideration_patches, validate_reconsideration,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_cvp_policy import load_cvp_policy, cvp_policy_prompt_sha256s
from backend.pipeline.viewpoint_cvp_coverage_contract import VERSION, PROMPT
from backend.tests.test_viewpoint_batch_resolution import _claim, _candidate

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ROOT/'backend/pipeline/policies'


def proposal(disposition='deferred'):
    component = {'spans':[{'start_char':0, 'end_char':2, 'exact_text':'判斷'}],
                 'disposition':disposition, 'reason':'自身原文明確提出判斷'}
    candidates = []
    if disposition == 'new_viewpoint':
        component.update(local_new_viewpoint_key='NEW', evidence_step_ids=['C1-E1'], source_fragment_ids=['C1-F1'])
        candidates = [_candidate('NEW')]
    elif disposition not in {'deferred', 'no_registry_assertion'}:
        component.update(target_viewpoint_revision_id='CVR-1', evidence_step_ids=['C1-E1'], source_fragment_ids=['C1-F1'])
    return CanonicalViewpointProposalResponse.model_validate({
        'batch_id':'b', 'claim_decisions':[{'claim_id':'C1','components':[component]}],
        'new_viewpoint_candidates':candidates})


def payload(p, requirement='cvp_required', **overrides):
    covered = p.claim_decisions[0].components[0].disposition not in {'deferred','no_registry_assertion'}
    missing = requirement == 'cvp_required' and not covered
    row = {'claim_id':'C1', 'component_index':0, 'decision':'correct' if missing else 'pass',
        'finding_codes':['missing_cvp_coverage'] if missing else [],
        'reason':'依自身原文審核', 'correction':'新增來源支持的可復用觀點' if missing else None,
        'coverage_requirement':requirement, 'coverage_claim_sha256':'sha-C1',
        'coverage_evidence_step_ids':['C1-E1'], 'coverage_reason':'自身證據充分，不能因無現有目標漏掉',
        'cvp_covered':covered}
    row.update(overrides)
    return {'schema_version':'wang_canonical_viewpoint_review_v2',
        'proposal_sha256':sha256_json(p.model_dump(mode='json')), 'change_reviews':[row],
        'novelty_review':{'status':'pass','missed_claim_ids':[],'reason':'無其他漏項'}}


def validate(p, raw, claims=None):
    return validate_review(review=CoverageCanonicalViewpointReviewResponse.model_validate(raw),
        proposal=p, proposal_sha256=sha256_json(p.model_dump(mode='json')),
        claims=[_claim('C1','判斷')] if claims is None else claims)


@pytest.mark.parametrize('disposition',['deferred','no_registry_assertion'])
def test_sufficient_missing_claim_routes_to_existing_one_correction(disposition):
    p=proposal(disposition); r=CoverageCanonicalViewpointReviewResponse.model_validate(payload(p))
    assert validate(p, payload(p))['correction_required'] is True
    corrected=proposal('new_viewpoint')
    correction=CanonicalViewpointReconsiderationResponse.model_validate({
        'proposal_sha256':sha256_json(p.model_dump(mode='json')), 'review_sha256':sha256_json(r.model_dump(mode='json')),
        'finding_dispositions':[{'claim_id':'C1','component_index':0,'disposition':'accepted','reason':'補回漏項'}],
        'component_patches':[{'claim_id':'C1','component_index':0,'replacement_components':[
            corrected.claim_decisions[0].components[0].model_dump(mode='json')]}],
        'candidate_patches':[{'action':'upsert','local_key':'NEW','candidate':_candidate('NEW')}]})
    assert validate_reconsideration(reconsideration=correction,proposal=p,review=r,
        proposal_sha256=correction.proposal_sha256,review_sha256=correction.review_sha256)['outcome']=='resolved'
    assert apply_reconsideration_patches(reconsideration=correction,proposal=p,review=r)==corrected
    assert validate(corrected,payload(corrected))['outcome']=='pass'


@pytest.mark.parametrize('decision',['pass','defer','reject'])
def test_sufficient_missing_claim_cannot_be_passed_or_deferred(decision):
    p=proposal(); raw=payload(p,decision=decision,
        finding_codes=[] if decision=='pass' else ['missing_cvp_coverage'],correction=None)
    with pytest.raises(ValidationError,match='requires a correct finding'):
        CoverageCanonicalViewpointReviewResponse.model_validate(raw)


@pytest.mark.parametrize('requirement',['evidence_insufficient','attribution_unresolved','source_conflict','human_hold','observation_only'])
def test_real_exceptions_are_not_forced_into_cvp(requirement):
    p=proposal()
    assert validate(p,payload(p,requirement= requirement,coverage_reason='具體未決／背景理由'))['outcome']=='pass'


@pytest.mark.parametrize('disposition',['member_existing','support_existing','qualification_existing','tension_existing','new_viewpoint'])
def test_actual_target_is_coverage_not_a_requirement_to_create_duplicate(disposition):
    p=proposal(disposition)
    assert validate(p,payload(p))['outcome']=='pass'


@pytest.mark.parametrize('fault',['fake_target','wrong_sha','foreign_evidence','ineligible','missing_claim','extra_component'])
def test_version_and_own_evidence_guards(fault):
    p=proposal(); raw=payload(p); claims=[_claim('C1','判斷')]
    if fault=='fake_target':raw['change_reviews'][0]['cvp_covered']=True
    elif fault=='wrong_sha':raw['change_reviews'][0]['coverage_claim_sha256']='other'
    elif fault=='foreign_evidence':raw['change_reviews'][0]['coverage_evidence_step_ids']=['C2-E1']
    elif fault=='ineligible':claims=[_claim('C1','判斷',eligible=False)]
    elif fault=='missing_claim':claims=[]
    else:raw['change_reviews'][0]['component_index']=1
    with pytest.raises(BatchResolutionError):validate(p,raw,claims)


@pytest.mark.parametrize('field',['coverage_requirement','coverage_claim_sha256','coverage_evidence_step_ids','coverage_reason','cvp_covered'])
def test_missing_coverage_fields_never_trigger_model_format_repair(tmp_path,field):
    from backend.pipeline.viewpoint_review_format_repair import parse_review
    p=proposal();raw=payload(p);raw['change_reviews'][0].pop(field)
    adapter=SimpleNamespace(_response_model=CoverageCanonicalViewpointReviewResponse)
    with pytest.raises(ValidationError):
        parse_review(raw,adapter=adapter,request={},cache=tmp_path/'repair.json',
            call=lambda *a:pytest.fail('missing semantic answer is not format-only'))


def test_old_review_cannot_be_relabelled_new_approval(tmp_path):
    from backend.pipeline.viewpoint_review_format_repair import parse_review
    p=proposal();old=payload(p);old['schema_version']='wang_canonical_viewpoint_review_v1'
    for k in list(old['change_reviews'][0]):
        if k.startswith('coverage_') or k=='cvp_covered':del old['change_reviews'][0][k]
    assert CanonicalViewpointReviewResponse.model_validate(old).outcome()=='findings'
    with pytest.raises(ValidationError):
        parse_review(old,adapter=SimpleNamespace(_response_model=CoverageCanonicalViewpointReviewResponse),
            request={},cache=tmp_path/'repair.json',call=lambda *a:pytest.fail('no review replay'))


def test_new_policy_changes_all_prompt_identities_without_changing_old_prompts(monkeypatch):
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    old=load_cvp_policy(POLICIES/'wang_cvp_resolution_policy_v5.json')
    new=load_cvp_policy(POLICIES/'wang_cvp_resolution_policy_v6.json')
    assert new['coverage_contract_version']==VERSION
    prompt_dir=ROOT/'backend/pipeline/prompts'
    a=cvp_policy_prompt_sha256s(old,prompt_dir=prompt_dir)
    b=cvp_policy_prompt_sha256s(new,prompt_dir=prompt_dir)
    assert all(a[role]!=b[role] for role in a)
    for role in old['prompts']:
        assert PROMPT not in runner._role_prompt(old,role)
        assert runner._role_prompt(new,role).endswith(PROMPT)
        assert old[role]==new[role]
    for field in ['max_request_bytes','batch_size','call_timeout_seconds','validator_version']:
        assert old[field]==new[field]
    monkeypatch.setattr(runner,'_subscription_client',lambda *a:SimpleNamespace(model=a[1],backend='claude_subscription',reasoning_effort='high'))
    assert runner.build_reviewer('claude-opus-5-5','high',cvp_policy=new)._response_model is CoverageCanonicalViewpointReviewResponse
    assert runner.build_reviewer('claude-opus-5-5','high',cvp_policy=old)._response_model is CanonicalViewpointReviewResponse


def test_compiler_does_not_use_initial_missing_review_to_approve_corrected_package():
    from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package,CvpBatchChangeSetError
    p=proposal();r=CoverageCanonicalViewpointReviewResponse.model_validate(payload(p))
    with pytest.raises(CvpBatchChangeSetError,match='passing review of effective proposal'):
        compile_cvp_batch_package(proposal=p,review=r,claims=[_claim('C1','判斷')],registry_context=[],
            deterministic_validation_sha256='d',scope_manifest_sha256='s',proposal_artifact_sha256='p',
            review_artifact_sha256='r',proposer_model_id='gpt',reviewer_model_id='opus',decided_at='2026-10-07T00:00:00Z')


@pytest.mark.parametrize('actually_corrected',[True,False])
def test_runtime_calls_gpt_one_correction_then_opus_final_once(tmp_path,actually_corrected):
    from backend.pipeline.viewpoint_batch_resolution_runner import run_batch
    calls=[]
    original=proposal()
    effective=proposal('new_viewpoint') if actually_corrected else original

    class Adapter:
        def __init__(self,role,model):
            self.role=role;self.model_id=model;self.backend=role+'_subscription'
            self.prompt_sha256=role;self.generation_config_sha256=role
            if role=='opus':self._response_model=CoverageCanonicalViewpointReviewResponse

        def generate(self,request):
            calls.append(self.role)
            if self.role=='proposal':return original.model_dump(mode='json')
            if self.role=='opus':
                return payload(CanonicalViewpointProposalResponse.model_validate(request['proposal']))
            return {'proposal_sha256':request['proposal_sha256'],'review_sha256':request['review_sha256'],
                'finding_dispositions':[{'claim_id':'C1','component_index':0,'disposition':'accepted','reason':'補回遺漏'}],
                'component_patches':[{'claim_id':'C1','component_index':0,
                    'replacement_components':[effective.claim_decisions[0].components[0].model_dump(mode='json')]}],
                'candidate_patches':[{'action':'upsert','local_key':'NEW','candidate':_candidate('NEW')}] if actually_corrected else []}

    report=run_batch(batch_id='b',scope_label='synthetic',claims=[_claim('C1','判斷')],registry_context=[],
        pending_candidates=[],output_dir=tmp_path,proposer=Adapter('proposal','gpt-6.1-sol'),
        reviewer=Adapter('opus','claude-opus-5-5'),reconsiderer=Adapter('correction','gpt-6.1-sol'))
    assert calls==['proposal','opus','correction','opus']
    assert report['approval_outcome']==('pass' if actually_corrected else 'findings')
    assert (tmp_path/'final-review.json').exists()
    assert report['_effective_proposal']==effective


@pytest.mark.parametrize('drift',[False,True])
def test_campaign_explicitly_pins_new_policy_no_implicit_upgrade(drift):
    from backend.pipeline import viewpoint_cvp_campaign_runner as campaign
    from backend.pipeline.viewpoint_semantic_contract import CONTRACT_PATH
    path=POLICIES/'wang_cvp_resolution_policy_v6.json'
    args=SimpleNamespace(cvp_policy=path,cvp_policy_sha256='changed' if drift else campaign.binding(path)['file_sha256'],
        semantic_contract_sha256=campaign.binding(CONTRACT_PATH)['file_sha256'])
    if drift:
        with pytest.raises(ValueError):campaign.campaign_policy(args)
    else:
        selected,bindings=campaign.campaign_policy(args)
        assert selected==str(path) and bindings['cvp_policy']['path']==str(path)
