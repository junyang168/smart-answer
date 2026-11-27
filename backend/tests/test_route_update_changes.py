"""Normal compact update, technical repair, full review, SQL and cache resume."""
from copy import deepcopy
import json
import sys

import pytest
from backend.api.canonical_repository.viewpoint_resolution import StructuredJsonReviewerAdapter
from backend.api.canonical_repository.viewpoint_observation_support import ArgumentRouteProposalV3Response, compile_observation_package
from backend.pipeline.viewpoint_route_update_projection import update_baseline
from backend.pipeline.viewpoint_route_correction_changes import PROPOSAL_VERSION, VERSION, proposal_adapter
from backend.pipeline.viewpoint_route_cvp_resolution import run_cvp_route_scope
from backend.pipeline import viewpoint_route_cvp_worker as worker
from test_viewpoint_observation_support import Answer, approved, fixture_packet, observation_proposal
from test_route_correction_changes import delta, edit, adapter
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres
from test_cvp_management_maintenance import _initial


class Client:
    model = 'gpt-test'
    backend = 'stub_subscription'
    reasoning_effort = 'high'
    def __init__(self, answer):
        self.answer, self.packets = answer, []
    def generate_json(self, **request):
        packet = json.loads(request['user_prompt'])
        self.packets.append(packet)
        return self.answer(packet, len(self.packets))


def proposer(client):
    return StructuredJsonReviewerAdapter(client=client, prompt='Original proposal business rules',
        response_model=ArgumentRouteProposalV3Response, schema_name='legacy-proposal')


def initial_edits(packet):
    full = observation_proposal(packet)
    nr = full['viewpoints_with_no_route'][0]
    ob = full['observation_supports'][0]
    return {'schema_version': PROPOSAL_VERSION, 'edits': [
        edit('create', 'viewpoints_with_no_route', nr['viewpoint_revision_id'],
             {k:v for k,v in nr.items() if k!='viewpoint_revision_id'}),
        edit('create', 'observation_supports', ob['local_observation_key'],
             {k:v for k,v in ob.items() if k!='local_observation_key'})]}


def test_projection_keeps_old_observation_evidence_and_exposes_ambiguity():
    packet = fixture_packet()
    old = compile_observation_package(ArgumentRouteProposalV3Response.model_validate(
        observation_proposal(packet)), packet, review_sha256='old-review')[0][0]
    packet['existing_observation_supports'] = [old]
    first = update_baseline(packet)['observation_supports'][0]
    assert first['local_observation_key'] == old['observation_support_id']
    assert first['observation_summary'] == old['observation_summary']
    assert first['evidence_bindings'][0]['source_fragment_id'] == old['evidence_bindings'][0]['source_fragment_id']
    packet['claim_components'].append(deepcopy(packet['claim_components'][0]) | {'claim_component_key': 'CCK-'+'f'*64})
    assert update_baseline(packet)['observation_supports'][0]['evidence_bindings'][0]['claim_component_key'] is None
    assert packet['existing_observation_supports'] == [old]


def test_changed_observation_reports_exact_old_record_needing_retirement():
    packet=fixture_packet()
    proposal=ArgumentRouteProposalV3Response.model_validate(observation_proposal(packet))
    old=compile_observation_package(proposal,packet,review_sha256='old-review')[0][0]
    packet['existing_observation_supports']=[old]
    updated=proposal.model_copy(deep=True)
    updated.observation_supports[0].supports='仅支持该经文中直接指出的用词。'
    with pytest.raises(ValueError,match=old['observation_support_id']) as error:
        compile_observation_package(updated,packet,review_sha256='new-review')
    assert 'missing_retirements=' in str(error.value)
    updated.retired_observation_support_ids=[old['observation_support_id']]
    created,retired,_=compile_observation_package(updated,packet,review_sha256='new-review')
    assert len(created)==1 and retired==[('viewpoint_observation_supports',old['observation_support_id'])]


