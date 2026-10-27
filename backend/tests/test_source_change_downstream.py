"""Normal CVP SQL + queued route SQL, with synthetic provider responses."""
import json
import pytest
from backend.pipeline import source_change_downstream as downstream
from test_cvp_management_runner import setup, postgres_store, _temporary_postgres
from test_viewpoint_observation_support import observation_proposal, approved


@pytest.mark.parametrize('existing', [False, True])
def test_actual_cvp_and_route_commit_then_zero_call_resume(setup, monkeypatch, existing):
    from backend.pipeline import viewpoint_resolution_runtime as runtime
    from backend.pipeline import viewpoint_route_resolution as route_models
    store, _, root, clients, _ = setup
    old_revision = None
    if existing:
        from test_cvp_management_maintenance import _initial, _behavior
        _,_,_,_,_,view,link,_ = _initial(setup)
        old_revision = store.get_record_state('viewpoint_revisions',view['viewpoint_revision_id'])
        clients['gpt'].behavior = _behavior(view,link,'scope')
    monkeypatch.setattr(runtime,'subscription_client',lambda provider,*args:clients['gpt' if provider=='codex' else 'opus'])
    calls=[]
    class RouteProvider:
        def __init__(self,model,backend):
            self.model=model;self.backend=backend;self.reasoning_effort='high';self.max_output_tokens=32000
        def generate_json(self, **request):
            packet=json.loads(request['user_prompt'])
            calls.append(self.backend)
            return approved(packet) if 'review_targets' in packet else observation_proposal(packet)
    monkeypatch.setattr(route_models,'_subscription_client',lambda provider,model,effort,**kwargs:
        RouteProvider(model,'codex_subscription' if provider=='codex' else 'claude_subscription'))
    result=downstream.run(store,['C1'],[],root/'source-downstream',instruction='synthetic source follow-through')
    assert result['status']=='completed',result
    if old_revision:
        assert store.get_record_state('viewpoint_revisions',view['viewpoint_revision_id']) == old_revision
    assert len(store.read_cvp_heads()) == 1
    vid=store.read_cvp_heads()[0]['viewpoint_id']
    assert store.read_cvp_links(claim_ids=['C1'])
    assert any(col=='viewpoint_observation_supports' for col,_ in store.read_cvp_route_records(vid))
    counts=[len(clients['gpt'].requests),len(clients['opus'].requests),len(calls)]
    result=downstream.run(store,['C1'],[],root/'source-downstream',instruction='synthetic source follow-through')
    assert result['status']=='completed',result
    assert [len(clients['gpt'].requests),len(clients['opus'].requests),len(calls)] == counts
