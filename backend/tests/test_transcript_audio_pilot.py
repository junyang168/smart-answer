from pathlib import Path

import numpy as np
import pytest

from backend.pipeline.transcript_audio_pilot import prepare, quiet_boundaries, read_audio, saved_call, write_audio
from backend.pipeline.transcript_text_layout import (
    apply_layout, inspect_timings, text_sha, text_units, token_spans,
)
from backend.pipeline.transcript_slide_assembly import paragraph_times, place_slides
from backend.pipeline.slide_change_pilot import file_sha, read_json


def layout_plan(text, operations=None, boundaries=None):
    units = text_units(text)
    return units, {'text_sha256': text_sha(text), 'operations': operations or [],
                   'paragraph_break_after': boundaries or [units[-1]['id']]}


def test_pcm_roundtrip_and_quiet_chunk_core_partition(tmp_path):
    # Quiet gap around the target, with a loud waveform that would overflow an
    # int16 square. No samples disappear; the core windows partition the source.
    samples = np.full(400 * 16000, 20000, dtype=np.int16)
    samples[178 * 16000:182 * 16000] = 0
    path = tmp_path / 'test.wav'
    write_audio(path, samples)
    assert np.array_equal(read_audio(path), samples)
    boundaries = quiet_boundaries(samples)
    assert boundaries[0]['sample'] == 0
    assert boundaries[-1]['sample'] == len(samples)
    assert boundaries[1]['sample'] == 180 * 16000
    assert boundaries[1]['kind'] == 'energy_quiet_midpoint'
    assert sum(b['sample'] - a['sample'] for a, b in zip(boundaries, boundaries[1:])) == len(samples)


def test_no_quiet_gap_is_explicit_not_a_fake_vad_success():
    samples = np.full(400 * 16000, 16000, dtype=np.int16)
    assert quiet_boundaries(samples)[1]['kind'] == 'hard_boundary_requires_review'


def test_cjk_and_foreign_token_spans_preserve_combining_marks():
    text = '你們切慕 ζηλοῦτε、e\u0301。'
    spans = token_spans(text, ['你', '們', '切', '慕', 'ζηλοῦτε', 'e'])
    assert text[slice(*spans[-1])] == 'e\u0301'
    with pytest.raises(ValueError, match='mapped'):
        token_spans(text, ['你', '錯'])


def test_raw_bad_timings_stay_unresolved_and_foreign_is_phrase_only():
    items = [{'text': '甲', 'start_ms': 100, 'end_ms': 200},
             {'text': '乙', 'start_ms': 200, 'end_ms': 200},
             {'text': '丙', 'start_ms': 100, 'end_ms': 160},
             {'text': 'ζηλοῦτε', 'start_ms': 0, 'end_ms': 0},
             {'text': '丁', 'start_ms': 900, 'end_ms': 1100}]
    assert inspect_timings(items, 1000) == ['word', 'invalid_or_zero_duration', 'non_monotonic',
                                           'foreign_phrase_only', 'invalid_or_zero_duration']


def test_punctuation_at_unit_end_stays_in_own_paragraph():
    text = '甲，乙。'
    units, plan = layout_plan(text, [{'unit_id': 'u0000', 'offset': 1, 'delete': '，', 'insert': '。'}],
                              ['u0000', 'u0001'])
    value = apply_layout(text, units, plan)
    assert [p['text'] for p in value['paragraphs']] == ['甲。', '乙。']
    # Insertion at the exact end of a unit belongs to that unit, not the next.
    plan['operations'] = [{'unit_id': 'u0000', 'offset': 2, 'delete': '', 'insert': '「'}]
    assert apply_layout(text, units, plan)['paragraphs'][0]['text'] == '甲，「'


@pytest.mark.parametrize('old,new', [('不', ''), ('', '所以'), ('甲', '乙')])
def test_layout_cannot_add_delete_or_correct_spoken_words(old, new):
    text = '不可以。'
    units, plan = layout_plan(text, [{'unit_id': 'u0000', 'offset': 0, 'delete': old, 'insert': new}])
    with pytest.raises(ValueError):
        apply_layout(text, units, plan)


def test_decimal_reference_abbreviation_and_diacritics_are_protected():
    for text, at, old in [('1.5。', 1, '.'), ('14:12。', 2, ':'), ('e.g. 結束。', 3, '.')]:
        units, plan = layout_plan(text, [{'unit_id': 'u0000', 'offset': at, 'delete': old, 'insert': '，'}])
        with pytest.raises(ValueError, match='protected'):
            apply_layout(text, units, plan)
    text = 'e\u0301。'
    units, plan = layout_plan(text, [{'unit_id': 'u0000', 'offset': 1, 'delete': '', 'insert': '，'}])
    with pytest.raises(ValueError, match='combining'):
        apply_layout(text, units, plan)


