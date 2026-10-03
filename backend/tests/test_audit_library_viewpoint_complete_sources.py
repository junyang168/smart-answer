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


def test_component_context_contract_preserves_identity_and_negative_guards():
    prompt = AUDIT._viewpoint_prompt("CV-test", {"core_proposition": "神所揀選的僕人"}, [
        "被歸入的成分：僕人\n主張全文：宣告確認耶穌具有王與僕人的身份。\n"
        "【原文】僕人指賽42:1中神所揀選的僕人。"
    ])
    for rule in ["不是必須獨立成句", "該條主張全文及本連結綁定的原文",
                 "不得僅因短成分", "未定位部分只用於理解原句語境",
                 "不得借其他主張或未綁定來源補限定", "缺少必要條件或範圍不同",
                 "仍須判異議", "替換它", "同樣的情況下為假"]:
        assert rule in prompt
    assert "僕人指賽42:1" in prompt


@pytest.mark.parametrize("verdict", ["same", "disputed"])
@pytest.mark.parametrize("link_type", ["equivalent_component", "supports"])
def test_viewpoint_review_receives_complete_bound_originals_and_keeps_verdict(
    tmp_path: Path, verdict: str, link_type: str
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
            "link_type": link_type, "effective_state": "active",
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
            assert f"連結類型 {link_type}" in prompt
            assert "共同支持完整主張" in prompt
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


@pytest.mark.parametrize("present", [False, True])
def test_explicit_viewpoint_without_active_links_is_blocked_not_skipped(tmp_path, present):
    class Store:
        def collection(self, name, **kwargs):
            if present and name == 'canonical_viewpoints':
                return [{'object_id':'CV-missing','payload':{'current_revision_id':'R'}}]
            return []

    class Model:
        def judge(self, *args):
            raise AssertionError('No evidence must not trigger a model guess')

    report=AUDIT.audit_viewpoints(Store(), AUDIT.SourceIndex([], tmp_path), Model(), None,
                                 random.Random(241), workers=1, only={'CV-missing'})
    assert report['population']==report['sampled']==1
    assert report['judged']==0 and report['blocked']==1
    assert report['results'][0]['viewpoint_id']=='CV-missing'


def test_default_membership_population_does_not_relabel_supports(tmp_path):
    class Store:
        def collection(self, name, **kwargs):
            if name == 'viewpoint_claim_links':
                return [{'object_id':'L','payload':{'viewpoint_id':'CV','link_type':'supports','effective_state':'active'}}]
            if name == 'canonical_viewpoints':
                return [{'object_id':'CV','payload':{}}]
            return []

    class Model:
        def judge(self, *args):
            raise AssertionError('Default membership population is not supports population')

    report=AUDIT.audit_viewpoints(Store(), AUDIT.SourceIndex([], tmp_path), Model(), None,
                                 random.Random(241), workers=1)
    assert report['population']==0 and report['results']==[]


@pytest.mark.parametrize('link_type,present,locator,expected',[
    ('equivalent_full',True,None,[]),
    ('equivalent_component',True,None,['no_component_locator']),
    ('equivalent_full',False,None,['claim_missing']),
    ('equivalent_full',True,{'statement_component':'不存在'},['stitched']),
])
def test_full_equivalence_does_not_require_component_but_preserves_real_errors(link_type,present,locator,expected):
    class Store:
        def collection(self,name,**kwargs):
            if name=='claims':
                return [{'object_id':'C','payload':{'statement':'完整原文'}}] if present else []
            return [{'object_id':'L','payload':{'claim_id':'C','link_type':link_type,'component_locator':locator}}]
    assert [r['verdict'] for r in AUDIT._audit_component_locators(Store())]==expected
