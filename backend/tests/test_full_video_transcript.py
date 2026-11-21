import copy
import json

import pytest

from backend.pipeline.full_video_transcript import (
    SR, absolute_ms, partition_samples, resolve_final_paragraph, sample_ms,
)
from backend.pipeline.transcript_text_layout import text_sha


def test_repeated_phrase_candidate_reaches_context_without_approving_playback():
    from backend.pipeline.full_video_transcript import check_paragraph_times
    rows=[dict(id='earlier',start_ms=1735640,end_ms=1762840),
          dict(id='repeated',start_ms=1707240,end_ms=1734120)]
    before=copy.deepcopy(rows)
    issues=check_paragraph_times(rows,1800000,candidate=True)
    assert rows==before  # Never sort, clamp, or fabricate timing.
    assert issues==[dict(paragraph_id='repeated',kind='isolated_alignment_moves_backwards',
                         previous_start_ms=1735640,start_ms=1707240)]
    with pytest.raises(ValueError,match='backwards'):
        check_paragraph_times(rows,1800000)
    for candidate in (False,True):
        with pytest.raises(ValueError,match='range'):
            check_paragraph_times([dict(id='out',start_ms=1799000,end_ms=1801000)],1800000,candidate=candidate)
    with pytest.raises(ValueError,match='unresolved'):
        check_paragraph_times([dict(id='unknown',start_ms=None,end_ms=None)],1800000)
    corrected=[rows[0],dict(id='repeated',start_ms=1763000,end_ms=1774500)]
    assert check_paragraph_times(corrected,1800000)==[]


def test_new_video_prepares_first_window_without_any_previous_pilot(tmp_path,monkeypatch):
    import numpy as np
    from backend.pipeline import full_video_transcript as full
    video=tmp_path/'另一篇講道.mp4';video.write_bytes(b'new movie')
    audio=np.full(full.SR*2,100,dtype=np.int16)
    probe={'streams':[{'codec_type':'video','start_time':'0','time_base':'1/30000'},
                      {'codec_type':'audio','start_time':'0','duration':'2','time_base':'1/16000'}],
           'format':{'duration':'2'}}
    monkeypatch.setattr(full.subprocess,'check_output',lambda *a,**k:json.dumps(probe))
    monkeypatch.setattr(full.subprocess,'run',lambda command,**k:full.write_audio(tmp_path/'out/master.wav',audio))
    result=full.prepare(video,tmp_path/'out')
    first=full.read(tmp_path/'out/windows/w00/audio/audio_manifest.json')
    assert result['lecture_id']==video.stem and result['windows'][0]['origin_sample']==0
    assert first['source_sha256']==full.sha(video) and first['source_window_origin_sample']==0
    assert first['chunks'][0]['core_start_sample']==0
    assert first['chunks'][-1]['core_end_sample']==len(audio)


def test_source_sample_partitions_cover_long_movie_without_cumulative_rounding():
    total = 4_299_703 * 16
    windows = partition_samples(total)
    assert windows[0][0] == 0 and windows[-1][1] == total
    assert all(a[1] == b[0] for a,b in zip(windows,windows[1:]))
    assert sum(b-a for a,b in windows) == total
    assert absolute_ms(3600*SR+7, 1200, 10*SR) == 3_601_200
    # Window origins are absolute, never the previous chunk's predicted end.
    assert absolute_ms(3600*SR, 0, 10*SR) == 3_600_000
    with pytest.raises(ValueError):absolute_ms(3600*SR, 10001, 10*SR)


