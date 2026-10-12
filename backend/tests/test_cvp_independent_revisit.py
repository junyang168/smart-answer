from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
from unittest.mock import patch
import pytest
from backend.pipeline import cvp_independent_revisit as m


def test_direction_exact_population_and_no_content_authority():
    packet={'campaign_sha256':'campaign','run_id':'run','targets':[{'identity':'C'}]}
    d={'schema_version':m.SCHEMA,'campaign_sha256':'campaign','run_id':'run',
       'packet_sha256':m.sha256_json(packet),'maximum_calls_per_target':1,
       'automatic_retry':False,'registry_writes':0,'human_content_approval':False,
       'authorization_quote':'complete independent revisit'}
    m.validate_direction(d,packet)
    for field in d:
        if field=='authorization_quote':bad={**d,field:''}
        else:bad={**d,field:'foreign'}
        with pytest.raises(ValueError):m.validate_direction(bad,packet)


def test_ordinary_or_completed_independent_group_has_no_revisit_budget():
    for checkpoint,status,revisit,independent in [('applied','succeeded',True,False),
       ('audited','failed',True,False),('audited','succeeded',False,True)]:
        with pytest.raises(ValueError):m.prepare(None,{'checkpoint':checkpoint,'status':status,
            'revisit_required':revisit,'independent_audit_complete':independent})


def model(tmp_path):
    r=m.reader_module()
    return r,r.IndependentModel('gemini-3.8-flash','not-a-real-key',cache_dir=tmp_path,
                                preserve_full_context=True,durable_once=True)


def test_once_success_full_response_and_exact_cache_no_replay(tmp_path):
    r,judge=model(tmp_path)
    raw={'candidates':[{'content':{'parts':[{'text':json.dumps(
         {'verdict':'supported','issue':'none','reason':'full original'})}]}}]}
    with patch.object(r.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(raw).encode())) as call:
        result=judge.judge('完整自身原文',{'type':'object'})
        assert result['verdict']=='supported' and call.call_count==1
        assert judge.judge('完整自身原文',{'type':'object'})==result and call.call_count==1
    response=next(tmp_path.glob('*.provider-response.json'))
    assert json.loads(response.read_text())['provider_response']==raw
    assert json.loads(next(tmp_path.glob('*.call-intent.json')).read_text())['automatic_retry'] is False
    with pytest.raises(ValueError,match='identity drift'):
        judge.judge('完整自身原文',{'type':'object','properties':{'foreign':{}}})
    cache=next(p for p in tmp_path.glob('*.json') if p.name.count('.')==1)
    changed=deepcopy(result);changed['reason']='fabricated'
    cache.write_text(json.dumps(changed))
    with pytest.raises(ValueError,match='response drift'):judge.judge('完整自身原文',{'type':'object'})


def test_once_network_unknown_never_retries_or_resends(tmp_path):
    r,judge=model(tmp_path)
    with patch.object(r.urllib.request,'urlopen',side_effect=TimeoutError('unknown')) as call:
        assert judge.judge('full',{})['verdict']=='model_error'
        assert call.call_count==1
        with pytest.raises(ValueError,match='outcome unknown'):judge.judge('full',{})
        assert call.call_count==1
    assert json.loads(next(tmp_path.glob('*.failure.json')).read_text())['outcome_unknown'] is True


def test_once_content_filter_preserved_no_supplement(tmp_path):
    r,judge=model(tmp_path)
    raw={'promptFeedback':{'blockReason':'PROHIBITED_CONTENT'}}
    with patch.object(r.urllib.request,'urlopen',return_value=io.BytesIO(json.dumps(raw).encode())) as call:
        result=judge.judge('full',{})
        assert result['verdict']=='blocked'
        assert judge.judge('full',{})==result and call.call_count==1
    assert json.loads(next(tmp_path.glob('*.provider-response.json')).read_text())['provider_response']==raw


def test_oversized_unique_source_refuses_before_intent(tmp_path):
    r,judge=model(tmp_path)
    with patch.object(r.urllib.request,'urlopen') as call:
        with pytest.raises(ValueError,match='600000'):judge.judge('x'*600001,{})
        call.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.getenv('WANG_CVP_REVISIT_SAVED_TEST')!='1',reason='explicit read-only saved SQL acceptance')
def test_actual_saved_revisit_prepare_has_no_calls_writes_or_old_artifact_change(monkeypatch):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2]/'.env')
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    s=PostgresKnowledgeStore()
    monkeypatch.setattr(m,'write_immutable',lambda *a,**k:pytest.fail('read-only prepare wrote artifact'))
    rows=[r for r in s.cvp_campaign_progress('a155c59979a3a1fe65fccd3368c9abca8ea0f1d4238b1624f4a7cfdbdc522ced')
          if r['sequence'] in (35,37,120)]
    assert len(rows)==3
    counts=[]
    for row in rows:
        before=s.get_cvp_group(row['run_id']);p=m.prepare(s,before)
        counts.append(len(p['targets']))
        assert s.get_cvp_group(row['run_id'])==before
        for b in (p['prior_assessment'],p['original_report']):m.check_binding(b)
        for target in p['targets']:
            for b in target['source_bindings']:m.check_binding(b)
            body={'contents':[{'parts':[{'text':target['prompt']}]}],
                  'generationConfig':{'temperature':0,'responseMimeType':'application/json','responseSchema':target['schema']}}
            assert len(json.dumps(body).encode())<600000
    assert counts==[2,1,9]
