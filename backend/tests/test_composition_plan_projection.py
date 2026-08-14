import json

from backend.pipeline.composition_plan_projection_runner import build_projection


def test_projection_adds_plan_routes_and_unavailable_media_without_fabrication() -> None:
    knowledge = {
        "package_id": "PKG",
        "source_documents": [{"source_id": "S1", "source_type": "notes_manuscript"}],
        "source_fragments": [{"fragment_id": "F1", "source_id": "S1"}],
        "evidence_steps": [
            {
                "evidence_step_id": "E1",
                "produced_claim_ids": ["C1"],
                "source_fragment_ids": ["F1"],
            }
        ],
        "claims": [{"claim_id": "C1", "evidence_step_ids": ["E1"]}],
        "knowledge_routes": [],
        "topic_nodes": [],
    }
    plan = {
        "plan_id": "CP-1",
        "decisions": [
            {
                "decision_id": "D1",
                "claim_ids": ["C1"],
                "topic_route_ids": ["topic-one"],
            }
        ],
    }

    result = build_projection(knowledge, plan, manuscript_bytes=b"draft")

    decision = result["product_plans"][0]["decisions"][0]
    assert decision["source_presentations"] == []
    assert decision["source_presentation_summary"]["mode"] == "unavailable"
    assert result["product_plans"][0]["manuscript_sha256"]
    assert {row["route_type"] for row in result["knowledge_routes"]} == {
        "scripture_exposition",
        "topic_research",
    }


def test_projection_reconciles_unique_exact_excerpt_to_published_time(tmp_path) -> None:
    transcript_dir = tmp_path / "published"
    transcript_dir.mkdir()
    (transcript_dir / "T1.json").write_text(
        json.dumps(
            {
                "script": [
                    {
                        "index": 7,
                        "start_time": 15,
                        "end_time": 42,
                        "text": "前文 精确原话 后文",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    knowledge = {
        "package_id": "PKG",
        "source_documents": [
            {
                "source_id": "S1",
                "source_type": "sermon_transcript",
                "transcript_id": "T1",
            }
        ],
        "source_fragments": [
            {
                "fragment_id": "F1",
                "source_id": "S1",
                "verbatim_excerpt": "精确原话",
                "media_time": None,
                "media_end_time": None,
            }
        ],
        "evidence_steps": [
            {
                "evidence_step_id": "E1",
                "produced_claim_ids": ["C1"],
                "source_fragment_ids": ["F1"],
            }
        ],
        "claims": [{"claim_id": "C1", "evidence_step_ids": ["E1"]}],
    }
    plan = {"plan_id": "CP-1", "decisions": [{"decision_id": "D1", "claim_ids": ["C1"]}]}

    result = build_projection(knowledge, plan, timed_transcript_dir=transcript_dir)

    fragment = result["source_fragments"][0]
    assert (fragment["media_time"], fragment["media_end_time"]) == (15, 42)
    assert fragment["media_timing_source"]["match_policy"] == "unique_exact_verbatim_excerpt"
    presentation = result["product_plans"][0]["decisions"][0]["source_presentations"][0]
    assert (presentation["start_seconds"], presentation["end_seconds"]) == (15, 42)
