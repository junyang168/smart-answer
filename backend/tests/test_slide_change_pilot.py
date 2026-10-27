from pathlib import Path

import cv2
import numpy as np
import pytest

from backend.pipeline import slide_change_pilot as pilot
from backend.pipeline.slide_pilot_models import VisionReviewer


def sample_manifest(tmp_path, images):
    rows = []
    for i, image in enumerate(images):
        path = tmp_path / f'frames/f-{i:09d}.png'
        pilot.image_write(path, image)
        rows.append({'id': f'f-{i:09d}', 'image': str(path.relative_to(tmp_path)),
                     'sha256': pilot.file_sha(path), 'time_ms': i * 520,
                     'source_frame_number': i, 'roi_original_xywh': [0, 0, 320, 240]})
    manifest = {'identity': {'source_sha256': 'a' * 64, 'start_ms': 0, 'end_ms': len(images) * 520},
                'original_width': 320, 'original_height': 240, 'frames': rows}
    pilot.save_json(tmp_path / 'manifest.json', manifest)
    pilot.save_json(tmp_path / 'reference.json', {'source_sha256': 'a' * 64,
        'manifest_sha256': pilot.file_sha(tmp_path / 'manifest.json'), 'survey_page_sha256': {}})
    return manifest


def test_white_slide_falls_back_to_full_frame():
    frame = np.full((240, 320, 3), 255, np.uint8)
    assert pilot.propose_roi(frame) == (0, 0, 320, 240)


def test_cursor_motion_is_not_a_content_candidate_but_small_text_build_is(tmp_path):
    base = np.full((240, 320, 3), 230, np.uint8)
    images = [base.copy() for _ in range(8)]
    # Cursor moves, then disappears; no text changes in the first four frames.
    images[1][30:34, 20:24] = 0
    images[2][80:84, 80:84] = 0
    for image in images[4:]:
        cv2.putText(image, 'new text', (30, 100), cv2.FONT_HERSHEY_SIMPLEX, .7, (0, 0, 0), 2)
    result = pilot.detect(tmp_path, sample_manifest(tmp_path, images))
    assert len(result['candidates']) == 1
    assert result['candidates'][0]['change_from_ms'] == 4 * 520


def test_persistent_highlight_is_retained(tmp_path):
    base = np.zeros((240, 320, 3), np.uint8)
    base[40:55, 40:180] = (220, 220, 220)
    highlight = base.copy()
    highlight[40:55, 40:180] = (0, 220, 220)
    result = pilot.detect(tmp_path, sample_manifest(tmp_path, [base, base, highlight, highlight]))
    assert len(result['candidates']) == 1


def test_matching_is_one_to_one_and_maximum_cardinality():
    refs = [{'id': 'r0', 'earliest_ms': 0, 'latest_ms': 100},
            {'id': 'r1', 'earliest_ms': 0, 'latest_ms': 10}]
    events = [{'id': 'd0', 'time_ms': 5}, {'id': 'd1', 'time_ms': 80}]
    assert pilot.match_events(refs, events, 0)['matched_count'] == 2
    result = pilot.match_events(refs, events[:1], 0)
    assert result['matched_count'] == 1
    assert len(result['missed']) == 1


@pytest.mark.parametrize('ids', [['c1'], ['c0', 'c0'], []])
def test_model_cannot_invent_omit_or_duplicate_candidate_ids(ids):
    with pytest.raises(ValueError):
        pilot.validate_results({'results': [{'candidate_id': i} for i in ids]}, ['c0'])


