import copy
import pytest
from backend.pipeline.transcript_playback_readiness import base_rows,usable,require_control


def test_noisy_owner_source_can_enter_editor_but_not_knowledge(tmp_path,monkeypatch):
    import hashlib
    import backend.pipeline.transcript_playback_readiness as module
    from backend.pipeline.slide_change_pilot import save_json
    from backend.pipeline.transcript_source_observations import record_unintelligible
    from backend.pipeline.contextual_transcript_timing import require_verified_timing
    evidence=tmp_path/'evidence';evidence.mkdir()
    paragraphs=[dict(id='p1',text='清楚',start_ms=0,end_ms=1000),dict(id='p2',text='不清楚粗稿',start_ms=1000,end_ms=2000)]
    record=dict(schema_version='full-illustrated-corrected-transcript.v1',source_sha256='a'*64,paragraphs=copy.deepcopy(paragraphs))
    save_json(evidence/'plan.json',dict(records={'source':dict(source_sha256='a'*64,paragraphs=paragraphs)},chunks=[]))
    save_json(evidence/'playback-readiness/input.json',dict(text_sha256={'source':{p['id']:hashlib.sha256(p['text'].encode()).hexdigest() for p in paragraphs}}))
    result=dict(blocked=1,source_plan_sha256='plan',input_sha256='input',plan_sha256='reviews',paragraphs={'source':[dict(**paragraphs[0],status='ready'),dict(**paragraphs[1],status='blocked')]})
    monkeypatch.setattr(module,'assess',lambda *a,**k:copy.deepcopy(result))
    monkeypatch.setattr('backend.pipeline.transcript_timing_report.write_report',lambda *a,**k:None)
    # An unresolved boundary without the exact owner observation is still blocked.
    with pytest.raises(ValueError):module.qualify_record(tmp_path,copy.deepcopy(record),evidence,saved_only=True)
    observation=record_unintelligible(evidence,'p2','owner','噪音太大聽不清楚')
    module.qualify_record(tmp_path,record,evidence,saved_only=True)
    assert record['timing_quality']['status']=='editor_draft_only'
    assert record['paragraphs'][1]['text']==paragraphs[1]['text']
    require_verified_timing(tmp_path,record,allow_editor_draft=True)
    with pytest.raises(ValueError,match='knowledge exports'):require_verified_timing(tmp_path,record)
    # Neither a forged qualified flag nor changed hold/source/text can bypass it.
    for mutate in [lambda r:r['timing_quality'].update(status='qualified'),lambda r:r['timing_quality'].update(source_holds={}),lambda r:r['paragraphs'][1].update(text='猜測內容')]:
        bad=copy.deepcopy(record);mutate(bad)
        with pytest.raises(ValueError):require_verified_timing(tmp_path,bad,allow_editor_draft=bad['timing_quality']['status']!='qualified')
    observation.unlink()
    with pytest.raises(ValueError):require_verified_timing(tmp_path,record,allow_editor_draft=True)


def test_editor_only_draft_withholds_stale_exports_without_deleting(tmp_path):
    from backend.pipeline.transcript_playback_readiness import withhold_knowledge_exports
    (tmp_path/'handoff').mkdir();(tmp_path/'handoff/source.json').write_text('old knowledge')
    (tmp_path/'argument_source_packet.json').write_text('old packet')
    withhold_knowledge_exports(tmp_path)
    assert not (tmp_path/'handoff').exists() and not (tmp_path/'argument_source_packet.json').exists()
    assert sorted(p.read_text() for p in (tmp_path/'withheld-source-exports').rglob('*.json'))==['old knowledge','old packet']
    withhold_knowledge_exports(tmp_path)  # Retry after the completed move is a no-op.


