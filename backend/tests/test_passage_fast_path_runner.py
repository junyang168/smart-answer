from backend.pipeline.passage_fast_path_runner import resolve_fast_path
from backend.pipeline.passage_knowledge_slice import Passage


PASSAGE = Passage("Matt", 16, 21, 23)


def _package(reference: str | None) -> dict:
    if reference is None:
        return {"package_id": "EMPTY", "claims": [], "evidence_steps": []}
    return {
        "package_id": "PKG",
        "claims": [
            {"claim_id": "CL-1", "scripture_refs": [reference], "evidence_step_ids": ["E-1"]}
        ],
        "evidence_steps": [
            {"evidence_step_id": "E-1", "produced_claim_ids": ["CL-1"], "support_eligibility": "eligible"}
        ],
        "observations": [
            {"observation_id": f"OBS-{verse}", "scripture_refs": [f"太16:{verse}"]}
            for verse in (21, 22, 23)
        ],
    }


def test_fast_path_prefers_complete_postgresql_slice() -> None:
    result = resolve_fast_path(_package("太16:21-23"), PASSAGE, _package("太16:21-23"))
    assert result["resolution"] == "postgresql_reuse"
    assert not result["requires_database_ingest"]
    assert not result["requires_model_extraction"]


def test_fast_path_reuses_reviewed_package_before_model() -> None:
    result = resolve_fast_path(_package(None), PASSAGE, _package("太16:21-23"))
    assert result["resolution"] == "reviewed_package_reuse"
    assert result["requires_database_ingest"]
    assert not result["requires_model_extraction"]


def test_fast_path_requests_model_only_when_both_sources_have_gaps() -> None:
    result = resolve_fast_path(_package(None), PASSAGE, _package(None))
    assert result["resolution"] == "model_extraction_required"
    assert result["requires_model_extraction"]