def test_corrected_text_alignment_is_bound_to_exact_revision_and_clip_origin():
    p={'id':'p1','text':'原文是什麼。','start_ms':1000,'end_ms':4000}
    clip={'audio_sha256':'clip','source_origin_sample':3600*SR,'sample_count':10*SR}
    raw={'identity':{'text_sha256':text_sha(p['text']),'audio_sha256':'clip'},
         'items':[{'text':'原','start_ms':1200,'end_ms':1400},
                  {'text':'文','start_ms':1400,'end_ms':1600}]}
    resolved=resolve_final_paragraph(p,clip,raw)
    assert (resolved['start_ms'],resolved['end_ms']) == (3_601_200,3_601_600)
    assert resolved['timing_text_sha256'] == text_sha(p['text'])
    assert p['start_ms']==1000
    with pytest.raises(ValueError,match='stale'):
        resolve_final_paragraph({**p,'text':'原則是什麼。'},clip,raw)
    with pytest.raises(ValueError,match='stale'):
        resolve_final_paragraph(p,{**clip,'audio_sha256':'other'},raw)
    invalid=copy.deepcopy(raw)
    invalid['items']=[{'text':'原','start_ms':11000,'end_ms':12000}]
    assert resolve_final_paragraph(p,clip,invalid)['timing_precision']=='unresolved'
    reversed_words=copy.deepcopy(raw)
    reversed_words['items']=[{'text':'原','start_ms':3000,'end_ms':3200},
                             {'text':'文','start_ms':1000,'end_ms':1100},
                             {'text':'是','start_ms':1100,'end_ms':1200}]
    pending=resolve_final_paragraph(p,clip,reversed_words)
    assert pending['start_ms'] is None and pending['end_ms'] is None
    assert pending['timing_precision']=='unresolved' and pending['text']==p['text']
    assert pending['alignment_issue']=='non_increasing_isolated_word_predictions'


def test_zero_time_long_silence_and_invalid_predictions_are_not_interpolated():
    assert sample_ms(0)==0
    assert absolute_ms(71*60*SR, 500, 2*SR)==4_260_500
    with pytest.raises(ValueError):sample_ms(-1)
    with pytest.raises(ValueError):partition_samples(0)


def test_model_selected_exclusions_preserve_arguments_and_originals():
    from backend.pipeline.full_video_transcript import apply_exclusions
    paragraphs=[{'id':'p0','text':'電腦打不開。'},
                {'id':'p1','text':'畫面出問題。保羅在這裡論證恩賜。'},
                {'id':'p2','text':'教授用報紙和引號作例子。'}]
    original=copy.deepcopy(paragraphs)
    answer={'deletions':[{'paragraph_id':'p0','old':'電腦打不開。','reason':'操作','kind':'technical_setup'},
                         {'paragraph_id':'p1','old':'畫面出問題。','reason':'操作','kind':'technical_setup'}]}
    result,ledger=apply_exclusions(paragraphs,answer)
    assert [p['text'] for p in result]==['保羅在這裡論證恩賜。','教授用報紙和引號作例子。']
    assert paragraphs==original
    assert len(ledger)==2 and ledger[0]['source_text_sha256']==text_sha(original[0]['text'])
    with pytest.raises(ValueError):
        apply_exclusions(paragraphs,{'deletions':[{**answer['deletions'][0],'old':'absent'}]})


@pytest.mark.parametrize('separate_paragraphs',[False,True])
def test_adjacent_quote_insertions_keep_their_own_unit_and_paragraph(separate_paragraphs):
    from backend.pipeline.transcript_text_layout import text_units,apply_layout
    text='他說甲。他說乙。';units=text_units(text)
    plan={'text_sha256':text_sha(text),'operations':[
        {'unit_id':'u0001','offset':0,'delete':'','insert':'「'},
        {'unit_id':'u0000','offset':len(units[0]['text']),'delete':'','insert':'」'}],
        'paragraph_break_after':['u0000','u0001'] if separate_paragraphs else ['u0001']}
    result=apply_layout(text,units,plan)
    expected=['他說甲。」','「他說乙。'] if separate_paragraphs else ['他說甲。」「他說乙。']
    assert [p['text'] for p in result['paragraphs']]==expected


