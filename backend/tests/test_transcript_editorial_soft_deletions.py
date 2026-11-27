import hashlib

import pytest

from backend.pipeline.slide_change_pilot import file_sha, save_json
from backend.pipeline.transcript_editorial_soft_deletions import editor_paragraphs
from backend.pipeline.transcript_text_layout import text_sha


def test_excluded_untimed_paragraph_keeps_exact_text_and_original_audio_context(tmp_path):
    hidden = '無關的口語。'
    window = tmp_path/'windows/w00'
    save_json(tmp_path/'master.json', dict(source_sha256='source', windows=[
        dict(id='w00', origin_sample=16000, end_sample=160000)]))
    asr = window/'audio/asr.json'
    save_json(asr, dict(chunks=[dict(id='chunk-001', sample_start=0, sample_end=64000)]))
    save_json(window/'audio/timed_text.json', dict(text=hidden, text_sha256=text_sha(hidden),
        asr_sha256=file_sha(asr), chunk_ownership=[dict(chunk_id='chunk-001', output_start=0, output_end=len(hidden))]))
    save_json(window/'correction/correction_trial.json', dict(original_paragraphs=[
        dict(id='p1', source_start=0, source_end=len(hidden))]))
    record = dict(source_sha256='source', duration_ms=10000, paragraphs=[],
        uncut_corrected_paragraphs=[dict(id='w00-p1', text=hidden, start_ms=None, end_ms=None)],
        editorial_exclusions=[dict(paragraph_id='w00-p1', start=0, end=len(hidden), old=hidden,
                                  source_text_sha256=hashlib.sha256(hidden.encode()).hexdigest())])
    with pytest.raises(ValueError, match='no source playback range'):
        editor_paragraphs(record)
    rows, _ = editor_paragraphs(record, tmp_path)
    assert rows[0]['text'] == '~~' + hidden + '~~' and rows[0]['excluded_only']
    assert (rows[0]['start_ms'], rows[0]['end_ms']) == (1000, 5000)
    assert rows[0]['timing_precision'] == 'excluded_original_asr_context'
    assert rows[0]['excluded_source_context']['chunks'] == ['chunk-001']
    assert record['uncut_corrected_paragraphs'][0]['start_ms'] is None and record['paragraphs'] == []
    with pytest.raises(ValueError, match='identity changed'):
        editor_paragraphs({**record, 'source_sha256': 'changed'}, tmp_path)
