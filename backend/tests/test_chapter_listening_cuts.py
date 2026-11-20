import hashlib
import json
import shutil
import subprocess

import pytest

from backend.pipeline import chapter_listening_cuts as cuts


PASSAGE = "這是一個完整段落，用於檢查發布資料是否包含所有需要處理的刪除標記。這些文字只供合成測試使用。保留段落在其他地方。"


def test_overlapping_exclusions_leave_one_continuous_media_clock():
    assert cuts.retained_clock([(2, 4), (4, 6), (5, 8)], 10) == [
        {'start': 0, 'end': 2, 'media_start': 0},
        {'start': 8, 'end': 10, 'media_start': 2},
    ]
    assert cuts.retained_clock([(0, 10)], 10) == []
    with pytest.raises(ValueError, match='Invalid'):
        cuts.retained_clock([(5, 11)], 10)


def test_exact_mark_coverage_and_no_audio_do_not_remove_other_sound(tmp_path):
    transcript = tmp_path / 'source.json'
    transcript.write_text(json.dumps({'script': [{'index': 1, 'text': f'~~{PASSAGE}~~'}]}))
    item = {'paragraph': 1, 'ordinal': 0, 'text_sha256': hashlib.sha256(PASSAGE.encode()).hexdigest(),
            'decision': 'no_audio', 'evidence_sha256': 'a' * 64}
    assert cuts._decision_intervals(transcript, {'decisions': [item]}) == ([], [])
    with pytest.raises(ValueError, match='Unreviewed'):
        cuts._decision_intervals(transcript, {'review_candidates': [['1', 0]], 'decisions': []})


def test_post_read_projection_never_crosses_seams_and_preserves_independent_sources(monkeypatch, tmp_path):
    managed = cuts.manifest(tmp_path, 'Managed')
    managed.parent.mkdir(parents=True)
    managed.touch()
    edit = {'media': 'listening-cut-example.mp3', 'media_sha256': 'rendition', 'audio_sha256': 'source',
            'excluded': [(3, 7)], 'clock': cuts.retained_clock([(3, 7)], 10)}
    monkeypatch.setattr(cuts, '_load', lambda *args: edit)
    untouched = {'transcript_id': 'Untouched', 'stretches': [{'start': 0, 'end': 2}], 'seconds': 2}
    row = {'transcript_id': 'Managed', 'source_id': 'managed', 'media_url': '/original.mp3', 'stretches': [{'start': 0, 'end': 10}],
           'transcript': [{'at': 1, 'end': 7, 'text': 'left'}, {'at': 7, 'end': 9, 'text': 'right'}],
           'slides': [{'at': 0, 'seconds': 10, 'title': 'Context', 'points': [{'at': 1}, {'at': 5}, {'at': 8}],
                       'diagram': {'at': 6}}]}
    index = {'resume_version': 'v1', 'sections': [{'sermons': [row, untouched]}]}
    out = cuts.protect(index, tmp_path)
    safe, other = out['sections'][0]['sermons']
    assert other == untouched
    assert safe['media_url'].endswith(edit['media'])
    assert [s['media_start'] for s in safe['stretches']] == [0, 3]
    assert safe['seconds'] == 6
    assert safe['transcript'] == [{'at': 1, 'end': 3, 'text': 'left'}, {'at': 7, 'end': 9, 'text': 'right'}]
    assert [s['points'] for s in safe['slides']] == [[{'at': 1}], [{'at': 1}, {'at': 8}]]
    assert all('diagram' not in slide for slide in safe['slides'])
    assert not cuts.playback_problems(out)
    assert cuts.protect({'sections': [{'sermons': [untouched]}]}, tmp_path)['sections'][0]['sermons'][0] is untouched
    def stale(*args):
        raise ValueError('changed audio')
    monkeypatch.setattr(cuts, '_load', stale)
    assert cuts.protect(index, tmp_path)['sections'][0]['sermons'] == [untouched]


@pytest.mark.skipif(not all(shutil.which(program) for program in ('ffmpeg', 'ffprobe')), reason='media tools unavailable')
def test_rendition_removes_the_actual_middle_audio(tmp_path):
    source = tmp_path / 'source.wav'
    dest = tmp_path / 'edited.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'aevalsrc=if(between(t\\,1\\,2)\\,0.7*sin(2*PI*997*t)\\,0):s=44100:d=3',
                    str(source)], check=True)
    cuts._encode(source, dest, cuts.retained_clock([(1, 2)], 3), False)
    decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(dest), '-f', 's16le', '-'])
    assert max(abs(int.from_bytes(decoded[i:i+2], 'little', signed=True)) for i in range(0, len(decoded), 2)) < 128
    assert abs(cuts._duration(dest)[0] - 2) < cuts.TOLERANCE

