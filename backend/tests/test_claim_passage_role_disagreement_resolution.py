"""Fail-closed tests for the partial #409 disagreement resolution overlay."""

import json

import pytest

from backend.pipeline import claim_passage_role_disagreement_resolution as resolution
from backend.pipeline import claim_passage_role_runner as base


def _fixture_report(tmp_path, monkeypatch):
    labels = (["recommend_exegesis"] * 17 + ["recommend_other"] * 280
              + ["unresolved"] * 2 + ["data_issue"] * 3)
    claims = [{
        "claim_id": f"C-{index:03d}",
        "claim_content_sha256": f"claim-{index}",
        "source_id": "S",
        "source_content_sha256": "source-sha",
        "scripture_refs": ["Matt 5:17"],
    } for index in range(1, 303)]
    packet = {"artifact_sha256": "packet-sha", "claims": claims}
    entries = [{
        "index": index,
        "claim_id": claim["claim_id"],
        "batch_id": 1,
        "triage": labels[index - 1],
        "analyst_note": "pinned analysis",
        "primary_artifact_sha256": "primary-sha",
        "independent_artifact_sha256": "independent-sha",
    } for index, claim in enumerate(claims, start=1)]
    report = {
        "frozen_packet_sha256": "packet-sha",
        "counts": resolution.EXPECTED_COUNTS,
        "entries": entries,
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report))
    monkeypatch.setattr(resolution.analysis, "build_report", lambda *args: report)
    monkeypatch.setattr(resolution.audited, "_check_resume",
                        lambda *args: (packet, {}))
    monkeypatch.setattr(resolution, "artifact_paths",
                        lambda *args, **kwargs: (tmp_path / "primary", tmp_path / "independent", "opus"))
    monkeypatch.setattr(resolution.audited, "_checked_decisions",
                        lambda *args: ([{
                            "claim_id": f"C-{index:03d}",
                            "role": "passage_exegesis",
                            "interpreted_ref_indices": [0],
                            "interpreted_evidence_refs": [],
                        } for index in range(1, 18)], "primary-sha"))
    return report_path


def test_accepts_exact_scope_without_promoting_holds(tmp_path, monkeypatch):
    report_path = _fixture_report(tmp_path, monkeypatch)
    artifact = resolution.compile_resolution(report_path, tmp_path, tmp_path,
                                             tmp_path, tmp_path)
    base._check_artifact(artifact)
    assert artifact["counts_by_role"] == {
        "other": 280, "passage_exegesis": 17, "unresolved": 5,
    }
    assert artifact["counts_by_disposition"] == {
        "needs_human": 2, "repair_required": 3, "resolved": 297,
    }
    assert all(row["interpreted_passage_keys"] == ["Matt.5.17"]
               for row in artifact["decisions"][:17])
    assert all(not row["interpreted_passage_keys"]
               for row in artifact["decisions"][17:])


def test_rejects_report_tampering(tmp_path, monkeypatch):
    report_path = _fixture_report(tmp_path, monkeypatch)
    changed = json.loads(report_path.read_text())
    changed["entries"][0]["triage"] = "recommend_other"
    report_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="differs from its frozen inputs"):
        resolution.compile_resolution(report_path, tmp_path, tmp_path,
                                      tmp_path, tmp_path)
