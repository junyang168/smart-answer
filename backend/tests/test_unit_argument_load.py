"""Tests for backend.pipeline.unit_argument_load."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from backend.api.canonical_repository.postgres_store import normalize_package
from backend.pipeline import unit_argument_load as U
from backend.pipeline.knowledge_package_merge import validate_merged_package


def _write_json(path: Path, data: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text, encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sample_unit_data() -> dict:
    return {
        "version": "unit_argument_candidate_v1",
        "status": "candidate",
        "unit_plan_sha256": "plan-sha-001",
        "sources": [
            {
                "source_id": "2016 NYSC Lecture 4",
                "file_sha256": "file-sha-1",
                "body_sha256": "body-sha-1",
                "ranges": [{"first": "S0019", "last": "S0020"}],
            },
            {
                "source_id": "2016 NYSC Lecture 5",
                "file_sha256": "file-sha-2",
                "body_sha256": "body-sha-2",
                "ranges": [{"first": "S0001", "last": "S0002"}],
            },
        ],
        "sentences": [
            {"source_id": "2016 NYSC Lecture 4", "paragraph": "S0019", "sentence_id": 1, "text": "Sentence one."},
            {"source_id": "2016 NYSC Lecture 5", "paragraph": "S0001", "sentence_id": 2, "text": "Sentence two."},
        ],
        "claims": {
            "C001": {
                "statement": "Main claim one",
                "claim_kind": "interpretive_judgment",
                "attribution": "professor",
                "scripture_refs": ["太16:24"],
                "topic_terms": ["捨己"],
                "at": [1],
                "source_id": "2016 NYSC Lecture 4",
            },
            "C002": {
                "statement": "Main claim two",
                "claim_kind": "interpretive_judgment",
                "attribution": "professor",
                "at": [2],
                "source_id": "2016 NYSC Lecture 5",
            },
        },
        "nodes": {
            "N1": {"kind": "evidence", "statement": "Evidence one", "at": [1]},
            "N2": {"kind": "position", "statement": "Wrong position", "at": [1]},
            "N3": {"kind": "observation", "statement": "Text observation", "at": [2]},
            "Q001": {"kind": "question", "statement": "What does self-denial mean?", "at": [1]},
        },
        "edges": [
            {"from_id": "N1", "to_id": "C001", "relation": "supports", "reason": "Direct scripture support"},
            {"from_id": "C001", "to_id": "Q001", "relation": "answers", "reason": "Defines self-denial"},
            {"from_id": "C001", "to_id": "N2", "relation": "refutes", "reason": "Rejects wrong view"},
            {"from_id": "C002", "to_id": "C001", "relation": "contextualizes", "reason": "Next lecture context"},
            {"from_id": "N3", "to_id": "N1", "relation": "illustrates", "reason": "Observation illustrates"},
        ],
        "question_states": [{"question_id": "Q001", "state": "answered", "remaining": ""}],
    }


def _stage_unit(tmp_path: Path, *, verdict: str = "approved", stale_attr: bool = False, changed: int = 0) -> tuple[Path, Path]:
    series_id = "2014-nysc-matthew-4"
    unit_id = "U04"
    unit_dir = tmp_path / "unit_argument_series" / series_id / "units" / unit_id
    _write_json(unit_dir / "unit-argument.json", _sample_unit_data())
    _write_json(
        unit_dir / "fidelity_summary.json",
        {"total_nodes": 5, "faithful": 5 - changed, "changed": changed, "added": 0, "findings": []},
    )
    diff_sha = _write_json(unit_dir / "roundtrip_diff.json", {"summary_metrics": {}, "core_argument_omissions": []})
    _write_json(
        unit_dir / "roundtrip_attribution.json",
        {"diff_sha256": "wrong-sha" if stale_attr else diff_sha, "attributions": []},
    )
    if verdict:
        _write_json(
            tmp_path / "argument_layer" / "series-unit-rulings.json",
            [{"series_id": series_id, "unit_id": unit_id, "verdict": verdict, "note": "ok"}],
        )
    return unit_dir, tmp_path


def test_unit_argument_to_package_multi_lecture_validates():
    pkg = U.unit_argument_to_package(_sample_unit_data(), series_id="2014-nysc-matthew-4", unit_id="U04")
    validate_merged_package(pkg, native_relations=True)
    records = normalize_package(pkg)
    assert len(records["source_documents"]) == 2
    assert len(records["claims"]) == 2
    assert len(records["evidence_steps"]) == 1
    assert len(records["position_nodes"]) == 1
    assert len(records["observations"]) == 1
    assert len(records["questions"]) == 1
    assert len(records["claim_relations"]) == 1
    assert len(records["knowledge_relations"]) == 4


def test_dry_run_and_gates(tmp_path: Path):
    unit_dir, data_dir = _stage_unit(tmp_path, verdict="approved")
    res = U.load_unit(None, unit_dir, series_id="2014-nysc-matthew-4", unit_id="U04", data_base_dir=data_dir, dry_run=True)
    assert res["dry_run"] is True
    assert res["counts"]["source_documents"] == 2

    unit_dir_bad_ruling, data_dir_bad = _stage_unit(tmp_path / "r1", verdict="needs_revision")
    with pytest.raises(ValueError, match="not approved"):
        U.verify_unit_gates(unit_dir_bad_ruling, series_id="2014-nysc-matthew-4", unit_id="U04", data_base_dir=data_dir_bad, allow_unapproved=False)

    unit_dir_bad_fid, data_dir_fid = _stage_unit(tmp_path / "r2", verdict="approved", changed=1)
    with pytest.raises(ValueError, match="unresolved fidelity"):
        U.verify_unit_gates(unit_dir_bad_fid, series_id="2014-nysc-matthew-4", unit_id="U04", data_base_dir=data_dir_fid, allow_unapproved=False)

    unit_dir_bad_attr, data_dir_attr = _stage_unit(tmp_path / "r3", verdict="approved", stale_attr=True)
    with pytest.raises(ValueError, match="stale roundtrip_attribution"):
        U.verify_unit_gates(unit_dir_bad_attr, series_id="2014-nysc-matthew-4", unit_id="U04", data_base_dir=data_dir_attr, allow_unapproved=False)


def test_allow_unapproved_loads_as_candidate(tmp_path: Path):
    unit_dir, data_dir = _stage_unit(tmp_path, verdict="", stale_attr=True, changed=1)
    res = U.load_unit(
        None,
        unit_dir,
        series_id="2014-nysc-matthew-4",
        unit_id="U04",
        data_base_dir=data_dir,
        dry_run=True,
        allow_unapproved=True,
    )
    assert res["dry_run"] is True
    assert res["gates"]["approved"] is False


def test_load_unit_into_temp_postgres_and_replace(tmp_path: Path):
    psycopg = pytest.importorskip("psycopg")
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    from backend.pipeline.argument_review import database_url

    base = database_url().rsplit("/", 1)[0]
    db_name = "unit_argument_load_test_845"
    try:
        with psycopg.connect(base + "/postgres", autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {db_name}")
            admin.execute(f"CREATE DATABASE {db_name}")
    except psycopg.Error:
        pytest.skip("no local Postgres to create a throwaway database")

    try:
        PostgresKnowledgeStore(base + "/" + db_name).migrate()
        with psycopg.connect(base + "/" + db_name) as conn:
            unit_dir, data_dir = _stage_unit(tmp_path, verdict="approved")
            first = U.load_unit(conn, unit_dir, series_id="2014-nysc-matthew-4", unit_id="U04", data_base_dir=data_dir)
            assert first["read_back"] == "matches"
            assert U.unit_holder(conn, "2014-nysc-matthew-4", "U04") == first["change_set_id"]

            # Re-loading without replaces= must fail.
            with pytest.raises(ValueError, match="pass replaces="):
                U.load_unit(conn, unit_dir, series_id="2014-nysc-matthew-4", unit_id="U04", data_base_dir=data_dir)

            # Update unit-argument.json and replace.
            updated = _sample_unit_data()
            updated["claims"]["C001"]["statement"] = "Updated main claim one"
            _write_json(unit_dir / "unit-argument.json", updated)
            second = U.load_unit(
                conn,
                unit_dir,
                series_id="2014-nysc-matthew-4",
                unit_id="U04",
                data_base_dir=data_dir,
                replaces=first["change_set_id"],
            )
            assert second["read_back"] == "matches"
            assert U.unit_holder(conn, "2014-nysc-matthew-4", "U04") == second["change_set_id"]
    finally:
        with psycopg.connect(base + "/postgres", autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {db_name}")