def test_projection_rebinds_revision_as_unapproved_draft_without_rewriting_route():
    from test_viewpoint_batch_resolution import _routes_v2
    p = fixture_packet(); route = _routes_v2().argument_route_candidates[0].model_dump(mode='json')
    nodes = deepcopy(route['ordered_inference_nodes'])
    for n in nodes:
        ref = n.pop('conclusion_ref', None)
        n['conclusion_viewpoint_revision_id'] = ref['target_viewpoint_revision_id'] if ref else None
    p['existing_routes'] = [{'argument_route_id': 'AR-1','route_revision_id': 'ARR-old',
        'conclusion_viewpoint_revision_id': 'CVR-old','route': {'conclusion_viewpoint_id': 'CV-1'},
        'revision': {'route_label': route['route_label'], 'ordered_inference_nodes': nodes,
            'route_signature': {'inference_method_codes': route['inference_method_codes'], 'inference_method_note': None}}}]
    base = update_baseline(p)['argument_route_candidates'][0]
    assert base['proposed_action'] == 'revise_existing'
    assert base['target_argument_route_revision_id'] == 'ARR-old'
    assert base['ordered_inference_nodes'][0]['normalized_proposition'] == nodes[0]['normalized_proposition']
    assert base['ordered_inference_nodes'][-1]['conclusion_ref']['target_viewpoint_revision_id'] == 'CVR-1'
    assert p['existing_routes'][0]['conclusion_viewpoint_revision_id'] == 'CVR-old'


def test_normal_queue_repairs_both_stages_and_commits_without_manual_continuation(setup,tmp_path,monkeypatch):
    store,_,_,_,_,view,_,_ = _initial(setup)
    vid=view['viewpoint_id'];before=worker.prepare_cvp_route_materials(store,vid,include_context=True)
    def propose(p, n):
        if n==1:
            return {'schema_version': PROPOSAL_VERSION, 'edits': [edit(fields={'supports':'missing target'})]}
        assert p['validation_findings'] and p['prior_invalid_response']['edits']
        assert p['route_proposal_baseline']['observation_supports']==[]
        return initial_edits(p)
    def correct(p, n):
        if n==1:
            invalid={'claim_component_key':p['route_evidence_packet']['claim_components'][0]['claim_component_key'],
                'evidence_step_id':'missing-evidence','source_fragment_id':'missing-fragment'}
            return delta([edit(fields={'supports':'收窄后的实际范围','evidence_bindings':[invalid]})])
        assert p['validation_findings'] and p['prior_invalid_response']['schema_version']==VERSION
        return delta([edit(fields={'supports':'收窄后的实际范围'})])
    pc,cc=Client(propose),Client(correct)
    prop,corr=proposer(pc),adapter(cc)
    def review(p):
        r=approved(p)
        if rev.calls==1:
            row=next(x for x in r['change_reviews'] if x['target_kind']=='observation')
            row.update(decision='correct',finding_codes=['scope'],reason='需收窄',correction='收窄 supports，保留证据')
        return r
    rev=Answer(review,model_id='opus')
    monkeypatch.setattr(worker,'PostgresKnowledgeStore',lambda *a,**kw:store)
    monkeypatch.setattr(worker,'build_route_proposer',lambda *a,**kw:prop)
    monkeypatch.setattr(worker,'build_route_reconsiderer',lambda *a,**kw:corr)
    monkeypatch.setattr(worker,'build_route_reviewer',lambda *a,**kw:rev)
    queue=tmp_path/'queue';out=tmp_path/'out'
    jid=worker.enqueue_current_cvp_routes(store,[vid],queue)[0]
    monkeypatch.setattr(sys,'argv',['route-worker','run','--queue-dir',str(queue),
        '--output-dir',str(out),'--job-id',jid,'--apply'])
    assert worker.main()==0
    root=out/jid
    run=json.loads((root/'route-cvp-run.json').read_text())
    assert run['program_validation_repairs']==2
    assert run['proposal_validation_repairs']==0
    assert len(run['rounds'])==2 and run['rounds'][-1]['outcome']=='pass'
    assert len(pc.packets)==len(cc.packets)==rev.calls==2
    state=json.loads((queue/'states'/f'{jid}.json').read_text())
    assert state['status']=='complete'
    after=worker.prepare_cvp_route_materials(store,vid,include_context=True)
    assert before['claims']==after['claims'] and before['links']==after['links']
    assert len(after['existing_observation_supports'])==1
    resumed=worker.resume_applied_cvp_route(store,vid,root)
    assert resumed['model_calls']==resumed['semantic_writes']==0
    assert len(pc.packets)==len(cc.packets)==rev.calls==2
    # No old observation text needs to be copied during an ordinary update.
    old=after['existing_observation_supports'][0]
    def update(p,n):
        assert p['route_proposal_baseline']['observation_supports'][0]['local_observation_key']==old['observation_support_id']
        return {'schema_version':PROPOSAL_VERSION,'edits':[
            initial_edits(p)['edits'][0],
            edit(fields={'supports':'进一步限定'},key=old['observation_support_id']),
            edit('create','retired_observation_support_ids',old['observation_support_id'])]}
    updated_client=Client(update)
    result=worker.process_cvp_route(store=store,viewpoint_id=vid,output_dir=tmp_path/'update',
        proposer=proposer(updated_client),reviewer=Answer(approved,model_id='opus'),
        reconsiderer=corr,max_correction_rounds=1,apply=True)
    assert result['status']=='applied_readback_verified' and len(updated_client.packets)==1
    assert worker.prepare_cvp_route_materials(store,vid,include_context=True)['claims']==before['claims']


