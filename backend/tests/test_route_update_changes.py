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
