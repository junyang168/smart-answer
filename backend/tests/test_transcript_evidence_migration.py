from copy import deepcopy
import pytest
from backend.tests.test_transcript_claim_migration import case
from backend.pipeline.transcript_evidence_migration import classify, expanded_source, preview
from backend.pipeline.source_projection import project_script


def snapshot(rows):
    p=project_script(rows)
    return {'body_sha256':p.body_sha256,'paragraphs':{f'S{i:04d}':r for i,r in enumerate(p.spoken_rows,1)}}


def probe(old_rows,new_rows,quote,key='S0001',**fields):
    old=snapshot(old_rows);new=snapshot(new_rows);doc={'script':new_rows}
    expanded,rows=expanded_source(doc)
    f={'source_id':'S','source_sha256':old['body_sha256'],'paragraph_key':key,'verbatim_excerpt':quote,**fields}
    return classify(f,old,new,expanded,rows)


def row(text,index=1,**extra):return {'index':index,'text':text,**extra}


def test_exact_move_and_precise_paragraph_clock_not_word_alignment():
    result=probe([row('獨一段話。')],[row('先講其他。',9),row('獨一段話。',12,start_time=9.5,end_time=14)],'段話')
    assert result['status']=='auto_rebind'
    assert result['proposed_fields']['paragraph_key']=='S0002'
    assert result['proposed_fields']['source_segment_index']==12
    assert result['proposed_fields']['media_time']==9.5
    assert result['timestamp_precision']=='paragraph_not_word'


def test_short_repeated_quote_uses_own_paragraph_not_deleted_elsewhere():
    old=[row('第一處：神的國。'),row('第二處：神的國。',2)]
    new=[row('第一處：神的國。'),row('第二處：~~神的國~~。',2)]
    assert probe(old,new,'神的國')['status']=='auto_rebind'
    deleted=probe(old,new,'神的國','S0002')
    assert deleted['status']=='source_deleted' and deleted['deletion_extent']=='entire_quote'


def test_partial_deletion_does_not_splice_quote_or_leave_valid_anchor():
    result=probe([row('前提、條件和結論。')],[row('前提、~~條件和~~結論。')],'前提、條件和結論。')
    assert result['status']=='source_deleted' and result['deletion_extent']=='partial_quote'
    assert result['proposed_fields']['anchor_state']=='source_deleted'
    assert 'verbatim_excerpt' not in result['proposed_fields']


def test_missing_reworded_quote_is_exception_not_deleted():
    assert probe([row('原來的講法。')],[row('改正的講法。')],'原來的講法。')['status']=='exception'


def test_ambiguous_old_or_new_occurrence_never_guessed():
    assert probe([row('甲甲')],[row('甲甲'),row('另段。',2)],'甲')['reason']=='old_quote_missing_or_repeated'
    assert probe([row('相同。')],[row('相同。'),row('相同。',2)],'相同。')['status']=='exception'


def test_changed_speaker_is_exception_even_when_quote_identical():
    quote='我們一起來看這段話，這是一個足夠長而且完全相同的引文，用來確認講者歸屬的改變。'*3
    result=probe([row('教授：'+quote)],[row('聽眾：'+quote)],quote)
    assert result['reason']=='speaker_labels_changed'


def test_editorial_rows_do_not_acquire_anchors_and_current_binding_is_preserved():
    old=[row('教授的話。')];new=[row('# 新標題',8,type='subtitle'),row('教授的話。',9)]
    result=probe(old,new,'教授的話。')
    assert result['proposed_fields']['paragraph_key']=='S0001'
    result=probe(old,new,'教授的話。',source_sha256=snapshot(new)['body_sha256'],anchor_state='source_version_bound')
    assert result['status']=='already_current' and 'proposed_fields' not in result


def test_empty_unknown_epoch_and_visual_are_exceptions():
    a=[row('原話。')];b=[row('原話。',4)]
    for fields in ({'source_sha256':'unknown'},{'source_modality':'visual'}):
        assert probe(a,b,'原話。',**fields)['status']=='exception'
    assert probe(a,b,'')['status']=='exception'


def test_full_deleted_row_keeps_body_locator_and_live_offset_after_deleted_span():
    old=[row('開始。'),row('保留後句。',2)]
    new=[row('~~開始。~~'),row('~~刪除插話~~保留後句。',2,start_time=4,end_time=9)]
    assert probe(old,new,'開始。')['status']=='source_deleted'
    result=probe(old,new,'保留後句。','S0002')
    assert result['status']=='auto_rebind' and result['target_paragraph']=='S0002'


