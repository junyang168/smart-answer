"""Source observations survive no_route and share its complete review/write flow."""
from copy import deepcopy
import pytest

from backend.api.canonical_repository.viewpoint_observation_support import (
    ArgumentRouteProposalV3Response, ArgumentRouteReviewV3Response,
    compile_observation_package, COLLECTION, CONTRACT, validate_observation_graph,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import (
    ArgumentRouteProposalV2Response, RouteComponentBinding, BatchResolutionError,
    validate_route_proposal, validate_route_review,
)
from backend.api.canonical_repository.viewpoint_resolution import ReviewClaim
from backend.api.canonical_repository.viewpoint_foundation import sha256_json, semantic_record_sha
from backend.pipeline.viewpoint_route_resolution import build_registry_route_packet, build_route_review_batches
from backend.pipeline.viewpoint_route_materials import prepare_cvp_route_materials
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope
from backend.pipeline import viewpoint_route_cvp_worker as worker
from test_viewpoint_batch_resolution import _claim, _registry_link, _approved_viewpoint, _StubAdapter, ROCK_STATEMENT
from test_cvp_management_runner import setup,postgres_store,_temporary_postgres
from test_cvp_management_maintenance import _initial


class Answer(_StubAdapter):
    def generate(self,packet):
        self.calls+=1;self.payloads.append(packet)
        return self._response(packet) if callable(self._response) else self._response


def observation_proposal(packet):
    revision=packet['approved_viewpoint_revision_ids'][0]
    source=next(iter(packet['source_revisions']))
    component=next(c for c in packet['claim_components'] if c['source_id']==source)
    claim=next(c for c in packet['claims'] if c['claim_id']==component['claim_id'])
    e=claim['evidence'][0]
    return ArgumentRouteProposalV3Response.model_validate({
        'scope_label':packet['scope_label'],'approved_viewpoint_revision_ids':[revision],
        'viewpoints_with_no_route':[{'viewpoint_revision_id':revision,'reason_code':'no_attested_route',
                                   'reason':'只记录原文观察，不虚构额外推理'}],
        'observation_supports':[{'local_observation_key':'OBS-1','viewpoint_revision_id':revision,
            'source_id':source,'observation_summary':'教授指出经文中的词形特征。',
            'supports':'将该词形作为观点的语言依据。','support_scope_note':'保存原文观察，未增加穷举证明。',
            'evidence_bindings':[{'claim_component_key':component['claim_component_key'],
                'evidence_step_id':e['evidence_step_id'],'source_fragment_id':e['source_fragment_id']}]}],
        'sources_without_observation':[]}).model_dump(mode='json')


def approved(packet):
    return ArgumentRouteReviewV3Response.model_validate({
        'route_proposal_sha256':packet['route_proposal_sha256'],
        'route_evidence_packet_sha256':packet['route_evidence_packet_sha256'],
        'change_reviews':[dict(t,decision='pass',reason='核对了原文观察及其实际支持范围')
                          for t in packet['review_targets']],
        'cross_source_composition_found':False,'reason':'完整集合通过'}).model_dump(mode='json')


def fixture_packet():
    claim=_claim('C1',ROCK_STATEMENT)
    return build_registry_route_packet(scope_label='observation-test',approved_viewpoints=[_approved_viewpoint()],
        claims=[claim],viewpoint_claim_links=[_registry_link(claim,component_locator=None)],existing_routes=[],
        complete_cvp=True,observation_supports=True,claim_evidence_for_intermediate_steps=True)


def validate(packet,raw):
    proposal=ArgumentRouteProposalV3Response.model_validate(raw)
    return validate_route_proposal(routes=proposal,scope_label=packet['scope_label'],
        claims=[ReviewClaim.model_validate(c) for c in packet['claims']],
        approved_viewpoint_revision_ids=packet['approved_viewpoint_revision_ids'],known_route_revision_ids=[],
        approved_viewpoint_ids={v['viewpoint_revision_id']:v['viewpoint_id'] for v in packet['approved_viewpoints']},
        component_bindings=[RouteComponentBinding.model_validate(c) for c in packet['claim_components']],
        complete_cvp=True,claim_evidence_for_intermediate_steps=True,
        existing_observation_supports=packet.get('existing_observation_supports',[]))


def test_no_route_observation_is_independently_reviewed_and_v2_bytes_do_not_change(tmp_path):
    packet=fixture_packet();proposal=observation_proposal(packet)
    assert validate(packet,proposal)['route_count']==0
    proposer=Answer(proposal,model_id='gpt');reviewer=Answer(approved,model_id='opus')
    result=run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path,
        proposer=proposer,reviewer=reviewer,reconsiderer=Answer({}),max_correction_rounds=4)
    assert result['passing_route_keys']==[] and len(result['rounds'])==1
    assert set((t['target_kind'],t['target_key']) for t in reviewer.payloads[0]['review_targets'])=={
        ('no_route','CVR-1'),('observation','OBS-1')}
    saved=ArgumentRouteProposalV2Response.model_validate({
        'scope_label':'legacy','approved_viewpoint_revision_ids':['CVR-1']}).model_dump(mode='json')
    assert 'observation_supports' not in saved
    assert 'observation_supports' not in ArgumentRouteProposalV2Response.model_json_schema()['properties']


