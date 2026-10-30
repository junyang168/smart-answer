import copy
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET

import cv2
import numpy as np
import pytest

from backend.pipeline import slide_visual_evidence as visual
from backend.pipeline.slide_change_pilot import file_sha, object_sha, read_json, save_json
from backend.pipeline.slide_pilot_models import VisionReviewer
from backend.pipeline.source_projection import visual_source_blocks
from backend.pipeline.visual_transcript_packet import build


def content(diagram=True):
    return {'asset_id': 'a' + 'b' * 64, 'background': '#FFFFFF', 'has_diagram': diagram,
        'text_regions': [
            {'id': 'r1', 'text': '甲 ζηλοῦτε e\u0301', 'bbox': [100, 200, 400, 300], 'color': '#111111', 'uncertain': False},
            {'id': 'r2', 'text': '乙 שָׁלוֹם & <原字>', 'bbox': [600, 200, 900, 300], 'color': '#111111', 'uncertain': True}],
        'shapes': [{'id': 's1', 'kind': 'line', 'points': [[410, 250], [590, 250]], 'stroke': '#222222',
                    'fill': None, 'dashed': False, 'arrow_start': False, 'arrow_end': True, 'uncertain': False}] if diagram else [],
        'edges': [{'id': 'e1', 'from_region': 'r1', 'to_region': 'r2', 'connector_id': 's1',
                   'direction': 'from_to', 'uncertain': False}] if diagram else [],
        'issues': [{'bbox': [600, 200, 900, 300], 'reason': 'Synthetic fixture uncertainty; not professor material.'}]}


def test_svg_preserves_literal_unicode_and_visible_arrow_with_no_executable_markup():
    value = content()
    svg = visual.render_svg(value, 1600, 900, 'a' * 64)
    root = ET.fromstring(svg)
    ns = {'s': 'http://www.w3.org/2000/svg'}
    assert [''.join(t.itertext()) for t in root.findall('s:text', ns)] == [r['text'] for r in value['text_regions']]
    assert root.find('s:line', ns).get('marker-end') == 'url(#arrow-222222)'
    assert root.find('s:line', ns).get('marker-start') is None
    assert root.get('data-derivation') == 'model_reconstruction_unreviewed'
    assert '<原字>' not in svg and '&lt;原字&gt;' in svg
    block = visual_source_blocks(svg, segment_index='S0001')[0]
    assert block.readable
    assert any(f['tag'] == 'line' and f['attributes']['marker-end'] for f in block.facts)


def test_text_only_slide_does_not_acquire_an_invented_diagram():
    value = content(False)
    visual.validate_content(value)
    with pytest.raises(ValueError, match='No visible diagram'):
        visual.render_svg(value, 800, 600, 'a' * 64)
    value['shapes'] = content()['shapes']
    with pytest.raises(ValueError, match='text-only'):
        visual.validate_content(value)


def test_hebrew_uses_first_strong_direction_without_reversing_a_chinese_label():
    ns = {'s': 'http://www.w3.org/2000/svg'}
    value = content()
    root = ET.fromstring(visual.render_svg(value, 800, 600, 'a' * 64))
    assert root.findall('s:text/s:tspan', ns)[1].get('direction') is None
    value['text_regions'][1]['text'] = 'שָׁלוֹם'
    root = ET.fromstring(visual.render_svg(value, 800, 600, 'a' * 64))
    assert root.findall('s:text/s:tspan', ns)[1].get('direction') == 'rtl'


