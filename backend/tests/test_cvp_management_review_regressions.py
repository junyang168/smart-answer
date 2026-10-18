"""Independent code-review regressions: scoped material and spent-call replay."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository import cvp_management as domain
from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline import cvp_management_runner as runner, cvp_materials as materials
from backend.pipeline import cvp_recall
from backend.pipeline.cvp_management_models import validate_response
from test_cvp_materials import MaterialStore, prepared
from test_cvp_recall import heads, index, claims
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres, authorize


def same_source_material(monkeypatch):
    store=MaterialStore(members=1)
    row=store.records['source_fragments','F1']
    row['payload'].update(source_id='S0',paragraph_key='S0008',verbatim_excerpt='原话7')
    row['content_sha256']=record_content_sha(row['payload'])
    preflight,recall=prepared(store)
    bundle=materials.prepare_materials(store,preflight,'g',recall,source_loader=store.load_source)
    prepare=materials.prepare_materials
    monkeypatch.setattr(runner,'prepare_materials',lambda *a,**kw:prepare(*a,**kw,source_loader=store.load_source))
    monkeypatch.setattr(runner,'expand_source',lambda *a,**kw:materials.expand_source(*a,**kw,source_loader=store.load_source))
    return store,preflight,recall,bundle


def test_new_member_same_source_preserves_required_section_and_prior_expansion(monkeypatch):
    store,preflight,recall,bundle=same_source_material(monkeypatch)
    source=bundle['material']['sources'][0]
    bundle=runner.expand_source(bundle,dict(source_ref=source['source_ref'],section_ref='p2',extent='adjacent',reason='Context'))
    before=deepcopy(bundle)
    response={'context_requests':[],'member_requests':[{'member_ref':bundle['references']['claims']['C1'],'reason':'Need member support'}]}
    request=SimpleNamespace(maintenance_viewpoint_ids=[],permissions=[])
    result=runner._expand(store,request,preflight,'g',recall,bundle,response)
    assert bundle==before
    assert result['context_requests']==before['context_requests']
    source=result['material']['sources'][0]
    assert [s['section_ref'] for s in source['sections']]==['p1','p2','p3','p4']
    assert source['expansion_level']==1
    assert any(p['paragraph_key']=='S0008' for s in source['sections'] for p in s['paragraphs'])
    assert next(c for c in result['material']['claims'] if c['claim_ref']==result['references']['claims']['C1'])['support_loaded']


@pytest.mark.parametrize('extent',['adjacent','full'])
def test_multiple_same_source_locations_share_one_expansion_gradient(monkeypatch,extent):
    store,preflight,recall,bundle=same_source_material(monkeypatch)
    # Loading the member initially supplies p2 and p4 of the same source.
    bundle=runner.prepare_materials(store,preflight,'g',recall,member_support_ids=['C1'])
    source=bundle['material']['sources'][0]
    if extent=='full':
        bundle=runner.expand_source(bundle,dict(source_ref=source['source_ref'],section_ref='p2',extent='adjacent',reason='Context'))
    response={'result':'context_insufficient','candidate':None,'review':None,'member_requests':[],
              'context_requests':[dict(source_ref=source['source_ref'],section_ref=p,extent=extent,reason='Context') for p in ['p2','p4']]}
    validate_response('proposal',response)
    request=SimpleNamespace(maintenance_viewpoint_ids=[],permissions=[])
    result=runner._expand(store,request,preflight,'g',recall,bundle,response)
    source=result['material']['sources'][0]
    assert [s['section_ref'] for s in source['sections']]==['p1','p2','p3','p4']
    assert source['expansion_level']==(1 if extent=='adjacent' else 2)
    assert result['context_requests'][-2:]==response['context_requests']
    with pytest.raises(ValueError,match='repeated|skips'):
        runner._expand(store,request,preflight,'g',recall,result,response)


@pytest.mark.parametrize('stage',['index','recall'])
def test_interrupted_stage_replays_spent_calls_with_original_sql_budget(setup,stage):
    store,request,root,clients,_=setup
    limit=500000
    if stage=='index':
        count=4
        def execute_stage(invoke,budget):
            return cvp_recall.maintain_reviewed_index(heads(2),proposer=clients['gpt'],reviewer=clients['opus'],
                output_dir=stage_root,max_request_bytes=limit,max_calls=budget,batch_size=1,invoke=invoke)
    else:
        # One complete Claim over eight index cards, forcing several shards.
        from backend.api.canonical_repository.cvp_recall import recall_plan
        probe={'schema_version':'wang_cvp_recall_v1','claims':[{'claim_ref':'c1',**{k:v for k,v in claims(1)[0].items() if k!='claim_id'}}],
               'viewpoints':[{'cvp_ref':'v1',**{k:v for k,v in index()['cards'][0].items() if k in ('core_proposition','summary','scope','proposition_signature')}}]}
        limit=cvp_recall.recall_adapter(clients['gpt'],probe,max_request_bytes=500000).request_bytes(probe)+80
        plan=recall_plan(index(8),claims(1),max_request_bytes=limit,
            measure=lambda p:cvp_recall.recall_adapter(clients['gpt'],p,max_request_bytes=limit).request_bytes(p))
        count=len(plan['shards'])
        assert count>3
        def execute_stage(invoke,budget):
            return cvp_recall.refresh_recall(index(8),claims(1),client=clients['gpt'],output_dir=stage_root,
                max_request_bytes=limit,max_calls=budget,invoke=invoke)
    request=domain.Request.model_validate(authorize(request,max_calls=count+2))
    preflight=runner.prepare_recall(store,request.input,allow_unqualified=True,defer_index=True)
    parent=runner.persistence.prepare_action(store,request,root=str(root),preflight=preflight)
    stage_root=root/stage
    transport=runner._transport(store,parent['run_id'],root,request)
    dispatched=0
    def crash_after_saved_response(adapter,payload,cache):
        nonlocal dispatched
        result=transport(adapter,payload,cache)
        dispatched+=1
        if dispatched==count-1:raise RuntimeError('stopped after durable returned call')
        return result
    with pytest.raises(RuntimeError,match='durable returned'):
        execute_stage(crash_after_saved_response,request.budget.max_calls)
    parent=store.get_cvp_group(parent['run_id'])
    assert len(parent['call_reservations'])==count-1
    remaining=request.budget.max_calls-len(parent['call_reservations'])
    assert remaining<count
    # The original bug compared the entire stage against only unspent calls.
    with pytest.raises(ValueError,match='budget'):execute_stage(transport,remaining)
    result,metrics=execute_stage(transport,runner._stage_call_budget(parent,stage_root,request))
    assert metrics['model_calls']==1
    assert len(clients['gpt'].requests)+len(clients['opus'].requests)==count
    assert len(store.get_cvp_group(parent['run_id'])['call_reservations'])==count
    assert result['artifact_sha256']


def test_new_member_cannot_rebind_old_expansion_to_changed_source(monkeypatch):
    store,preflight,recall,bundle=same_source_material(monkeypatch)
    source=bundle['material']['sources'][0]
    bundle=runner.expand_source(bundle,dict(source_ref=source['source_ref'],section_ref='p2',extent='adjacent',reason='Context'))
    load=store.load_source
    store.load_source=lambda doc:(load(doc)[0],'b'*64,load(doc)[2])
    response={'context_requests':[],'member_requests':[{'member_ref':bundle['references']['claims']['C1'],'reason':'Support'}]}
    with pytest.raises(runner.InputChanged,match='source changed'):
        runner._expand(store,SimpleNamespace(maintenance_viewpoint_ids=[],permissions=[]),preflight,'g',recall,bundle,response)


def test_explicit_relation_scope_keeps_unmaintained_endpoint_readonly():
    from test_cvp_materials import maintenance_graph,material
    store=MaterialStore(2);maintenance_graph(store)
    bundle=material(store,maintenance_viewpoint_ids=['V1'],
                    maintenance_record_keys=[('viewpoint_relations','Q1')])
    assert bundle['maintenance_viewpoint_ids']==['V1']
    assert bundle['candidate_viewpoint_ids']==['V1']
    assert bundle['material']['maintenance_objects'][0]['content']['viewpoint_relation_id']=='Q1'
    assert next(v for v in bundle['material']['viewpoints']
                if v['cvp_ref']==bundle['references']['canonical_viewpoints']['V2'])['context_only']
    assert 'C-unrelated' not in bundle['references']['claims']
    assert 'Q2' not in bundle['references']['viewpoint_relations']
    with pytest.raises(ValueError,match='outside viewpoint authority'):
        material(store,maintenance_viewpoint_ids=['V1'],maintenance_record_keys=[('viewpoint_relations','Q2')])
