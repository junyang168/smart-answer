"""What the batch loop does when a source fails, and what it leaves behind.

The loop used to be `subprocess.run(..., check=True)` inside a `for`, so the
first failure raised out of the batch. Ten sources meant seven could finish and
the remaining three would never run, with nothing on disk saying which three --
and the sources in a batch have no dependency on one another at all.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from backend.pipeline import research_batch_runner as runner


def _batch_file(tmp_path: Path, manuscript: Path | None = None) -> Path:
    payload = {
        "schema_version": "wang_research_batch_v1",
        "batch_id": "RB-LOOP-01",
        "purpose": "test",
        "semantic_assumption": "none",
        "transcript_ids": ["甲", "乙", "丙"],
        "candidate_generation_policy": {
            "derive_after_independent_extraction": True,
            "allow_unassigned_material": True,
        },
    }
    if manuscript is not None:
        payload["sources"] = [
            {
                "source_id": "notes_manuscript:母本",
                "source_path": str(manuscript),
                "source_type": "notes_manuscript",
            }
        ]
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _transcripts(tmp_path: Path, *names: str) -> Path:
    directory = tmp_path / "transcripts"
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / f"{name}.json").write_text("[]", encoding="utf-8")
    return directory


def _run(monkeypatch, argv: list[str], fail_on=lambda command: False) -> tuple[int, list[list[str]]]:
    calls: list[list[str]] = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if fail_on(command):
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr("sys.argv", ["research_batch_runner", *argv])
    return runner.main(), calls


def test_one_failing_source_does_not_take_the_others_down(tmp_path, monkeypatch, capsys) -> None:
    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")
    output = tmp_path / "out"

    code, calls = _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(output), "--stage", "extract"],
        fail_on=lambda command: "乙" in command,
    )

    assert code == 1
    # 丙 still ran: it comes after the failure and shares nothing with it.
    assert any("丙" in command for command in calls)

    report = json.loads(capsys.readouterr().out)
    status = {row["source"]: row["status"] for row in report["members"]}
    assert status == {"甲": "completed", "乙": "failed", "丙": "completed"}
    assert report["status"] == "partial"


def test_a_failed_source_skips_its_own_later_stages(tmp_path, monkeypatch, capsys) -> None:
    """Reviewing an extraction that was never written is a second, noisier error."""

    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")

    code, calls = _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(tmp_path / "out")],
        fail_on=lambda command: "甲" in command
        and any("detailed_knowledge_extraction_runner" in part for part in command),
    )

    assert code == 1
    甲 = [command for command in calls if "甲" in " ".join(command)]
    assert len(甲) == 1

    report = json.loads(capsys.readouterr().out)
    row = next(row for row in report["members"] if row["source"] == "甲")
    assert row["failed_stage"] == "extract"
    assert "cross_section" in row["skipped_stages"]


def test_the_manifest_records_where_each_source_stopped(tmp_path, monkeypatch, capsys) -> None:
    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")
    output = tmp_path / "out"

    _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(output), "--stage", "extract"],
        fail_on=lambda command: "乙" in command,
    )
    capsys.readouterr()

    manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "partial"
    assert {row["source"]: row["status"] for row in manifest["members"]}["乙"] == "failed"


def test_notes_member_gets_its_source_manifest_written(tmp_path, monkeypatch, capsys) -> None:
    """Nobody hand-writes the manifest; it is derived from the batch config."""

    manuscript = tmp_path / "final.md"
    manuscript.write_text("# 一\n\n正文\n", encoding="utf-8")
    batch = _batch_file(tmp_path, manuscript)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")
    output = tmp_path / "out"

    code, _ = _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(output), "--stage", "extract"],
    )
    capsys.readouterr()

    assert code == 0
    written = runner.artifact_paths(output, "notes_manuscript:母本")["source_manifest"]
    rows = json.loads(written.read_text(encoding="utf-8"))["sources"]
    assert rows[0]["source_path"] == str(manuscript)
    assert "key" not in rows[0]


def test_only_runs_the_named_members(tmp_path, monkeypatch, capsys) -> None:
    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")

    code, calls = _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(tmp_path / "out"), "--stage", "extract", "--only", "丙"],
    )
    capsys.readouterr()

    assert code == 0
    assert len(calls) == 1
    assert "丙" in calls[0]


def test_ingest_is_not_in_stage_all_unless_asked(tmp_path, monkeypatch, capsys) -> None:
    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")

    _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(tmp_path / "out"), "--only", "甲"],
    )
    default_out = capsys.readouterr().out
    assert "extraction_supersede_runner" not in default_out

    _, calls = _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(tmp_path / "out"), "--only", "甲", "--ingest"],
    )
    capsys.readouterr()
    assert any("backend.pipeline.extraction_supersede_runner" in command for command in calls)


def test_apply_without_ingest_is_refused(tmp_path, monkeypatch) -> None:
    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")
    with pytest.raises(SystemExit):
        _run(
            monkeypatch,
            ["--batch", str(batch), "--transcript-dir", str(transcripts),
             "--output-root", str(tmp_path / "out"), "--apply"],
        )


def test_an_interrupt_still_leaves_a_terminal_status(tmp_path, monkeypatch, capsys) -> None:
    """Ctrl-C used to leave the manifest saying "running" for good.

    Everything already finished stays on disk, and its stage runner skips it on
    the next run -- so the point of recording the interrupt is to say where to
    look, not to preserve work that would otherwise be lost.
    """

    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")
    output = tmp_path / "out"

    def fake_run(command, **kwargs):
        if "乙" in command:
            raise KeyboardInterrupt
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        ["research_batch_runner", "--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(output), "--stage", "extract"],
    )
    assert runner.main() == 1
    capsys.readouterr()

    manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted"
    status = {row["source"]: row["status"] for row in manifest["members"]}
    assert status["甲"] == "completed"
    assert status["乙"] == "interrupted"
    assert status["丙"] == "not_started"


def test_a_transcript_is_found_across_several_directories(tmp_path, monkeypatch, capsys) -> None:
    """Chapter 16 is split across two transcript directories.

    Six of its sermons sit in `script_review` and three in `script_published`.
    A single `--transcript-dir` could not describe the chapter, and getting it
    wrong is silent in the worst way: the runner would report the transcript
    missing for exactly the sources that do exist.
    """

    batch = _batch_file(tmp_path)
    published = tmp_path / "published"
    review = tmp_path / "review"
    published.mkdir()
    review.mkdir()
    (published / "甲.json").write_text("[]", encoding="utf-8")
    (review / "乙.json").write_text("[]", encoding="utf-8")
    (review / "丙.json").write_text("[]", encoding="utf-8")

    code, calls = _run(
        monkeypatch,
        ["--batch", str(batch),
         "--transcript-dir", str(published), "--transcript-dir", str(review),
         "--output-root", str(tmp_path / "out"), "--stage", "extract"],
    )
    capsys.readouterr()

    assert code == 0
    by_member = {command[command.index("--ids") + 1]: command for command in calls}
    assert str(published) in by_member["甲"]
    assert str(review) in by_member["乙"]


def test_a_genuinely_missing_transcript_still_stops_the_run(tmp_path, monkeypatch) -> None:
    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙")
    with pytest.raises(SystemExit):
        _run(
            monkeypatch,
            ["--batch", str(batch), "--transcript-dir", str(transcripts),
             "--output-root", str(tmp_path / "out"), "--stage", "extract"],
        )


def _reviewed_package(path: Path, transcript_id: str, suffix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "wang_shared_knowledge_v1.2",
        "source_documents": [{"source_id": f"SRC-{suffix}", "transcript_id": transcript_id}],
        "source_fragments": [{"fragment_id": f"FR-{suffix}", "source_id": f"SRC-{suffix}"}],
        "questions": [], "position_nodes": [], "observations": [],
        "evidence_steps": [{"evidence_step_id": f"E-{suffix}"}],
        "claims": [{"claim_id": f"CL-{suffix}", "evidence_step_ids": [f"E-{suffix}"]}],
        "knowledge_relations": [], "claim_relations": [],
        "extraction": {"fingerprint_sha256": suffix},
        "consensus_application": {"approval_status": "not_human_approved"},
    }, ensure_ascii=False), encoding="utf-8")


def test_only_does_not_overwrite_a_whole_batch_merge(tmp_path, monkeypatch, capsys) -> None:
    """The merged package describes the batch, so a narrowed run must not write one.

    Merging what `--only` selected replaced a full merge with a one-member
    file and reported `completed` -- silently wrong in exactly the way this
    orchestration exists to stop.
    """

    batch = _batch_file(tmp_path)
    transcripts = _transcripts(tmp_path, "甲", "乙", "丙")
    output = tmp_path / "out"
    merged = output / "merged" / "research-batch-knowledge.json"
    merged.parent.mkdir(parents=True)
    merged.write_text('{"note": "full merge"}', encoding="utf-8")
    _reviewed_package(runner.artifact_paths(output, "甲")["reviewed"], "甲", "A")

    code, _ = _run(
        monkeypatch,
        ["--batch", str(batch), "--transcript-dir", str(transcripts),
         "--output-root", str(output), "--only", "甲"],
    )
    report = json.loads(capsys.readouterr().out)

    assert json.loads(merged.read_text(encoding="utf-8")) == {"note": "full merge"}
    assert report["status"] == "partial_selection"
    assert "merge skipped" in report["merge_error"]
    # Narrowing the run on purpose is not a failure.
    assert code == 0
