"""Shared grammar remains in the parent Claim, not duplicated component owners."""
from copy import deepcopy
from pathlib import Path

import pytest

from backend.api.canonical_repository.viewpoint_batch_resolution import ProposedClaimDecision


def decision():
    # Synthetic example; no professor/source material is stored in this test.
    statement = "甲與乙具有性質一且具有性質二，因此構成結論。"
    def span(text):
        start = statement.index(text)
        return dict(start_char=start, end_char=start + len(text), exact_text=text)
    def component(texts, role):
        return dict(spans=[span(t) for t in texts], disposition=role,
                    target_viewpoint_revision_id="R1", local_new_viewpoint_key=None,
                    evidence_step_ids=["E1"], source_fragment_ids=["F1"],
                    reason="主語與範圍從同一完整原句理解；不增加原文。")
    return statement, dict(claim_id="C1", components=[
        component(["甲與乙", "構成結論"], "member_existing"),
        component(["具有性質一"], "support_existing"),
        component(["具有性質二"], "support_existing"),
    ])


def test_shared_subject_single_owner_preserves_all_three_components():
    statement, raw = decision()
    before = deepcopy(raw)
    parsed = ProposedClaimDecision.model_validate(raw)
    assert raw == before  # The validator does not silently edit/drop spans.
    assert len(parsed.components) == 3
    assert [c.disposition for c in parsed.components] == ["member_existing", "support_existing", "support_existing"]
    for component in parsed.components:
        assert component.evidence_step_ids == ["E1"]
        assert component.source_fragment_ids == ["F1"]
        for span in component.spans:
            assert statement[span.start_char:span.end_char] == span.exact_text


def test_repeated_shared_subject_still_rejected_without_auto_deduplication():
    _, raw = decision()
    raw["components"][1]["spans"].insert(0, deepcopy(raw["components"][0]["spans"][0]))
    before = deepcopy(raw)
    with pytest.raises(ValueError, match="same span to two components"):
        ProposedClaimDecision.model_validate(raw)
    assert raw == before


@pytest.mark.parametrize("role", ["proposal", "review", "reconsideration"])
def test_all_model_roles_explain_shared_grammar_without_duplicate_spans(role):
    path = Path(__file__).resolve().parents[1] / "pipeline" / "prompts" / f"canonical_viewpoint_batch_{role}.md"
    text = path.read_text(encoding="utf-8")
    assert "只标记一次" in text
    assert "完整" in text
    assert ("原句里的定位" if role == "proposal" else "原句定位") in text
