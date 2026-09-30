"""#409 original-context arbitration stays source-bound and fail-closed."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

from backend.pipeline import claim_passage_role_context_arbitration as arb
from backend.pipeline import claim_passage_role_context_reconcile as reconciliation
from backend.pipeline import claim_passage_role_runner as base


def _audit_module():
    path = Path(__file__).resolve().parents[2] / "scripts/claim-role-source-context-audit.py"
    spec = importlib.util.spec_from_file_location("claim_role_source_context_audit", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_original_body_identity_ignores_editorial_subtitle() -> None:
    module = _audit_module()
    body = [{"text": "教授講的話", "index": 1}]
    with_title = [{"text": "## 編輯標題", "type": "subtitle", "index": "subtitle-1"}] + body
    plain_rows, plain_sha = module.sermon_rows(json.dumps(body).encode())
    titled_rows, titled_sha = module.sermon_rows(json.dumps(with_title).encode())
    assert plain_rows == titled_rows == ["教授講的話"]
    assert plain_sha == titled_sha


def test_original_body_identity_detects_spoken_change() -> None:
    module = _audit_module()
    before = [{"text": "教授講的話", "index": 1}]
    after = [{"text": "教授講的另一句話", "index": 1}]
    assert module.sermon_rows(json.dumps(before).encode())[1] != module.sermon_rows(
        json.dumps(after).encode()
    )[1]


def test_context_audit_recovers_exact_old_body_without_accepting_new_body(tmp_path) -> None:
    module = _audit_module()
    review = tmp_path / "script_review" / "S.json"
    patched = tmp_path / "script_patched" / "S.json"
    review.parent.mkdir()
    patched.parent.mkdir()
    review.write_text(json.dumps([{"text": "新版正文", "index": 1}]))
    patched.write_text(json.dumps([{"text": "旧版正文", "index": 1}]))
    expected_body = module.sermon_rows(patched.read_bytes())[1]
    source = {"source_id": "SRC-1", "source_type": "sermon_transcript",
              "source_path": str(review), "source_body_sha256": expected_body}
    path, _raw, rows, _file_sha, body_sha = module.matching_source(source, "f" * 64)
    assert path == patched
    assert rows == ["旧版正文"]
    assert body_sha == expected_body
    source["source_body_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="no frozen file or body match"):
        module.matching_source(source, "f" * 64)


@pytest.fixture
def source_batch():
    return [{"claim_id": "CL-1", "context": [{"paragraph_key": "S0001",
                                                "text": "提多書第二章第十二節中的情慾。"}]}]


def test_arbitration_requires_exact_quote(source_batch) -> None:
    good = {"decisions": {"CL-1": {"role": "passage_exegesis",
                                    "candidate_reference": "提多書 2:12",
                                    "supporting_context_key": "S0001",
                                    "supporting_quote": "情慾",
                                    "reason": "解釋本節用詞"}}}
    arb.validate(good, source_batch)
    wrong_quote = copy.deepcopy(good)
    wrong_quote["decisions"]["CL-1"]["supporting_quote"] = "不存在"
    with pytest.raises(ValueError, match="support quote"):
        arb.validate(wrong_quote, source_batch)


def test_arbitration_refuses_missing_claim_and_unanchored_exegesis(source_batch) -> None:
    with pytest.raises(ValueError, match="exact Claim batch"):
        arb.validate({"decisions": {}}, source_batch)
    with pytest.raises(ValueError, match="lacks source-grounded"):
        arb.validate({"decisions": {"CL-1": {"role": "passage_exegesis",
                                             "candidate_reference": "",
                                             "supporting_context_key": "",
                                             "supporting_quote": "",
                                             "reason": "guess"}}}, source_batch)


def test_invalid_raw_answer_is_retained_before_one_bounded_retry(tmp_path) -> None:
    row = {"claim_id": "CL-1", "statement": "經文的詞義", "claim_scripture_refs": [],
           "evidence_steps": [], "anchor_indices": [0],
           "context": [{"paragraph_key": "S0001", "text": "這是經文原文的詞義。"}]}

    class FakeClient:
        model = arb.MODEL["gpt"]
        calls = 0

        def generate_json(self, *_args):
            self.calls += 1
            quote = "原文的詞義" if self.calls == 2 else "不是原文"
            return {"decisions": {"CL-1": {"role": "passage_exegesis",
                                             "candidate_reference": "太 1:1",
                                             "supporting_context_key": "S0001",
                                             "supporting_quote": quote,
                                             "reason": "test"}}}

    client = FakeClient()
    result = arb.one_batch({"artifact_sha256": "a" * 64}, [row], provider="gpt",
                           output_root=tmp_path, client=client, retry_invalid_once=True)
    original = list((tmp_path / "gpt").glob("batch-????????????????.json"))
    assert len(original) == 1
    assert json.loads(original[0].read_text())["response"]["decisions"]["CL-1"][
        "supporting_quote"
    ] == "不是原文"
    assert result["retry_of"] == str(original[0])
    assert client.calls == 2


def test_reconciliation_counts_two_independent_providers(tmp_path) -> None:
    row = {"claim_id": "CL-1", "statement": "test", "claim_scripture_refs": [],
           "evidence_steps": [], "anchor_indices": [0],
           "context": [{"paragraph_key": "S0001", "text": "test source"}]}
    audit = base._artifact({"schema_version": "wang_claim_role_source_context_audit_v1",
                            "rows": [row]})
    payload = json.dumps({"audit_sha256": audit["artifact_sha256"],
                          "claims": [arb.compact(row)]}, ensure_ascii=False,
                         separators=(",", ":"))
    response = {"decisions": {"CL-1": {"role": "other",
                                        "candidate_reference": "",
                                        "supporting_context_key": "S0001",
                                        "supporting_quote": "test source",
                                        "reason": "test"}}}
    for provider in arb.MODEL:
        root = tmp_path / provider
        root.mkdir()
        artifact = base._artifact({"schema_version": "wang_claim_role_source_context_arbitration_v1",
                                   "audit_sha256": audit["artifact_sha256"],
                                   "provider": provider, "model": arb.MODEL[provider],
                                   "claim_ids": ["CL-1"],
                                   "payload_sha256": arb.hashlib.sha256(payload.encode()).hexdigest(),
                                   "response": response})
        base._write_immutable(root / f"batch-{provider}.json", artifact)
    report = reconciliation.reconcile(audit, [tmp_path])
    assert report["dual_reviewed"] == 1
    assert report["single_reviewed"] == report["unreviewed"] == 0
    assert report["role_pair_counts"] == {"other|other": 1}
