from copy import deepcopy
import json

import pytest

from backend.pipeline import transcript_migration_impact as impact
from backend.pipeline import transcript_evidence_migration as relocation
from backend.pipeline import transcript_claim_migration as m
from backend.tests.test_claim_management_postgres import postgres_store, _temporary_postgres


@pytest.fixture
def case():
    text = '教授說明，神為全世界的人預備救恩。'
    old = {'source_id':'S', 'source_sha256':'old', 'paragraph_key':'S0001',
           'fragment_id':'F', 'verbatim_excerpt':'神為全世界上的的人預備救恩。'}
    new = {**old, 'source_sha256':'new', 'paragraph_key':'S0002',
           'source_segment_index':2,'media_time':1,'media_end_time':2,
           'verbatim_excerpt':'神為全世界的人預備救恩。', 'anchor_state':'source_version_bound',
           'paragraph_text_sha256':m.sha(text.encode()),
           'verbatim_excerpt_sha256':m.sha('神為全世界的人預備救恩。'.encode())}
    ev = {'statement':'神為所有人預備救恩。','source_fragment_ids':['F'],
          'support_eligibility':'eligible_candidate','valid_for_identity_review':True}
    records = {k:{'payload':v,'retired':False} for k,v in {
        'source_fragments/F':old, 'evidence_steps/E':ev,
        'claims/C':{'statement':'神為所有人預備救恩。','evidence_step_ids':['E']}}.items()}
    report = {'source_id':'S','updates':{'source_fragments/F':new,
        'evidence_steps/E':{**ev,'support_eligibility':'withheld_unreviewed',
                             'valid_for_identity_review':False}},'model_calls':0}
    source = {'body_sha256':'new','paragraphs':{'S0002':{'text':text,'index':2,'start_time':1,'end_time':2}}}
    return records, report, source


def answer(request, choice='unchanged'):
    return {'model':impact.MODEL, 'answers':{k:{'type':'choice','choice':choice,
        'confidence':.95,'probabilities':{c:float(c==choice) for c in
        ['unchanged','affected','uncertain']}} for k in request['questions']}}


def test_whole_evidence_preserves_only_existing_qualification(case,tmp_path):
    records, report, new = case
    proof = impact.review(report,records,new,tmp_path,call=answer)
    after = impact.preserve(report,records,new,proof)
    assert after['updates']['evidence_steps/E']['support_eligibility']=='eligible_candidate'
    assert after['updates']['evidence_steps/E']['valid_for_identity_review'] is True
    assert after['preserved_eligibility']==['evidence_steps/E']
    assert report['updates']['evidence_steps/E']['support_eligibility']=='withheld_unreviewed'
    def forbidden(_): raise AssertionError('saved response should be reused')
    assert impact.review(report,records,new,tmp_path,call=forbidden)==proof


@pytest.mark.parametrize('choice',['affected','uncertain'])
def test_changed_or_unknown_support_stays_withheld(case,tmp_path,choice):
    records,report,new=case
    proof=impact.review(report,records,new,tmp_path,call=lambda r:answer(r,choice))
    after=impact.preserve(report,records,new,proof)
    assert not after['preserved_eligibility']
    assert after['updates']['evidence_steps/E']['support_eligibility']=='withheld_unreviewed'


@pytest.mark.parametrize('defect',['hold','already_withheld','foreign','stale_quote','missing','no_claim','wrong_clock','wrong_index'])
def test_no_semantic_vote_can_release_holds_or_replace_physical_binding(case,defect):
    records,report,new=case
    if defect=='hold':records['evidence_steps/E']['payload']['review_status']='human_review_required'
    if defect=='already_withheld':records['evidence_steps/E']['payload']['support_eligibility']='withheld_unreviewed'
    if defect=='foreign':report['updates']['source_fragments/F']['source_id']='OTHER'
    if defect=='stale_quote':report['updates']['source_fragments/F']['verbatim_excerpt']='原文沒有這句話'
    if defect=='missing':report['updates']['source_fragments/F']['anchor_state']='source_deleted'
    if defect=='no_claim':records['claims/C']['payload']['evidence_step_ids']=[]
    if defect=='wrong_clock':report['updates']['source_fragments/F']['media_time']=99
    if defect=='wrong_index':report['updates']['source_fragments/F']['source_segment_index']=99
    assert impact.materials(report,records,new)==[]


def test_complete_input_drift_and_invented_decision_are_rejected(case,tmp_path):
    records,report,new=case
    proof=impact.review(report,records,new,tmp_path,call=answer)
    changed=deepcopy(records);changed['claims/C']['payload']['statement']='另一個主張'
    with pytest.raises(ValueError,match='input differs|input changed'):
        impact.preserve(report,changed,new,proof)
    forged=deepcopy(proof);forged['decisions']['evidence_steps/E']['preserve']=False
    with pytest.raises(ValueError,match='saved model response'):
        impact.preserve(report,records,new,forged)