@pytest.mark.parametrize('mutate', [
    lambda c: c['text_regions'][0].update(color='url(https://example.org)'),
    lambda c: c['text_regions'][0].update(id='x" onload="bad'),
    lambda c: c['text_regions'][0].update(bbox=[0, 0, float('nan'), 10]),
    lambda c: c['text_regions'][0].update(bbox=[0, 0, 1001, 10]),
    lambda c: c['text_regions'][0].update(bbox=[True, 0, 100, 10]),
    lambda c: c['text_regions'][0].update(script='bad'),
    lambda c: c['edges'][0].update(from_region='invented'),
    lambda c: c['edges'][0].update(to_region=None),
    lambda c: c['edges'][0].update(connector_id='missing'),
    lambda c: c['shapes'][0].update(id='r1'),
])
def test_invalid_or_invented_visual_geometry_is_rejected(mutate):
    value = content()
    mutate(value)
    with pytest.raises(ValueError):
        visual.validate_content(value)


def setup_packet(tmp_path, diagram=True):
    transcript, evidence, out = [tmp_path / n for n in ('speech', 'evidence', 'joined')]
    evidence.mkdir()
    image = evidence / 'original.png'
    cv2.imwrite(str(image), np.full((200, 320, 3), 255, np.uint8))
    digest = file_sha(image)
    value = content(diagram)
    value['asset_id'] = 'a' + digest
    asset = {'schema_version': 'slide-visual-evidence.v1', 'source_video_sha256': 'a' * 64,
        'asset_id': value['asset_id'], 'image_sha256': digest, 'content': value, 'content_sha256': object_sha(value),
        'visible_text': '\n'.join(r['text'] for r in value['text_regions']), 'image_width': 320, 'image_height': 200,
        'review_status': 'unreviewed_model_candidate', 'quotation_status': 'not_spoken_verbatim',
        'original_image': 'original.png', 'svg': None}
    asset['text_sha256'] = hashlib.sha256(asset['visible_text'].encode()).hexdigest()
    if diagram:
        svg_path = evidence / 'reconstruction.svg'
        svg_path.write_text(visual.render_svg(value, 320, 200, digest))
        asset['svg'] = {'path': svg_path.name, 'sha256': file_sha(svg_path), 'derivation': 'model_reconstruction'}
    source = {'source_sha256': 'a' * 64, 'paragraphs': [{'id': 'p0', 'text': '口述重複，重複。', 'start_ms': 1000}],
        'slides': [{'id': 's0', 'image_sha256': digest, 'time_ms': 100, 'capture_ms': 200, 'paragraph_id': 'p0'},
                   {'id': 's1', 'image_sha256': digest, 'time_ms': 2000, 'capture_ms': 2200, 'paragraph_id': None}]}
    collection = {'source_video_sha256': 'a' * 64, 'assets': [asset], 'occurrences': [
        {'event_id': 's0', 'time_ms': 100, 'capture_ms': 200, 'kind': 'new_slide', 'asset_id': value['asset_id']},
        {'event_id': 's1', 'time_ms': 2000, 'capture_ms': 2200, 'kind': 'return', 'asset_id': value['asset_id']}]}
    save_json(transcript / 'illustrated_transcript.json', source)
    save_json(evidence / 'slide_evidence.json', collection)
    return transcript, evidence, out, source, collection


def test_packet_separates_visual_text_and_edges_from_unchanged_speech(tmp_path):
    transcript, evidence, out, source, collection = setup_packet(tmp_path)
    result = build(transcript, evidence, out)
    assert result['speech_text_unchanged']
    packet = read_json(out / 'argument_source_packet.json')
    joined = read_json(out / 'illustrated_with_visual_text.json')
    assert joined['paragraphs'] == source['paragraphs']
    assert len(packet['visual_occurrences']) == 2  # One extraction, two true occurrences.
    assert len(packet['slide_text_regions']) == 2
    assert packet['slide_text_regions'][0]['text'] not in packet['speech_segments'][0]['text']
    assert all(r['quotation_status'] == 'not_spoken_verbatim' for r in packet['slide_text_regions'])
    assert packet['diagram_edges'][0]['interpretation'] == 'visible_connector_only_not_an_argument_claim'
    assert packet['visual_source_attestations'] == []
    assert packet['admission_status'] == 'candidate_not_ingested'
    assert '下載 SVG' in (out / 'index.html').read_text()


