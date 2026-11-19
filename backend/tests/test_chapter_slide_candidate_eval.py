import json
from pathlib import Path

import pytest

from backend.api.scripture import slide_reference_slugs
from backend.pipeline import chapter_exposition, chapter_slide_eval as ev, chapter_slides
from backend.pipeline.chapter_material import CHAPTERS


GOLD = Path(__file__).parent / "fixtures" / "chapter_slides" / "actual_candidate_eval_gold.json"


def test_gold_candidate_set_preserves_actual_source_and_distinguishes_four_criteria():
    cases = json.loads(GOLD.read_text())
    assert {c["criterion"] for c in cases} == ev.SEMANTIC_CRITERIA.keys()
    for case in cases:
        assert case["known_good"] != case["known_bad"]
        assert case["support_sentence"] in {line["id"] for line in case["source"]}


def test_hebrews_regression_detects_multiple_readings_in_one_composite_sentence(tmp_path):
    case = next(c for c in json.loads(GOLD.read_text()) if c["id"] == "hebrews-disjoint-readings")
    line = next(line["text"] for line in case["source"] if line["id"] == case["support_sentence"])
    kept = [{"id": "U1", "start": 0, "end": 15, "text": line}]
    bad = [{"first": "S0001", "title": "一次獻祭", "scriptures": [{"sentence": "S0001", "slug": "heb-10-12"}],
            "points": []}]
    ref = "希伯來書 10:12，14"
    assert slide_reference_slugs(ref) == ["heb-10-12", "heb-10-14"]
    assert slide_reference_slugs("希伯來書 10:12，14:2") == []
    assert ev.evaluate(bad, kept, candidates=["heb-10-12-14"])["problems"] == [
        "一次獻祭（S0001）: 他在念 heb-10-14，畫面沒有顯示這節經文"]
    raw = [{"first": "S0001", "title": "一次獻祭", "verses": "太 5:17–20",
            "points": [{"sentence": "S0001", "text": "一次贖罪祭"}, {"sentence": "S0001", "text": "經文對照"}],
            "scriptures": [{"sentence": "S0001", "ref": ref}]}]
    errors, _, good = chapter_slides.check(raw, kept, CHAPTERS["mat-5-1-48"])
    assert errors == []
    assert [item["slug"] for item in good[0]["scriptures"]] == ["heb-10-12", "heb-10-14"]
    assert ev.evaluate(good, kept, candidates=["heb-10-12-14"])["problems"] == []


@pytest.mark.parametrize("invalid", ["missing", "incomplete", "repeated", "failed", "stale", "published", "none"])
def test_saved_semantic_eval_requires_exact_complete_passing_candidate(tmp_path, invalid):
    selection = tmp_path / "selection.json"
    selection.write_text("{}")
    candidate = tmp_path / "slides.json"
    candidate.write_text(json.dumps({"slides": [{"first": "S0001"}]}))
    receipt = tmp_path / "semantic-eval.json"
    rows = [{"slide": "S0001", "criterion": k, "passed": True, "evidence": "test evidence"}
            for k in ev.SEMANTIC_CRITERIA]
    if invalid == "incomplete":
        rows.pop()
    if invalid == "repeated":
        rows.append(rows[0])
    if invalid == "failed":
        rows[0]["passed"] = False
    if invalid != "missing":
        receipt.write_text(json.dumps({
            "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
            "slides_sha256": ev.semantic_slide_sha256(candidate),
            "selection_sha256": chapter_exposition.file_sha256(selection),
            "checks": rows,
        }))
    if invalid == "stale":
        candidate.write_text(json.dumps({"slides": [{"first": "S0002"}]}))
    if invalid == "published":
        candidate.write_text(json.dumps({"slides": [{"first": "S0001"}], "publication": {"published_at": "now"}}))
    assert bool(ev.semantic_eval_problems(candidate, selection, receipt)) == (invalid not in {"published", "none"})


def test_unit_reader_uses_the_exact_semantic_eval_without_a_second_review(tmp_path):
    from backend.pipeline.chapter_listening_units import reviewed_unit_recording

    title = "Lecture"
    text = "第一個論點。第二個論點。"
    selection = tmp_path / f"{title}.selection.json"
    selection.write_text(json.dumps({
        "source_id": "source", "lecture": title, "transcript_sha256": "transcript",
        "pieces": [{"id": "P1", "source_paragraph_key": "K1", "start": 0, "end": 10, "text": text}],
        "decisions": [{"id": "P1", "keep": True}],
    }))
    edition = tmp_path / f"{title}.slides.json"
    edition.write_text(json.dumps({
        "semantic_eval": ev.SEMANTIC_SCHEMA_VERSION,
        "decisions_sha256": chapter_exposition.file_sha256(selection),
        "claim_context": {"instance": {"change_set_id": "current"}},
        "slides": [{"first": "S0001", "last": "S0002", "title": "論點", "verses": ["mat-5-1-12"],
                    "scriptures": [], "points": []}],
    }))
    unit = {"groups": [{"source_id": "source", "source_ranges": [
        {"paragraph_key": "K1", "start": 0, "end": 10, "text": text}]}]}
    entry = {"lecture": title, "source_id": "source", "changeset": "current", "source_sha256": "transcript"}
    with pytest.raises(ValueError, match="Missing or unreadable semantic Eval"):
        reviewed_unit_recording(tmp_path, unit, entry, {})
    (tmp_path / f"{title}.semantic-eval.json").write_text(json.dumps({
        "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
        "slides_sha256": ev.semantic_slide_sha256(edition),
        "selection_sha256": chapter_exposition.file_sha256(selection),
        "checks": [{"slide": "S0001", "criterion": k, "passed": True, "evidence": text}
                   for k in ev.SEMANTIC_CRITERIA],
    }))
    assert reviewed_unit_recording(tmp_path, unit, entry, {})["slides"][0]["title"] == "論點"


def test_fixing_the_first_question_does_not_clear_early_answer_on_next_slide(tmp_path):
    gold = next(c for c in json.loads(GOLD.read_text()) if c['id'] == 'next-title-reveals-answer-after-question-is-fixed')
    candidate = tmp_path / "slides.json"
    source = tmp_path / "selection.json"
    source.write_text(json.dumps({"source": gold["source"]}))
    bad, good, prior = gold['known_bad'], gold['known_good'], gold['preceding']
    assert prior['title'] == '「以眼還眼」是甚麼意思？'
    assert gold['support_sentence'] == 'S0058'
    assert '審判官' in next(line['text'] for line in gold['source'] if line['id'] == 'S0058')
    for slide, expected in ((bad, False), (good, True)):
        candidate.write_text(json.dumps({"slides": [prior, slide]}))
        receipt = tmp_path / "semantic-eval.json"
        receipt.write_text(json.dumps({
            "schema_version": ev.SEMANTIC_SCHEMA_VERSION,
            "slides_sha256": ev.semantic_slide_sha256(candidate),
            "selection_sha256": chapter_exposition.file_sha256(source),
            "checks": [{"slide": s["first"], "criterion": k,
                        "passed": expected or s is prior or k != "timing",
                        "evidence": "節錄顯示 S0058 才說到司法原則。"}
                       for s in (prior, slide) for k in ev.SEMANTIC_CRITERIA],
        }))
        problems = ev.semantic_eval_problems(candidate, source, receipt)
        assert problems == ([] if expected else ["S0020 timing: 節錄顯示 S0058 才說到司法原則。"])