def test_visual_json_may_echo_exact_schema_but_cannot_hide_extra_payload():
    import json
    from backend.pipeline.slide_visual_evidence import SCHEMA,parse_visual_json
    data={'results':[]}
    assert parse_visual_json(json.dumps(SCHEMA)+'\n'+json.dumps(data)) == data
    assert parse_visual_json(json.dumps(SCHEMA)+', "results": []}') == data
    literal = {'results': [{'text': '心 וּבְתִמְה֥וֹן καρδία "quoted" , results'}]}
    assert parse_visual_json(json.dumps(SCHEMA)+', '+json.dumps(literal)[1:]) == literal
    assert parse_visual_json(json.dumps(data))==data
    assert parse_visual_json(json.dumps({'type':SCHEMA['type'],'properties':SCHEMA['properties'],**data}))==data
    # Non-schema extras are never dropped.
    from backend.pipeline.slide_visual_evidence import check_schema
    with pytest.raises(ValueError):check_schema(parse_visual_json(json.dumps({'unexpected':True,**data})),SCHEMA)
    with pytest.raises(ValueError):parse_visual_json('{"unexpected":true}\n'+json.dumps(data))
    with pytest.raises(ValueError):parse_visual_json(json.dumps(SCHEMA)+'\n'+json.dumps(data)+'\n{}')
    with pytest.raises(ValueError):parse_visual_json(json.dumps(SCHEMA)+', "results": [], "unexpected": true}')
    with pytest.raises(ValueError):parse_visual_json(json.dumps(SCHEMA)+', "unexpected": []}')
    with pytest.raises(ValueError):parse_visual_json('{"unexpected":true}, "results": []}')


def test_unresolved_paragraph_gets_neighbour_bounded_audio_without_invented_timestamps(tmp_path):
    import numpy as np
    from backend.pipeline import full_video_transcript as full
    folder=tmp_path/'windows/w00';folder.mkdir(parents=True)
    full.write_audio(tmp_path/'master.wav',np.zeros(20*SR,dtype=np.int16))
    full.save(tmp_path/'master.json',{'source_sha256':'b'*64,'windows':[{'id':'w00','origin_sample':0,'end_sample':20*SR,'start_ms':0,'end_ms':20000}]})
    full.save(folder/'correction/correction_trial.json',{})
    p=[{'id':'p0','text':'前文。','start_ms':1000,'end_ms':2000},
       {'id':'p1','text':'待對齊的原文。','start_ms':None,'end_ms':None},
       {'id':'p2','text':'後文。','start_ms':8000,'end_ms':9000}]
    full.save(folder/'editorial/reading_view.json',{'paragraphs':p,'source_correction_sha256':full.sha(folder/'correction/correction_trial.json')})
    full.prepare_final_alignment(tmp_path)
    final=folder/full.read(folder/'final_alignment_ref.json')['folder']
    clip=full.read(final/'asr.json')['chunks'][1]
    assert clip['source_origin_sample']==0 and clip['sample_count']==11000*16
    assert clip['initial_unresolved_recovery']['method']=='acoustic_neighbour_clip_bounds'
    assert full.read(folder/'editorial/reading_view.json')['paragraphs'][1]['start_ms'] is None


def test_traditional_model_quote_binds_to_exact_simplified_source_without_fuzzy_matching():
    from backend.pipeline.transcript_audio_correction import validate_proposals
    p={'id':'p','text':'也是这个是灵。'}
    row={'paragraph_id':'p','old':'也是這個是靈','new':'也是指恩賜','occurrence':1,
         'kind':'lexical','audio_quote':'也是指恩賜','audio_support':'clear','reason':'Synthetic','slide_event_ids':[]}
    edit=validate_proposals({'proposals':[row]},[p],set())[0]
    assert edit['old']=='也是这个是灵' and edit['model_old']=='也是這個是靈'
    assert edit['source_script_equivalence_used']
    with pytest.raises(ValueError):
        validate_proposals({'proposals':[{**row,'old':'也是這個是令'}]},[p],set())


