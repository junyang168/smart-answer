import copy

import pytest

from backend.pipeline.full_video_transcript import (
    SR, absolute_ms, partition_samples, resolve_final_paragraph, sample_ms,
)
from backend.pipeline.transcript_text_layout import text_sha


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
    assert parse_visual_json(json.dumps(data))==data
    with pytest.raises(ValueError):parse_visual_json('{"unexpected":true}\n'+json.dumps(data))
    with pytest.raises(ValueError):parse_visual_json(json.dumps(SCHEMA)+'\n'+json.dumps(data)+'\n{}')


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
