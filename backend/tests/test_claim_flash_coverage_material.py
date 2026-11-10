from copy import deepcopy
import json
from types import SimpleNamespace
import pytest
from backend.pipeline import claim_flash_evidence as eo, claim_flash_common as f


def size(value):return len(json.dumps(value,ensure_ascii=False).encode())


def test_normal_coverage_keeps_original_saved_request_shape():
    ports=SimpleNamespace(identity={'gpt':{'model':'test'}})
    shared={'sources':{'F':{'paragraph_key':'S0001','quote':'完整原話。'}},'records':{},'writable_keys':[]}
    before=deepcopy(shared)
    prompt,packed=eo.coverage_request_material(ports,[],shared)
    assert prompt==eo.COVERAGE_PROMPT and packed==before and shared==before


def test_oversized_material_retains_every_id_position_and_literal_and_dispatches(tmp_path,monkeypatch):
    captured=[]
    ports=SimpleNamespace(identity={'gpt':{'model':'test'}})
    def repair(req):
        captured.append(req)
        return {'items':[{'job_id':'G','items':[]}]}
    ports.repair=repair
    quote='教授重要的完整原文與經文證據。'*30
    shared={'sources':{f'F{i}':{'paragraph_key':'S0001' if i%2 else 'S0002','quote':quote} for i in range(20)},
            'records':{'E':{'source_fragment_ids':['F0','F19'],'statement':'原意。'}},'writable_keys':['E']}
    jobs=[{'job_id':'G','candidates':[{'job_id':'U0','required_fragment_ids':['F0','F19']}]}]
    before=deepcopy(shared)
    def request(prompt,value):return {'identity':ports.identity['gpt'],'system':prompt,
        'input':{'task':'evidence_coverage_repair','jobs':jobs,**value},'schema':eo.MissingEdits.model_json_schema()}
    monkeypatch.setattr(f,'GPT_BYTES',size(request(eo.COVERAGE_PROMPT,shared))-1)
    prompt,packed=eo.coverage_request_material(ports,jobs,shared)
    assert shared==before and packed['records']==shared['records'] and packed['writable_keys']==shared['writable_keys']
    assert {fid:dict(zip(packed['source_columns'],row)) for fid,row in packed['sources'].items()}==shared['sources']
    assert len(packed['sources'])==20  # All identities and distinct positions stay intact.
    assert size(request(prompt,packed))<f.GPT_BYTES
    list(f.gpt(tmp_path,ports,'evidence_coverage_repair',jobs,eo.MissingEdits.model_json_schema(),prompt,shared=packed,semantic_review=False))
    assert len(captured)==1 and captured[0]==request(prompt,packed)
    monkeypatch.setattr(f,'GPT_BYTES',size(captured[0])-1)
    with pytest.raises(ValueError,match='payload limit'):
        list(f.gpt(tmp_path/'too-small',ports,'evidence_coverage_repair',jobs,eo.MissingEdits.model_json_schema(),prompt,shared=packed,semantic_review=False))
    assert len(captured)==1


def test_record_field_compaction_is_lossless_when_sources_alone_still_exceed_limit(monkeypatch):
    ports=SimpleNamespace(identity={'gpt':{'model':'test'}})
    shared={'sources':{'F':{'paragraph_key':'S0001','quote':'原文。'}},
            'records':{str(n):{'statement':'已有內容。','source_fragment_ids':['F'],
                **({'speaker':'professor'} if n%2 else {'scripture_refs':[]})} for n in range(80)},'writable_keys':['0']}
    before=deepcopy(shared)
    monkeypatch.setattr(f,'GPT_BYTES',1)
    _,packed=eo.coverage_request_material(ports,[],shared)
    restored={key:{packed['record_fields'][field]:value for field,value in row.items()} for key,row in packed['records'].items()}
    assert restored==shared['records'] and shared==before
    assert len(json.dumps(packed,ensure_ascii=False))<len(json.dumps(shared,ensure_ascii=False))