def test_original_video_pts_resolution_and_changed_cache(tmp_path):
    video = tmp_path / 'source.avi'
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'MJPG'), 10, (320, 240))
    if not writer.isOpened():
        pytest.skip('MJPG encoder not available')
    for i in range(10):
        frame = np.full((240, 320, 3), 230, np.uint8)
        cv2.putText(frame, str(i), (40, 60), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        writer.write(frame)
    writer.release()
    out = tmp_path / 'out'
    manifest = pilot.sample_video(video, out, start_ms=0, end_ms=1000, interval_ms=200)
    assert [r['time_ms'] for r in manifest['frames']] == [0, 200, 400, 600, 800]
    candidate = {'id': 'c0', 'stable_ms': 200, 'change_from_ms': 0}
    result = pilot.export_originals(out, manifest, {'candidates': [candidate]}, [dict(candidate)])
    event = result[0]
    assert cv2.imread(str(out / event['original_image'])).shape == (240, 320, 3)
    assert event['capture_ms'] == event['decoder_pts_ms']
    (out / manifest['frames'][0]['image']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        pilot.sample_video(video, out, start_ms=0, end_ms=1000, interval_ms=200)


def test_resume_reuses_saved_model_response_and_detects_tampering(tmp_path, monkeypatch):
    from backend.pipeline import slide_pilot_models
    calls = {'generate': 0, 'count': 0}
    class FakeReviewer:
        model = 'fake-test-only'
        config = {}
        input_rate, output_rate = 2.5, 10
        price_source = 'test'
        def __init__(self, *args): pass
        def contents(self, ids, paths): return ids
        def count(self, contents):
            calls['count'] += 1
            return 100
        def generate(self, contents):
            calls['generate'] += 1
            return {'test': contents}
        def text(self, raw):
            import json
            return json.dumps({'results': [{'candidate_id': i, 'kind': 'same_content',
                'confidence': .9, 'label': 'cursor only', 'reason': 'same slide'} for i in raw['test']]})
        def usage(self, raw): return {'estimated_usd_upper': .01}
    monkeypatch.setattr(slide_pilot_models, 'VisionReviewer', FakeReviewer)
    manifest = sample_manifest(tmp_path, [np.full((240, 320, 3), 230, np.uint8)] * 2)
    candidate = {'id': 'c0', 'before_id': manifest['frames'][0]['id'],
                 'after_id': manifest['frames'][1]['id'], 'change_from_ms': 520, 'stable_ms': 520}
    candidates = {'manifest_sha256': pilot.file_sha(tmp_path / 'manifest.json'), 'candidates': [candidate]}
    pilot.review_candidates(tmp_path, manifest, candidates)
    pilot.review_candidates(tmp_path, manifest, candidates)
    assert calls == {'generate': 1, 'count': 1}
    (tmp_path / 'pairs/c0.jpg').write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'):
        pilot.review_candidates(tmp_path, manifest, candidates)
    assert calls['generate'] == 1


def test_budget_ledger_blocks_unknown_outcome_even_for_different_provider(tmp_path):
    request = {'model': 'old-provider'}
    folder = tmp_path / 'calls' / pilot.object_sha(request)
    pilot.save_json(folder / 'request.json', request)
    pilot.save_json(folder / 'state.json', {'status': 'sent_outcome_unknown', 'reserved_usd': .1})
    with pytest.raises(ValueError, match='Unresolved'):
        pilot.call_ledger(tmp_path)
    pilot.save_json(folder / 'state.json', {'status': 'complete', 'reserved_usd': .1})
    assert pilot.call_ledger(tmp_path) == (1, .1)


def test_cost_counts_reasoning_once_for_both_providers():
    adapter = VisionReviewer.__new__(VisionReviewer)
    adapter.provider, adapter.input_rate, adapter.output_rate = 'openai', 2.5, 10
    raw = {'usage': {'input_tokens': 1000, 'output_tokens': 200,
                     'output_tokens_details': {'reasoning_tokens': 100}}}
    assert adapter.usage(raw)['output_including_reasoning_tokens'] == 200
    adapter.provider, adapter.input_rate, adapter.output_rate = 'gemini', .75, 3.75
    raw = {'usage_metadata': {'prompt_token_count': 1000, 'candidates_token_count': 100,
                             'thoughts_token_count': 100, 'total_token_count': 1200}}
    assert adapter.usage(raw)['output_including_reasoning_tokens'] == 200


def test_saved_pilot_excludes_all_cursor_candidates():
    fixture = Path(__file__).parent / 'fixtures/slide_pilot/s210307_first15m_results.json'
    saved = pilot.read_json(fixture)
    results = {r['candidate_id']: r for r in saved['review']['results']}
    assert all(results[i]['kind'] == 'same_content' for i in saved['cursor_or_controls_candidate_ids'])
    assert saved['summary']['exported_state_count'] == 24
