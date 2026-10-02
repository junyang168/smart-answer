from __future__ import annotations

from backend.bible_study.check import check, stage_of, status


NOTES = """# 討論紀要

## n1 · 採用 · 三次受難預言
- 問：……
- 結論：……
- 依據：carson v2 p429；太 20:17-19

## n2 · 採用 · 用人與奴僕
- 依據：carson v2 p432

## n3 · 下次講 · 作多人的贖價
- 依據：claim DK-1

## n4 · 不用 · 強盜可能是造反者
- 依據：約 18:40
"""

OUTLINE = """<!-- approved: 2026-09-24 -->
# 範圍
<!-- deferred: n3 -->

## 一、耶穌說了三次
<!-- notes: n1 | sources: carson v2 p429 -->

## 二、座位與服事
<!-- notes: n2 n3 | sources: carson v2 p432 -->
"""

SCRIPT = """## 一、耶穌說了三次

這是第三次預言。 <!-- n1; carson v2 p429 -->

## 二、座位與服事

用人和奴僕是兩個詞。 <!-- n2 -->

作多人的贖價，下次來看。 <!-- n3 -->
"""


def _study(tmp_path, notes=NOTES, outline=OUTLINE, script=SCRIPT):
    folder = tmp_path / "2026-09-25-mat-20-17-34"
    folder.mkdir()
    for name, text in (("notes.md", notes), ("outline.md", outline), ("script.md", script)):
        if text is not None:
            (folder / name).write_text(text, encoding="utf-8")
    return folder


def _errors(folder, stage=None):
    return check(folder, stage).errors


def test_a_complete_study_passes(tmp_path):
    assert _errors(_study(tmp_path)) == []


def test_a_section_left_out_of_the_script(tmp_path):
    script = SCRIPT[: SCRIPT.index("## 二")]
    assert _errors(_study(tmp_path, script=script)) == ["script.md: outline section 「二、座位與服事」 is not written yet"]


def test_a_section_never_planned(tmp_path):
    # What happened on 2026-09-24: the service section was agreed in
    # discussion, then neither planned nor written.
    outline = OUTLINE[: OUTLINE.index("## 二")]
    script = SCRIPT[: SCRIPT.index("## 二")]
    errors = _errors(_study(tmp_path, outline=outline, script=script))
    assert "n2 「用人與奴僕」 is 採用 but no outline section carries it" in errors
    assert "n2 「用人與奴僕」 is 採用 but no script paragraph carries it" in errors


def test_deferred_notes_are_previewed_once_at_most(tmp_path):
    script = SCRIPT + "\n贖價的意思是…… <!-- n3 -->\n"
    assert _errors(_study(tmp_path, script=script)) == [
        "n3 「作多人的贖價」 is 下次講: preview it in one paragraph at most, it is in 2"
    ]


def test_deferred_notes_must_be_listed_in_scope(tmp_path):
    outline = OUTLINE.replace("<!-- deferred: n3 -->", "")
    assert "n3 「作多人的贖價」 is 下次講 but not listed as deferred in 範圍" in _errors(_study(tmp_path, outline=outline))


def test_rejected_notes_stay_out(tmp_path):
    script = SCRIPT + "\n強盜可能是造反的人。 <!-- n4 -->\n"
    outline = OUTLINE.replace("notes: n1 |", "notes: n1 n4 |")
    errors = _errors(_study(tmp_path, outline=outline, script=script))
    assert "outline.md places n4, which the owner marked 不用" in errors
    assert "n4 「強盜可能是造反者」 is 不用 but the script carries it" in errors


def test_open_notes_block_the_outline(tmp_path):
    notes = NOTES + "\n## n5 · 待定 · 歷史性\n- 依據：carson v2 p430\n"
    assert "n5 「歷史性」 is still 待定: decide 採用 / 不用 / 下次講 first" in _errors(_study(tmp_path, notes=notes))


def test_every_note_needs_a_basis(tmp_path):
    notes = NOTES + "\n## n5 · 不用 · 沒有依據\n- 結論：……\n"
    assert "notes.md: n5 has no 依據 (Carson page, claim id, scripture, or 負責人)" in _errors(_study(tmp_path, notes=notes))


def test_no_script_before_the_outline_is_approved(tmp_path):
    outline = OUTLINE.replace("<!-- approved: 2026-09-24 -->\n", "")
    assert any("outline.md is not approved" in e for e in _errors(_study(tmp_path, outline=outline)))


def test_a_draft_outline_can_be_checked_before_approval(tmp_path):
    outline = OUTLINE.replace("<!-- approved: 2026-09-24 -->\n", "")
    assert _errors(_study(tmp_path, outline=outline, script=None)) == []


def test_sections_must_follow_the_outline_order(tmp_path):
    first, second = SCRIPT.split("## 二")
    assert "script.md: sections are not in the outline's order" in _errors(_study(tmp_path, script="## 二" + second + "\n" + first))


def test_every_section_names_a_source(tmp_path):
    outline = OUTLINE + "\n## 三、結語\n<!-- notes: - | sources: - -->\n"
    script = SCRIPT + "\n## 三、結語\n\n結束禱告。\n"
    assert "outline.md: section 「三、結語」 names no source and carries no notes" in _errors(
        _study(tmp_path, outline=outline, script=script)
    )


def test_status_reports_each_study_stage(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_BASE_DIR", str(tmp_path))
    root = tmp_path / "bible-study"
    root.mkdir()
    done = _study(root)
    assert stage_of(done) == "script (2/2 sections)"
    early = root / "2026-10-02-mat-20-28"
    early.mkdir()
    (early / "notes.md").write_text(NOTES, encoding="utf-8")
    (root / "_generator-snapshot-2026-09-25").mkdir()  # not a study
    assert [(name, stage) for name, _, stage in status(root)] == [
        ("2026-09-25-mat-20-17-34", "script (2/2 sections)"),
        ("2026-10-02-mat-20-28", "discussion (4 notes)"),
    ]
