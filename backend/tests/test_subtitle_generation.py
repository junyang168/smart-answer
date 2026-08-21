"""The call that decides how 90 of 115 transcripts are cut.

Nothing here talks to a model: every test drives `generate_subtitles` with a
stub client whose answers are written in the test.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.api.models import SubtitleInsertion
from backend.pipeline import run_ledger
from backend.pipeline.extraction_sections import (
    FROM_GENERATOR,
    SectionBoundaryError,
    plan_sections,
)
from backend.pipeline.subtitle_generation import (
    LEDGER_STAGE,
    PROMPT_PATH,
    SUBTITLE_SCHEMA,
    SubtitleGenerationError,
    SubtitleValidationError,
    generate_subtitles,
    subtitle_model,
    validate_insertions,
)


# -- stubs -----------------------------------------------------------------


class _Usage:
    prompt_tokens = 4_000
    completion_tokens = 300
    total_tokens = 4_300
    prompt_tokens_details = None


class _StubClient:
    """A `Stage1OpenAIClient` that answers from a script instead of the API."""

    model = "gpt-5.6-sol"
    max_output_tokens = 16000

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.last_usage = _Usage()

    def generate_json(self, system_prompt, user_prompt, json_schema, cache_prefix=None):
        self.calls.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "json_schema": json_schema,
            "cache_prefix": cache_prefix,
        })
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class _Recorded(run_ledger.RunRecord):
    """A ledger row kept in memory, so no test needs a database."""

    def __init__(self, **kwargs):
        super().__init__(conn=None, **kwargs)
        self.finished: list[tuple[str, str | None]] = []

    def start(self, **kwargs) -> None:  # no row, no heartbeat thread
        pass

    def finish(self, status, error_message=None):
        self.finished.append((status, error_message))


@pytest.fixture
def ledger(monkeypatch) -> list[_Recorded]:
    made: list[_Recorded] = []

    def _factory(*, run_id, subject_id, stage, subject_kind, conn):
        record = _Recorded(
            run_id=run_id, subject_id=subject_id, stage=stage, subject_kind=subject_kind
        )
        made.append(record)
        return record

    monkeypatch.setattr("backend.pipeline.run_ledger.RunRecord", _factory)
    monkeypatch.setattr("backend.pipeline.run_ledger._connect", lambda: None)
    return made


def _paragraphs(count: int = 8) -> list[dict[str, Any]]:
    return [
        {"index": str(position), "text": f"第{position}段的正文，這裡有一句夠長的話。",
         "type": "content", "user_name": "王守仁"}
        for position in range(count)
    ]


def _answer(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"insertions": list(rows)}


# -- validation ------------------------------------------------------------


def test_an_after_index_that_names_no_paragraph_is_rejected() -> None:
    """It used to be dropped by `_position_after`, and the section vanished."""

    rows = [{"index": "0", "text": "a"}, {"index": "1", "text": "b"}]
    with pytest.raises(SubtitleValidationError, match="不是输入里的任何一个段落 index"):
        validate_insertions(_answer({"after_index": "7", "text": "## 標題", "level": 1}), rows)


def test_start_is_accepted_and_spelled_the_way_the_editor_matches_it() -> None:
    rows = [{"index": "0", "text": "a"}]
    accepted = validate_insertions(
        _answer({"after_index": "start", "text": "## 導論", "level": 1}), rows
    )
    assert accepted[0]["after_index"] == "START"


def test_a_level_outside_one_and_two_is_rejected() -> None:
    rows = [{"index": "0", "text": "a"}]
    with pytest.raises(SubtitleValidationError, match="level"):
        validate_insertions(_answer({"after_index": "0", "text": "#### x", "level": 4}), rows)


def test_opaque_editor_indices_are_matched_as_written() -> None:
    """The editor's paragraph indices are strings, not necessarily numbers."""

    rows = [{"index": "p-9f3a", "text": "a"}, {"index": "p-11bc", "text": "b"}]
    accepted = validate_insertions(
        _answer({"after_index": "p-9f3a", "text": "## 標題", "level": 1}), rows
    )
    assert accepted[0]["after_index"] == "p-9f3a"


# -- the call --------------------------------------------------------------


def test_a_hallucinated_index_goes_back_through_the_retry_loop(ledger) -> None:
    client = _StubClient(
        _answer({"after_index": "99", "text": "## 錯的", "level": 1}),
        _answer({"after_index": "4", "text": "## 對的", "level": 1}),
    )
    insertions = generate_subtitles(
        _paragraphs(), subject="2016_NYSC_3", consumer="test", client=client
    )
    assert insertions == [{"after_index": "4", "text": "## 對的", "level": 1}]
    assert len(client.calls) == 2, "the bad answer must be re-asked, not skipped"
    assert "99" in client.calls[1]["user_prompt"], "the retry must say what was wrong"
    assert ledger[0].finished == [("succeeded", None)]