@pytest.mark.parametrize('defect',['foreign_component','wrong_pair','source','revision','withheld','coverage','double_coverage'])
def test_observation_rejects_foreign_unusable_or_unaccounted_material(defect):
    packet=fixture_packet();raw=observation_proposal(packet);ob=raw['observation_supports'][0]
    if defect=='foreign_component':ob['evidence_bindings'][0]['claim_component_key']='CCK-'+'f'*64
    elif defect=='wrong_pair':ob['evidence_bindings'][0]['source_fragment_id']='neighbouring-fragment'
    elif defect=='source':ob['source_id']='another-sermon'
    elif defect=='revision':ob['viewpoint_revision_id']='different-revision'
    elif defect=='withheld':packet['claims'][0]['evidence'][0]['support_eligibility']='withheld_unreviewed';packet['claims'][0]['evidence'][0]['valid_for_identity_review']=False
    elif defect=='coverage':raw['observation_supports']=[]
    else:raw['sources_without_observation']=[{'viewpoint_revision_id':'CVR-1','source_id':'S1','reason':'没有观察'}]
    with pytest.raises((BatchResolutionError,ValueError)):validate(packet,raw)


def test_observation_missing_review_cannot_pass_and_retirement_is_explicit():
    packet=fixture_packet();raw=observation_proposal(packet);proposal=ArgumentRouteProposalV3Response.model_validate(raw)
    review_packet=build_route_review_batches(proposal=proposal,route_packet=packet,
        route_proposal_sha256=sha256_json(raw),whole_cvp=True)[0]
    review=approved(review_packet);review['change_reviews']=[v for v in review['change_reviews'] if v['target_kind']!='observation']
    with pytest.raises(BatchResolutionError,match='no route review decision'):
        validate_route_review(review=ArgumentRouteReviewV3Response.model_validate(review),proposal=proposal,
            route_proposal_sha256=sha256_json(raw),route_evidence_packet_sha256=packet['packet_sha256'])
    created,_,_=compile_observation_package(proposal,packet,review_sha256='review')
    packet['existing_observation_supports']=created
    assert compile_observation_package(proposal,packet,review_sha256='another-review')[0]==[]
    changed=deepcopy(raw);changed['observation_supports'][0]['supports']='修订支持作用'
    with pytest.raises(BatchResolutionError,match='explicitly retire'):validate(packet,changed)
    changed['retired_observation_support_ids']=[created[0]['observation_support_id']]
    assert validate(packet,changed)['route_count']==0


