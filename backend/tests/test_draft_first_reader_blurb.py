"""The card intro and opening summary of a topic essay (#509)."""

import json

import pytest

from backend.pipeline.draft_first_reader_blurb import blurb_problems, write_blurb
from backend.pipeline.matthew_exposition_authoring import sha256_text

MANUSCRIPT = "# 「多人」的贖價\n\n> 「並且要捨命，作**多人**的贖價。」\n\n「多人」指的是所有的人。\n"
SUMMARY = "「多人」指的是所有的人。"


class FakeClient:
    model, backend = "fake", "fake"

    def __init__(self, *results):
        self.results, self.payloads = list(results), []

    def generate_json(self, prompt, payload, schema):
        self.payloads.append(json.loads(payload))
        return self.results.pop(0)


def test_a_quotation_the_manuscript_does_not_have_is_a_problem():
    assert blurb_problems("耶穌為誰捨命？", "耶穌捨命「作多人的贖價」，多人就是所有的人。", MANUSCRIPT) == []
    problems = blurb_problems("耶" * 81, "1. 「只為選民」\n2. 結論。", MANUSCRIPT)
    assert problems == [
        "reader_intro 有 81 字，超过 80 字",
        "reader_summary 应是一段连贯的文字，不分条、不换行",
        "引号里的「只為選民」不是文章里逐字出现的文字",
    ]


def test_a_failed_attempt_is_retried_once_with_its_problems(tmp_path):
    manuscript = tmp_path / "final.md"
    manuscript.write_text(MANUSCRIPT, encoding="utf-8")
    good = SUMMARY
    client = FakeClient({"reader_intro": "問題？", "reader_summary": "- 一。"},
                        {"reader_intro": "問題？", "reader_summary": good})

    record = write_blurb(manuscript, tmp_path / "reader-blurb.json", client)

    assert client.payloads[1]["previous_attempt"]["problems"] == ["reader_summary 应是一段连贯的文字，不分条、不换行"]
    assert record["reader_summary"] == good
    assert record["manuscript_sha256"] == sha256_text(MANUSCRIPT)
    assert json.loads((tmp_path / "reader-blurb.json").read_text())["reader_intro"] == "問題？"


def test_two_failed_attempts_write_nothing(tmp_path):
    manuscript = tmp_path / "final.md"
    manuscript.write_text(MANUSCRIPT, encoding="utf-8")
    bad = {"reader_intro": "", "reader_summary": SUMMARY}
    with pytest.raises(ValueError):
        write_blurb(manuscript, tmp_path / "reader-blurb.json", FakeClient(bad, bad))
    assert not (tmp_path / "reader-blurb.json").exists()