def test_technical_repair_does_not_use_an_opus_correction_round(tmp_path):
    p=fixture_packet()
    def initial(packet,n):
        return {'schema_version':PROPOSAL_VERSION,'edits':[]} if n==1 else initial_edits(packet)
    pc=Client(initial)
    def review(packet):
        r=approved(packet)
        if rev.calls==1:
            row=next(x for x in r['change_reviews'] if x['target_kind']=='observation')
            row.update(decision='correct',finding_codes=['scope'],reason='范围',correction='修改作用')
        return r
    rev=Answer(review,model_id='opus');cc=Client(lambda p,n:delta([edit(fields={'supports':'限定用例'})]))
    args=dict(route_packet=p,existing_routes=[],output_dir=tmp_path,proposer=proposer(pc),
        reviewer=rev,reconsiderer=adapter(cc),max_correction_rounds=1)
    result=run_cvp_route_scope(**args)
    assert result['program_validation_repairs']==1 and len(result['rounds'])==2
    before={x.name:x.read_bytes() for x in tmp_path.glob('*.json')}
    run_cvp_route_scope(**args)
    assert len(pc.packets)==2 and len(cc.packets)==1 and rev.calls==2
    assert before=={x.name:x.read_bytes() for x in tmp_path.glob('*.json')}


@pytest.mark.parametrize('suffix',['.json','.call-intent.json','.request-encoding.json'])
def test_saved_or_pending_initial_call_keeps_legacy_output(tmp_path,suffix):
    p=proposer(Client(lambda *a:{}));(tmp_path/('raw'+suffix)).write_text('{}')
    assert proposal_adapter(p,tmp_path/'packet.json',tmp_path/'raw.json') is p


def test_repeated_invalid_initial_edits_stop_before_review(tmp_path):
    pc=Client(lambda p,n:{'schema_version':PROPOSAL_VERSION,'edits':[]})
    rev=Answer(approved,model_id='opus')
    with pytest.raises(ValueError,match='program validation remains unresolved'):
        run_cvp_route_scope(route_packet=fixture_packet(),existing_routes=[],output_dir=tmp_path,
            proposer=proposer(pc),reviewer=rev,reconsiderer=adapter(Client(lambda *a:{})),
            max_correction_rounds=4)
    assert len(pc.packets)==2 and rev.calls==0
    assert not (tmp_path/'route-run.json').exists()


def test_technical_repair_allowance_is_shared_between_stages(tmp_path):
    pc=Client(lambda p,n:{'schema_version':PROPOSAL_VERSION,'edits':[]} if n==1 else initial_edits(p))
    def review(p):
        r=approved(p)
        row=next(x for x in r['change_reviews'] if x['target_kind']=='observation')
        row.update(decision='correct',finding_codes=['scope'],reason='范围',correction='修改作用')
        return r
    rev=Answer(review,model_id='opus')
    cc=Client(lambda p,n:delta([edit(fields={'not_a_field':True})]))
    with pytest.raises(ValueError,match='program validation remains unresolved'):
        run_cvp_route_scope(route_packet=fixture_packet(),existing_routes=[],output_dir=tmp_path,
            proposer=proposer(pc),reviewer=rev,reconsiderer=adapter(cc),max_correction_rounds=1)
    assert len(pc.packets)==2 and rev.calls==1 and len(cc.packets)==1