def test_unknown_call_is_never_retried(case,tmp_path):
    records,report,new=case
    def lost(_):raise TimeoutError('no receipt')
    with pytest.raises(TimeoutError):impact.review(report,records,new,tmp_path,call=lost)
    with pytest.raises(ValueError,match='do not retry'):
        impact.review(report,records,new,tmp_path,call=answer)


def test_script_variants_are_mechanical_but_negation_numbers_and_greek_are_not():
    assert relocation.script_layout('這裡說：聖經的話。')[0]==relocation.script_layout('这里说：圣经的话。')[0]
    for old,new in [('是','不是'),('1.5','15'),('十二','二十'),('θεός','θεούς'),('ab c','a bc')]:
        assert relocation.script_layout(old)[0]!=relocation.script_layout(new)[0]


@pytest.mark.parametrize('mode',['rephrase','partial_deletion','script_variant'])
def test_real_sql_migration_preserves_qualification_and_synchronizes_claim_anchor(postgres_store,tmp_path,monkeypatch,mode):
    from backend.pipeline import transcript_evidence_apply as ea
    from backend.pipeline import transcript_locator_migration as runner
    monkeypatch.setattr(impact,'jev',answer)
    old_quote='神為全世界上的每一個人預備救恩。'
    new_quote='神為全世界的每一個人預備救恩。'
    prefix='前面先說明這個問題的歷史背景與經文脈絡，請注意以下解釋。'
    suffix='這裡繼續說明另一個獨立例子，讓我們仔細閱讀下一節上下文。'
    old=tmp_path/'old.json';new=tmp_path/'new.json';instruction=tmp_path/'direction.txt'
    old.write_text(json.dumps({'script':[{'index':1,'text':prefix+old_quote+suffix,'start_time':1,'end_time':20}]}))
    text=prefix+new_quote+suffix
    if mode=='partial_deletion':text=prefix+old_quote.replace('上的','~~上~~的')+suffix
    if mode=='script_variant':
        from opencc import OpenCC
        new_quote=OpenCC('t2s').convert(old_quote)
        text=prefix+new_quote+suffix
    new.write_text(json.dumps({'script':[{'index':9,'text':text,'start_time':1,'end_time':20}]}))
    instruction.write_text('保留未受來源改動影響的既有證據資格，只更新真實定位。')
    before,after=ea.snap(old),ea.snap(new)
    package={'source_documents':[{'source_id':'S','source_type':'sermon_transcript','transcript_id':'lecture',
        'source_path':str(new),'source_file_sha256':m.sha(new.read_bytes()),'locator_space':'spoken_body_v1',
        'source_sha256':after['body_sha256'],'source_body_sha256':after['body_sha256']}],
        'source_fragments':[{'fragment_id':'F','source_id':'S','paragraph_key':'S0001',
            'source_sha256':before['body_sha256'],'verbatim_excerpt':old_quote,'media_time':1,'media_end_time':20}],
        'evidence_steps':[{'evidence_step_id':'E','statement':'神為所有人預備救恩。','produced_claim_ids':['C'],
                          'source_fragment_ids':['F'],'support_eligibility':'eligible_candidate'}],
        'claims':[{'claim_id':'C','statement':'神為所有人預備救恩。','claim_type':'exegesis','evidence_step_ids':['E'],
                   'occurrences':[{'source_id':'S','anchors':[{'evidence_id':'E','paragraph_key':'S0001',
                       'proposed_highlight':{'text':old_quote,'status':'proposed'}}]}]}]}
    store=postgres_store;store.apply_plan(store.plan_package(package))
    root=tmp_path/'migration'
    prepared=runner.prepare(store,root,source_id='S',old_paths=[old],published=new,
                            instruction_file=instruction,jev_impact=True)
    assert prepared['model_calls']==(0 if mode=='script_variant' else 1)
    result=runner.run(store,root)
    assert result['needs_content_refresh']==[]
    evidence=store.get_record_state('evidence_steps','E')['payload']
    assert evidence['support_eligibility']=='eligible_candidate'
    anchor=store.get_record_state('claims','C')['payload']['occurrences'][0]['anchors'][0]
    assert anchor['source_segment_index']==9
    assert relocation.literal_layout(anchor['proposed_highlight']['text'])[0]==relocation.literal_layout(new_quote)[0]
    assert anchor['locator_content_review_required'] is False
    assert runner.verify(store,root)==result
