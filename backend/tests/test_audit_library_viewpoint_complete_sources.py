from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "audit_complete_sources", Path(__file__).parents[2] / "scripts/audit-library.py"
)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


@pytest.mark.parametrize('values',[
    [],[''],[' ', '\n\t'],[' 原文 '],['甲~~乙~~丙', '~~全部刪除~~'],
    ['第一段\n第二行',' 同一句 ', ' 同一句 ', '最後限定。'],
    [None,0,1,False,True],['<svg><text>圖</text></svg>', '【編輯】字面原文'],
])
def test_whole_original_text_is_identical_to_previous_expression_without_duplicate_normalization(values,monkeypatch):
    # Independent old expression as oracle, including order, duplicates,
    # whitespace and struck-through gaps. No model/provider or SQL invocation.
    segments=[{'text':value} for value in values]
    live_text=AUDIT.live_text
    expected='\n\n'.join(live_text(s.get('text','')).strip() for s in segments
                           if live_text(s.get('text','')).strip())
    calls=[]
    def counted(value):
        calls.append(value);return live_text(value)
    monkeypatch.setattr(AUDIT,'live_text',counted)
    sources=SimpleNamespace(documents={'S':{'title':'來源'}},
                            file_for=lambda sid:SimpleNamespace(segments=segments) if sid=='S' else None)
    fragments={'F':{'source_id':'S','verbatim_excerpt':'完整錨點'}}
    actual=AUDIT._whole_sources_for_claim(['F','F','missing'],fragments,sources)
    assert actual==([f'《來源》\n{expected}'],['完整錨點'])
    assert calls==values


def test_thousand_source_identities_keep_all_source_text_and_do_not_cache_mutable_context(monkeypatch):
    segments=[{'text':'第一句。'},{'text':' '},{'text':'最後必要限定。'}]
    source=SimpleNamespace(segments=segments)
    documents={f'S{i}':{'title':f'講道{i}'} for i in range(1000)}
    sources=SimpleNamespace(documents=documents,file_for=lambda sid:source if sid in documents else None)
    fragments={f'F{i}':{'source_id':f'S{i}','verbatim_excerpt':f'錨點{i}'} for i in range(1000)}
    real=AUDIT.live_text;calls=[]
    monkeypatch.setattr(AUDIT,'live_text',lambda text:(calls.append(text),real(text))[1])
    texts,anchors=AUDIT._whole_sources_for_claim(list(fragments),fragments,sources)
    assert texts==[f'《講道{i}》\n第一句。\n\n最後必要限定。' for i in range(1000)]
    assert anchors==[f'錨點{i}' for i in range(1000)]
    assert len(calls)==3000
    # No new lifetime cache: a later invocation reads its actual current input.
    segments[-1]['text']='改後限定。';documents['S0']['title']='改後來源'
    assert AUDIT._whole_sources_for_claim(['F0'],fragments,sources)[0]==['《改後來源》\n第一句。\n\n改後限定。']


def test_reconsideration_is_not_a_forced_pass():
    prompt = AUDIT._reconsideration_prompt({'reason': '原合取異議', 'verdict': 'disputed'})
    for text in ['原合取異議', '不是要求通過', 'equivalent_full', 'equivalent_component',
                 '仍判 disputed', '不得改連結', '唯一一次復核', '不成立的原文依據']:
        assert text in prompt


@pytest.mark.parametrize('mutation', ['none', 'recursive', 'incomplete', 'foreign', 'duplicate', 'wrong_model'])
def test_reconsideration_input_binding_and_one_round(tmp_path, mutation):
    original = {'meta': {'model': 'gemini-3.8-flash'}, 'layers': {'4': {
        'complete': True, 'judged': 1, 'disputed': 1,
        'results': [{'viewpoint_id': 'V', 'revision_id': 'R', 'verdict': 'disputed'}]}}}
    if mutation == 'recursive': original['meta']['reconsidered_audit'] = {'path': 'old'}
    elif mutation == 'incomplete': original['layers']['4']['complete'] = False
    elif mutation == 'foreign': original['layers']['4']['results'][0]['viewpoint_id'] = 'foreign'
    elif mutation == 'duplicate': original['layers']['4']['results'] *= 2
    elif mutation == 'wrong_model': original['meta']['model'] = 'other'
    path = tmp_path/'original.json'; path.write_text(json.dumps(original))
    if mutation != 'none':
        with pytest.raises(ValueError): AUDIT.load_reconsiderations(path, {'V'})
    else:
        found, bound = AUDIT.load_reconsiderations(path, {'V'})
        import hashlib
        assert found['V']['revision_id'] == 'R'
        assert bound['file_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_component_context_contract_preserves_identity_and_negative_guards():
    prompt = AUDIT._viewpoint_prompt("CV-test", {"core_proposition": "神所揀選的僕人"}, [
        "被歸入的成分：僕人\n主張全文：宣告確認耶穌具有王與僕人的身份。\n"
        "【原文】僕人指賽42:1中神所揀選的僕人。"
    ])
    for rule in ["完整 Claim", "自身语境", "定位工具", "未定位部分只是解释语境",
                 "未绑定来源补证", "必要条件与充分条件", "保留未决", "不能取代 Claim"]:
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
            assert "整个 CVP 的所有实质内容是否有来源" in prompt
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