def test_saved_initial_repair_chain_is_authenticated_without_new_calls(tmp_path):
    from backend.pipeline.viewpoint_route_validation_repair import read_initial_saved_proposal
    p=fixture_packet()
    pc=Client(lambda p,n:{'schema_version':PROPOSAL_VERSION,'edits':[]} if n==1 else initial_edits(p))
    prop=proposer(pc)
    result=run_cvp_route_scope(route_packet=p,existing_routes=[],output_dir=tmp_path,
        proposer=prop,reviewer=Answer(approved,model_id='opus'),
        reconsiderer=adapter(Client(lambda *a:{})),max_correction_rounds=1)
    full,names=read_initial_saved_proposal(prop,p,tmp_path)
    assert len(names)==2 and len(pc.packets)==2
    assert full['observation_supports']
    saved=tmp_path/names[-1]
    raw=json.loads(saved.read_text());raw['edits']=[];saved.write_text(json.dumps(raw))
    with pytest.raises(ValueError):
        read_initial_saved_proposal(prop,p,tmp_path)
    assert len(pc.packets)==2


def test_clearer_validator_does_not_rewrite_dispatched_repair_input(tmp_path):
    from backend.pipeline.viewpoint_route_validation_repair import validate_changes
    pc=Client(lambda p,n:{'fixed':True});prop=proposer(pc)
    def check(message):
        def validate(raw):
            if not raw.get('fixed'):raise ValueError(message)
            return raw
        return validate
    args=dict(raw={'fixed':False},adapter=prop,packet={'baseline':'unchanged'},
        cache=tmp_path/'raw.json',max_repairs=1)
    validate_changes(**args,validate=check('original diagnostic'),state={'used':0,'attempts':[]})
    files={p.name:p.read_bytes() for p in tmp_path.glob('*.json')}
    result,_=validate_changes(**args,validate=check('clearer diagnostic with exact record'),
        state={'used':0,'attempts':[]})
    assert result=={'fixed':True} and len(pc.packets)==1
    assert files=={p.name:p.read_bytes() for p in tmp_path.glob('*.json')}


def test_semantic_disagreement_does_not_trigger_program_repairs(tmp_path):
    pc=Client(lambda p,n:initial_edits(p))
    def review(p):
        r=approved(p)
        row=next(x for x in r['change_reviews'] if x['target_kind']=='observation')
        row.update(decision='correct',finding_codes=['scope'],reason='范围',correction='修改作用')
        return r
    rev=Answer(review,model_id='opus')
    def disagree(p,n):
        answer=delta([])
        answer['finding_dispositions'][0].update(disposition='rebutted',reason='原文已明确限定支持范围')
        return answer
    cc=Client(disagree)
    with pytest.raises(ValueError,match='reviewer finding unresolved'):
        run_cvp_route_scope(route_packet=fixture_packet(),existing_routes=[],output_dir=tmp_path,
            proposer=proposer(pc),reviewer=rev,reconsiderer=adapter(cc),max_correction_rounds=4)
    assert len(pc.packets)==1 and len(cc.packets)==1 and rev.calls==1


def test_valid_changed_candidate_returns_rebuttal_to_full_review(tmp_path):
    pc=Client(lambda p,n:initial_edits(p))
    def review(p):
        r=approved(p)
        if rev.calls==1:
            row=next(x for x in r['change_reviews'] if x['target_kind']=='observation')
            row.update(decision='correct',finding_codes=['scope'],reason='范围',correction='修改作用')
        else:
            assert p['route_review_continuity']['rounds'][0]['finding_dispositions'][0]['disposition']=='rebutted'
        return r
    rev=Answer(review,model_id='opus')
    def disagree(p,n):
        answer=delta([edit(fields={'supports':'只支持该经文的用词观察。'})])
        answer['finding_dispositions'][0].update(disposition='rebutted',reason='保留真实观察；修正支持范围后请重新核对原文。')
        return answer
    cc=Client(disagree)
    result=run_cvp_route_scope(route_packet=fixture_packet(),existing_routes=[],output_dir=tmp_path,
        proposer=proposer(pc),reviewer=rev,reconsiderer=adapter(cc),max_correction_rounds=1)
    assert len(result['rounds'])==2 and rev.calls==2 and len(cc.packets)==1
    assert result['rounds'][-1]['outcome']=='pass' and result['program_validation_repairs']==0


