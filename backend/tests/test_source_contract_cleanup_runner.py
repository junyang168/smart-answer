import json

import pytest

from backend.pipeline.source_contract_cleanup_runner import (
    _evidence_fragment_locator_proofs,
    _needs_canonical_source_path_repair,
    parse_only_sources,
)
from backend.pipeline.source_projection import project_script


def test_parse_only_sources_uses_manifest_to_recover_space_bearing_argv():
    known = ["S 210101", "S 210101 extended", "plain"]
    command = "python -m runner --batch batch.json --only S 210101 extended plain"
    assert parse_only_sources(command, known) == {"S 210101 extended", "plain"}


def test_parse_only_sources_without_only_owns_the_whole_batch():
    assert parse_only_sources("python -m runner --batch batch.json", ["a", "b"]) == {
        "a",
        "b",
    }


def test_parse_only_sources_fails_closed_on_unrecognized_tail():
    with pytest.raises(ValueError, match="cannot parse"):
        parse_only_sources("python -m runner --only unknown", ["known"])


def test_evidence_locator_requires_every_fragment_to_prove_one_body_row():
    owners = [
        {
            "evidence_step_id": "E-1",
            "source_fragment_ids": ["FR-1", "FR-1", "FR-2"],
        },
        {"evidence_step_id": "E-2", "source_fragment_ids": ["FR-1", "missing"]},
        {"evidence_step_id": "E-3", "source_fragment_ids": ["FR-1", "FR-3"]},
    ]

    assert _evidence_fragment_locator_proofs(
        owners, {"FR-1": "S0006", "FR-2": "S0006", "FR-3": "S0008"}
    ) == {"E-1": "S0006"}


def test_modern_source_repairs_path_only_when_canonical_body_matches(tmp_path):
    published = tmp_path / "script_published"
    published.mkdir()
    script = [{"index": 1, "text": "professor body"}]
    canonical = published / "sermon.json"
    canonical.write_text(json.dumps(script), encoding="utf-8")
    source = {
        "source_type": "sermon_transcript",
        "transcript_id": "sermon",
        "source_path": "/old/review/sermon.json",
        "source_body_sha256": project_script(script).body_sha256,
    }

    assert _needs_canonical_source_path_repair(source, tmp_path) is True
    assert _needs_canonical_source_path_repair(
        {**source, "source_body_sha256": "0" * 64}, tmp_path
    ) is False