def test_long_unchanged_english_context_is_trimmed_without_changing_model_edit():
    from backend.pipeline.transcript_audio_correction import validate_proposals
    old='是……你看，是 prepositional phrase';new='是 en agape。你看，是 prepositional phrase'
    p={'id':'p','text':'前文。'+old+'。後文。'}
    row={'paragraph_id':'p','old':old,'new':new,'occurrence':1,'kind':'foreign_word',
         'audio_quote':'en agape','audio_support':'clear','reason':'Synthetic','slide_event_ids':[]}
    edit=validate_proposals({'proposals':[row]},[p],set())[0]
    assert edit['unchanged_context_trimmed'] and len(edit['new'])<=32
    assert edit['model_context_new']==new
    assert p['text'][:edit['start']]+edit['new']+p['text'][edit['end']:]==p['text'].replace(old,new)
    with pytest.raises(ValueError,match='bounded'):
        validate_proposals({'proposals':[{**row,'new':'不同內容'*20}]},[p],set())


def test_exact_lexical_quote_can_cross_an_asr_hard_wrap_but_not_different_words():
    from backend.pipeline.transcript_audio_correction import validate_proposals
    p={'id':'p','text':'神的作為都\n是對的，是有愛。'}
    row={'paragraph_id':'p','old':'都是對的，是有愛','new':'都是出於愛','occurrence':1,'kind':'lexical',
         'audio_quote':'都是出於愛','audio_support':'clear','reason':'Synthetic','slide_event_ids':[]}
    edit=validate_proposals({'proposals':[row]},[p],set())[0]
    assert edit['source_linebreak_equivalence_used'] and not edit['source_script_equivalence_used']
    assert edit['old']=='都\n是對的，是有愛'
    assert p['text'][edit['start']:edit['end']]==edit['old']
    with pytest.raises(ValueError):validate_proposals({'proposals':[{**row,'old':'都是對的，是友愛'}]},[p],set())


def test_conflicting_edits_are_withheld_after_repair_without_losing_disjoint_corrections():
    from backend.pipeline.transcript_audio_correction import withhold_overlap_conflicts
    p={'id':'p','text':'甲乙丙丁戊己'}
    base={'paragraph_id':'p','occurrence':1,'kind':'lexical','audio_quote':'synthetic',
          'audio_support':'clear','reason':'Synthetic','slide_event_ids':[]}
    rows=[{**base,'old':'甲乙','new':'一'},{**base,'old':'乙丙','new':'二'},{**base,'old':'戊己','new':'三'}]
    valid,withheld=withhold_overlap_conflicts({'proposals':rows},[p],set())
    assert [r['old'] for r in valid]==['戊己']
    assert len(withheld)==2 and all(r['status']=='withheld_overlap_conflict' for r in withheld)


def test_cleanup_reads_cross_window_teaching_context_and_retains_invalid_deletions(tmp_path,monkeypatch):
    from backend.pipeline import full_video_transcript as full
    full.save(tmp_path/'master.json',{'windows':[{'id':'w00'},{'id':'w01'}]})
    for wid,text in [('w00','設備壞了。電腦的生活例子。'),('w01','回到剛才電腦的例子，說明字義。')]:
        full.save(tmp_path/f'windows/{wid}/correction/correction_trial.json',{'corrected_paragraphs':[{'id':'p0','text':text}]})
    answer={'deletions':[{'paragraph_id':'w00-p0','old':'設備壞了。','reason':'操作','kind':'technical_setup'},
                         {'paragraph_id':'w01-p0','old':'跨段不存在的字串','reason':'bad quote','kind':'logistics'}]}
    class Adapter:
        def __init__(self,*a):self.config={'response_json_schema':{}};self.model='fixture'
        def text(self,raw):return json.dumps(answer)
        def usage(self,raw):return {'estimated_usd_upper':0}
    def saved(folder,request,*args,**kwargs):
        assert '設備壞了。電腦的生活例子。' in request['prompt']
        assert '回到剛才電腦的例子' in request['prompt']
        return {}
    monkeypatch.setattr(full,'TranscriptSubscriptionReviewer',Adapter);monkeypatch.setattr(full,'saved_call',saved)
    result=full.clean_full_transcript(tmp_path)
    assert len(result['withheld_deletions'])==1
    assert [p['text'] for p in result['paragraphs']]==['電腦的生活例子。','回到剛才電腦的例子，說明字義。']
    assert full.read(tmp_path/'windows/w00/correction/correction_trial.json')['corrected_paragraphs'][0]['text'].startswith('設備壞了。')