@pytest.mark.parametrize('change', ['source', 'text', 'time', 'svg', 'missing'])
def test_packet_rejects_source_text_timeline_or_svg_drift(tmp_path, change):
    transcript, evidence, out, source, collection = setup_packet(tmp_path)
    if change == 'source':
        collection['source_video_sha256'] = 'other'
    elif change == 'text':
        collection['assets'][0]['content']['text_regions'][0]['text'] = 'Silently rewritten'
    elif change == 'time':
        collection['occurrences'][0]['time_ms'] += 1
    elif change == 'svg':
        (evidence / 'reconstruction.svg').write_text('<svg>changed</svg>')
    else:
        collection['occurrences'].pop()
    save_json(evidence / 'slide_evidence.json', collection)
    with pytest.raises(ValueError):
        build(transcript, evidence, out)


def test_native_png_and_jpeg_mime_types_are_preserved():
    adapter = VisionReviewer.__new__(VisionReviewer)
    adapter.provider = 'openai'
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for name in ('original.png', 'pair.jpg'):
            (root / name).write_bytes(b'fixture')
        result = adapter.contents(['a', 'b'], [root / 'original.png', root / 'pair.jpg'])
    parts = result[0]['content']
    assert parts[1]['image_url'].startswith('data:image/png;base64,')
    assert parts[2]['image_url'].startswith('data:image/jpeg;base64,')


def test_extract_resume_reuses_response_without_new_count_or_generation(tmp_path, monkeypatch):
    slides, out = tmp_path / 'slides', tmp_path / 'extracted'
    slides.mkdir()
    cv2.imwrite(str(slides / 's0.png'), np.full((100, 100, 3), 255, np.uint8))
    save_json(slides / 'report.json', {'source_sha256': 'a' * 64, 'events': [
        {'id': 's0', 'time_ms': 0, 'capture_ms': 0, 'kind': 'new_slide', 'slide_image': 's0.png',
         'slide_sha256': file_sha(slides / 's0.png')}]})
    calls = {'count': 0, 'generate': 0}
    class FakeReviewer:
        model, input_rate, output_rate = 'fake-test-only', .75, 3.75
        def __init__(self, *args):
            self.config, self.prompt = {'response_json_schema': visual.SCHEMA}, visual.PROMPT
        def contents(self, ids, paths): return ids
        def count(self, contents):
            calls['count'] += 1
            return 10
        def generate(self, contents):
            calls['generate'] += 1
            results = []
            for asset_id in contents:
                row = content(False)
                row['asset_id'] = asset_id
                results.append(row)
            return {'results': results}
        def text(self, raw): return json.dumps(raw, ensure_ascii=False)
        def usage(self, raw): return {'estimated_usd_upper': .01}
    monkeypatch.setattr(visual, 'VisionReviewer', FakeReviewer)
    visual.extract(slides, out)
    visual.extract(slides, out)
    assert calls == {'count': 1, 'generate': 1}
    assert read_json(out / 'slide_evidence.json')['diagram_count'] == 0
    (slides / 's0.png').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        visual.extract(slides, out)
    assert calls['generate'] == 1


def test_independent_sample_disagreement_is_disclosed_without_rewriting_source(tmp_path):
    transcript, evidence, out, source, collection = setup_packet(tmp_path)
    collection.update(model='primary', estimated_usd_upper=.01)
    independent = copy.deepcopy(collection)
    independent['model'] = 'other-provider'
    independent['assets'][0]['visible_text'] = '辨識不同'
    independent['assets'][0]['text_sha256'] = hashlib.sha256('辨識不同'.encode()).hexdigest()
    save_json(evidence / 'slide_evidence.json', collection)
    save_json(evidence / 'independent/slide_evidence.json', independent)
    compared = visual.compare_sample(evidence, evidence / 'independent')
    assert compared['exact_matches'] == 0 and compared['auto_corrections'] == 0
    build(transcript, evidence, out)
    packet = read_json(out / 'argument_source_packet.json')
    assert packet['independent_comparison']['exact_matches'] == 0
    assert packet['slide_text_regions'][0]['text'] == collection['assets'][0]['content']['text_regions'][0]['text']
    assert '兩家模型的文字辨識有差異' in (out / 'index.html').read_text()
    compared['primary_collection_sha256'] = 'wrong-revision'
    save_json(evidence / 'independent_comparison.json', compared)
    with pytest.raises(ValueError, match='another extraction'):
        build(transcript, evidence, out)