def test_incomplete_opus_review_is_repaired_and_authenticated_before_commit(setup,tmp_path,monkeypatch):
    store,_,_,_,_,view,_,_ = _initial(setup)
    pc=Client(lambda p,n:initial_edits(p))
    prop=proposer(pc)
    def review(p):
        result=approved(p)
        if rev.calls==1:
            result['change_reviews']=[r for r in result['change_reviews'] if r['target_kind']!='observation']
        else:
            assert p['validation_findings'] and 'complete corrected review' in p['validation_instruction']
            assert p['prior_invalid_response']['change_reviews']
        return result
    rev=Answer(review,model_id='opus')
    root=tmp_path/'route'
    result=worker.process_cvp_route(store=store,viewpoint_id=view['viewpoint_id'],output_dir=root,
        proposer=prop,reviewer=rev,reconsiderer=Answer({}),max_correction_rounds=2,apply=True)
    assert result['status']=='applied_readback_verified' and rev.calls==2
    saved=json.loads((root/'raw-route-review-000.json').read_text())['response']
    assert all(r['target_kind']!='observation' for r in saved['change_reviews'])
    assert (root/'raw-route-review-000-validation-000.json').exists()
    monkeypatch.setattr(worker,'build_route_reviewer',lambda *a,**kw:rev)
    recovered=worker.resume_applied_cvp_route(store,view['viewpoint_id'],root)
    assert recovered['status']=='applied_readback_verified' and recovered['model_calls']==0
    assert rev.calls==2
    repair_path=root/'raw-route-review-000-validation-000-packet.json'
    altered=json.loads(repair_path.read_text());altered['prior_invalid_response']['reason']='tampered'
    repair_path.write_text(json.dumps(altered))
    with pytest.raises(ValueError,match='repair input changed'):
        worker.resume_applied_cvp_route(store,view['viewpoint_id'],root)


def test_unknown_update_target_reports_exact_baseline_identity():
    from backend.pipeline.viewpoint_route_correction_changes import apply_edits,ProposalChanges
    baseline=update_baseline(fixture_packet())
    baseline['observation_supports']=[{'local_observation_key':'VOS-current'}]
    changes=ProposalChanges.model_validate({'schema_version':PROPOSAL_VERSION,'edits':[
        edit(key='invented-alias',fields={'supports':'x'})]})
    with pytest.raises(ValueError) as error:
        apply_edits(changes.edits,baseline,fixture_packet())
    assert 'invented-alias' in str(error.value) and 'VOS-current' in str(error.value)
    assert 'prior invalid edits were not applied' in str(error.value)


def test_review_completion_cannot_turn_substantive_rejection_into_approval(tmp_path):
    p=fixture_packet()
    def review(packet):
        result=approved(packet)
        if rev.calls==1:
            result['change_reviews']=result['change_reviews'][:1]
        else:
            row=next(r for r in result['change_reviews'] if r['target_kind']=='observation')
            row.update(decision='reject',finding_codes=['invented_observation'],reason='原文不支持')
        return result
    rev=Answer(review,model_id='opus')
    from backend.pipeline.viewpoint_route_cvp_resolution import CvpRouteReviewIncomplete
    with pytest.raises(CvpRouteReviewIncomplete):
        run_cvp_route_scope(route_packet=p,existing_routes=[],output_dir=tmp_path,
            proposer=Answer(observation_proposal,model_id='gpt'),reviewer=rev,
            reconsiderer=Answer({}),max_correction_rounds=2)
    assert rev.calls==2
    saved=json.loads((tmp_path/'route-review-000.json').read_text())
    assert saved['validation']['outcome']!='pass'
    assert any(r['decision']=='reject' for r in saved['review']['change_reviews'])
    assert not (tmp_path/'route-change-plan.json').exists()


def test_observation_pair_error_identifies_the_actual_registry_owner():
    from test_viewpoint_observation_support import validate
    from backend.api.canonical_repository.viewpoint_batch_resolution import BatchResolutionError
    packet=fixture_packet();raw=observation_proposal(packet)
    binding=raw['observation_supports'][0]['evidence_bindings'][0]
    original=binding['evidence_step_id'];binding['evidence_step_id']='wrong-evidence'
    with pytest.raises(BatchResolutionError) as error:
        validate(packet,raw)
    message=str(error.value)
    assert 'wrong-evidence' in message and original in message
    assert binding['source_fragment_id'] in message and 'fragment_registry_pairs=' in message


