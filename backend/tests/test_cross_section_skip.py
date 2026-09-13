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

import pytest

from backend.pipeline.cross_section_relation import CrossSectionValidationError, discovery_identity
from backend.pipeline.cross_section_relation_runner import (
    _stamp_artifact,
    already_current,
    run,
)
from backend.pipeline.detailed_knowledge_extraction_runner import (
    _package_artifact_sha256,
)

PROMPT = "討論跨章節關係"
MODEL = "gpt-5.6-sol"


def _package(tmp_path: Path) -> Path:
    path = tmp_path / "pkg.json"
    package = {
        "source_documents": [{"source_id": "SRC-A", "transcript_id": "A"}],
        "source_fragments": [],
        "questions": [],
        "position_nodes": [],
        "observations": [],
        "evidence_steps": [],
        "claims": [],
        "knowledge_relations": [],
        "claim_relations": [],
        "complete": True,
        "extraction": {"section_plan": {"boundaries": [0, 5]}},
    }
    package["extraction"]["artifact_sha256"] = _package_artifact_sha256(package)
    path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    return path


def _output_matching(package_path: Path, out: Path, *, section_count: int = 2) -> None:
    raw = package_path.read_bytes()
    package = json.loads(raw)
    identity = discovery_identity(
        package_sha256=hashlib.sha256(raw).hexdigest(),
        prompt=PROMPT, model_id=MODEL, section_count=section_count,
    )
    package["cross_section_relations"] = identity
    _stamp_artifact(package)
    out.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")


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


def test_same_fingerprint_with_modified_output_is_not_current(tmp_path: Path) -> None:
    package = _package(tmp_path)
    out = tmp_path / "out.json"
    _output_matching(package, out)
    payload = json.loads(out.read_text(encoding="utf-8"))
    payload["summary"] = {"tampered": True}
    out.write_text(json.dumps(payload), encoding="utf-8")

    assert not already_current(
        package_path=package,
        output_path=out,
        prompt=PROMPT,
        model_id=MODEL,
    )


def test_current_shape_does_not_reuse_stale_single_section_output(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["extraction"]["section_plan"] = {
        "section_count": 2,
        "sections": [
            {"index": 1, "start": 0, "end": 5},
            {"index": 2, "start": 5, "end": 10},
        ],
    }
    payload["extraction"]["artifact_sha256"] = _package_artifact_sha256(payload)
    package.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out.json"
    _output_matching(package, out, section_count=1)

    assert not already_current(
        package_path=package, output_path=out, prompt=PROMPT, model_id=MODEL
    )
    _output_matching(package, out, section_count=2)
    assert already_current(
        package_path=package, output_path=out, prompt=PROMPT, model_id=MODEL
    )


def test_current_shape_multi_section_run_does_not_write_through(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["extraction"]["section_plan"] = {
        "section_count": 2,
        "sections": [
            {"index": 1, "start": 0, "end": 5},
            {"index": 2, "start": 5, "end": 10},
        ],
    }
    payload["source_fragments"] = [
        {
            "fragment_id": "FR-1",
            "source_id": "SRC-A",
            "paragraph_key": "S0001",
            "verbatim_excerpt": "甲",
        }
    ]
    payload["observations"] = [
        {
            "observation_id": "OBS-1",
            "statement": "甲",
            "argument_role": "load_bearing",
            "source_fragment_ids": ["FR-1"],
        }
    ]
    payload["extraction"]["artifact_sha256"] = _package_artifact_sha256(payload)
    package.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    class EmptyProposalClient:
        model = MODEL
        reasoning_effort = "medium"
        max_output_tokens = 16000
        backend = "api"
        last_usage = None

        def __init__(self) -> None:
            self.calls = 0

        def generate_json(self, *_args, **_kwargs) -> dict:
            self.calls += 1
            return {"evidence_relations": [], "claim_relations": []}

    client = EmptyProposalClient()
    result = run(
        package_path=package,
        output_path=tmp_path / "out.json",
        client=client,
        prompt=PROMPT,
    )

    assert client.calls == 1
    assert (result.get("cross_section_relations") or {}).get("skipped") is None


def test_incomplete_extraction_cannot_enter_cross_section_discovery(tmp_path: Path) -> None:
    package = _package(tmp_path)
    payload = json.loads(package.read_text(encoding="utf-8"))
    payload["complete"] = False
    package.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        CrossSectionValidationError,
        match="requires a complete extraction package",
    ):
        already_current(
            package_path=package,
            output_path=tmp_path / "out.json",
            prompt=PROMPT,
            model_id=MODEL,
        )