def test_backward_prediction_is_reheard_in_a_neighbour_bounded_clip_without_overwriting_original(tmp_path,monkeypatch):
    import numpy as np
    from backend.pipeline import full_video_transcript as full
    folder=tmp_path/'windows/w00';original=folder/'initial'
    full.write_audio(tmp_path/'master.wav',np.zeros(10*SR,dtype=np.int16))
    full.save(tmp_path/'master.json',{'windows':[{'id':'w00'}]})
    paragraphs=[{'id':'p0','text':'甲','start_ms':1000,'end_ms':1600},{'id':'p1','text':'乙','start_ms':2000,'end_ms':2500}]
    full.save(folder/'editorial/reading_view.json',{'paragraphs':paragraphs})
    full.save(folder/'final_alignment_ref.json',{'folder':'initial','reading_view_sha256':full.sha(folder/'editorial/reading_view.json')})
    chunks=[]
    for p,start in zip(paragraphs,[1000,900]):
        path=original/f'audio/{p["id"]}.wav';full.write_audio(path,np.zeros(5*SR,dtype=np.int16))
        chunk={'id':p['id'],'text':p['text'],'audio':f'audio/{p["id"]}.wav','audio_sha256':full.sha(path),
               'source_origin_sample':0,'sample_count':5*SR}
        chunks.append(chunk)
        full.save(original/f'alignment/{p["id"]}.json',{'identity':{'text_sha256':text_sha(p['text']),'audio_sha256':full.sha(path)},
            'items':[{'text':p['text'],'start_ms':start,'end_ms':p['end_ms']}]})
    full.save(original/'asr.json',{'chunks':chunks})
    def align(path,python):
        c=full.read(path/'asr.json')['chunks'][1]
        assert c['source_origin_sample']==1600*16
        full.save(path/'alignment/p1.json',{'identity':{'text_sha256':text_sha('乙'),'audio_sha256':c['audio_sha256']},
            'items':[{'text':'乙','start_ms':200,'end_ms':400}]})
    monkeypatch.setattr(full,'align',align)
    full.refine_backward_predictions(tmp_path,tmp_path/'python')
    assert full.read(original/'alignment/p1.json')['items'][0]['start_ms']==900
    ref=full.read(folder/'final_alignment_ref.json');refined=folder/ref['folder']
    clip=full.read(refined/'asr.json')['chunks'][1]
    assert full.resolve_final_paragraph(paragraphs[1],clip,full.read(refined/'alignment/p1.json'))['start_ms']==1800


def test_local_movie_preview_supports_byte_ranges_for_late_video_seeks(tmp_path):
    from starlette.testclient import TestClient
    from backend.pipeline.full_video_transcript import preview_app
    (tmp_path/'source.mp4').write_bytes(bytes(range(200)))
    with TestClient(preview_app(tmp_path)) as client:
        response=client.get('/source.mp4',headers={'Range':'bytes=150-159'})
        assert response.status_code==206
        assert response.headers['content-range']=='bytes 150-159/200'
        assert response.content==bytes(range(150,160))
        assert client.get('/source.mp4',headers={'Range':'bytes=300-399'}).status_code==416