def test_layout_requires_revision_binding_order_and_nonoverlapping_operations():
    text = '甲，乙。'
    units, plan = layout_plan(text)
    plan['text_sha256'] = 'changed'
    with pytest.raises(ValueError, match='revision'):
        apply_layout(text, units, plan)
    plan['text_sha256'] = text_sha(text)
    plan['paragraph_break_after'] = ['u0001', 'u0000']
    with pytest.raises(ValueError, match='ordered'):
        apply_layout(text, units, plan)
    plan['paragraph_break_after'] = ['u0001']
    plan['operations'] = [{'unit_id': 'u0000', 'offset': 1, 'delete': '，', 'insert': '。'}] * 2
    with pytest.raises(ValueError, match='Overlapping'):
        apply_layout(text, units, plan)


def test_repeated_speech_is_preserved_and_unknown_times_are_not_interpolated():
    text = '不要，不要。'
    units, plan = layout_plan(text)
    paragraphs = apply_layout(text, units, plan)['paragraphs']
    result = paragraph_times(paragraphs, [])
    assert result[0]['text'] == text
    assert result[0]['start_ms'] is None and result[0]['timing_precision'] == 'unresolved'


def test_visual_occurrences_are_not_deduplicated_or_split_into_speech():
    paragraphs = [{'id': 'p0', 'start_ms': 100, 'end_ms': 500},
                  {'id': 'p1', 'start_ms': 1000, 'end_ms': 1600}]
    events = [{'id': 's0', 'time_ms': 0}, {'id': 's1', 'time_ms': 300},
              {'id': 's2', 'time_ms': 700}, {'id': 's3', 'time_ms': 1500}]
    result = place_slides(paragraphs, events)
    assert [r['paragraph_id'] for r in result] == ['p0', 'p0', 'p1', 'p1']
    assert [r['time_ms'] for r in result] == [0, 300, 700, 1500]
    assert result[2]['distance_to_paragraph_interval_ms'] == 300


def test_saved_calls_resume_and_stop_on_unknown_or_tampered_outcomes(tmp_path):
    calls = []
    request = {'test': 'bound input'}
    def invoke():
        calls.append(1)
        return {'text': 'result'}
    assert saved_call(tmp_path, request, .1, invoke) == {'text': 'result'}
    assert saved_call(tmp_path, request, .1, invoke) == {'text': 'result'}
    assert len(calls) == 1
    response = next((tmp_path / 'calls').glob('*/response.json'))
    response.write_text('{}')
    with pytest.raises(ValueError, match='changed'):
        saved_call(tmp_path, request, .1, invoke)
    other = tmp_path / 'other'
    def unknown():
        raise TimeoutError('test only')
    with pytest.raises(TimeoutError):
        saved_call(other, request, .1, unknown)
    with pytest.raises(ValueError, match='unknown'):
        saved_call(other, {'different': True}, .1, invoke)
    assert len(calls) == 1


def test_pilot_budget_stops_before_paid_call(tmp_path):
    def forbidden():
        raise AssertionError('must not call')
    with pytest.raises(ValueError, match='limit'):
        saved_call(tmp_path, {'test': 1}, 4, forbidden)


def test_delayed_audio_keeps_its_movie_offset(tmp_path):
    import subprocess
    video = tmp_path / 'offset.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
        'color=c=black:s=320x240:r=25:d=4', '-itsoffset', '0.5', '-f', 'lavfi', '-i',
        'sine=frequency=440:sample_rate=44100:duration=3.5', '-c:v', 'mpeg4', '-c:a', 'aac',
        '-t', '4', str(video)], check=True)
    out = tmp_path / 'pilot'
    manifest = prepare(video, out, duration_ms=4000)
    audio = read_audio(out / 'audio.wav').astype(np.float32)
    assert len(audio) == 4 * 16000
    assert np.max(np.abs(audio[:4800])) < 10
    assert np.max(np.abs(audio[9600:11200])) > 100
    assert next(s for s in manifest['streams'] if s['codec_type'] == 'audio')['start_time'] != '0.000000'


def test_cli_keeps_the_isolated_venv_interpreter_symlink(tmp_path, monkeypatch):
    import sys
    from backend.pipeline import transcript_audio_pilot as audio_pilot
    python = tmp_path / 'venv/bin/python'
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    calls = []
    monkeypatch.setattr(audio_pilot.subprocess, 'run', lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(sys, 'argv', ['pilot', 'align', '--out', str(tmp_path), '--aligner-python', str(python)])
    audio_pilot.main()
    assert calls[0][0] == str(python.absolute())
    assert calls[0][0] != str(python.resolve())
