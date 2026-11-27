import json
from pathlib import Path

import pytest

from backend.pipeline import series_unit_pipeline as P


def _write_lecture(path: Path, texts: list[str]) -> Path:
    doc = {"script": [{"index": i + 1, "text": t} for i, t in enumerate(texts)]}
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


def test_discover_lecture_specs_sorts_naturally(tmp_path):
    pub = tmp_path / "script_published"
    pub.mkdir()
    _write_lecture(pub / "2016 NYSC 專題：馬太福音釋經（五）3.json", ["c"])
    _write_lecture(pub / "2016 NYSC 專題：馬太福音釋經（五）1.json", ["a"])
    _write_lecture(pub / "2016 NYSC 專題：馬太福音釋經（五）2.json", ["b"])

    title, specs = P.discover_lecture_specs("2016-nysc-matthew-5", data_base_dir=tmp_path)
    assert title == "2016 NYSC 專題：馬太福音釋經（五）"
    assert [s.lecture for s in specs] == ["L1", "L2", "L3"]
    assert [s.path.name for s in specs] == [
        "2016 NYSC 專題：馬太福音釋經（五）1.json",
        "2016 NYSC 專題：馬太福音釋經（五）2.json",
        "2016 NYSC 專題：馬太福音釋經（五）3.json",
    ]


def test_phase_a_runs_without_opus_and_phase_b_reuses_stage1(tmp_path, monkeypatch):
    pub = tmp_path / "script_published"
    pub.mkdir()
    l1 = _write_lecture(pub / "2016 NYSC 專題：馬太福音釋經（五）1.json", ["第一講重點論述。"])

    opus_calls = []

    def fail_opus(*_args, **_kwargs):
        raise AssertionError("Phase A must never instantiate Claude Opus")

    monkeypatch.setattr(P, "_build_opus_clients", fail_opus)

    class FakeGPT:
        backend = "codex_subscription"
        model = "gpt-6.1-sol"
        reasoning_effort = "medium"

    # Stub segmentation, plan, argument, fidelity, and roundtrip to verify phase isolation
    monkeypatch.setattr(
        P.seg,
        "build_paragraph_cards",
        lambda series, client, cache_dir: {"L1": [{"pk": "L1-S0001", "chars": 7, "summary": "論述", "is_pure_shell": False}]},
    )
    monkeypatch.setattr(
        P.seg,
        "segment_series",
        lambda series, cards, client, cache_dir: {
            "version": "series_unit_segmentation_v1",
            "series": series.series_name,
            "sources": [{"lecture": "L1", "source_id": "2016 NYSC 專題：馬太福音釋經（五）1", "file": str(l1), "sha256": "0" * 64, "body_sha256": "1" * 64}],
            "units": [{"unit_id": "U01", "title": "單元一", "passage": "太 1", "cross_lecture": False, "seam_evidence": None, "ranges": ["L1-S0001"]}],
            "non_argument_paragraphs": [],
        },
    )
    monkeypatch.setattr(P.seg, "verify_split_seams", lambda *a, **k: [])
    monkeypatch.setattr(P.unit_plan, "run", lambda m, u, ports: (u / "unit-plan.json").write_text("{}", encoding="utf-8"))

    def fake_unit_arg(plan_dir, root, ports):
        payload = {
            "version": "unit_argument_candidate_v1",
            "sources": [{"source_id": "2016 NYSC 專題：馬太福音釋經（五）1", "file_sha256": "0" * 64, "body_sha256": "1" * 64}],
            "sentences": [{"sentence_id": "S0001-1", "source_id": "2016 NYSC 專題：馬太福音釋經（五）1", "paragraph": "S0001", "text": "第一講重點論述。", "time": "00:00", "start": 0, "end": 7, "modality": "spoken"}],
            "nodes": {"C001": {"kind": "claim", "statement": "重點論述", "at": [1]}},
            "edges": [],
            "question_states": [],
            "gaps": [],
        }
        (Path(root) / "unit-argument.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    monkeypatch.setattr(P.unit_arg, "run", fake_unit_arg)
    monkeypatch.setattr(P.fidelity, "prefilter", lambda rows, fid_dir, ports: {r["job_id"]: 0.8 for r in rows})

    stage1_calls = []
    stage2_calls = []

    def fake_roundtrip(sample_path, out_dir, **kwargs):
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if kwargs.get("stage1_only"):
            stage1_calls.append(kwargs)
            (out_dir / "02_blind_reconstruction.json").write_text('{"gaps": []}', encoding="utf-8")
            return {"stage1_gaps": 0}
        stage2_calls.append(kwargs)
        (out_dir / "04_structured_diff_report.json").write_text('{"summary_metrics": {}}', encoding="utf-8")
        return {"stage1_gaps": 0}

    monkeypatch.setattr(P.roundtrip, "run_verification", fake_roundtrip)

    res_a = P.run_phase_a("2016-nysc-matthew-5", data_base_dir=tmp_path, gpt_client=FakeGPT())
    assert res_a["unit_count"] == 1
    assert len(stage1_calls) == 1
    assert len(stage2_calls) == 0

    unit_dir = tmp_path / "unit_argument_series" / "2016-nysc-matthew-5" / "units" / "U01"
    assert (unit_dir / "fidelity" / "prefilter.json").exists()
    assert not (unit_dir / "fidelity_summary.json").exists()

    class FakeOpus:
        model = "claude-opus-5-5"

    def fake_judge(candidate, fid_dir, client):
        opus_calls.append(client.model)
        return {"records": 1, "findings": [], "needs_look": []}

    monkeypatch.setattr(P.fidelity, "run_judge", fake_judge)

    res_b = P.run_phase_b(
        "2016-nysc-matthew-5",
        data_base_dir=tmp_path,
        gpt_client=FakeGPT(),
        opus_judge_client=FakeOpus(),
        opus_attr_client=FakeOpus(),
    )
    assert res_b["unit_count"] == 1
    assert opus_calls == ["claude-opus-5-5"]
    assert len(stage2_calls) == 1
    assert stage2_calls[0]["reuse_stage1"] is True
    assert (unit_dir / "fidelity_summary.json").exists()
    assert (unit_dir / "roundtrip_diff.json").exists()