def test_a_call_that_never_validates_raises_instead_of_returning_nothing(ledger) -> None:
    """`return []` is why a failure looked like a sermon with one section."""

    bad = _answer({"after_index": "99", "text": "## 錯的", "level": 1})
    client = _StubClient(bad, bad, bad)
    with pytest.raises(SubtitleValidationError):
        generate_subtitles(
            _paragraphs(), subject="2016_NYSC_3", consumer="test", client=client
        )
    assert ledger[0].finished[0][0] == "failed"


def test_an_api_failure_is_not_an_empty_suggestion_list(ledger) -> None:
    client = _StubClient(RuntimeError("500 upstream connect error"))
    with pytest.raises(RuntimeError, match="upstream"):
        generate_subtitles(
            _paragraphs(), subject="2016_NYSC_3", consumer="test", client=client
        )
    assert ledger[0].finished[0][0] == "failed"


def test_no_paragraphs_is_a_failure_not_a_silent_empty_answer(ledger) -> None:
    with pytest.raises(SubtitleGenerationError):
        generate_subtitles([], subject="2016_NYSC_3", consumer="test", client=_StubClient())
    assert ledger == [], "nothing was asked of a model, so nothing is filed"


def test_finding_no_break_worth_marking_is_an_answer_not_an_error(ledger) -> None:
    client = _StubClient(_answer())
    assert generate_subtitles(
        _paragraphs(), subject="2016_NYSC_3", consumer="test", client=client
    ) == []
    assert ledger[0].finished == [("succeeded", None)]


# -- the ledger row --------------------------------------------------------


def test_every_attempt_is_measured_including_the_rejected_one(ledger) -> None:
    """Without `usage`, a run prices at $0.00 -- which reads as free, not unmeasured."""

    bad = _answer({"after_index": "99", "text": "## 錯的", "level": 1})
    client = _StubClient(bad, _answer({"after_index": "4", "text": "## 對的", "level": 1}))
    generate_subtitles(_paragraphs(), subject="2016_NYSC_3", consumer="test", client=client)
    record = ledger[0]
    assert record.stage == LEDGER_STAGE
    assert record._model_id == "gpt-5.6-sol"
    assert [row["attempt"] for row in record._usage] == [1, 2]
    assert all(row["prompt_tokens"] == 4_000 for row in record._usage)


def test_a_failed_run_still_reports_what_it_spent(ledger) -> None:
    bad = _answer({"after_index": "99", "text": "## 錯的", "level": 1})
    client = _StubClient(bad, bad, bad)
    with pytest.raises(SubtitleValidationError):
        generate_subtitles(_paragraphs(), subject="2016_NYSC_3", consumer="test", client=client)
    assert len(ledger[0]._usage) == 3


def test_the_run_is_filed_under_the_source_it_sectioned(ledger) -> None:
    client = _StubClient(_answer({"after_index": "4", "text": "## 對的", "level": 1}))
    generate_subtitles(
        _paragraphs(), subject="notes_manuscript:16_章", consumer="sermon_editor",
        trigger="panel", client=client,
    )
    record = ledger[0]
    assert record.subject_id == "16_章", "the ledger normalizes the key on write"
    assert record._recorded_sources() == ["16_章"]
    assert record._metadata["consumer"] == "sermon_editor"


def test_the_ledger_stage_exists_in_the_database_check_too() -> None:
    """`STAGES` and the CHECK constraint have to be changed together."""

    from pathlib import Path

    migration = (
        Path(run_ledger.__file__).resolve().parents[1]
        / "api" / "canonical_repository" / "migrations" / "004_pipeline_run_stages.sql"
    ).read_text(encoding="utf-8")
    for stage in run_ledger.STAGES:
        assert f"'{stage}'" in migration, f"{stage} would be rejected by the database"
    assert "DROP CONSTRAINT IF EXISTS" in migration, "every migration here is replayed"


# -- what the call is made with --------------------------------------------


def test_the_prompt_is_a_file_with_a_sha_not_a_string_in_a_method(ledger) -> None:
    client = _StubClient(_answer({"after_index": "4", "text": "## 對的", "level": 1}))
    generate_subtitles(_paragraphs(), subject="2016_NYSC_3", consumer="test", client=client)
    assert client.calls[0]["system_prompt"] == PROMPT_PATH.read_text(encoding="utf-8")
    assert len(ledger[0]._inputs["prompt_sha256"]) == 64


def test_the_source_text_leads_the_request_so_a_retry_re_reads_it(ledger) -> None:
    client = _StubClient(_answer({"after_index": "4", "text": "## 對的", "level": 1}))
    generate_subtitles(_paragraphs(), subject="2016_NYSC_3", consumer="test", client=client)
    call = client.calls[0]
    assert call["json_schema"] is SUBTITLE_SCHEMA
    assert "[4] 第4段的正文" in call["cache_prefix"]
    assert call["user_prompt"] == "", "nothing to correct on the first attempt"