def test_census_preserves_retired_and_marks_only_all_deleted_evidence_as_deleted():
    old=snapshot([row('甲。'),row('乙。',2)]);doc={'script':[row('~~甲。~~'),row('乙。',2)]};new=snapshot(doc['script'])
    def state(payload,retired=False):return {'revision':1,'content_sha256':'hash','retired':retired,'payload':payload}
    records={
        'source_fragments/F1':state({'source_id':'S','paragraph_key':'S0001','source_sha256':old['body_sha256'],'verbatim_excerpt':'甲。'}),
        'source_fragments/F2':state({'source_id':'S','paragraph_key':'S0002','source_sha256':old['body_sha256'],'verbatim_excerpt':'乙。'}),
        'source_fragments/F3':state({'source_id':'S','verbatim_excerpt':'歷史'},True),
        'source_fragments/foreign':state({'source_id':'OTHER','verbatim_excerpt':'別篇'}),
        'evidence_steps/E1':state({'source_fragment_ids':['F1']}),
        'evidence_steps/E2':state({'source_fragment_ids':['F1','F2']}),
        'claims/C':state({'evidence_step_ids':['E1','E2']}),
        'viewpoint_claim_links/V':state({'claim_id':'C'})}
    before=deepcopy(records);result=preview(records,source_id='S',old=old,new=new,document=doc)
    assert records==before and result['production_writes']==0 and result['model_calls']==0
    assert result['summary']=={'source_deleted':1,'auto_rebind':1,'retired':1}
    evidence={r['record_key']:r for r in result['evidence_deletion_proposals']}
    assert evidence['evidence_steps/E1']['status']=='source_deleted'
    assert evidence['evidence_steps/E2']['status']=='partially_deleted'
    fragment=result['fragments'][0]
    assert fragment['affected_claims']==['claims/C'] and fragment['downstream_consumers']==['viewpoint_claim_links/V']


def test_owner_stop_blocks_model_run_and_recovery_before_any_store_access(tmp_path):
    import pytest
    from backend.pipeline import transcript_claim_migration as m
    (tmp_path/'refresh-stopped.json').write_text('{}')
    with pytest.raises(ValueError,match='owner stopped'):
        m.assert_refresh_enabled(tmp_path)


def test_layout_only_preserves_lexical_characters_and_extracts_actual_new_quote():
    quote='這是我們討論的一個具體判斷，保留原來每個文字。'
    new='這是我們討論的\n一個具體判斷、保留原來每個文字。'
    result=probe([row(quote)],[row(new)],quote)
    assert result['status']=='auto_rebind' and not result['quote_unchanged']
    assert result['proposed_fields']['verbatim_excerpt'] in new
    assert result['lexical_characters_unchanged']


def test_identical_quote_elsewhere_does_not_relocate_deleted_or_rewritten_original():
    quote='這是一個在兩個地方出現的相同句子。'
    old=[row('第一處。'+quote),row('第二處。'+quote,2)]
    new=[row('第一處。全部改寫。'),row('第二處。'+quote,2)]
    assert probe(old,new,quote)['status']=='exception'


def test_numeric_references_negation_and_math_are_not_normalized_away():
    for old,new in [('不是我們得救的一個原因。','是我們得救的一個原因。'),
        ('我們現在所講的數目是 1.2 倍。','我們現在所講的數目是 12 倍。'),
        ('我們現在引用的經文是 4:19。','我們現在引用的經文是 419。')]:
        assert probe([row(old)],[row(new)],old)['status']=='exception'


def test_english_token_boundaries_and_numeric_lists_remain_distinct():
    from backend.pipeline.transcript_evidence_migration import literal_layout
    assert literal_layout('now here')[0]!=literal_layout('nowhere')[0]
    assert literal_layout('1 2')[0]!=literal_layout('12')[0]
    assert literal_layout('1、2')[0]!=literal_layout('12')[0]
    assert literal_layout('καρδία')[0]=='καρδία'


@pytest.mark.parametrize('align_sequence',[False,True])
def test_dry_run_repeats_exact_output_and_fails_on_source_drift(case,tmp_path,align_sequence):
    from backend.pipeline import transcript_claim_migration as m
    from backend.pipeline.transcript_evidence_migration import dry_run
    from backend.tests.test_transcript_claim_migration import prepare
    store,_,root,_,_=case;manifest=prepare(case);m.register(store,root)
    store.rows['source_documents',manifest['source_id']]['payload']['revision']=2
    store.get_cvp_group=lambda key:None
    store.read_current_records=lambda keys:{key:deepcopy(store.rows[key]) for key in keys if key in store.rows}
    output=tmp_path/'preview'
    before=deepcopy(store.rows);writes=store.writes
    result=dry_run(store,root,output,align_sequence=align_sequence)
    assert dry_run(store,root,output,align_sequence=align_sequence)==result
    assert store.rows==before and store.writes==writes
    assert result['production_writes']==0
    store.rows['source_documents',manifest['source_id']]['payload']['source_path']='another-source'
    import pytest
    with pytest.raises(ValueError,match='registered source changed'):dry_run(store,root,output)


def test_report_contains_working_filter_controls_and_escapes_source_markup():
    from backend.pipeline.transcript_evidence_migration import render
    html=render({'summary':{'exception':1},'evidence_deletion_proposals':[],
        'fragments':[{'record_key':'source_fragments/F','status':'exception','reason':'no_unique_exact_context',
            'quote':'<script>alert(1)</script>','old_paragraph':'S0001','old_media_time':0,'affected_claims':[]}]})
    assert 'id="filter"' in html and 'id="query"' in html and 'id="fragments"' in html
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html