def test_audio_only_source_retains_clock_without_inventing_frames(tmp_path, monkeypatch):
    import numpy as np
    from backend.pipeline import full_video_transcript as full
    source = tmp_path / '講道.mp3'
    source.write_bytes(b'original audio')
    out = tmp_path / 'out'
    samples = np.full(full.SR * 2, 100, dtype=np.int16)
    probe = {'streams': [{'codec_type': 'audio', 'start_time': '0'}],
             'format': {'duration': '2'}}
    monkeypatch.setattr(full.subprocess, 'check_output', lambda *a, **k: json.dumps(probe))
    monkeypatch.setattr(full.subprocess, 'run', lambda *a, **k: full.write_audio(out / 'master.wav', samples))
    master = full.prepare(source, out)
    assert master['media_type'] == 'audio' and master['media_mime_type'] == 'audio/mpeg'
    assert master['source_sha256'] == full.sha(source)
    full.prepare_audio_visuals(out)
    assert full.read(out / 'slides/report.json')['events'] == []
    span, = full.read(out / 'slides/report.json')['visibility']['spans']
    assert span['state'] == 'unknown' and span['image'] is None
    assert (span['start_ms'], span['end_ms']) == (0, 2000)
    assert full.read(out / 'visual-evidence/slide_evidence.json')['assets'] == []
    assert full.read(out / 'frame_pts_audit.json')['applicable'] is False
    local = full.local_slides(out, master['windows'][0])
    assert full.read(local / 'report.json')['events'] == []
    assert master['windows'][-1]['end_sample'] == len(samples)


def test_late_partial_alignment_retries_after_previous_speech_and_keeps_original(tmp_path, monkeypatch):
    import numpy as np
    from backend.pipeline import full_video_transcript as full
    folder=tmp_path/'windows/w00';original=folder/'initial'
    full.write_audio(tmp_path/'master.wav',np.zeros(10*SR,dtype=np.int16))
    full.save(tmp_path/'master.json',{'windows':[{'id':'w00','start_ms':0}]})
    paragraphs=[{'id':'p0','text':'甲','start_ms':1000,'end_ms':1600},
                {'id':'p1','text':'乙丙丁','start_ms':2000,'end_ms':3000}]
    full.save(folder/'editorial/reading_view.json',{'paragraphs':paragraphs})
    full.save(folder/'final_alignment_ref.json',{'folder':'initial','reading_view_sha256':full.sha(folder/'editorial/reading_view.json')})
    chunks=[]
    for p in paragraphs:
        path=original/f'audio/{p["id"]}.wav';full.write_audio(path,np.zeros(10*SR,dtype=np.int16))
        c={'id':p['id'],'text':p['text'],'audio':f'audio/{p["id"]}.wav','audio_sha256':full.sha(path),'source_origin_sample':0,'sample_count':10*SR};chunks.append(c)
        items=([{'text':'甲','start_ms':1000,'end_ms':1600}] if p['id']=='p0' else
               [{'text':'乙','start_ms':7000,'end_ms':7200},{'text':'丙','start_ms':0,'end_ms':0},{'text':'丁','start_ms':0,'end_ms':0}])
        full.save(original/f'alignment/{p["id"]}.json',{'identity':{'text_sha256':text_sha(p['text']),'audio_sha256':c['audio_sha256']},'items':items})
    full.save(original/'asr.json',{'chunks':chunks})
    def align(path, python):
        c=full.read(path/'asr.json')['chunks'][1]
        assert c['source_origin_sample']==1600*16
        assert c['timing_refinement']['reason']=='late_partial_alignment_in_preceding_speech_padding'
        full.save(path/'alignment/p1.json',{'identity':{'text_sha256':text_sha('乙丙丁'),'audio_sha256':c['audio_sha256']},
                  'items':[{'text':char,'start_ms':200+i*100,'end_ms':300+i*100} for i,char in enumerate('乙丙丁')]})
    monkeypatch.setattr(full,'align',align)
    full.refine_backward_predictions(tmp_path,tmp_path/'python')
    assert full.read(original/'alignment/p1.json')['items'][0]['start_ms']==7000
    ref=full.read(folder/'final_alignment_ref.json');refined=folder/ref['folder'];c=full.read(refined/'asr.json')['chunks'][1]
    assert full.resolve_final_paragraph(paragraphs[1],c,full.read(refined/'alignment/p1.json'))['start_ms']==1800
    assert c['text']=='乙丙丁'