def test_current_cvp_sql_apply_readback_and_no_model_completion_recovery(setup,tmp_path,monkeypatch):
    store,_,_,_,_,view,_,_=_initial(setup);vid=view['viewpoint_id']
    before=prepare_cvp_route_materials(store,vid,include_context=True)
    proposer=Answer(observation_proposal,model_id='gpt');reviewer=Answer(approved,model_id='opus')
    result=worker.process_cvp_route(store=store,viewpoint_id=vid,output_dir=tmp_path/'route',
        proposer=proposer,reviewer=reviewer,reconsiderer=Answer({}),max_correction_rounds=4,apply=True)
    assert result['status']=='applied_readback_verified' and result['change_count']==1
    records=store.read_cvp_route_records(vid)
    assert [col for col,_ in records]==[COLLECTION]
    observation=next(iter(records.values()))['payload']
    assert observation['viewpoint_id']==vid and observation['review_status']=='system_approved'
    assert observation['evidence_bindings'][0]['claim_id']=='C1'
    after=prepare_cvp_route_materials(store,vid,include_context=True)
    assert after['claims']==before['claims'] and after['links']==before['links']
    monkeypatch.setattr(worker,'authenticate_saved_route_review',lambda *a:None)
    recovered=worker.process_cvp_route(store=store,viewpoint_id=vid,output_dir=tmp_path/'route',
        proposer=None,reviewer=None,reconsiderer=None,max_correction_rounds=4,apply=True)
    assert recovered['model_calls']==recovered['semantic_writes']==0
    assert proposer.calls==reviewer.calls==1
    # A context observation becomes a real dependency; retirement cannot strand it.
    context=store.read_claim_context([('claims','C1')])
    assert (COLLECTION,observation['observation_support_id']) in context
    with pytest.raises(Exception):
        store.retire_objects([('claims','C1')],reason='invalid retirement',package_id='invalid-retire',apply=True)
    def replacement(packet):
        raw=observation_proposal(packet)
        raw['observation_supports'][0]['support_scope_note']='修订后的范围说明仍限于原文所示观察。'
        raw['retired_observation_support_ids']=[observation['observation_support_id']]
        return raw
    maintained=worker.process_cvp_route(store=store,viewpoint_id=vid,output_dir=tmp_path/'maintenance',
        proposer=Answer(replacement,model_id='gpt'),reviewer=Answer(approved,model_id='opus'),
        reconsiderer=Answer({}),max_correction_rounds=4,apply=True)
    assert maintained['status']=='applied_readback_verified' and maintained['change_count']==2
    current=store.read_cvp_route_records(vid)
    assert len(current)==1 and (COLLECTION,observation['observation_support_id']) not in current
    old=store.read_current_records([(COLLECTION,observation['observation_support_id'])])
    assert old[COLLECTION,observation['observation_support_id']]['retired'] is True
    assert prepare_cvp_route_materials(store,vid,include_context=True)['links']==before['links']


def add_fixture_observation(store):
    from backend.api.canonical_repository.knowledge_models import ViewpointObservationSupportRecord
    claim=store.records['claims'][0]
    observation=ViewpointObservationSupportRecord.model_validate({
        'observation_support_id':'VOS-fixture','viewpoint_id':'CV-PETER',
        'validated_against_viewpoint_revision_id':'CVR-PETER-1','source_id':'SRC-16',
        'source_revision_sha256':'s'*64,'observation_summary':'教授指出经文的用词。',
        'supports':'提供语言观察。','support_scope_note':'只记录所展示的观察。',
        'evidence_bindings':[{'claim_id':claim.claim_id,'pinned_claim_revision':claim.revision,
            'claim_revision_sha256':semantic_record_sha(claim),'evidence_step_id':'EV-ROCK',
            'source_fragment_id':'FR-ROCK'}],'review_artifact_sha256':'observation-review',
        'review_status':'system_approved'})
    store.records[COLLECTION]=[observation]
    return observation


def test_runtime_and_admin_preserve_observations_without_route_and_pin_dependencies():
    from backend.tests.test_viewpoint_admin_projection import _fixture
    from backend.api.canonical_repository.viewpoint_admin_projection import AdminViewpointProjectionCompiler
    from backend.api.canonical_repository.viewpoint_runtime_projection import ViewpointRuntimeCompiler,ViewpointRuntimeProjectionError
    store=_fixture();observation=add_fixture_observation(store)
    store.records['argument_route_attestations']=[]
    store.records['argument_route_revisions']=[]
    store.records['argument_routes']=[]
    compiler=ViewpointRuntimeCompiler(store.records,store.citations)
    projection=compiler.compile_projection(consumer_kind='qa_answer',coverage_snapshot_id='CVS-16',viewpoint_ids=['CV-PETER'])
    assert projection.argument_routes==[]
    assert projection.observation_supports==[observation.model_dump(mode='json')]
    assert any(d.collection==COLLECTION for d in projection.dependency_manifest)
    detail=AdminViewpointProjectionCompiler(store).detail('CV-PETER')['data']
    assert detail['routes']==[]
    assert detail['observation_supports'][0]['current_registry_ready'] is True
    assert detail['observation_supports'][0]['bindings'][0]['source_fragment']['verbatim_excerpt']
    store.records['claims'][0]=store.records['claims'][0].model_copy(update={'revision':2})
    with pytest.raises(ViewpointRuntimeProjectionError,match='stale'):
        ViewpointRuntimeCompiler(store.records,store.citations).compile_projection(
            consumer_kind='qa_answer',coverage_snapshot_id='CVS-16',viewpoint_ids=['CV-PETER'])
    assert AdminViewpointProjectionCompiler(store).detail('CV-PETER')['data']['observation_supports'][0]['current_registry_ready'] is False


