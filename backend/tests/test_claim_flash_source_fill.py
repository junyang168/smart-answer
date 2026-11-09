"""Source-only completion, real isolated SQL, and replay/crash counterexamples."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from backend.pipeline import claim_flash_coverage as coverage, claim_flash_source_fill as fill
from backend.pipeline import claim_flash_common as f, claim_update_flash as flash, evidence_refresh as e
from backend.tests.test_claim_flash_source import fixture
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


def report(sources, quote='重要限定不能漏。', pk='S0001', verdict='missing_material'):
    return {'contract':coverage.CONTRACT,'source_id':'S','stage':'reverse_coverage','database_writes':0,
        'findings':[{'job_id':'G','findings':[{'candidate_ids':['G-U0'],'paragraph_key':pk,
            'quote':quote,'verdict':verdict,'statement':'保留教授的重要限定。','reason':'未見記錄。',
            'reference':'','record_refs':[],'source_notes':[]}]}]}


def save_report(tmp_path,sources,**kwargs):
    root=tmp_path/'coverage';path=root/'reviews'/'input'/'result.json'
    value=report(sources,**kwargs);value['report_path']=str(path)
    e.save(root/'intent.json',{'contract':coverage.CONTRACT,'source_id':'S','source':sources['S']['file']})
    e.save(path,value)
    return path


def test_literal_gap_creates_only_source_fragments_and_deduplicates(tmp_path):
    records,sources=fixture(tmp_path,['已有內容。重要限定不能漏。'],[('S0001','已有內容。')])
    before=deepcopy(records);r=report(sources)
    r['findings'][0]['findings']*=2
    result=fill.plan(records,sources,'S',r)
    assert result['status']=='verified' and result['updates']=={} and len(result['new_fragments'])==1
    assert result['model_calls']==0 and result['claim_qualification']=='not_granted' and records==before
    fragment=next(iter(result['new_fragments'].values()))
    assert fragment['verbatim_excerpt']=='重要限定不能漏。' and fragment['media_time']==1.0
    assert fragment['review_status']=='candidate' and 'support_eligibility' not in fragment
    overlay=f.apply_overlay(records,{},result['new_fragments'])
    repeated=fill.plan(overlay,sources,'S',r)
    assert not repeated['new_fragments'] and all(x['status']=='already_covered' for x in repeated['items'])


def test_larger_valid_span_covers_shorter_confirmed_quote(tmp_path):
    records,sources=fixture(tmp_path,['已有內容。重要限定不能漏。'],[('S0001','已有內容。重要限定不能漏。')])
    result=fill.plan(records,sources,'S',report(sources))
    assert result['items'][0]['status']=='already_covered' and not result['new_fragments']


@pytest.mark.parametrize('partial',[False,True])
def test_existing_split_spans_skip_only_when_union_is_complete(tmp_path,partial):
    quotes=[('S0001','重要限定'),('S0001','漏。' if partial else '不能漏。')]
    records,sources=fixture(tmp_path,['重要限定不能漏。'],quotes)
    result=fill.plan(records,sources,'S',report(sources))
    assert bool(result['new_fragments'])==partial
    if not partial:assert result['items'][0]['fragment_ids']==['F0','F1']


@pytest.mark.parametrize('gap,covered',[('。\n',True),('，',True),('不是',False),('%',False),('−',False)])
def test_only_punctuation_separators_do_not_force_duplicate_fragment(gap,covered,tmp_path):
    text='前提'+gap+'結論。'
    records,sources=fixture(tmp_path,[text],[('S0001','前提'),('S0001','結論')])
    result=fill.plan(records,sources,'S',report(sources,quote=text))
    assert bool(result['new_fragments']) != covered


def test_joint_findings_keep_each_explicit_source_and_scripture_quote(tmp_path):
    records,sources=fixture(tmp_path,['重要限定不能漏。','腓立比書三章二十節。'],[('S0001','重要限定不能漏。')])
    r=report(sources);r['findings'][0]['findings'][0]['candidate_sources']=[
        {'candidate_id':'G-U1','paragraph_key':'S0002','quote':'腓立比書三章二十節。'}]
    result=fill.plan(records,sources,'S',r)
    assert len(result['new_fragments'])==1
    assert next(iter(result['new_fragments'].values()))['verbatim_excerpt']=='腓立比書三章二十節。'


@pytest.mark.parametrize('failure',['absent','wrong_paragraph','ambiguous','foreign'])
def test_invalid_source_never_gets_a_fragment_or_write(failure,tmp_path):
    records,sources=fixture(tmp_path,['已有內容。重要限定不能漏。重要限定不能漏。'],[('S0001','已有內容。')])
    r=report(sources)
    if failure=='absent':r['findings'][0]['findings'][0]['quote']='不是原文。'
    if failure=='wrong_paragraph':r['findings'][0]['findings'][0]['paragraph_key']='S0099'
    if failure=='foreign':
        r['source_id']='OTHER'
        with pytest.raises(ValueError):fill.plan(records,sources,'S',r)
        return
    result=fill.plan(records,sources,'S',r)
    assert result['status']=='needs_attention' and not result['new_fragments']


@pytest.mark.parametrize('verdict',['already_recorded','not_required','needs_context','claim_layer_out_of_scope'])
def test_unconfirmed_or_out_of_scope_findings_do_not_create_source(verdict,tmp_path):
    records,sources=fixture(tmp_path)
    result=fill.plan(records,sources,'S',report(sources,verdict=verdict))
    assert result['items']==[] and result['new_fragments']=={}


def test_old_deleted_span_is_preserved_not_revived(tmp_path):
    records,sources=fixture(tmp_path,['重要限定不能漏。'],[('S0001','重要限定不能漏。')])
    records['source_fragments/F0']['payload']['anchor_state']='source_deleted'
    before=deepcopy(records)
    result=fill.plan(records,sources,'S',report(sources))
    assert len(result['new_fragments'])==1 and records==before
    assert 'source_fragments/F0' not in result['new_fragments']


@pytest.fixture
def seeded(postgres_store,tmp_path):
    records,sources=fixture(tmp_path,['已有內容。重要限定不能漏。'],[('S0001','已有內容。')])
    doc=deepcopy(records['source_documents/S']['payload'])
    doc.pop('source_body_sha256');doc.pop('source_sha256')
    claim={'claim_id':'C','statement':'既有主張。','claim_type':'exegesis',
           'evidence_step_ids':['E'],'occurrences':[{'source_id':'S','anchors':[]}]}
    ev=deepcopy(records['evidence_steps/E']['payload']);ev['produced_claim_ids']=['C']
    postgres_store.apply_plan(postgres_store.plan_package({'package_id':'source-fill-fixture',
        'source_documents':[doc],'source_fragments':[records['source_fragments/F0']['payload']],
        'evidence_steps':[ev],'claims':[claim]}))
    return postgres_store,save_report(tmp_path,sources),tmp_path/'fill',sources


def test_preview_apply_readback_replay_and_fresh_scan_skip(seeded,monkeypatch):
    store,path,root,sources=seeded
    original={k:store.get_record_state(*k.split('/')) for k in ['claims/C','evidence_steps/E','source_fragments/F0']}
    preview=fill.run(store,'S',path,root)
    assert preview['status']=='verified' and not preview['applied']
    key=next(iter(preview['new_fragments']));assert store.get_record_state(*key.split('/')) is None
    result=fill.run(store,'S',path,root,apply=True)
    assert result['applied'] and result['status']=='applied'
    history=store.get_change_history(result['commit']['change_set_id'])
    assert history['source_kind']==fill.VERSION
    saved=store.get_record_state(*key.split('/'))
    assert saved and saved['payload']['verbatim_excerpt']=='重要限定不能漏。'
    assert original=={k:store.get_record_state(*k.split('/')) for k in original}
    monkeypatch.setattr(f,'load_source',lambda *a,**k:pytest.fail('resume repeated census'))
    assert fill.run(store,'S',path,root,apply=True)['commit']==result['commit']
    assert store.get_record_state(*key.split('/'))==saved


def test_new_run_skips_fragment_already_saved(seeded):
    store,path,root,_=seeded;first=fill.run(store,'S',path,root,apply=True)
    second=fill.run(store,'S',path,root.parent/'second',apply=True)
    assert not second['new_fragments'] and not second['applied']
    assert second['items'][0]['status']=='already_covered'


def test_source_only_submission_rejects_accidental_upper_layer_write(seeded):
    store,path,root,_=seeded;fill.run(store,'S',path,root)
    verification=root/'verification.json';data=json.loads(verification.read_text())
    data['updates']={'evidence_steps/E':store.get_record_state('evidence_steps','E')['payload']}
    verification.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='source-only commit'):fill.run(store,'S',path,root,apply=True)
    assert not (root/'commit-intent.json').exists()


def test_commit_succeeded_but_receipt_lost_recovers_without_second_transaction(seeded,monkeypatch):
    store,path,root,_=seeded;real=store.apply_plan
    def crash(*args,**kwargs):
        real(*args,**kwargs)
        raise RuntimeError('simulated crash after SQL commit')
    monkeypatch.setattr(store,'apply_plan',crash)
    with pytest.raises(RuntimeError,match='after SQL commit'):fill.run(store,'S',path,root,apply=True)
    monkeypatch.setattr(store,'apply_plan',lambda *a,**k:pytest.fail('duplicate SQL apply'))
    recovered=fill.run(store,'S',path,root,apply=True)
    assert recovered['applied'] and (root/'commit.json').exists()


@pytest.mark.parametrize('change',['report','file','document'])
def test_drift_after_preview_never_writes(seeded,change):
    store,path,root,sources=seeded;preview=fill.run(store,'S',path,root)
    if change=='report':
        value=json.loads(path.read_text());value['findings'][0]['findings'][0]['reason']='changed';path.write_text(json.dumps(value))
    elif change=='file':Path(sources['S']['file']['path']).write_text('changed source')
    else:
        state=store.get_record_state('source_documents','S')['payload'];state['title']='changed'
        store.apply_plan(store.plan_package({'package_id':'drift','source_documents':[state]}))
    with pytest.raises(Exception):fill.run(store,'S',path,root,apply=True)
    assert not (root/'commit.json').exists()
    assert all(store.get_record_state(*key.split('/')) is None for key in preview['new_fragments'])


def test_cli_source_fill_needs_no_models_and_honors_explicit_apply(tmp_path,monkeypatch,capsys):
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    monkeypatch.setattr(PostgresKnowledgeStore,'__init__',lambda *a,**k:None)
    def run(store,sid,path,root,*,apply):
        assert sid=='S' and path=='report.json' and apply
        return {'version':fill.VERSION,'model_calls':0}
    monkeypatch.setattr(fill,'run',run)
    monkeypatch.setattr(sys,'argv',['flash','--source-id','S','--fill-source-from','report.json','--root',str(tmp_path),'--apply'])
    flash.main();assert json.loads(capsys.readouterr().out)['model_calls']==0