def test_a_paragraph_containing_a_newline_stays_one_line(ledger) -> None:
    """Otherwise one paragraph looks like two entries and the ids stop lining up."""

    paragraphs = [{"index": "0", "text": "第一句。\n第二句。"}, {"index": "1", "text": "b"}]
    client = _StubClient(_answer({"after_index": "0", "text": "## 對的", "level": 1}))
    generate_subtitles(paragraphs, subject="s", consumer="test", client=client)
    assert "[0] 第一句。 第二句。\n[1] b" in client.calls[0]["cache_prefix"]


def test_subtitles_are_configured_on_their_own_variable(monkeypatch) -> None:
    """`FULL_ARTICLE_MODEL` used to decide this, and it is about another feature."""

    monkeypatch.delenv("SUBTITLE_MODEL", raising=False)
    monkeypatch.setenv("FULL_ARTICLE_MODEL", "gemini-9-nonsense")
    assert subtitle_model() == "gpt-5.6-sol"
    monkeypatch.setenv("SUBTITLE_MODEL", "gpt-5.6-sol-preview")
    assert subtitle_model() == "gpt-5.6-sol-preview"


# -- the two consumers -----------------------------------------------------


def test_the_editor_contract_is_unchanged(ledger) -> None:
    """Same endpoint, same `SubtitleInsertion[]`, same camelCase aliases."""

    client = _StubClient(_answer(
        {"after_index": "START", "text": "## 導論", "level": 1},
        {"after_index": "4", "text": "### 小節", "level": 2},
    ))
    insertions = generate_subtitles(
        _paragraphs(), subject="2016_NYSC_3", consumer="sermon_editor",
        trigger="panel", client=client,
    )
    encoded = [SubtitleInsertion(**row).model_dump(by_alias=True) for row in insertions]
    assert encoded == [
        {"afterIndex": "START", "text": "## 導論", "level": 1},
        {"afterIndex": "4", "text": "### 小節", "level": 2},
    ]


def _endpoint():
    """The editor's route object.

    Imported inside the test: `backend.api.sc_api` rebinds its own `router`
    attribute to the `APIRouter`, so the module has to come out of `sys.modules`
    rather than out of the import statement.
    """

    import importlib
    import sys

    importlib.import_module("backend.api.sc_api.router")
    return sys.modules["backend.api.sc_api.router"]


def test_the_endpoint_still_answers_in_camel_case(monkeypatch) -> None:
    """`afterIndex` / `text` / `level`, exactly as `SurmonEditor.tsx` reads it."""

    import backend.pipeline.subtitle_generation as module

    monkeypatch.setattr(
        module, "generate_subtitles",
        lambda paragraphs, **kwargs: [{"after_index": "2", "text": "## 標題", "level": 1}],
    )
    from backend.api.models import GenerateSubtitlesRequest

    response = _endpoint().generate_subtitles(
        GenerateSubtitlesRequest(paragraphs=[{"index": "2", "text": "x"}], item="2016_NYSC_3")
    )
    import json

    assert json.loads(response.body) == [
        {"afterIndex": "2", "text": "## 標題", "level": 1}
    ]


def test_the_endpoint_says_it_failed_instead_of_suggesting_nothing(monkeypatch) -> None:
    """An empty list here is the editor's 「沒有建議」; a failure must not borrow it."""

    import backend.pipeline.subtitle_generation as module

    def _explode(paragraphs, **kwargs):
        raise module.SubtitleGenerationError("upstream connect error")

    monkeypatch.setattr(module, "generate_subtitles", _explode)
    from fastapi import HTTPException

    from backend.api.models import GenerateSubtitlesRequest

    with pytest.raises(HTTPException) as caught:
        _endpoint().generate_subtitles(
            GenerateSubtitlesRequest(paragraphs=[{"index": "2", "text": "x"}], item="x")
        )
    assert caught.value.status_code == 502
    assert "upstream connect error" in caught.value.detail


def test_a_failed_generation_does_not_become_one_section(ledger) -> None:
    """One section is whole-document extraction: the behaviour #88 replaced."""

    client = _StubClient(RuntimeError("500 upstream connect error"))

    def provider(paragraphs):
        return generate_subtitles(
            paragraphs, subject="2016_NYSC_3", consumer="extraction_sections", client=client
        )

    segments = [f"第{position}段的正文內容。" for position in range(20)]
    with pytest.raises(RuntimeError, match="upstream"):
        plan_sections(segments, provider=provider)


def test_a_boundary_that_lands_nowhere_fails_the_source(ledger) -> None:
    """Belt and braces: the sectioner does not silently drop one either."""

    segments = [f"第{position}段的正文內容。" for position in range(20)]
    with pytest.raises(SectionBoundaryError):
        plan_sections(
            segments,
            provider=lambda rows: [{"after_index": "第五段", "text": "## x", "level": 1}],
        )


def test_a_generated_plan_says_so_even_when_nothing_was_marked(ledger) -> None:
    """A one-section plan filed as `source_headings` is what hid this for months."""

    segments = [f"第{position}段的正文內容。" for position in range(20)]
    plan = plan_sections(segments, provider=lambda rows: [])
    assert plan.origin == FROM_GENERATOR
    assert len(plan.sections) == 1
