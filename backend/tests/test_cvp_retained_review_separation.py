"""Separation never changes committed semantic facts or fabricates a pass."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.pipeline import cvp_retained_review_separation as flow
from backend.pipeline.viewpoint_resolution_runtime import write_immutable, read_artifact
from backend.api.canonical_repository.postgres_store import record_content_sha, sha256_json
from backend.api.canonical_repository.viewpoint_batch_changeset import reuse_exact_current_links, CvpBatchChangeSetError
from test_viewpoint_batch_resolution import _claim, _proposal, _component, ROCK_STATEMENT
from test_claim_statement_revision import link


@pytest.fixture
def sample(tmp_path,monkeypatch):
    claim=_claim('C1',ROCK_STATEMENT).model_dump(mode='json')
    # One Evidence has two fragments; never collapse it to one arbitrary row.
    second=deepcopy(claim['evidence'][0]);second['source_fragment_id']='C1-F2'
    claim['evidence'].append(second)
    spans=[{'start_char':0,'end_char':len(ROCK_STATEMENT),'exact_text':ROCK_STATEMENT}]
    proposal=_proposal(new_viewpoint_candidates=[],claim_decisions=[{'claim_id':'C1','components':[
        _component(ROCK_STATEMENT,ROCK_STATEMENT,'member_existing',target_viewpoint_revision_id='R',
                   source_fragment_ids=['C1-F1','C1-F2'])]}]).model_dump(mode='json')
    old={'viewpoint_claim_link_id':'L','claim_id':'C1','pinned_claim_revision':1,
         'viewpoint_id':'V','validated_against_viewpoint_revision_id':'R','decision_id':'D',
         'effective_state':'active','review_status':'system_approved','link_type':'equivalent_component',
         'component_locator':{'canonical_spans':spans},
         'evidence_bindings':[{'evidence_step_id':'C1-E1','source_fragment_id':f} for f in ('C1-F1','C1-F2')]}
    review={'change_reviews':[{'claim_id':'C1','component_index':0,'decision':'reject',
        'finding_codes':['member_target_adds_load_bearing_content'],'reason':'局部摘要未复述完整上下文'}],
        'novelty_review':{'status':'pass'},'structure_reviews':[],'relation_reviews':[],'revision_reviews':[]}
    raw={'model_id':'original-model','backend':'original-subscription','prompt_sha256':'p',
         'generation_config_sha256':'g','request_payload_sha256':'q',
         'response':{'change_reviews':[{'claim_id':'C1','component_index':0,'decision':'pass',
                     'reason':'在教授同一教导的语境中，所指内容明确，欠定归入精确命题正确。'}]}}
    raw['response_sha256']=sha256_json(raw['response']);raw['artifact_sha256']=sha256_json(raw)
    p1=tmp_path/'original-review.json';write_immutable(p1,raw)
    r2=deepcopy(raw);r2['request_payload_sha256']='other';r2['response']={'context':'原生成完整语境'}
    r2['response_sha256']=sha256_json(r2['response']);r2['artifact_sha256']=sha256_json({k:v for k,v in r2.items() if k!='artifact_sha256'})
    p2=tmp_path/'original-proposal.json';write_immutable(p2,r2)
    policy={'generation_artifacts':flow.generation_artifacts([p1,p2]),'schema_version':flow.VERSION}
    decision={'resolved_viewpoint_id':'V','approval_basis':'dual_model_consensus',
        'review_status':'system_approved','review_artifact_sha256':raw['artifact_sha256'],
        'semantic_call_artifact_sha256s':[raw['artifact_sha256'],r2['artifact_sha256']],
        'reviewer_model_ids':['original-model'],
        'claim_link_decisions':[{'claim_id':'C1','link_type':'equivalent_component'}]}
    payloads={('viewpoint_claim_links','L'):old,('viewpoint_identity_decisions','D'):decision,
        ('canonical_viewpoints','V'):{'viewpoint_id':'V','current_revision_id':'R','review_status':'system_approved'},
        ('viewpoint_revisions','R'):{'viewpoint_id':'V','review_status':'system_approved','core_proposition':'同一教导中明确的命题'}}
    records={k:{'revision':1,'content_sha256':record_content_sha(v),'payload':v,'retired':False} for k,v in payloads.items()}
    store=SimpleNamespace(get_record_state=lambda col,oid:deepcopy(records.get((col,oid))))
    monkeypatch.setattr(flow,'policy_for',lambda root:deepcopy(policy))
    root=tmp_path/'stages';root.mkdir()
    for n in ('raw-proposal.json','raw-consolidation.json','raw-review.json','raw-reconsideration.json'):
        write_immutable(root/n,{'saved':n,'verdicts':'original preserved'})
    packet={'claims':[claim],'retained_existing_claim_links':[old]}
    kwargs={'original':proposal,'revised':deepcopy(proposal),'review':review,'packet':packet,
            'reconsideration':{},'source_loader':lambda ids:[{'source_id':'S1','paragraph_texts':['完整自身正文，逐字保留。']}]}
    return root,store,records,kwargs,policy


def test_exact_original_context_separated_zero_mutation_and_zero_new_correction(sample):
    root,store,records,kw,policy=sample;before=deepcopy(records)
    raw_before={p.name:p.read_bytes() for p in root.glob('raw*.json')}
    projected,receipt=flow.build_boundary(root,store,**kw)
    assert projected.model_dump(mode='json')==kw['original']
    assert receipt['representation_projection']==[]
    assert receipt['registry_mutations']==receipt['additional_correction_calls']==0
    assert receipt['old_raw_verdicts_changed'] is False and receipt['all_pending_impacts_preserved'] is True
    assert receipt['original_findings'][0]['decision']=='reject'
    assert '教授同一教导' in str(receipt['prior_current_approvals'])
    assert len(receipt['current_read_pins'])==4 and len(receipt['own_source_bodies'])==1
    assert records==before and raw_before=={p.name:p.read_bytes() for p in root.glob('raw*.json')}
    assert flow.build_boundary(root,store,**kw)[1]==receipt


def test_explicit_retained_doubt_still_requires_exact_existing_connection(sample):
    root,store,records,kw,_=sample
    kw['review']['change_reviews'][0]['finding_codes']=[
        'proposal_disposition_contradicts_reason','retained_link_equivalence_doubt']
    before=deepcopy(records)
    assert flow.build_boundary(root,store,**kw)[1]['original_findings'][0]['decision']=='reject'
    assert records==before


@pytest.mark.parametrize('codes', [['retained_link_equivalence_doubt'],
    ['proposal_disposition_contradicts_reason'],
    ['retained_link_equivalence_doubt','proposal_disposition_contradicts_reason','evidence_insufficient']])
def test_retained_doubt_does_not_admit_other_findings(sample,codes):
    _,_,_,kw,_=sample
    kw['review']['change_reviews'][0]['finding_codes']=codes
    with pytest.raises(ValueError):flow.match_retained(kw['original'],kw['review'],kw['packet']['retained_existing_claim_links'])


def test_generation_leaf_append_preserves_original_binding(sample):
    root,_,_,_,policy=sample
    path=root/'binding.json'
    original={'campaign_sha256':'campaign','policy':'policy','generation_artifacts':policy['generation_artifacts'][:1]}
    flow.persist_generation_binding(path,original);before=path.read_bytes()
    value={**original,'generation_artifacts':policy['generation_artifacts']}
    flow.persist_generation_binding(path,value);flow.persist_generation_binding(path,value)
    assert path.read_bytes()==before
    assert len(list((root/'retained-generation-leaves').glob('*.json')))==1
    with pytest.raises(ValueError):flow.persist_generation_binding(path,{**value,'campaign_sha256':'foreign'})
    with pytest.raises(ValueError):flow.persist_generation_binding(path,{**value,'generation_artifacts':[]})


def test_no_candidates_requires_no_fictitious_identity_call(sample):
    root,store,_,kw,_=sample
    (root/'raw-consolidation.json').unlink()
    assert len(flow.build_boundary(root,store,**kw)[1]['original_responses'])==3


@pytest.mark.parametrize('fault',['candidate','pending','intent','encoding','budget'])
def test_missing_identity_response_not_skipped_when_required_or_unknown(sample,fault):
    root,store,_,kw,_=sample
    (root/'raw-consolidation.json').unlink()
    if fault=='candidate':kw['original']['new_viewpoint_candidates']=[{'local_key':'new'}]
    elif fault=='pending':kw['packet']['pending_candidates']=[{'id':'pending'}]
    else:
        suffix={'intent':'.call-intent.json','encoding':'.request-encoding.json','budget':'.request-budget.json'}[fault]
        write_immutable(root/('raw-consolidation'+suffix),{'unknown':True})
    with pytest.raises(ValueError,match='consolidation'):flow.build_boundary(root,store,**kw)


@pytest.mark.parametrize('fault',['missing','retired','sha','head','cvr','hold','decision','review','fragment','claim-version','source','raw','ambiguous','new-finding','empty-finding'])
def test_uncertain_or_foreign_proof_never_becomes_retained_pass(sample,fault):
    root,store,rows,kw,policy=sample
    if fault=='missing':del rows['viewpoint_identity_decisions','D']
    elif fault=='retired':rows['viewpoint_claim_links','L']['retired']=True
    elif fault=='sha':rows['viewpoint_identity_decisions','D']['content_sha256']='foreign'
    elif fault=='head':rows['canonical_viewpoints','V']['payload']['current_revision_id']='foreign'
    elif fault=='cvr':rows['viewpoint_revisions','R']['payload']['viewpoint_id']='foreign'
    elif fault=='hold':
        row=rows['viewpoint_revisions','R'];row['payload']['review_status']='human_review_required';row['content_sha256']=record_content_sha(row['payload'])
    elif fault=='decision':
        row=rows['viewpoint_identity_decisions','D'];row['payload']['claim_link_decisions']=[];row['content_sha256']=record_content_sha(row['payload'])
    elif fault=='review':policy['generation_artifacts']=[]
    elif fault=='fragment':kw['packet']['claims'][0]['evidence'].pop()
    elif fault=='claim-version':kw['packet']['claims'][0]['pinned_claim_revision']=2
    elif fault=='source':kw['source_loader']=lambda ids:[]
    elif fault=='raw':(root/'raw-reconsideration.json').unlink()
    elif fault=='ambiguous':kw['packet']['retained_existing_claim_links'].append(deepcopy(kw['packet']['retained_existing_claim_links'][0]))
    elif fault=='new-finding':kw['review']['change_reviews'][0]['finding_codes']=['over_merge']
    else:kw['review']['change_reviews'][0]['finding_codes']=[]
    with pytest.raises(ValueError):flow.build_boundary(root,store,**kw)
    assert not (root/'retained-review-boundary.json').exists()


@pytest.mark.parametrize('suffix',['.json','.call-intent.json','.request-encoding.json','.request-budget.json'])
def test_reserved_unknown_or_returned_final_cannot_get_another_input(sample,suffix):
    root,store,_,kw,_=sample;write_immutable(root/('raw-final-review'+suffix),{'existing':'not replayed'})
    with pytest.raises(ValueError,match='reserved/returned'):flow.build_boundary(root,store,**kw)


@pytest.mark.parametrize('category',['structure','relation','revision','novelty'])
def test_new_findings_not_cleared_by_old_connection_boundary(sample,category):
    root,store,_,kw,_=sample
    if category=='novelty':kw['review']['novelty_review']['status']='fail'
    else:kw['review'][category+'_reviews']=[{'decision':'reject'}]
    with pytest.raises(ValueError):flow.build_boundary(root,store,**kw)


def test_accepted_new_structure_still_gets_real_unused_independent_final(sample):
    root,store,_,kw,_=sample
    kw['review']['structure_reviews']=[{'decision':'correct'}]
    kw['reconsideration']['structure_dispositions']=[{'disposition':'accepted'}]
    assert flow.build_boundary(root,store,**kw)[1]['registry_mutations']==0


@pytest.mark.parametrize('decision',['pass','reject','defer'])
def test_real_final_uncertainty_separate_human_handoff_not_retry_or_fake_pass(sample,decision):
    root,store,_,kw,_=sample;_,receipt=flow.build_boundary(root,store,**kw)
    write_immutable(root/'raw-final-review.json',{'synthetic_test_only':True,'actual_verdict':decision})
    final={'change_reviews':[{'claim_id':'C1','component_index':0,'decision':decision,'reason':'完整上下文后仍拿不准' if decision!='pass' else '保留'}]}
    before=deepcopy(final);flow.pending_maintenance(root,receipt,final)
    path=root/'retained-connection-maintenance-handoff.json'
    assert final==before and path.exists() is (decision!='pass')
    if path.exists():
        result=read_artifact(path)
        assert result['status']=='requires_human_content_judgment'
        assert result['items'][0]['link_id']=='L' and result['items'][0]['final_finding']['decision']==decision
        assert result['additional_calls_allowed']==result['registry_mutations']==0
        assert result['group_completion_granted'] is False


@pytest.mark.parametrize('field,value',[('human_content_approval',True),('additional_gpt_corrections',1),
    ('additional_gpt_corrections',False),('new_registry_write_permissions',['links']),
    ('independent_affected_audit_required',False)])
def test_policy_never_content_approval_retry_or_audit_waiver(field,value):
    path=Path(flow.__file__).parent/'policies/wang_cvp_retained_review_separation_v1.json'
    p=read_artifact(path);flow.validate_policy(p);p[field]=value
    with pytest.raises(ValueError):flow.validate_policy(p)


def test_explicit_null_retention_does_not_backfill_sql_locator():
    old=link({'claim_id':'CL-test','revision':1});locator=deepcopy(old['component_locator'])
    old['link_type']='supports';old['component_locator']=None
    fresh=deepcopy(old);fresh['viewpoint_claim_link_id']='fresh'
    fresh['component_locator']=locator
    with pytest.raises(CvpBatchChangeSetError):reuse_exact_current_links([fresh],[old])
    assert reuse_exact_current_links([fresh],[old],retained_null_link_ids=['VCL-test'])==[old]
    assert old['component_locator'] is None
    for field,value in [('link_type','equivalent_component'),('pinned_claim_revision',2),
                        ('viewpoint_id','foreign'),('evidence_bindings',[])]:
        changed=deepcopy(fresh);changed[field]=value
        with pytest.raises(CvpBatchChangeSetError):reuse_exact_current_links([changed],[old],retained_null_link_ids=['VCL-test'])
    with pytest.raises(CvpBatchChangeSetError):reuse_exact_current_links([fresh],[old],retained_null_link_ids=['foreign'])


def test_final_prompt_new_identity_only_and_no_model_replacement():
    from backend.pipeline.viewpoint_batch_resolution_runner import build_reviewer
    old=build_reviewer('claude-opus-5-5','high',provider='claude')
    original=old.prompt_sha256;new=flow.final_adapter(old)
    assert old.prompt_sha256==original and new.prompt_sha256!=original
    assert (old.model_id,old.backend)==(new.model_id,new.backend)
    assert '不自动降supports' in new._prompt


@pytest.mark.parametrize('already_started',[False,True])
def test_future_initial_review_full_context_but_saved_contract_never_changed(tmp_path,monkeypatch,already_started):
    from backend.pipeline import viewpoint_batch_resolution_runner as runner
    from backend.api.canonical_repository.viewpoint_batch_resolution import CoverageCanonicalViewpointReviewResponse
    from test_viewpoint_batch_resolution import _StubAdapter
    from backend.pipeline.viewpoint_resolution_runtime import read_artifact
    policy={'policy':{'path':'scoped-policy','file_sha256':'sealed'}}
    monkeypatch.setattr(flow,'policy_for',lambda _:policy)
    calls=[];p=_proposal();reviewer=_StubAdapter({});reviewer._response_model=CoverageCanonicalViewpointReviewResponse
    monkeypatch.setattr(flow,'final_adapter',lambda a,**kw:(calls.append(('profile',kw)) or a))
    if already_started:write_immutable(tmp_path/'raw-proposal.json',{'existing':'must not reinterpret'})
    class ReviewBoundary(Exception):pass
    captured=[]
    def call(model,payload,cache):
        if cache.name=='raw-proposal.json':return p.model_dump(mode='json'),0,0.0
        assert cache.name=='raw-review.json';captured.append(payload);raise ReviewBoundary()
    monkeypatch.setattr(runner,'_call',call)
    with pytest.raises(ReviewBoundary):runner.run_batch(batch_id=p.batch_id,scope_label='test',
        claims=[_claim('C1',ROCK_STATEMENT)],registry_context=[{'viewpoint_revision_id':'CVR-1'}],
        pending_candidates=[],output_dir=tmp_path,proposer=_StubAdapter({}),reviewer=reviewer,
        current_claim_links=[],identity_source_loader=lambda ids:[{'source_id':'S1','paragraph_texts':['完整正文，不是摘要。']}])
    marker=tmp_path/'retained-review-input-profile.json'
    assert marker.exists() is (not already_started)
    if already_started:
        assert not calls and 'own_source_bodies' not in captured[0]
        assert read_artifact(tmp_path/'raw-proposal.json')=={'existing':'must not reinterpret'}
    else:
        assert calls==[('profile',{'initial':True})]
        assert captured[0]['own_source_bodies'][0]['paragraph_texts']==['完整正文，不是摘要。']
        assert read_artifact(marker)['old_answers_upgraded'] is False


@pytest.mark.skipif(__import__('os').environ.get('WKP_CVP_RETAINED_REAL_ACCEPTANCE')!='1',
                   reason='explicit read-only saved g094 acceptance')
def test_actual_four_saved_stages_full_original_context_and_unused_final_zero_calls(monkeypatch):
    import json
    from dotenv import load_dotenv
    from backend.pipeline import cvp_sql_flow as sql, viewpoint_batch_resolution_runner as runner
    from backend.pipeline.cvp_batch_identity_integration import ENABLED
    from backend.pipeline.viewpoint_resolution_runtime import response_adapter,read_saved_model_response,bound_model_response
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.api.canonical_repository.viewpoint_batch_resolution import CoverageCanonicalViewpointReviewResponse
    from backend.api.canonical_repository.viewpoint_batch_changeset import compile_cvp_batch_package
    from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
    load_dotenv('.env')
    class ReadOnly(PostgresKnowledgeStore):
        def connect(self):
            conn=super().connect();conn.execute('SET TRANSACTION READ ONLY');return conn
    store=ReadOnly()
    # Production takes short row locks for its guard; this diagnostic performs
    # the IDENTICAL content/version/hold checks with read-only SELECT instead.
    def read_guard(cursor,pins):
        class ReadCursor:
            def execute(self,query,params):
                assert query.endswith(' FOR UPDATE');cursor.execute(query.removesuffix(' FOR UPDATE'),params)
            def fetchone(self):return cursor.fetchone()
        return PostgresKnowledgeStore._assert_transaction_read_pins(ReadCursor(),pins)
    monkeypatch.setattr(store,'_assert_transaction_read_pins',read_guard)
    campaign=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/cvp-production-357/campaign-sql-20261008-v1-from-g059')
    run='CVPG-0490be727b9eef01497d20f558f7b437fc7beacd434324c8d71c1a82ce024c83'
    action='CVPA-7f7bf996ebe7b3fc0bb8aca982ca47a14c287aaf644dc42c031f8de9386763c8'
    root=campaign/'execution'/run/'required-coverage'/action/'model-stages'
    old=Path('/opt/homebrew/var/www/church/web/data/wang-knowledge-platform/staging/viewpoint-backfill/matthew16-rest-2026-08-26/cvp-part8/batch-006')
    retarget=campaign/'execution/CVPG-164d6b5cc392e4333d9ed872dcde954da1678b76007aa83e2795d8dc195fcf2e/actions/link-retarget/CVPA-d0779c1534da93ea09226f4e4198ae1804f88fc53b8b1d0d2aff7a0ea72bac8d'
    policy={'schema_version':flow.VERSION,'policy':sql.binding(Path(flow.__file__).parent/'policies/wang_cvp_retained_review_separation_v1.json'),
            'generation_artifacts':flow.generation_artifacts([old/'raw-reconsideration.json',old/'raw-review.json',
                                        retarget/'proposal.raw.json',retarget/'initial_review.raw.json'])}
    monkeypatch.setattr(flow,'policy_for',lambda _:policy)
    materials=store.get_cvp_action(run,action)['materials'];before=store.get_cvp_group(run)
    raw_before={p.name:p.read_bytes() for p in root.glob('raw*.json')}
    captured={};authenticated=[]
    def no_write(path,value,**kwargs):
        assert path.exists() and read_artifact(path)==value
    original_read=read_artifact
    import backend.pipeline.viewpoint_resolution_runtime as runtime
    def receipt_write(path,value,**kwargs):
        assert path.name=='retained-review-boundary.json';captured['receipt']=deepcopy(value)
    monkeypatch.setattr(runtime,'write_immutable',receipt_write)
    monkeypatch.setattr(runtime,'read_artifact',lambda p:captured['receipt'] if Path(p).name=='retained-review-boundary.json' else original_read(p))
    class FinalBoundary(Exception):pass
    def call_or_stop(model,payload,cache):
        if cache.name=='raw-final-review.json':
            selected=response_adapter(model).shared_values_copy()
            proof=selected.request_encoding_proof(payload)
            assert proof['exact_roundtrip_verified'] and proof['semantic_payload_sha256']==proof['restored_payload_sha256']
            assert selected.request_bytes(payload)<=600000
            assert len(payload['packet']['claims'])==20
            assert len(payload['packet']['retained_existing_claim_links'])==25
            assert not cache.exists() and not cache.with_name(cache.stem+'.call-intent.json').exists()
            captured['request']=deepcopy(payload);captured['proof']=proof
            print('ACTUAL_RETAINED_FINAL_PREFLIGHT',json.dumps({'bytes':selected.request_bytes(payload),'proof':proof,
                'claims':20,'retained_links':25,'original_model_responses_authenticated':authenticated}))
            raise FinalBoundary()
        assert cache.name in {'raw-proposal.json','raw-consolidation.json','raw-review.json','raw-reconsideration.json'}
        selected=response_adapter(model,read_artifact(cache));raw=read_saved_model_response(selected,payload,cache)
        authenticated.append(cache.name)
        return bound_model_response(selected,payload,raw,cache,persist=False),0,0.0
    monkeypatch.setattr(runner,'_write_immutable',no_write);monkeypatch.setattr(runner,'_call',call_or_stop)
    token=ENABLED.set(True)
    try:
        with pytest.raises(FinalBoundary):sql.reviewed_cvp_package(root,materials,store,require_resolved=True)
    finally:ENABLED.reset(token)
    assert len(authenticated)==4 and captured['receipt']['representation_projection']==[]
    assert len(captured['receipt']['prior_current_approvals'])==4
    assert '欠定与精确合并原则' in str(captured['receipt']['prior_current_approvals'])
    assert store.get_cvp_group(run)==before and raw_before=={p.name:p.read_bytes() for p in root.glob('raw*.json')}
    # Simulated review is ONLY a compiler test, never persisted or production approval.
    request=captured['request'];p=runner.CanonicalViewpointProposalResponse.model_validate(request['proposal'])
    r=deepcopy(request['initial_review']);r['proposal_sha256']=sha256_json(request['proposal'])
    for name in ('change_reviews','structure_reviews','relation_reviews','revision_reviews'):
        for item in r.get(name,[]):item.update(decision='pass',reason='SIMULATED compiler acceptance only',finding_codes=[],correction=None)
    r=CoverageCanonicalViewpointReviewResponse.model_validate(r)
    packet=request['packet']
    validation=runner.validate_proposal(proposal=p,batch_id=p.batch_id,claims=[ReviewClaim.model_validate(c) for c in packet['claims']],
        registry_revision_ids=[row['viewpoint_revision_id'] for row in packet['registry_context']])
    package=compile_cvp_batch_package(proposal=p,review=r,deterministic_validation_sha256=validation['artifact_sha256'],
        scope_manifest_sha256=materials['packet']['current_claim_manifest_sha256'],claims=[ReviewClaim.model_validate(c) for c in packet['claims']],
        registry_context=packet['registry_context'],revision_dependents=request['revision_dependents'],
        proposal_artifact_sha256='simulated-proposal',review_artifact_sha256='simulated-review',
        proposer_model_id='gpt-6.1-sol',reviewer_model_id='claude-opus-5-5',decided_at='2026-10-09T22:00:00Z',
        current_claim_links=materials['packet']['existing_links'],retained_null_link_ids=captured['receipt']['retained_null_locator_links'])
    old_links={l['viewpoint_claim_link_id']:l for l in packet['retained_existing_claim_links']}
    for l in package['viewpoint_claim_links']:
        if l['viewpoint_claim_link_id'] in old_links:assert l==old_links[l['viewpoint_claim_link_id']]
    plan=store.plan_package(package,transactional=True,source_kind='cvp_batch_resolution')
    assert all(o.operation=='create' and o.collection in sql.NORMAL_COLLECTIONS for o in plan.operations)
    assert not any(o.collection=='viewpoint_claim_links' and o.object_id in old_links for o in plan.operations)
    assert store.get_cvp_group(run)==before
