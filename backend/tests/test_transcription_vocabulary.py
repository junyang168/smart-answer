import copy
from pathlib import Path

import pytest

from backend.pipeline.transcription_vocabulary import (
    asr_vocabulary_config, load_vocabulary, validate_vocabulary, vocabulary_binding,
    vocabulary_from_manifest,
)
from backend.pipeline.transcript_audio_pilot import ASR_CONFIG, transcribe
from backend.pipeline.slide_change_pilot import read_json, save_json
from backend.pipeline.full_video_transcript import configure_vocabulary


PROFILE = Path(__file__).parents[1] / 'pipeline/policies/wang_transcription_vocabulary_v1.json'


def test_hints_preserve_baseline_and_never_prime_error_spellings():
    original = copy.deepcopy(ASR_CONFIG)
    vocabulary = load_vocabulary(PROFILE)
    config = asr_vocabulary_config(ASR_CONFIG, vocabulary)
    assert config['model'] == ASR_CONFIG['model']
    assert config['extra_body']['languages'] == ASR_CONFIG['extra_body']['languages']
    assert '耶穌' in config['extra_body']['keywords']
    assert 'Asbury' in config['extra_body']['keywords']
    assert '黃教授' not in config['prompt'] and '阿特蘭大' not in config['prompt']
    assert ASR_CONFIG == original == asr_vocabulary_config(ASR_CONFIG, None)


@pytest.mark.parametrize('bad', ['<bad>', 'two\nlines', '', 12])
def test_invalid_keywords_stop_before_model_call(bad):
    profile = load_vocabulary(PROFILE)
    profile['entries'][0]['term'] = bad
    with pytest.raises(ValueError):
        validate_vocabulary(profile)


def test_profile_is_fixed_before_audio_work_and_cannot_silently_change(tmp_path):
    save_json(tmp_path / 'master.json', {'windows': []})
    configure_vocabulary(tmp_path, PROFILE)
    bound = read_json(tmp_path / 'master.json')
    assert bound['vocabulary_hint'] == vocabulary_binding(load_vocabulary(PROFILE))
    configure_vocabulary(tmp_path, PROFILE)
    assert read_json(tmp_path / 'master.json') == bound
    modified = load_vocabulary(PROFILE)
    modified['entries'][0]['term'] = '別人'
    other = tmp_path / 'other.json'; save_json(other, modified)
    with pytest.raises(ValueError, match='fresh output'):
        configure_vocabulary(tmp_path, other)
    save_json(tmp_path / 'master.json', {'windows': []})
    save_json(tmp_path / 'windows/w00/audio/asr.json', {})
    with pytest.raises(ValueError, match='fresh output'):
        configure_vocabulary(tmp_path, PROFILE)


def test_existing_asr_cannot_be_overwritten_by_a_changed_hint(tmp_path):
    save_json(tmp_path / 'asr.json', {'config': ASR_CONFIG, 'chunks': []})
    with pytest.raises(ValueError, match='fresh output'):
        transcribe(tmp_path, vocabulary=load_vocabulary(PROFILE))


def test_saved_hint_cannot_drift_or_be_applied_to_a_different_speaker():
    manifest = {'speaker_name': '王教授', 'vocabulary_hint': vocabulary_binding(load_vocabulary(PROFILE))}
    assert vocabulary_from_manifest(manifest) == load_vocabulary(PROFILE)
    with pytest.raises(ValueError, match='another speaker'):
        vocabulary_from_manifest({**manifest, 'speaker_name': '另一位講者'})
    manifest['vocabulary_hint']['profile']['entries'][0]['term'] = '別人'
    with pytest.raises(ValueError, match='binding changed'):
        vocabulary_from_manifest(manifest)


def test_full_video_passes_the_same_profile_to_both_audio_stages(tmp_path, monkeypatch):
    from backend.pipeline import full_video_transcript as module
    profile = load_vocabulary(PROFILE)
    save_json(tmp_path / 'master.json', {'vocabulary_hint': vocabulary_binding(profile),
        'windows': [{'id': 'w00', 'audio': 'windows/w00/audio', 'origin_sample': 0}]})
    seen = []
    monkeypatch.setattr(module, 'transcribe', lambda *a, **kw: seen.append(('asr', kw['vocabulary'])))
    monkeypatch.setattr(module, 'correct', lambda *a, **kw: seen.append(('correction', kw['vocabulary'])))
    for name in ('align', 'stitch', 'format_text', 'assemble', 'join_window_visuals', 'clean_full_transcript'):
        monkeypatch.setattr(module, name, lambda *a: None)
    monkeypatch.setattr(module, 'local_slides', lambda *a: tmp_path / 'slides')
    module.process_windows(tmp_path, Path('unused-aligner'), None)
    assert seen == [('asr', profile), ('correction', profile)]
    with pytest.raises(ValueError, match='fresh speech'):
        module.process_windows(tmp_path, Path('unused-aligner'), Path('old-pilot'))
