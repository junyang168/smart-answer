"""A run that recomputes nothing must not look like a run that did.

`cross_section` opened its ledger row before its own skip check, so re-running
a source whose cross-section was already current wrote a fresh `succeeded` row
in under a second. That row was newer than the review which had read the very
same package, and the overview -- correctly, on the evidence it had -- marked
that review 舊: its input had apparently moved. Nothing had moved. The stage
had recognised its own output and stopped.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.pipeline.cross_section_relation import discovery_identity
from backend.pipeline.cross_section_relation_runner import already_current

PROMPT = "討論跨章節關係"
MODEL = "gpt-5.6-sol"


def _package(tmp_path: Path) -> Path:
    path = tmp_path / "pkg.json"
    path.write_text(json.dumps({
        "source_documents": [{"source_id": "SRC-A", "transcript_id": "A"}],
        "extraction": {"section_plan": {"boundaries": [0, 5]}},
    }, ensure_ascii=False), encoding="utf-8")
    return path


def _output_matching(package_path: Path, out: Path) -> None:
    raw = package_path.read_bytes()
    identity = discovery_identity(
        package_sha256=hashlib.sha256(raw).hexdigest(),
        prompt=PROMPT, model_id=MODEL, section_count=2,
    )
    out.write_text(
        json.dumps({"cross_section_relations": identity}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_output_already_answering_this_question_is_recognised(tmp_path: Path) -> None:
    package = _package(tmp_path)
    out = tmp_path / "out.json"
    _output_matching(package, out)
    assert already_current(
        package_path=package, output_path=out, prompt=PROMPT, model_id=MODEL
    )


def test_a_missing_output_is_not_current(tmp_path: Path) -> None:
    package = _package(tmp_path)
    assert not already_current(
        package_path=package, output_path=tmp_path / "absent.json",
        prompt=PROMPT, model_id=MODEL,
    )


def test_a_different_prompt_or_model_is_not_current(tmp_path: Path) -> None:
    """The fingerprint is what makes the skip safe; it must still bind."""

    package = _package(tmp_path)
    out = tmp_path / "out.json"
    _output_matching(package, out)
    assert not already_current(
        package_path=package, output_path=out, prompt="別的 prompt", model_id=MODEL
    )
    assert not already_current(
        package_path=package, output_path=out, prompt=PROMPT, model_id="other-model"
    )


def test_unreadable_output_is_not_mistaken_for_current(tmp_path: Path) -> None:
    package = _package(tmp_path)
    out = tmp_path / "out.json"
    out.write_text("{ not json", encoding="utf-8")
    assert not already_current(
        package_path=package, output_path=out, prompt=PROMPT, model_id=MODEL
    )
