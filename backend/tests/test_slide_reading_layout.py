from copy import deepcopy

import pytest

from backend.pipeline.slide_reading_layout import compile_layout, render


def source():
    return {'source_sha256': 'video', 'slides': [
        {'id': f'c{i}', 'time_ms': 10000+i*10000, 'capture_ms': 11000+i*10000,
         'paragraph_id': f'p{i}', 'slide_sha256': str(i)} for i in range(3)],
        'paragraphs': [{'id': f'p{i}', 'text': f'原話 {i} < &', 'start_ms': i*20000,
                        'end_ms': i*20000+15000} for i in range(3)],
        'visual_assets': [{'asset_id': f'a{i}', 'visible_text': f'圖文{i}', 'svg': None} for i in range(3)]}


def proposal():
    return {'groups': [
        {'member_ids': ['c0', 'c1'], 'reason': '逐步顯示', 'representatives': [
            {'slide_id': 'c1', 'before_paragraph_id': 'p0', 'confidence': 'high', 'reason': '開始解說'}]},
        {'member_ids': ['c2'], 'reason': '另頁', 'representatives': [
            {'slide_id': 'c2', 'before_paragraph_id': 'p2', 'confidence': 'high', 'reason': '此處解說'}]}]}


def test_preserve_source_and_exact_occurrences():
    record = source(); before = deepcopy(record)
    layout = compile_layout(record, proposal())
    assert record == before
    assert [s for g in layout['groups'] for s in g['source_occurrences']] == record['slides']
    assert layout['groups'][0]['representatives'][0]['before_paragraph_id'] == 'p0'
    assert layout['groups'][0]['representatives'][0]['source_time_ms'] == 20000


@pytest.mark.parametrize('members', [['c0'], ['c0', 'c1', 'c1'], ['c1', 'c0']])
def test_reject_omitted_duplicate_or_reordered_occurrences(members):
    p = proposal(); p['groups'][0]['member_ids'] = members
    with pytest.raises(ValueError, match='cover every'):
        compile_layout(source(), p)


def test_reject_fabricated_representative_or_paragraph():
    p = proposal(); p['groups'][0]['representatives'][0]['slide_id'] = 'not-real'
    with pytest.raises(ValueError, match='group members'):
        compile_layout(source(), p)
    p = proposal(); p['groups'][0]['representatives'][0]['before_paragraph_id'] = 'not-real'
    with pytest.raises(ValueError, match='Unknown paragraph'):
        compile_layout(source(), p)


def test_uncertain_uses_temporal_anchor_and_orphan_stays_orphan():
    r = source(); r['slides'][0]['paragraph_id'] = None
    p = proposal(); p['groups'][0]['representatives'][0]['confidence'] = 'uncertain'
    rep = compile_layout(r, p)['groups'][0]['representatives'][0]
    assert rep['before_paragraph_id'] is None and rep['needs_review']
    r['slides'][0]['paragraph_id'] = 'p0'
    assert compile_layout(r, p)['groups'][0]['representatives'][0]['before_paragraph_id'] == 'p0'


def test_far_semantic_anchor_falls_back():
    r = source(); r['paragraphs'][2].update(start_ms=1000000, end_ms=1010000)
    p = proposal(); p['groups'][0]['representatives'][0]['before_paragraph_id'] = 'p2'
    rep = compile_layout(r, p)['groups'][0]['representatives'][0]
    assert rep['before_paragraph_id'] == 'p0' and rep['needs_review']


def test_backward_argument_return_not_moved_to_prior_explanation():
    p = proposal(); p['groups'][0]['representatives'][0]['before_paragraph_id'] = 'p1'
    p['groups'][1]['representatives'][0]['before_paragraph_id'] = 'p0'
    rep = compile_layout(source(), p)['groups'][1]['representatives'][0]
    assert rep['before_paragraph_id'] == 'p2' and rep['needs_review']


def test_renderer_preserves_speech_and_all_states_and_places_before_paragraph(tmp_path):
    r = source(); layout = compile_layout(r, proposal()); render(r, layout, tmp_path)
    text = (tmp_path/'index.html').read_text()
    assert text.index('data-slide="c1"') < text.index('id="p0"')
    assert all(text.count(f'data-occurrence="c{i}"') == 1 for i in range(3))
    assert '原話 0 &lt; &amp;' in text and '投影片文字（非口述）' in text
    assert '並非影片當時已顯示完整內容' in text
    r['paragraphs'][0]['text'] = 'changed'
    with pytest.raises(ValueError, match='different source'):
        render(r, layout, tmp_path)


def test_multiple_argument_stages_keep_distinct_anchors():
    p = proposal(); p['groups'][0]['representatives'].insert(0,
        {'slide_id': 'c0', 'before_paragraph_id': 'p0', 'confidence': 'high', 'reason': '問題'})
    p['groups'][0]['representatives'][1]['before_paragraph_id'] = 'p1'
    reps = compile_layout(source(), p)['groups'][0]['representatives']
    assert [r['before_paragraph_id'] for r in reps] == ['p0', 'p1']


def test_cannot_merge_across_no_slide_or_return():
    r = source(); r['visibility'] = {'spans': [
        {'start_ms': 14000, 'end_ms': 15000, 'state': 'absent'}]}
    with pytest.raises(ValueError, match='no-slide'):
        compile_layout(r, proposal())
    r = source(); r['slides'][1]['kind'] = 'return'
    with pytest.raises(ValueError, match='return'):
        compile_layout(r, proposal())


def test_video_without_slides_has_empty_layout_and_no_images(tmp_path):
    r = source(); r['slides'] = []; r['visual_assets'] = []
    layout = compile_layout(r, {'groups': []}); render(r, layout, tmp_path)
    assert layout['groups'] == []
    page = (tmp_path/'index.html').read_text()
    assert '<img' not in page
    assert '原話 0 &lt; &amp;' in page