def test_cut_tolerance_requires_a_verified_pause(tmp_path):
    proof = tmp_path / 'proof.json'
    proof.write_text(json.dumps({
        'audio_sha256': 'audio', 'published_transcript_sha256': 'transcript',
        'source_paragraph_index': 1, 'removed_source_interval': [2, 5],
        'safe_cut_intervals': [[1.99, 2.9], [5, 5.25]],
        'review': {'left': 'pass', 'right': 'pass'},
    }))
    decision = {'paragraph': 1, 'ordinal': 0, 'decision': 'skip',
                'evidence_file': proof.name, 'evidence_sha256': cuts.file_sha(proof),
                'interval': [2.8, 5.2]}
    report = {'audio_sha256': 'audio', 'transcript_sha256': 'transcript', 'decisions': [decision]}
    cuts._verify_evidence(report, tmp_path)
    decision['interval'] = [1.7, 5.2]
    with pytest.raises(ValueError, match='passing review'):
        cuts._verify_evidence(report, tmp_path)
    decision['interval'] = [2.1, 5.3]
    with pytest.raises(ValueError, match='passing review'):
        cuts._verify_evidence(report, tmp_path)


@pytest.mark.skipif(not all(shutil.which(program) for program in ('ffmpeg', 'ffprobe')), reason='media tools unavailable')
def test_activation_reuses_previous_bytes_and_rejects_source_change(tmp_path):
    active, staged = (tmp_path / part / 'data' for part in ('active', 'staged'))
    video = active.parent / 'video'
    video.mkdir(parents=True)
    source = video / 'Lecture.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=500:duration=22',
                    str(source)], check=True)
    transcript = active / 'script_published' / 'Lecture.json'
    transcript.parent.mkdir(parents=True)
    transcript.write_text(json.dumps({'script': [{'index': 1, 'text': f'~~{PASSAGE}~~'}]}))
    (staged / 'script_published').mkdir(parents=True)
    (staged / 'script_published' / transcript.name).symlink_to(transcript)
    (staged.parent / 'video').mkdir()
    (staged.parent / 'video' / source.name).symlink_to(source)
    proof = tmp_path / 'proof.json'
    source_sha, transcript_sha = cuts.file_sha(source), cuts.file_sha(transcript)
    proof.write_text(json.dumps({
        'audio_sha256': source_sha, 'published_transcript_sha256': transcript_sha,
        'source_paragraph_index': 1, 'removed_source_interval': [2, 18],
        'review': {'left': 'pass', 'right': 'pass'},
    }))
    decisions = tmp_path / 'decisions.json'
    decisions.write_text(json.dumps({
        'audio_sha256': source_sha, 'transcript_sha256': transcript_sha,
        'decisions': [{'paragraph': 1, 'ordinal': 0, 'decision': 'skip', 'interval': [2, 18],
                       'text_sha256': hashlib.sha256(PASSAGE.encode()).hexdigest(),
                       'evidence_file': proof.name, 'evidence_sha256': cuts.file_sha(proof)}],
    }))
    prepared = cuts.prepare(active, 'Lecture', decisions, staged)
    cuts.activate(active, staged, 'Lecture', None)
    active_manifest = cuts.manifest(active, 'Lecture')
    digest = cuts.file_sha(active_manifest)
    cuts.activate(active, staged, 'Lecture', None)
    assert cuts.file_sha(active_manifest) == digest
    selected = cuts._load(active, 'Lecture', active_manifest)
    assert selected['media'] == prepared['media']
    transcript.write_text(transcript.read_text() + '\n')
    with pytest.raises(ValueError, match='source changed'):
        cuts.activate(active, staged, 'Lecture', digest)
    assert cuts.file_sha(active_manifest) == digest


def test_caption_spanning_an_intra_sentence_cut_uses_both_verified_sides():
    caption = {'at': 1, 'end': 9, 'text': '前一句，後一句。'}
    seam = {'interval': [3, 7], 'left': '前一句', 'right': '後一句'}
    result, unresolved = cuts._split_captions([caption], [seam])
    assert not unresolved
    assert result == [{'at': 1, 'end': 3, 'text': '前一句，'},
                      {'at': 7, 'end': 9, 'text': '後一句。'}]
    assert cuts._split_captions([caption], [{**seam, 'right': '錯句'}]) == ([], True)