def test_conservative_retention_keeps_uncertain_teaching_and_is_bound_to_source_text():
    from backend.pipeline.full_video_transcript import retain_uncertain_paragraphs, apply_exclusions
    paragraphs=[{'id':'p0','text':'來，站在那裡。好了，所以他們把他殺了。'}, {'id':'p1','text':'電腦出問題。'}]
    deletions=[{'paragraph_id':'p0','old':'來，站在那裡。好了，','reason':'模型判作現場指示','kind':'logistics'},
               {'paragraph_id':'p1','old':'電腦出問題。','reason':'設備故障','kind':'technical_setup'}]
    reviews=[{'paragraph_id':'p0','source_text_sha256':text_sha(paragraphs[0]['text']),'reason':'可能是正在讀的經文，原文保留待核對'}]
    kept, withheld=retain_uncertain_paragraphs(paragraphs,deletions,reviews)
    reading,_=apply_exclusions(paragraphs,{'deletions':kept})
    assert reading==[paragraphs[0]]
    assert withheld[0]['status']=='retained_uncertain_content' and withheld[0]['human_confirmed'] is False
    with pytest.raises(ValueError,match='another paragraph revision'):
        retain_uncertain_paragraphs(paragraphs,deletions,[{**reviews[0],'source_text_sha256':'stale'}])


def test_conservative_cleanup_supports_restart_and_aside_without_rewriting():
    from backend.pipeline.full_video_transcript import apply_exclusions
    paragraphs=[{'id':'p','text':'因為受達爾文影響，達爾文進化論影響。'},
                {'id':'q','text':'搬家後開車二十分鐘。'}]
    cleaned,rows=apply_exclusions(paragraphs,{'deletions':[
        dict(paragraph_id='p',old='影響，達爾文',reason='放棄半句，後面重講完成',kind='speech_restart'),
        dict(paragraph_id='q',old=paragraphs[1]['text'],reason='獨立生活旁枝',kind='unrelated_aside')]})
    assert cleaned[0]['text']=='因為受達爾文進化論影響。' and len(cleaned)==1
    assert paragraphs[0]['text'].startswith('因為受達爾文影響')
    assert all(not r['human_confirmed'] for r in rows)
    for old in ['。','因為受達爾文影響，達爾文進化論影響。']:
        with pytest.raises(ValueError):apply_exclusions(paragraphs,{'deletions':[dict(paragraph_id='p',old=old,reason='bad',kind='speech_restart')]})


def test_cleanup_withholds_negation_even_if_model_calls_it_a_restart():
    from backend.pipeline.full_video_transcript import apply_exclusions

    rows = [{'id': 'p', 'text': '不會不不談話。這話不是那個意思。'}]
    for old in ('不會', '不是'):
        with pytest.raises(ValueError, match='negation requires review'):
            apply_exclusions(rows, {'deletions': [
                dict(paragraph_id='p', old=old, reason='疑似重啟', kind='speech_restart'),
            ]})


