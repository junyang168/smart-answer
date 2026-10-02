from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "audit_complete_sources", Path(__file__).parents[2] / "scripts/audit-library.py"
)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


@pytest.mark.parametrize("verdict", ["same", "disputed"])
def test_viewpoint_review_receives_complete_bound_originals_and_keeps_verdict(
    tmp_path: Path, verdict: str
) -> None:
    path = tmp_path / "source.json"
    path.write_text(json.dumps({"script": [
        {"index": 1, "text": "開場"},
        {"index": 2, "text": "路12:20：魂"},
        {"index": 3, "text": "中間論證"},
        {"index": 4, "text": "太2:20：尋求小孩子魂的人"},
        {"index": 5, "text": "結論。~~已刪除說法~~"},
        {"index": "comment-1", "type": "comment", "text": "編輯評論"},
    ]}, ensure_ascii=False))
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"script": [{"index": 1, "text": "未綁定另一篇"}]}))
    documents = [{"object_id": sid, "payload": {
        "source_path": str(source_path), "source_type": "sermon_transcript",
        "locator_space": "spoken_body_v1",
    }} for sid, source_path in [("SRC-own", path), ("SRC-foreign", foreign)]]
    collections = {
        "canonical_viewpoints": [{"object_id": "CV-test", "payload": {"current_revision_id": "CVR-test"}}],
        "viewpoint_revisions": [{"object_id": "CVR-test", "payload": {"core_proposition": "魂"}}],
        "claims": [{"object_id": "CL-test", "payload": {"statement": "魂"}}],
        "source_fragments": [{"object_id": fid, "payload": {
            "source_id": "SRC-own", "paragraph_key": key,
        }} for fid, key in [("FR-first", "S0002"), ("FR-later", "S0004")]] + [
            {"object_id": "FR-foreign", "payload": {"source_id": "SRC-foreign", "paragraph_key": "S0001"}},
        ],
        "viewpoint_claim_links": [{"object_id": "VCL-test", "payload": {
            "viewpoint_id": "CV-test", "claim_id": "CL-test",
            "link_type": "equivalent_component", "effective_state": "active",
            "component_locator": {"statement_component": "魂"},
            "evidence_bindings": [{"source_fragment_id": fid} for fid in ["FR-first", "FR-later"]],
        }}],
    }

    class Store:
        def collection(self, name, **kwargs):
            return collections[name]

    class Model:
        calls = 0

        def judge(self, prompt, schema):
            self.calls += 1
            for text in ["開場", "路12:20：魂", "中間論證", "太2:20：尋求小孩子魂的人", "結論。"]:
                assert text in prompt
            assert prompt.count("太2:20：尋求小孩子魂的人") == 1
            for excluded in ["未綁定另一篇", "已刪除說法", "編輯評論"]:
                assert excluded not in prompt
            assert "連結類型 equivalent_component" in prompt
            return {"verdict": verdict, "issue": "other" if verdict == "disputed" else "none"}

    model = Model()
    report = AUDIT.audit_viewpoints(
        Store(), AUDIT.SourceIndex(documents, tmp_path), model, None,
        random.Random(241), workers=1, only={"CV-test"},
    )
    assert model.calls == 1
    assert report["results"][0]["verdict"] == verdict
    assert report["disputed"] == int(verdict == "disputed")
    assert report["judged"] == 1