def test_unavailable_edited_source_is_removed_from_plan_before_other_sources_load(tmp_path):
    bad_manifest = cuts.manifest(tmp_path, 'Bad')
    bad_manifest.parent.mkdir(parents=True)
    bad_manifest.write_text('{"incomplete":true}')
    plan = {'inputs': [{'lecture': 'Bad', 'source_id': 'old'}, {'lecture': 'Good', 'source_id': 'new'}],
            'units': {'both': {'groups': [{'key': 'a', 'source_id': 'old'}, {'key': 'b', 'source_id': 'new'}],
                              'sub_units': [{'id': '1.1', 'group_keys': ['a']}, {'id': '1.2', 'group_keys': ['b']}],
                              'playback_order': [{'group_key': 'a'}, {'group_key': 'b'}]}}}
    path = tmp_path / 'chapter_exposition' / 'mat-5-1-48' / 'listening-units.json'
    path.parent.mkdir()
    path.write_text(json.dumps(plan))
    filtered, held = cuts.available_plan(tmp_path, 'mat-5-1-48')
    assert held == ['old']
    assert filtered['inputs'] == [plan['inputs'][1]]
    assert filtered['units']['both']['sub_units'] == [plan['units']['both']['sub_units'][1]]
    assert json.loads(path.read_text()) == plan

def test_cut_threshold_uses_duration_instead_of_character_count(tmp_path):
    transcript = tmp_path / 'source.json'
    marks = ('少字但慢', '剛好十五秒', '字數較多而且語速快所以沒有超過門檻')
    transcript.write_text(json.dumps({'script': [
        {'index': i, 'text': f'~~{words}~~'} for i, words in enumerate(marks, 1)
    ]}))
    raw = tmp_path / 'raw.json'
    raw.write_text(json.dumps({'entries': [
        {'index': 1, 'text': marks[0], 'start_ms': 0, 'end_ms': 15001},
        {'index': 2, 'text': marks[1], 'start_ms': 16000, 'end_ms': 31000},
        {'index': 3, 'text': marks[2], 'start_ms': 32000, 'end_ms': 46999},
    ]}))
    candidates = cuts._candidate_decisions(transcript, raw, 50)
    assert [candidate['paragraph'] for candidate in candidates] == [1]
    candidate = {**candidates[0], 'evidence_sha256': 'a' * 64, 'decision': 'skip'}
    report = {'review_candidates': [['1', 0]], 'decisions': [candidate]}
    assert cuts._decision_intervals(transcript, report) == ([[0, 15.001]], [])
    candidate['interval'] = [0, 15]
    with pytest.raises(ValueError, match='more than 15 seconds'):
        cuts._decision_intervals(transcript, report)
    candidate['decision'] = 'keep'
    assert cuts._decision_intervals(transcript, report) == ([], [])


def test_retained_slide_and_scripture_follow_corrected_source_before_cut():
    row = {
        'media_url': '/original.mp3',
        'stretches': [{'start': 3474.7, 'end': 3663.8}],
        'transcript': [{'at': 3474.7, 'end': 3593.37, 'text': '開頭'},
                       {'at': 3593.37, 'end': 3606.58, 'text': '經文'},
                       {'at': 3606.58, 'end': 3663.8, 'text': '休書'}],
        'slides': [{'at': 3474.7, 'seconds': 189.1, 'title': '再婚',
                    'points': [{'at': 3606.58, 'text': '休書'}], 'scriptures': [{'at': 3593.37}]}],
        'judgements': [],
    }
    edit = {'media': 'listening-cut-example.mp3', 'audio_sha256': 'source',
            'excluded': [(3604.9, 3663.8)], 'clock': cuts.retained_clock([(3604.9, 3663.8)], 3663.8),
            'sync_windows': [{'anchors': [[3474.7, 3479.985], [3593.37, 3554.985],
                                         [3606.58, 3562.985], [3663.8, 3604.9]]}]}
    result = cuts._project_row(row, edit)
    last = result['transcript'][-1]
    slide, = result['slides']
    assert last['at'] == slide['points'][0]['at'] == pytest.approx(3562.985, abs=.011)
    assert result['transcript'][1]['at'] == slide['scriptures'][0]['at'] == pytest.approx(3554.985, abs=.011)
    assert last['end'] == result['stretches'][0]['end'] == 3604.9
    assert result['stretches'][0]['start'] == 3474.7