def test_visual_stage_rejects_different_video_before_any_paid_call(tmp_path, monkeypatch):
    import sys
    from backend.pipeline import transcript_audio_pilot as audio
    save_json(tmp_path / 'speech/illustrated_transcript.json', {'source_sha256': 'a'})
    save_json(tmp_path / 'slides/report.json', {'source_sha256': 'b'})
    monkeypatch.setattr(sys, 'argv', ['pilot', 'visual', '--out', str(tmp_path / 'speech'), '--slides', str(tmp_path / 'slides')])
    monkeypatch.setattr(visual, 'extract', lambda *a, **kw: pytest.fail('must not send a paid call'))
    with pytest.raises(ValueError, match='no visual calls sent'):
        audio.main()


def test_real_narrow_cjk_label_scales_uniformly_without_distorting_glyphs():
    value = content()
    value['text_regions'][0].update(text='理性', bbox=[287, 285, 353, 396])
    root = ET.fromstring(visual.render_svg(value, 642, 480, 'a' * 64, font_style='kai'))
    ns = {'s': 'http://www.w3.org/2000/svg'}
    label = root.find('s:text', ns)
    assert 40 < float(label.get('font-size')) < 45
    assert 'Kaiti TC' in label.get('font-family')
    assert label.find('s:tspan', ns).text == '理性'
    for span in root.findall('s:text/s:tspan', ns):
        assert span.get('textLength') is None
        assert span.get('lengthAdjust') is None
        assert span.get('transform') is None


def test_font_fit_does_not_count_combining_marks_as_new_characters():
    assert visual.natural_text_size(['e\u0301'], 20, 100) == visual.natural_text_size(['e'], 20, 100)
    assert visual.natural_text_size(['שָׁלוֹם'], 100, 50) == visual.natural_text_size(['שלום'], 100, 50)
    assert visual.natural_text_size(['甲乙', '甲'], 40, 100) <= 20
    with pytest.raises(ValueError, match='font style'):
        visual.render_svg(content(), 642, 480, 'a' * 64, font_style='unknown')


def test_diagram_peer_labels_share_size_despite_bad_ocr_rectangle():
    value = content()
    value['edges'] = []
    value['text_regions'] = [
        {'id': 'r1', 'text': '理性', 'bbox': [287, 285, 353, 396], 'color': '#FFFFFF', 'uncertain': False},
        {'id': 'r2', 'text': '情感', 'bbox': [486, 287, 552, 353], 'color': '#FFFFFF', 'uncertain': False},
        {'id': 'r3', 'text': '意志', 'bbox': [390, 508, 496, 580], 'color': '#FFFFFF', 'uncertain': False}]
    value['shapes'] = [{'id': 's1', 'kind': 'ellipse', 'points': [[232, 85], [651, 646]],
        'stroke': '#FFFFFF', 'fill': None, 'dashed': False, 'arrow_start': False, 'arrow_end': False, 'uncertain': False}]
    ns = {'s': 'http://www.w3.org/2000/svg'}
    root = ET.fromstring(visual.render_svg(value, 642, 480, 'a'*64))
    sizes = [float(t.get('font-size')) for t in root.findall('s:text', ns)]
    assert len(set(sizes)) == 1 and 27 < sizes[0] < 30
