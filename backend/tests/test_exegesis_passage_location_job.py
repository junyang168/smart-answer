import json
from pathlib import Path
import pytest
from backend.pipeline import exegesis_passage_location_job as job
from backend.pipeline import exegesis_passage_location_runner as loc


def batch():
    return {"sources": [{"source_id": "s", "paragraphs": [{"paragraph_key": "S0001", "text": "馬太福音十六章這裏說鑰匙"}]}],
            "claims": [{"claim_id": cid, "source_id": "s"} for cid in ["a", "b"]]}


def row(cid, quote):
    return {"claim_id": cid, "status": "resolved", "primary": "Matt.16", "missing": "",
            "reason": "只證明章級", "secondary": [], "evidence": [{"paragraph_key": "S0001", "quote": quote, "purpose": "定位"}]}


def retain_failure(root):
    root.mkdir()
    loc.seal(root / "request.json", {"batch_sha256": loc.digest(batch()), "model": "test-model",
        "provider": "gpt", "prompt": loc.PROMPT, "schema": loc.response_schema()})
    loc.seal(root / "transport.raw.json", {"returncode": 0, "stdout": "", "stderr": ""})
    raw = {"decisions": [row("a", "馬太福音十六章"), row("b", "猜測的經文") ]}
    (root / "last-message.raw.txt").write_text(json.dumps(raw, ensure_ascii=False))


def test_selective_single_correction_and_cache_reuse(tmp_path, monkeypatch):
    root = tmp_path / "review"
    retain_failure(root)
    calls = []
    def fake(provider, model, payload, directory, limit):
        calls.append(payload)
        return loc.seal(directory / "validated.json", {"batch_sha256": loc.digest(payload), "model": model,
            "provider": provider, "response": {"decisions": [row("b", "馬太福音十六章")]}})
    monkeypatch.setattr(loc, "call", fake)
    result = job.obtain("gpt", "test-model", batch(), root, 500000)
    assert [c["claim_id"] for c in calls[0]["claims"]] == ["b"]
    assert result["response"]["decisions"][0] == row("a", "馬太福音十六章")
    assert job.obtain("gpt", "test-model", batch(), root, 500000) == result
    assert len(calls) == 1
    assert "猜測的經文" in (root / "last-message.raw.txt").read_text()


def test_exhausted_correction_never_calls_again(tmp_path, monkeypatch):
    root = tmp_path / "review"
    retain_failure(root)
    (root / "background-quote-correction-1").mkdir()
    def forbidden(*args):
        pytest.fail("exhausted correction must not launch another call")
    monkeypatch.setattr(loc, "call", forbidden)
    with pytest.raises(ValueError, match="exhausted"):
        job.obtain("gpt", "test-model", batch(), root, 500000)


def test_model_drift_cannot_reuse_existing_answer(tmp_path):
    root = tmp_path / "review"
    retain_failure(root)
    loc.seal(root / "validated.json", {"batch_sha256": loc.digest(batch()), "model": "previous-model",
        "response": {"decisions": [row("a", "馬太福音十六章"), row("b", "馬太福音十六章")]}})
    with pytest.raises(ValueError, match="binding differs"):
        job.obtain("gpt", "test-model", batch(), root, 500000)