def test_explicit_restore_uses_pinned_record_without_copying_content(tmp_path):
    from backend.pipeline.viewpoint_route_observation_restore import FIELD,MARKER as RESTORE,restore_observation
    from backend.pipeline.viewpoint_route_correction_changes import apply_edits,ProposalChanges
    from backend.api.canonical_repository.viewpoint_observation_support import observation_payload,ProposedObservationSupport
    packet=fixture_packet();old=compile_observation_package(ArgumentRouteProposalV3Response.model_validate(
        observation_proposal(packet)),packet,review_sha256='old')[0][0]
    packet['existing_observation_supports']=[old];key=old['observation_support_id']
    def initial(p,n):
        return {'schema_version':PROPOSAL_VERSION,'edits':[
            initial_edits(p)['edits'][0],edit(key=key,fields={'supports':'被错误修改的范围'}),
            edit('create','retired_observation_support_ids',key)]}
    def correction(p,n):
        if n==1:edits=[edit('remove',key=key),edit('remove','retired_observation_support_ids',key)]
        else:
            assert p[RESTORE]['route_packet_sha256']==packet['packet_sha256']
            edits=[edit(key=key,fields={FIELD:key}),edit('remove','retired_observation_support_ids',key)]
        return {'schema_version':VERSION,'finding_dispositions':[{'target_kind':'observation',
            'target_key':key,'disposition':'accepted','reason':'恢复原版，不再重写证据'}],'edits':edits}
    pc,cc=Client(initial),Client(correction)
    def review(p):
        result=approved(p)
        if rev.calls==1:
            row=next(r for r in result['change_reviews'] if r['target_kind']=='observation')
            row.update(decision='correct',finding_codes=['evidence_dropped'],reason='保留原记录',correction='恢复原记录全部内容')
        return result
    rev=Answer(review,model_id='opus')
    run_cvp_route_scope(route_packet=packet,existing_routes=[],output_dir=tmp_path,
        proposer=proposer(pc),reviewer=rev,reconsiderer=adapter(cc),max_correction_rounds=2)
    assert len(pc.packets)==1 and len(cc.packets)==rev.calls==2
    raw=json.loads((tmp_path/'raw-route-reconsideration-000-validation-000.json').read_text())['response']
    restore=next(e for e in raw['edits'] if e['collection']=='observation_supports')
    assert json.loads(restore['fields_json'])=={FIELD:key}
    restored=restore_observation(packet,key)
    expected_payload=observation_payload(ProposedObservationSupport.model_validate(restored),packet)
    assert expected_payload=={k:old[k] for k in expected_payload}
    aliases=deepcopy(packet);alias=deepcopy(aliases['claim_components'][0])
    alias.update(claim_component_key='CCK-'+'0'*64,disposition='no_registry_assertion',
        target_viewpoint_revision_id=None,viewpoint_claim_link_id=None)
    aliases['claim_components'].append(alias)
    aliased=ProposedObservationSupport.model_validate(restore_observation(aliases,key))
    assert aliased.evidence_bindings[0].claim_component_key==alias['claim_component_key']
    assert observation_payload(aliased,aliases)==expected_payload
    stale=deepcopy(packet);stale['source_revisions'][old['source_id']]='b'*64
    with pytest.raises(ValueError,match='source versions'):restore_observation(stale,key)
    edits=ProposalChanges.model_validate({'schema_version':PROPOSAL_VERSION,'edits':[restore]}).edits
    with pytest.raises(ValueError,match='offered contract'):
        apply_edits(edits,update_baseline(packet),packet)
    changed=deepcopy(packet);changed['claims'][0]['pinned_claim_revision']+=1
    with pytest.raises(ValueError,match='Claim versions'):restore_observation(changed,key)
    with pytest.raises(ValueError,match='exact existing'):restore_observation(packet,'VOS-unknown')
    # Cached technical answers keep their exact request contract and need no calls.
    from backend.pipeline.viewpoint_route_validation_repair import read_validated_changes
    from backend.pipeline.viewpoint_route_correction_changes import correction_adapter
    cache=tmp_path/'raw-route-reconsideration-000.json';request=tmp_path/'route-reconsideration-packet-000.json'
    active=correction_adapter(adapter(cc),request,cache)
    read_validated_changes(active,json.loads(request.read_text()),cache)
    assert len(cc.packets)==2