def test_joint_selection_reuses_complete_candidates_without_rewriting_model_preference():
    from backend.pipeline.transcript_playback_readiness import choose_compatible_ranges
    complete=dict(verdict='complete',clipped_word=False,leading_context='none',trailing_context='brief',heard_opening='開頭',heard_ending='結尾')
    rows=[dict(id='left',start_ms=0,end_ms=9000),dict(id='target',model_selected_id='wide',selected_id='wide',start_ms=10000,end_ms=26000,
          complete_options=[dict(id='wide',start_ms=10000,end_ms=26000,judgment=complete),dict(id='short',start_ms=10000,end_ms=20000,judgment=complete)]),dict(id='next',start_ms=21000,end_ms=30000)]
    choose_compatible_ranges(rows)
    assert rows[1]['selected_id']=='short' and rows[1]['model_selected_id']=='wide'
    assert rows[1]['end_ms']==20000 and rows[1]['status']=='ready_with_context'
    assert 'program_selection_note' in rows[1]


def test_complete_alternative_repairs_a_greedy_overlap_without_new_audio():
    from backend.pipeline.transcript_playback_readiness import choose_compatible_ranges, repair_range_conflicts
    judgment=dict(leading_context='brief',trailing_context='none')
    def candidate(id, start, end):
        return dict(id=id,start_ms=start,end_ms=end,judgment=judgment)
    rows=[dict(status='ready',start_ms=0,end_ms=11240),
          dict(status='ready',start_ms=12440,end_ms=22840,model_selected_id='preferred',selected_id='preferred',
               complete_options=[candidate('earlier',10160,22000),candidate('preferred',12440,22840)]),
          dict(status='ready',start_ms=19070,end_ms=28910)]
    choose_compatible_ranges(rows)
    assert rows[1]['selected_id']=='preferred'
    repair_range_conflicts(rows)
    assert rows[1]['selected_id']=='earlier' and rows[1]['model_selected_id']=='preferred'
    assert rows[1]['end_ms']-rows[2]['start_ms']==2930


def test_brief_overlap_does_not_erase_strict_verdict_or_omit_content_gate():
    rows=[dict(id='a',status='qualified',start_ms=1000,end_ms=5000),dict(id='b',status='withheld',start_ms=4800,end_ms=8000)]
    before=copy.deepcopy(rows);lecture=dict(paragraphs=rows,duration_ms=10000)
    whole={'b':dict(start_ms=4800,end_ms=8000,raw_response_sha256='real-complete-audio-review')}
    result=base_rows(lecture,whole)
    assert result[1]['status']=='ready_with_context' and result[1]['strict_status']=='withheld'
    assert result[1]['start_ms']==4800 and result[1]['context_overlap_ms']==200
    assert rows==before
    assert base_rows(lecture,{})[1]['status']=='blocked'  # Boundary leakage is not completeness evidence.
    whole['b']['start_ms']=500
    assert base_rows(lecture,whole)[1]['status']=='blocked'  # Long unrelated overlap is not a warning.


def answer(verdict='complete',context='brief'):
    return dict(selected_id='c1',reason='本段完整，前方僅有少量鄰段聲音',candidates=[
        dict(id='c0',verdict='missing_content',clipped_word=False,leading_context='none',trailing_context='none',heard_opening='',heard_ending='尾句'),
        dict(id='c1',verdict=verdict,clipped_word=False,leading_context=context,trailing_context='none',heard_opening='本段開頭',heard_ending='本段結尾')])


def test_new_policy_accepts_brief_context_but_not_missing_content_wrong_occurrence_or_clipped_words():
    case=dict(candidates=[dict(id='c0'),dict(id='c1')]);a=answer()
    assert usable(a,case)['id']=='c1'
    for verdict in ['missing_content','wrong_occurrence','text_audio_mismatch','unclear']:
        assert usable(answer(verdict),case) is None
    assert usable(answer(context='substantial'),case) is None
    bad=copy.deepcopy(a);bad['candidates'][1]['clipped_word']=True
    assert usable(bad,case) is None
    bad=copy.deepcopy(a);bad['candidates'][1]['heard_opening']=''
    assert usable(bad,case) is None
    bad=copy.deepcopy(a);bad['selected_id']='invented'
    with pytest.raises(ValueError):usable(bad,case)
    require_control(a,case)
    bad=copy.deepcopy(a);bad['candidates'][0]['verdict']='complete'
    with pytest.raises(ValueError,match='calibration failed'):require_control(bad,case)