def test_overlapping_whole_lecture_proposals_keep_disjoint_valid_deletions(tmp_path,monkeypatch):
    from backend.pipeline import full_video_transcript as f
    f.save(tmp_path/'master.json',{'windows':[{'id':'w00'}]})
    f.save(tmp_path/'windows/w00/correction/correction_trial.json',{'corrected_paragraphs':[{'id':'p','text':'甲乙丙丁戊己庚'}]})
    answer={'deletions':[dict(paragraph_id='w00-p',old=s,reason='fixture',kind='unrelated_aside') for s in ['甲乙','乙丙','戊己']]}
    class Adapter:
        config={};model='fixture'
        def text(self,raw):return json.dumps(answer)
        def usage(self,raw):return {'estimated_usd_upper':0}
    monkeypatch.setattr(f,'cleanup_adapter',lambda:Adapter())
    monkeypatch.setattr(f,'saved_call',lambda *a,**kw:{})
    result=f.clean_full_transcript(tmp_path)
    assert len(result['withheld_deletions'])==1
    assert result['paragraphs'][0]['text']=='丙丁庚'
    f.save(tmp_path/'windows/w00/correction/correction_trial.json',{'corrected_paragraphs':[{'id':'p','text':'新版本'}]})
    with pytest.raises(ValueError,match='changed'):f.clean_full_transcript(tmp_path)


def test_standalone_cleanup_respects_two_shared_sol_slots(tmp_path,monkeypatch):
    from backend.pipeline.full_video_transcript import cleanup_generate
    from backend.pipeline.media_batch.runtime import slot,WaitingCapacity
    monkeypatch.delenv('MEDIA_BATCH_CONTEXT',raising=False)
    monkeypatch.setenv('MEDIA_BATCH_CAPACITY_ROOT',str(tmp_path))
    class Adapter:
        def generate(self,contents):raise AssertionError('No third call allowed')
    with slot(tmp_path,'sol',2),slot(tmp_path,'sol',2):
        with pytest.raises(WaitingCapacity):cleanup_generate(Adapter(),'fixture')


def test_unknown_cleanup_call_keeps_capacity_quarantined(tmp_path,monkeypatch):
    from backend.pipeline.full_video_transcript import cleanup_generate
    from backend.pipeline.media_batch.runtime import capacity_status
    monkeypatch.delenv('MEDIA_BATCH_CONTEXT',raising=False)
    monkeypatch.setenv('MEDIA_BATCH_CAPACITY_ROOT',str(tmp_path))
    class Adapter:
        def generate(self,contents):raise TimeoutError('Unknown provider outcome')
    with pytest.raises(TimeoutError):cleanup_generate(Adapter(),'fixture')
    assert capacity_status(tmp_path)['sol']['unknown']==1


def test_untimed_clip_uses_original_audio_chunk_not_neighbour_gap_or_character_ratio(tmp_path):
    from backend.pipeline import full_video_transcript as f
    folder=tmp_path/'windows/w00';audio=folder/'audio'
    f.save(audio/'asr.json',{'chunks':[dict(id='a',sample_start=0,sample_end=185*16000),dict(id='b',sample_start=181*16000,sample_end=365*16000)]})
    f.save(audio/'timed_text.json',dict(asr_sha256=f.sha(audio/'asr.json'),text='甲乙丙丁',text_sha256=f.text_sha('甲乙丙丁'),chunk_ownership=[dict(chunk_id='a',output_start=0,output_end=2),dict(chunk_id='b',output_start=2,output_end=4)]))
    f.save(folder/'correction/correction_trial.json',{'original_paragraphs':[dict(id='p',source_start=0,source_end=1)]})
    window=dict(origin_sample=900*16000,end_sample=1800*16000)
    begin,end,proof=f.original_paragraph_clip(folder,dict(id='p',text='改正文字'),window)
    assert (begin,end)==(900*16000,1085*16000)
    assert proof['method']=='original_asr_chunk_clip_not_predicted_timing'
    f.save(folder/'correction/correction_trial.json',{'original_paragraphs':[dict(id='p',source_start=0,source_end=4)]})
    with pytest.raises(ValueError,match='model limit'):f.original_paragraph_clip(folder,dict(id='p'),window)
