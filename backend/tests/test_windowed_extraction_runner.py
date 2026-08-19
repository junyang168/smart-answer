"""End-to-end cover for the windowed runner, with no model in the loop.

The merge is where windowing can lose material silently, and it only runs at the
end of a many-call sequence. A fake client lets the whole sequence -- plan, per
window call, retry, cache, merge, whole-package validation, compile -- be
exercised in a test instead of only in a paid run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from backend.pipeline.detailed_knowledge_extraction import DetailedExtractionValidationError
from backend.pipeline.detailed_knowledge_extraction_runner import (
    WindowSettings,
    run_source,
)


class _FakeUsage:
    prompt_tokens = 1000
    completion_tokens = 200
    total_tokens = 1200
    prompt_tokens_details = None


class _FakeClient:
    """Answers each window from the text it was actually shown.

    It reads the fetch zone out of the rendered prompt rather than being handed
    a script, so a window that is never rendered, or rendered without its
    locators, produces nothing and the test fails for the right reason.
    """

    model = "fake-model"
    max_output_tokens = 32000

    def __init__(self) -> None:
        self.last_usage = _FakeUsage()
        self.prompts: list[str] = []

    def generate_json(
        self, system_prompt: str, user_prompt: str, schema: dict[str, Any],
        cache_prefix: str | None = None,
    ) -> dict[str, Any]:
        rendered = (cache_prefix or "") + user_prompt
        self.prompts.append(rendered)
        fetch_block = rendered.split("===== 负责范围（必须逐句穷举）=====")[1]
        fetch_block = fetch_block.split("===== 下文")[0]
        response: dict[str, Any] = {
            "questions": [], "positions": [], "observations": [], "evidence_steps": [],
            "claims": [], "evidence_relations": [], "claim_relations": [],
        }
        for index, chunk in enumerate(fetch_block.split("[segment ")[1:], start=1):
            locator = chunk[:5]
            text = chunk.split("]\n", 1)[1].strip().split("\n")[0]
            excerpt = text[:6]
            if not excerpt:
                continue
            response["observations"].append({
                "observation_id": f"OBS{index:03d}", "statement": f"观察 {locator}",
                "observation_type": "scripture_text", "argument_role": "background",
                "scripture_refs": [],
                "anchors": [{"segment_index": locator, "start_time": None,
                             "end_time": None, "verbatim_excerpt": excerpt}],
            })
            response["evidence_steps"].append({
                "evidence_step_id": f"E{index:03d}", "statement": f"证据 {locator}",
                "step_type": "reasoning", "speaker": "professor", "stance": "asserted",
                "discourse_role": "argument", "support_eligibility": "eligible_candidate",
                "scripture_refs": [], "produced_claim_ids": [f"CL{index:03d}"],
                "anchors": [{"segment_index": locator, "start_time": None,
                             "end_time": None, "verbatim_excerpt": excerpt}],
            })
            response["claims"].append({
                "claim_id": f"CL{index:03d}", "statement": f"主张 {locator}",
                "claim_kind": "reasoning_conclusion", "attribution": "professor",
                "scripture_refs": [], "topic_terms": [], "evidence_step_ids": [f"E{index:03d}"],
                "opposed_position_ids": [], "review_status": "candidate",
            })
            response["evidence_relations"].append({
                "relation_id": f"ER{index:03d}", "from_id": f"OBS{index:03d}",
                "to_id": f"E{index:03d}", "relation_type": "supports", "reason": "r",
            })
        return response


def _manuscript(tmp_path: Path) -> dict[str, Any]:
    blocks = ["## 一、彌賽亞秘密理論"]
    for index in range(1, 24):
        blocks.append(f"第{index:02d}段的正文内容，这里写了一句足够长的话来当作材料。")
        if index % 7 == 0:
            blocks.append(f"### 小节 {index}")
    path = tmp_path / "final.md"
    path.write_text("\n\n".join(blocks), encoding="utf-8")
    return {
        "source_id": "notes_manuscript:test",
        "source_path": str(path),
        "source_type": "notes_manuscript",
        "title": "測試母本",
    }


def _run(tmp_path: Path, client: _FakeClient, **kwargs: Any) -> dict[str, Any]:
    output_dir = tmp_path / "out"
    status, path = run_source(
        _manuscript(tmp_path), output_dir=output_dir, client=client,
        prompt="系统提示", reasoning_effort="medium", force=False,
        windows=WindowSettings(fetch=5, context=5, snap=2), **kwargs,
    )
    assert status == "created"
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_segment_is_covered_exactly_once(tmp_path: Path) -> None:
    """The point of the change: no segment goes unasked, and none is asked twice."""

    package = _run(tmp_path, _FakeClient())
    anchored = [
        fragment["paragraph_key"]
        for fragment in package["source_fragments"]
    ]
    assert len(anchored) == len(set(anchored)), "a segment was recorded twice"
    covered = {fragment["paragraph_key"] for fragment in package["source_fragments"]}
    expected = {f"S{index:04d}" for index in range(1, package["summary"]["source_fragment_count"] + 1)}
    assert covered == expected


def test_package_records_how_the_source_was_cut(tmp_path: Path) -> None:
    package = _run(tmp_path, _FakeClient())
    windows = package["windows"]
    assert windows, "package does not say how it was windowed"
    assert [row["fetch_start"] for row in windows][0] == 0
    assert windows[-1]["fetch_end"] == package["summary"]["source_fragment_count"]
    assert package["extraction"]["window_plan"]["fetch"] == 5
    assert package["extraction"]["window_plan"]["context"] == 5


def test_ids_from_different_windows_do_not_collide(tmp_path: Path) -> None:
    package = _run(tmp_path, _FakeClient())
    observation_ids = [row["observation_id"] for row in package["observations"]]
    assert len(observation_ids) == len(set(observation_ids))
    # Each window answered with OBS001..; only the window prefix keeps them apart.
    assert any("-W01-OBS001" in value for value in observation_ids)
    assert any("-W02-OBS001" in value for value in observation_ids)


def test_second_run_reuses_cached_windows_instead_of_recalling(tmp_path: Path) -> None:
    """A source is tens of calls now; a rerun must not pay for all of them again."""

    client = _FakeClient()
    output_dir = tmp_path / "out"
    descriptor = _manuscript(tmp_path)
    settings = WindowSettings(fetch=5, context=5, snap=2)
    run_source(descriptor, output_dir=output_dir, client=client, prompt="系统提示",
               reasoning_effort="medium", force=False, windows=settings)
    first_call_count = len(client.prompts)
    assert first_call_count > 1
    # Delete the package but keep the window cache: the next run must rebuild
    # the package without re-asking the model.
    next(output_dir.glob("*.detailed-knowledge.json")).unlink()
    run_source(descriptor, output_dir=output_dir, client=client, prompt="系统提示",
               reasoning_effort="medium", force=False, windows=settings)
    assert len(client.prompts) == first_call_count


def test_window_prompt_shows_context_without_making_it_answerable(tmp_path: Path) -> None:
    client = _FakeClient()
    _run(tmp_path, client)
    middle = client.prompts[2]
    assert "===== 上文（只读，供理解论证依赖）=====" in middle
    assert "===== 负责范围（必须逐句穷举）=====" in middle
    assert "本窗口负责范围：" in middle
    assert "所在标题层级：" in middle


def test_a_window_that_cannot_be_validated_fails_the_source(tmp_path: Path) -> None:
    """No silent partial package: a window nobody could validate is a failed run."""

    class _Fabricating(_FakeClient):
        def generate_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            response = super().generate_json(*args, **kwargs)
            if response["observations"]:
                response["observations"][0]["anchors"][0]["verbatim_excerpt"] = "这句话不在原文里"
            return response

    with pytest.raises(DetailedExtractionValidationError, match="not verbatim"):
        _run(tmp_path, _Fabricating())
