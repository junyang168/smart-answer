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
    assert not result["requires_media_projection"]


def test_fast_path_reuses_reviewed_package_before_model() -> None:
    result = resolve_fast_path(_package(None), PASSAGE, _package("太16:21-23"))
    assert result["resolution"] == "reviewed_package_reuse"
    assert result["requires_database_ingest"]
    assert not result["requires_model_extraction"]
    assert not result["requires_media_projection"]


def test_fast_path_requests_model_only_when_both_sources_have_gaps() -> None:
    result = resolve_fast_path(_package(None), PASSAGE, _package(None))
    assert result["resolution"] == "model_extraction_required"
    assert result["requires_model_extraction"]


def test_text_complete_still_requires_expected_sermon_media() -> None:
    result = resolve_fast_path(
        _package("太16:21-23"),
        PASSAGE,
        expected_sermon_ids={"講道四"},
    )
    assert result["resolution"] == "postgresql_reuse_media_projection_required"
    assert not result["requires_model_extraction"]
    assert result["requires_media_projection"]


def test_timed_sermon_evidence_satisfies_media_gate() -> None:
    package = _package("太16:21-23")
    package["source_documents"] = [
        {"source_id": "SRC-S", "source_type": "sermon_transcript", "transcript_id": "講道四"}
    ]
    package["source_fragments"] = [
        {"fragment_id": "FR-S", "source_id": "SRC-S", "media_time": 10, "media_end_time": 20}
    ]
    package["evidence_steps"][0]["source_fragment_ids"] = ["FR-S"]
    result = resolve_fast_path(package, PASSAGE, expected_sermon_ids={"講道四"})
    assert result["resolution"] == "postgresql_reuse"
    assert not result["requires_media_projection"]


def test_media_gate_requires_every_expected_sermon() -> None:
    package = _package("太16:21-23")
    package["source_documents"] = [
        {"source_id": "SRC-S", "source_type": "sermon_transcript", "transcript_id": "講道四"}
    ]
    package["source_fragments"] = [
        {"fragment_id": "FR-S", "source_id": "SRC-S", "media_time": 10, "media_end_time": 20}
    ]
    package["evidence_steps"][0]["source_fragment_ids"] = ["FR-S"]
    result = resolve_fast_path(
        package,
        PASSAGE,
        expected_sermon_ids={"講道四", "講道五"},
    )
    assert result["requires_media_projection"]
