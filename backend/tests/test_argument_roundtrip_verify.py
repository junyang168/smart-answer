import json
from pathlib import Path

import pytest

from backend.pipeline import argument_fidelity as af
from backend.pipeline import argument_roundtrip_verify as rtv


def _unit_candidate() -> dict:
    return {
        "version": "unit_argument_candidate_v1",
        "title": "測試單元",
        "sentences": [
            {"source_id": "2012-L4", "paragraph": "S0009", "time": "00:35", "text": "耶穌為什麼用比喻？"},
            {"source_id": "2012-L5", "paragraph": "S0019", "time": "00:20", "text": "mēpote 應該翻成或者。"},
            {"source_id": "2012-L6", "paragraph": "S0003", "time": "00:09", "text": "解比喻要抓重點。"},
        ],
        "nodes": {
            "Q001": {"kind": "question", "statement": "耶穌為什麼用比喻？", "at": [1]},
            "C001": {"kind": "claim", "statement": "mēpote 應譯為或者", "at": [2]},
            "C002": {"kind": "claim", "statement": "解比喻要抓重點", "at": [3]},
        },
        "edges": [
            {
                "from_id": "C001",
                "to_id": "Q001",
                "relation": "answers",
                "reason": "正面回答用比喻目的",
                "at": [1, 2],
                "attribution": "explicit",
            }
        ],
        "question_states": [{"question_id": "Q001", "state": "answered", "remaining": ""}],
        "gaps": [],
    }


def test_project_blind_graph_strips_transcript_and_groups_components():
    cand = _unit_candidate()
    blind = rtv.project_blind_graph(cand)
    rtv.assert_no_transcript_leakage(blind)
    assert blind["format"] == "unit_argument_blind_v2"
    assert len(blind["components"]) == 2
    assert set(blind["nodes"]) == {"Q001", "C001", "C002"}
    assert "at" not in blind["nodes"]["C001"]
    assert "sentences" not in blind

    with pytest.raises(AssertionError, match="Transcript leakage"):
        rtv.assert_no_transcript_leakage({"nodes": {"C001": {"at": [1]}}})


def test_extract_transcript_text_and_fidelity_adapter_for_unit_candidate():
    cand = _unit_candidate()
    text = rtv.extract_transcript_text(cand)
    assert "S0009] 耶穌為什麼用比喻？" in text
    assert "S0019] mēpote 應該翻成或者。" in text

    rows = af.records(cand)
    assert [r["job_id"] for r in rows] == ["Q001", "C001", "C002"]
    assert rows[1]["kind"] == "Claim"
    assert rows[1]["his_words"] == "[本節點結論原話] mēpote 應該翻成或者。"


def test_run_verification_end_to_end_with_fake_clients(tmp_path: Path):
    sample = tmp_path / "unit-argument.json"
    sample.write_text(json.dumps(_unit_candidate(), ensure_ascii=False), encoding="utf-8")

    class FakeClient:
        def __init__(self, reply):
            self.reply = reply
            self.calls = 0

        def generate_json(self, system_prompt, user_prompt, json_schema):
            self.calls += 1
            return self.reply

    rec = FakeClient(
        {
            "overall_structure_summary": "兩個模組",
            "sections": [],
            "full_narrative": "教授解釋 mēpote 應譯為或者。",
            "gaps": [],
            "isolated_nodes": ["C002"],
        }
    )
    judge = FakeClient(
        {
            "original_units": [],
            "core_argument_omissions": [
                {
                    "omission_id": "O01",
                    "severity": "low",
                    "category": "missing_logical_bridge",
                    "transcript_refs": ["S0003"],
                    "original_excerpt": "解比喻要抓重點。",
                    "manifested_as_gap": False,
                    "explanation": "未連到主線",
                }
            ],
            "stance_and_logic_distortions": [],
            "non_argument_noise": [],
            "gap_diagnoses": [],
            "summary_metrics": {
                "total_original_units": 2,
                "core_units_total": 2,
                "core_units_covered": 2,
                "core_units_partial": 0,
                "core_units_missing": 0,
                "omission_count": 1,
                "silent_omission_count": 1,
                "distortion_count": 0,
                "noise_count": 0,
                "true_extraction_gap_count": 0,
                "overall_assessment": "OK",
            },
            "top_actionable_fixes": [],
        }
    )
    attr = FakeClient(
        {
            "attributions": [
                {
                    "finding_id": "O01",
                    "root_cause": "extraction_defect",
                    "evidence_ids": ["C002"],
                    "explanation": "C002 無連邊",
                }
            ],
            "true_extraction_defect_count": 1,
            "reconstructor_error_count": 0,
        }
    )

    out_dir = tmp_path / "rtv_out"
    summary = rtv.run_verification(
        sample,
        out_dir,
        reconstructor_client=rec,
        judge_client=judge,
        attributor_client=attr,
    )
    assert summary["leakage_check"] == "PASS"
    assert summary["attribution_summary"] == {"true_extraction_defect_count": 1, "reconstructor_error_count": 0}
    assert rec.calls == judge.calls == attr.calls == 1
    assert (out_dir / "01_blind_graph_input.json").exists()
    assert (out_dir / "04_structured_diff_report.json").exists()
    assert (out_dir / "05_root_cause_attribution.json").exists()
    attr_saved = json.loads((out_dir / "05_root_cause_attribution.json").read_text(encoding="utf-8"))
    assert attr_saved["diff_sha256"] == rtv.sha256_json(
        json.loads((out_dir / "04_structured_diff_report.json").read_text(encoding="utf-8"))
    )

    # Re-running in the same out_dir with attribute=False must remove stale attribution
    rtv.run_verification(
        sample,
        out_dir,
        attribute=False,
        reconstructor_client=rec,
        judge_client=judge,
        attributor_client=attr,
    )
    assert not (out_dir / "05_root_cause_attribution.json").exists()


