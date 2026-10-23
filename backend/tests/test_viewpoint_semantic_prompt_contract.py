"""Prompt-contract regressions, not a substitute for model semantic evaluation."""
from pathlib import Path

import pytest


PROMPTS = Path(__file__).parents[1] / "pipeline" / "prompts"


@pytest.mark.parametrize("name", ["proposal", "review", "reconsideration"])
def test_whole_claim_default_and_consequence_direction(name):
    prompt = (PROMPTS / f"canonical_viewpoint_batch_{name}.md").read_text()
    assert "默认整条 Claim" in prompt
    assert "由 A 推出 B" in prompt
    assert "保留 B 的观点身份" in prompt
    assert "不要把 B 用 `support_existing` 反挂到 A" in prompt
    assert "B 用 `support_existing` 指向 A" not in prompt
    assert "由观点推出的后果，都不应成为独立 viewpoint" not in prompt


def test_proposer_requires_meaningful_split_not_sentence_form():
    prompt = (PROMPTS / "canonical_viewpoint_batch_proposal.md").read_text()
    for rule in ["真值条件能分离", "另一篇来源中独立匹配", "不拆会使下游误解",
                 "reason 必须说明这三项", "不是拆分理由", "限定、否定和论据不得"]:
        assert rule in prompt
    assert "那是两个观点，要拆成两条" not in prompt
    assert "整条 Claim 只表达一个不可再分的真值条件时" in prompt
    assert "任一方向不成立" in prompt  # whole default does not relax identity


def test_reviewer_applies_split_gates_to_existing_proposal_too():
    prompt = (PROMPTS / "canonical_viewpoint_batch_review.md").read_text()
    assert "提议已经拆分时，同样核对下面三关" in prompt
    for rule in ["真值条件真的能分离", "将来还认得出", "这个区分承重", "三关都过"]:
        assert rule in prompt
    assert "没有被 reviewer 点名" not in prompt  # reviewer is not correction editor
