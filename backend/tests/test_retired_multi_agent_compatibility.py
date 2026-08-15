import pytest
from fastapi import HTTPException

from backend.api import sermon_converter_router as router_module


def test_legacy_generate_draft_routes_to_detached_stage1_worker(monkeypatch):
    calls = []
    expected_job = {"status": "starting", "mode": "generate_all", "pid": 1234}

    def fake_start_stage1_pipeline_job(**kwargs):
        calls.append(kwargs)
        return expected_job

    monkeypatch.setattr(
        router_module,
        "start_stage1_pipeline_job",
        fake_start_stage1_pipeline_job,
    )

    response = router_module.trigger_draft_generation(
        "matthew-16",
        router_module.GenerateDraftRequest(
            prompt_id="retired-prompt",
            use_mas=False,
            restart=True,
        ),
    )

    assert calls == [
        {
            "project_id": "matthew-16",
            "mode": "generate_all",
            "force": True,
        }
    ]
    assert response["job"] == expected_job
    assert response["message"] == "Stage 1 draft generation started."


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (RuntimeError("already running"), 409),
        (FileNotFoundError("missing project"), 404),
        (ValueError("invalid project"), 400),
        (Exception("unexpected"), 500),
    ],
)
def test_legacy_generate_draft_maps_stage1_errors(monkeypatch, error, expected_status):
    def fail_to_start(**_kwargs):
        raise error

    monkeypatch.setattr(router_module, "start_stage1_pipeline_job", fail_to_start)

    with pytest.raises(HTTPException) as exc_info:
        router_module.trigger_draft_generation("matthew-16")

    assert exc_info.value.status_code == expected_status
    assert exc_info.value.detail == str(error)


def test_retired_routes_are_not_registered_and_compatibility_route_is_deprecated():
    routes = {
        route.path: route
        for route in router_module.router.routes
        if hasattr(route, "path")
    }

    compatibility_path = "/admin/notes-to-sermon/sermon-project/{project_id}/generate-draft"
    assert routes[compatibility_path].deprecated is True
    assert "/admin/notes-to-sermon/sermon-project/{project_id}/agent-state" not in routes
    assert "/admin/notes-to-sermon/sermon-project/{project_id}/agent-logs" not in routes
