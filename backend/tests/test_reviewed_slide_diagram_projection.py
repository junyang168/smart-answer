import pytest

from backend.pipeline.chapter_slides import timed_diagrams


def test_reviewed_diagram_uses_saved_sentence_inside_its_slide():
    lines = [{"id": f"S000{n}", "start": n * 10} for n in range(1, 5)]
    slides = [{"first": "S0002", "last": "S0004", "title": "分段", "diagram_ref": "D", "points": []}]
    context = {"claims": [{"claim_id": "CL1", "stated": "S0004", "diagrams": [
        {"sha256": "D", "title": "分段", "origin": "原圖", "kind": "professor", "svg": "<svg/>"}]}]}
    assert timed_diagrams(slides, lines, context)["S0002"]["at"] == 40
    explicit = {"S0002": {"sha256": "D", "sentence": "S0002"}}
    assert timed_diagrams(slides, lines, context, explicit)["S0002"]["at"] == 20
    explicit["S0002"]["sentence"] = "S0001"
    with pytest.raises(ValueError, match="圖的出場句與本張原文不符"):
        timed_diagrams(slides, lines, context, explicit)
    explicit["S0002"]["sentence"] = None
    assert timed_diagrams(slides, lines, context, explicit) == {}