def test_context_claim_can_supply_observation_without_becoming_cvp_member():
    claim=_claim('C1',ROCK_STATEMENT)
    other=_claim('C2','同一篇的以西结对比')
    packet=build_registry_route_packet(scope_label='context-observation',approved_viewpoints=[_approved_viewpoint()],
        claims=[claim,other],viewpoint_claim_links=[_registry_link(claim,component_locator=None)],existing_routes=[],
        complete_cvp=True,observation_supports=True,claim_evidence_for_intermediate_steps=True)
    raw=observation_proposal(packet)
    context=next(c for c in packet['claim_components'] if c['claim_id']=='C2')
    raw['observation_supports'][0]['evidence_bindings']=[{'claim_component_key':context['claim_component_key'],
        'evidence_step_id':'C2-E1','source_fragment_id':'C2-F1'}]
    assert context['disposition']=='no_registry_assertion'
    assert validate(packet,raw)['route_count']==0
    created,_,_=compile_observation_package(ArgumentRouteProposalV3Response.model_validate(raw),packet,review_sha256='review')
    assert created[0]['evidence_bindings'][0]['claim_id']=='C2'
    assert len([c for c in packet['claim_components'] if c['disposition']=='member_existing'])==1


def test_observation_correction_gets_one_whole_set_review_per_revision(tmp_path):
    from backend.api.canonical_repository.viewpoint_batch_resolution import canonicalize_proposal
    packet=fixture_packet()
    original=canonicalize_proposal(observation_proposal(packet))[0]
    revised=deepcopy(original)
    revised['observation_supports'][0]['support_scope_note']='仅记录展示的用词，不声称穷举全部用例。'
    def first_review(p):
        raw=approved(p)
        item=next(r for r in raw['change_reviews'] if r['target_kind']=='observation')
        item.update(decision='correct',finding_codes=['scope_overstates'],reason='须明确观察范围',correction='收窄范围说明')
        return raw
    def correction(p):
        return {'schema_version':'wang_argument_route_reconsideration_v3',
            'route_proposal_sha256':p['route_proposal_sha256'],'route_review_sha256':p['route_review_sha256'],
            'finding_dispositions':[{'target_kind':'observation','target_key':'OBS-1',
                'disposition':'accepted','reason':'已收窄观察支持范围'}], 'revised_proposal':revised}
    reviewer=Answer(lambda p:first_review(p) if reviewer.calls==1 else approved(p),model_id='opus')
    corrector=Answer(correction,model_id='gpt')
    result=run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path,
        proposer=Answer(original),reviewer=reviewer,reconsiderer=corrector,max_correction_rounds=4)
    assert len(result['rounds'])==2 and reviewer.calls==2 and corrector.calls==1
    assert reviewer.payloads[1]['route_proposal_context']['observation_supports']==revised['observation_supports']


def test_theological_packet_includes_observations_without_inventing_a_route():
    from backend.tests.test_theological_evidence_packet import _records,_compile
    records=_records()
    source=records['source_documents'][0]
    fragment=records['source_fragments'][0]
    fragment.update(source_sha256=source['source_sha256'],anchor_state='source_version_bound')
    records['evidence_steps'][0]['support_eligibility']='eligible'
    records['argument_route_attestations']=[]
    records['argument_route_revisions']=[]
    records['argument_routes']=[]
    records[COLLECTION]=[{'observation_support_id':'VOS-test','viewpoint_id':'CV-CENTRAL',
        'validated_against_viewpoint_revision_id':'CVR-CENTRAL','source_id':'SRC-1',
        'source_revision_sha256':source['source_sha256'],'observation_summary':'经文用词观察',
        'supports':'支持观点的用词依据','support_scope_note':'限于所示用词',
        'evidence_bindings':[{'claim_id':'CL-1','pinned_claim_revision':1,
            'claim_revision_sha256':semantic_record_sha(records['claims'][0]),
            'evidence_step_id':'EV-1','source_fragment_id':'FR-1'}],
        'effective_state':'active','review_status':'system_approved','review_artifact_sha256':'review'}]
    packet=_compile(records)
    assert packet['observation_supports']==records[COLLECTION]
    assert packet['argument_routes']==[]
    assert any(row['collection']==COLLECTION for row in packet['dependency_manifest'])
    records['claims'][0]['revision']=2
    with pytest.raises(ValueError,match='stale or invalid observation'):_compile(records)
