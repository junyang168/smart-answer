import pytest

from backend.pipeline.required_step_claim_backfill import (
    RequiredStepBackfillError,
    apply_claim_ids_to_plan,
    build_backfill_package,
    plan_backfill,
    verify_excerpts,
)


MANUSCRIPT = """太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。

彼得的思維方式是人的思維方式，而非神的思維方式。
"""


def _plan_payload():
    return {
        "plan_id": "CP-test",
        "base_source": {"source_id": "notes:16", "path": "/tmp/final.md"},
        "decision_ids": ["CD-1"],
        "authoring_sections": [
            {
                "section_id": "sec-01",
                "decision_ids": ["CD-1"],
                "required_argument_steps": [
                    {
                        "step_id": "S01",
                        "statement": "說明『從此』是轉折標誌。",
                        "source_id": "notes:16",
                        "source_excerpt": "太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。",
                    },
                    {
                        "step_id": "S02",
                        "statement": "把責備連回彼得的思維方式。",
                        "source_id": "notes:16",
                        "source_excerpt": "彼得的思維方式是人的思維方式，而非神的思維方式。",
                    },
                ],
            }
        ],
    }


def _knowledge_with_first_step_covered():
    return {
        "source_fragments": [
            {
                "fragment_id": "FR-1",
                "verbatim_excerpt": "太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。",
            }
        ],
        "evidence_steps": [{"evidence_step_id": "E-1", "source_fragment_ids": ["FR-1"]}],
        "claims": [{"claim_id": "CL-1", "evidence_step_ids": ["E-1"]}],
        "source_documents": [{"source_id": "notes:16", "source_path": "/tmp/final.md"}],
    }


def test_plan_reuses_an_existing_claim_and_only_backfills_the_uncovered_step():
    plan = plan_backfill(_plan_payload(), _knowledge_with_first_step_covered(), id_prefix="T")
    covered, uncovered = plan.steps
    assert covered.existing_claim_id == "CL-1"
    assert covered.needs_backfill is False
    assert uncovered.existing_claim_id is None
    assert [step.step_id for step in plan.pending] == ["S02"]


def test_plan_matches_when_the_fragment_is_longer_than_the_quoted_excerpt():
    knowledge = _knowledge_with_first_step_covered()
    knowledge["source_fragments"][0]["verbatim_excerpt"] = (
        "前綴。太 16:21 以「從此」作為轉折，標誌著耶穌對門徒教導進入第二個階段。後綴。"
    )
    plan = plan_backfill(_plan_payload(), knowledge, id_prefix="T")
    assert plan.steps[0].existing_claim_id == "CL-1"


def test_plan_does_not_reuse_a_fragment_with_no_claim_behind_it():
    knowledge = _knowledge_with_first_step_covered()
    knowledge["claims"] = []
    plan = plan_backfill(_plan_payload(), knowledge, id_prefix="T")
    assert plan.steps[0].existing_claim_id is None


def test_plan_rejects_a_step_without_an_excerpt():
    payload = _plan_payload()
    payload["authoring_sections"][0]["required_argument_steps"][0]["source_excerpt"] = ""
    with pytest.raises(RequiredStepBackfillError, match="S01"):
        plan_backfill(payload, _knowledge_with_first_step_covered(), id_prefix="T")


def test_verify_reports_an_excerpt_that_is_not_in_the_manuscript(tmp_path):
    manuscript = tmp_path / "final.md"
    manuscript.write_text("完全不同的內容。", encoding="utf-8")
    plan = plan_backfill(_plan_payload(), _knowledge_with_first_step_covered(), id_prefix="T")
    failures = verify_excerpts(plan, {"notes:16": manuscript})
    assert len(failures) == 1
    assert "S02" in failures[0]


def test_verify_passes_when_every_pending_excerpt_is_verbatim(tmp_path):
    manuscript = tmp_path / "final.md"
    manuscript.write_text(MANUSCRIPT, encoding="utf-8")
    plan = plan_backfill(_plan_payload(), _knowledge_with_first_step_covered(), id_prefix="T")
    assert verify_excerpts(plan, {"notes:16": manuscript}) == []


def test_package_creates_a_linked_fragment_evidence_and_claim_for_each_pending_step():
    plan = plan_backfill(_plan_payload(), _knowledge_with_first_step_covered(), id_prefix="T")
    package = build_backfill_package(
        plan, _plan_payload(), source_documents=[{"source_id": "notes:16"}]
    )
    assert len(package["claims"]) == 1
    claim = package["claims"][0]
    step = package["evidence_steps"][0]
    fragment = package["source_fragments"][0]
    assert claim["evidence_step_ids"] == [step["evidence_step_id"]]
    assert step["produced_claim_ids"] == [claim["claim_id"]]
    assert step["source_fragment_id"] == fragment["fragment_id"]
    assert fragment["verbatim_excerpt"] == "彼得的思維方式是人的思維方式，而非神的思維方式。"
    assert claim["backfilled_for_step_id"] == "S02"
    # The claim states what the professor said; the step's editorial
    # instruction is kept separately, not passed off as his assertion.
    assert claim["statement"] == "彼得的思維方式是人的思維方式，而非神的思維方式。"
    assert claim["editorial_instruction"] == "把責備連回彼得的思維方式。"
    assert step["statement"] == "彼得的思維方式是人的思維方式，而非神的思維方式。"


def test_plan_update_records_claim_ids_on_every_step_including_reused_ones():
    plan = plan_backfill(_plan_payload(), _knowledge_with_first_step_covered(), id_prefix="T")
    updated = apply_claim_ids_to_plan(_plan_payload(), plan)
    steps = updated["authoring_sections"][0]["required_argument_steps"]
    assert steps[0]["claim_id"] == "CL-1"
    assert steps[1]["claim_id"] == plan.pending[0].claim_id


def test_plan_update_does_not_mutate_the_input_payload():
    payload = _plan_payload()
    plan = plan_backfill(payload, _knowledge_with_first_step_covered(), id_prefix="T")
    apply_claim_ids_to_plan(payload, plan)
    assert "claim_id" not in payload["authoring_sections"][0]["required_argument_steps"][0]
