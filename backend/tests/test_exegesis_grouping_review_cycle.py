from pathlib import Path
from types import SimpleNamespace
import copy
import pytest
from backend.pipeline import exegesis_grouping_review_cycle as cycle


def test_legacy_keys_are_normalized_without_changing_semantic_findings():
    raw=dict(status='needs_resolution',findings=[dict(key='group:first',status='needs_resolution',reason='merge two groups',evidence=['verbatim']),dict(key='group:second',status='pass',reason='complete route',evidence=['original']),dict(key='unit:p009',status='needs_resolution',reason='outside L2 scope',evidence=['preserved'])])
    before=copy.deepcopy(raw)
    normalized,extras=cycle.normalize_legacy_review(raw,dict(groups=[dict(group_key='first'),dict(group_key='second')]))
    assert normalized['status']=='needs_resolution' and [f['key'] for f in normalized['findings']]==['first','second']
    assert normalized['findings'][0]==dict(raw['findings'][0],key='first')
    assert extras==[raw['findings'][2]] and raw==before
    with pytest.raises(ValueError):cycle.normalize_legacy_review(raw,dict(groups=[dict(group_key='first')]))


def test_one_original_source_correction_then_terminal_review(tmp_path,monkeypatch):
    ids=[str(n) for n in range(21)]
    answer=dict(schema_version='wang_canonical_viewpoint_claim_grouping_v1',scope_label='p009',groups=[dict(group_key='first',claim_ids=ids[:10],rationale='first'),dict(group_key='second',claim_ids=ids[10:],rationale='second')])
    reports=[dict(artifact_sha256='initial',status='needs_resolution',findings=[dict(status='needs_resolution',reason='correct boundary')]),dict(artifact_sha256='final',status='needs_resolution',findings=[dict(status='needs_resolution',reason='still not supported')])]
    review_calls=[];model_calls=[];source_reads=[]
    def review(*args,**kwargs):review_calls.append((args,kwargs));return reports.pop(0)
    def complete(payload):source_reads.append(payload);return dict(payload,sources=['SHA-checked original'])
    def call(**kwargs):
        model_calls.append(kwargs)
        assert kwargs['payload']['sources']==['SHA-checked original']
        assert kwargs['schema']['properties']['scope_label']['const']=='p009'
        assert len(kwargs['payload']['claims'])==21
        return answer
    monkeypatch.setattr(cycle,'review',review);monkeypatch.setattr(cycle,'complete_originals',complete);monkeypatch.setattr(cycle,'call',call)
    monkeypatch.setattr(cycle.core,'obtain',lambda d,f,g:g())
    phases=[]
    result,final=cycle.cycle(tmp_path,dict(unit_id='p009',passage_key='Matt.3.13-Matt.3.17',claim_ids=ids),dict(claims=[dict(claim_id=i) for i in ids]),answer,SimpleNamespace(model='claude-opus-5-5',max_request_bytes=2500000),on_stage=phases.append)
    assert len(model_calls)==1 and len(review_calls)==2 and len(source_reads)==1
    assert final['status']=='needs_resolution' and result==answer
    assert phases==['independent_review','original_source_correction','final_independent_review']
    assert (tmp_path/'original-source-closeout.json').exists()


def test_initial_pass_does_not_add_correction_or_another_review(tmp_path,monkeypatch):
    calls=[]
    def review(*a,**k):calls.append(1);return dict(status='pass')
    monkeypatch.setattr(cycle,'review',review)
    monkeypatch.setattr(cycle,'complete_originals',lambda *a:pytest.fail('no original repair needed'))
    assert cycle.cycle(tmp_path,{}, {},{'groups':[]},SimpleNamespace())[1]['status']=='pass'
    assert len(calls)==1


def test_review_schema_requires_exact_raw_group_keys_only():
    module=cycle.reader()
    schema=module.review_schema(dict(groups=[dict(group_key='first'),dict(group_key='second')]))
    finding=schema['properties']['findings']
    assert finding['minItems']==finding['maxItems']==2
    assert finding['items']['properties']['key']['enum']==['first','second']
    assert 'group:first' not in finding['items']['properties']['key']['enum']
    assert 'unit:p009' not in finding['items']['properties']['key']['enum']