def test_series_unit_reader_ignores_stale_or_unbound_attribution(tmp_path: Path):
    from backend.pipeline import series_unit_reader as reader

    series_dir = tmp_path / "unit_argument_series" / "s1"
    udir = series_dir / "units" / "U01"
    udir.mkdir(parents=True)
    (series_dir / "segmentation.json").write_text(
        json.dumps({
            "series": "S1",
            "sources": [{"source_id": "SRC1", "lecture": "L1"}],
            "units": [{"unit_id": "U01", "title": "Unit 1", "passage": "Mt 1", "chars": 10, "ranges": ["L1-P1"]}],
        }),
        encoding="utf-8",
    )
    (udir / "unit-argument.json").write_text(
        json.dumps({
            "version": "unit_argument_candidate_v1",
            "sentences": [{"source_id": "SRC1", "paragraph": "P1", "time": "00:01", "text": "原話"}],
            "nodes": {"C001": {"kind": "claim", "statement": "主張", "at": [1]}},
            "edges": [],
            "question_states": [],
        }),
        encoding="utf-8",
    )
    diff = {
        "summary_metrics": {"core_units_covered": 1, "core_units_total": 1, "distortion_count": 1, "omission_count": 0},
        "stance_and_logic_distortions": [{"distortion_id": "D01", "severity": "medium", "explanation": "新報告扭曲"}],
        "core_argument_omissions": [],
        "non_argument_noise": [],
    }
    (udir / "roundtrip_diff.json").write_text(json.dumps(diff), encoding="utf-8")
    (udir / "roundtrip_attribution.json").write_text(
        json.dumps({
            "diff_sha256": "deadbeef",
            "attributions": [{"finding_id": "D01", "root_cause": "reconstructor_compression"}],
        }),
        encoding="utf-8",
    )

    series_info = reader.get_series(tmp_path, "s1")
    rt_row = series_info["units"][0]["roundtrip"]
    assert rt_row["distortions"] == 1
    assert rt_row["attribution_verified"] is False

    unit_info = reader.get_unit(tmp_path, "s1", "U01")
    assert unit_info["roundtrip"]["metrics"]["distortion_count"] == 1
    assert unit_info["roundtrip"]["attribution_verified"] is False

    (udir / "roundtrip_attribution.json").write_text(
        json.dumps({
            "diff_sha256": rtv.sha256_json(diff),
            "attributions": [{"finding_id": "D01", "root_cause": "reconstructor_compression"}],
        }),
        encoding="utf-8",
    )
    unit_info2 = reader.get_unit(tmp_path, "s1", "U01")
    assert unit_info2["roundtrip"]["metrics"]["distortion_count"] == 0
    assert unit_info2["roundtrip"]["attribution_verified"] is True
