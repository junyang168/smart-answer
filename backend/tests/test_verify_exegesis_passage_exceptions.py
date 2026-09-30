import hashlib

import pytest

from scripts.verify_exegesis_passage_exceptions import verify


def fixture(tmp_path):
    source = tmp_path / "source.md"
    source.write_text("教授原话：解释这一段。\n", encoding="utf-8")
    content_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    exceptions = {
        "artifact_sha256": "e" * 64,
        "exception_count": 1,
        "rows": [{
            "claim_id": "CL-1",
            "source_id": "SRC-1",
            "claim_content_sha256": "c" * 64,
            "evidence_steps": [{"fragments": [{
                "fragment_id": "FR-1", "verbatim_excerpt": "教授原话：解释这一段。"
            }]}],
        }],
    }
    packet = {
        "artifact_sha256": "p" * 64,
        "claims": [{
            "claim_id": "CL-1",
            "source_id": "SRC-1",
            "claim_content_sha256": "c" * 64,
            "source_file_sha256": content_sha,
        }],
    }
    return source, exceptions, packet


def test_verifies_current_source_file_and_verbatim_excerpt(tmp_path):
    source, exceptions, packet = fixture(tmp_path)
    report = verify(exceptions, packet, {"SRC-1": source})
    assert report["verified_claim_count"] == 1
    assert report["verified_source_count"] == 1
    assert report["verified_fragment_count"] == 1
    assert report["database_mutations"] == 0


def test_source_change_fails_closed(tmp_path):
    source, exceptions, packet = fixture(tmp_path)
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source file SHA mismatch"):
        verify(exceptions, packet, {"SRC-1": source})


def test_excerpt_mismatch_fails_closed(tmp_path):
    source, exceptions, packet = fixture(tmp_path)
    exceptions["rows"][0]["evidence_steps"][0]["fragments"][0]["verbatim_excerpt"] = "not in original"
    with pytest.raises(ValueError, match="fragment not found"):
        verify(exceptions, packet, {"SRC-1": source})
