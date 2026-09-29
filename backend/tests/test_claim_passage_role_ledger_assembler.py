"""No-model tests for exact-once role ledger assembly."""

from pathlib import Path

import pytest

from backend.pipeline import claim_passage_role_ledger_assembler as assembler
from backend.pipeline import claim_passage_role_runner as base


def test_artifact_paths_cover_all_four_roots_without_overlap(tmp_path):
    roots = {name: tmp_path / name for name in ("original", "opus", "primary", "independent")}
    def paths(n):
        return assembler.artifact_paths(n, original_root=roots["original"],
                                        opus_root=roots["opus"],
                                        primary_root=roots["primary"],
                                        independent_root=roots["independent"])
    assert paths(68)[0].parent == roots["original"]
    assert paths(68)[1].parent == roots["original"]
    assert paths(69)[0].parent == roots["original"]
    assert paths(69)[1].parent == roots["opus"]
    assert paths(82)[0].parent == roots["opus"]
    assert paths(82)[1].parent == roots["independent"] / "worker-a"
    assert paths(83)[0].parent == roots["primary"] / "worker-a"
    assert paths(83)[1].parent == roots["independent"] / "worker-b"
    with pytest.raises(ValueError, match="positive"):
        paths(0)


def test_incomplete_coverage_never_writes_ledger(tmp_path, monkeypatch):
    packet = {"artifact_sha256": "packet", "claims": [{"claim_id": "C1"}]}
    monkeypatch.setattr(assembler.audited, "_check_resume", lambda *args: (packet, {"artifact_sha256": "opus"}))
    monkeypatch.setattr(assembler.primary_queue, "_check_manifest", lambda *args: (packet, {"artifact_sha256": "primary"}))
    monkeypatch.setattr(assembler.independent_queue, "_check_manifest", lambda *args: (packet, {"artifact_sha256": "independent"}))
    output = tmp_path / "ledger" / "role-ledger-v6.json"
    report = assembler.audit(tmp_path, tmp_path, tmp_path, tmp_path, None)
    assert report["paired_claims"] == 0
    assert report["missing_role_artifact_count"] == 2
    with pytest.raises(ValueError, match="incomplete"):
        assembler.audit(tmp_path, tmp_path, tmp_path, tmp_path, None, output_path=output)
    assert not output.exists()


def test_full_coverage_writes_one_sha_bound_ledger(tmp_path, monkeypatch):
    packet = {"artifact_sha256": "packet", "claims": [
        {"claim_id": "C2", "scripture_refs": []},
        {"claim_id": "C1", "scripture_refs": []},
    ]}
    monkeypatch.setattr(assembler.audited, "_check_resume", lambda *args: (packet, {"artifact_sha256": "opus"}))
    monkeypatch.setattr(assembler.primary_queue, "_check_manifest", lambda *args: (packet, {"artifact_sha256": "primary"}))
    monkeypatch.setattr(assembler.independent_queue, "_check_manifest", lambda *args: (packet, {"artifact_sha256": "independent"}))
    monkeypatch.setattr(assembler.audited, "_check_graph", lambda *args: None)
    monkeypatch.setattr(assembler.audited, "_checked_decisions", lambda path, packet, n, role, model: (
        [{"claim_id": claim_id, "role": "other", "interpreted_ref_indices": [],
          "interpreted_evidence_refs": [], "reason": "not exegesis"}
         for claim_id in ("C1", "C2")], role + "-sha",
    ))
    (tmp_path / "primary-00001.json").touch()
    (tmp_path / "independent-00001.json").touch()
    output = tmp_path / "ledger" / "role-ledger-v6.json"
    result = assembler.audit(tmp_path, tmp_path, tmp_path, tmp_path, None, output_path=output)
    ledger = base._read_json(output)
    base._check_artifact(ledger)
    assert result["paired_claims"] == 2
    assert ledger["status"] == "all_eligible_reviewed"
    assert ledger["counts"] == {"other": 2}
    assert ledger["review_artifact_shas"] == {
        "primary-00001": "primary-sha", "independent-00001": "independent-sha",
    }


def test_applies_bounded_resolution_and_preserves_both_model_answers():
    source = {"claim_id": "C1", "scripture_refs": ["Matt 5:17"]}
    primary = {"claim_id": "C1", "role": "passage_exegesis",
               "interpreted_ref_indices": [0], "interpreted_evidence_refs": [],
               "reason": "interprets the verse"}
    independent = {"claim_id": "C1", "role": "other",
                   "interpreted_ref_indices": [], "interpreted_evidence_refs": [],
                   "reason": "doctrinal inference"}
    reconciled = base.reconcile([primary], [independent], [source])
    approved = {"C1": {
        "role": "passage_exegesis", "disposition": "resolved",
        "interpreted_passage_keys": ["Matt.5.17"],
        "primary_artifact_sha256": "p", "independent_artifact_sha256": "i",
        "analysis_index": 1,
    }}
    seen = set()
    assembler._apply_resolution(reconciled, [source], "p", "i", approved, seen)
    assert seen == {"C1"}
    assert reconciled[0]["role"] == "passage_exegesis"
    assert reconciled[0]["interpreted_passage_keys"] == ["Matt.5.17"]
    assert reconciled[0]["primary"] == primary
    assert reconciled[0]["independent"] == independent


def test_rejects_resolution_when_the_model_pair_does_not_match():
    source = {"claim_id": "C1", "scripture_refs": ["Matt 5:17"]}
    primary = {"claim_id": "C1", "role": "passage_exegesis",
               "interpreted_ref_indices": [0], "interpreted_evidence_refs": [],
               "reason": "interprets"}
    reconciled = base.reconcile([primary], [primary], [source])
    approved = {"C1": {
        "role": "other", "disposition": "resolved", "interpreted_passage_keys": [],
        "primary_artifact_sha256": "p", "independent_artifact_sha256": "i",
        "analysis_index": 1,
    }}
    with pytest.raises(ValueError, match="does not match model pair"):
        assembler._apply_resolution(reconciled, [source], "p", "i", approved, set())
