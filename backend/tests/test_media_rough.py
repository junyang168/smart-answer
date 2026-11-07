"""Real PCM/MP3 regeneration and saved-answer reuse; no paid model calls."""

import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from backend.pipeline import full_video_transcript as full
from backend.pipeline import transcript_audio_pilot as audio
from backend.pipeline.media_batch import rough
from backend.pipeline.media_batch.runtime import atomic, file_sha


def test_mp3_cache_survives_pcm_cleanup_and_full_pipeline_reuses_without_calls(
    tmp_path, monkeypatch
):
    source = tmp_path / "original.wav"
    audio.write_audio(source, (np.sin(np.arange(16000 * 8) * 0.1) * 4000).astype("<i2"))
    out = tmp_path / "rough"
    master = full.prepare(source, out)
    window = out / master["windows"][0]["audio"]
    calls = []

    def invoke(**kwargs):
        calls.append(kwargs["file"].name)
        return SimpleNamespace(
            model_dump=lambda **kw: dict(text="粗逐字稿，原聲內容。")
        )

    monkeypatch.setattr(
        "openai.OpenAI",
        lambda **kw: SimpleNamespace(
            audio=SimpleNamespace(transcriptions=SimpleNamespace(create=invoke))
        ),
    )
    result = audio.transcribe(window, transport="mp3")
    assert len(calls) == 1 and calls[0].endswith(".mp3")
    atomic(out / "rough.json", dict(chunks=result["chunks"]))
    before = file_sha(window / "asr.json")
    manifest_sha = file_sha(out / "master.json")
    rough.cleanup_audio(out)
    assert not list(out.rglob("*.wav")) and list(out.rglob("*.mp3"))
    assert source.exists()
    full.prepare(source, out)
    assert file_sha(out / "master.json") == manifest_sha
    result2 = audio.transcribe(window)
    assert result2 == result and file_sha(window / "asr.json") == before
    assert len(calls) == 1
    mp3=next(out.rglob('*.mp3'));mp3_before=mp3.read_bytes();mp3.write_bytes(mp3_before+b'changed')
    with pytest.raises(ValueError,match='MP3 transport changed'):audio.transcribe(window)
    assert len(calls)==1
    mp3.write_bytes(mp3_before)
    # A changed source cannot authorize regenerating or resending cached audio.
    rough.cleanup_audio(out)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="changed rough source"):
        full.prepare(source, out)
    assert len(calls) == 1


def test_pcm_cleanup_preserves_changed_files_and_needs_all_windows(tmp_path):
    source = tmp_path / "original.wav"
    audio.write_audio(source, np.zeros(16000, dtype="<i2"))
    out = tmp_path / "out"
    master = full.prepare(source, out)
    w = out / master["windows"][0]["audio"]
    with pytest.raises(ValueError, match="ASR not complete"):
        rough.cleanup_audio(out)
    atomic(
        w / "asr.json",
        dict(
            manifest_sha256=file_sha(w / "audio_manifest.json"),
            chunks=json.loads((w / "audio_manifest.json").read_text())["chunks"],
        ),
    )
    (w / "audio.wav").write_bytes(b"changed")
    with pytest.raises(ValueError, match="Changed PCM"):
        rough.cleanup_audio(out)
    assert (out / "master.wav").exists() and (
        w / "audio.wav"
    ).read_bytes() == b"changed"


def test_raw_aac_uses_decoded_samples_not_estimated_container_duration(tmp_path):
    import subprocess

    source = tmp_path / "original.wav"
    audio.write_audio(
        source, (np.sin(np.arange(16000 * 10) * 0.1) * 4000).astype("<i2")
    )
    aac = tmp_path / "audio.aac"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(source), "-c:a", "aac", str(aac)],
        check=True,
    )
    master = full.prepare(aac, tmp_path / "prepared")
    assert master["probe"]["format"]["format_name"] == "aac"
    assert 9900 < master["duration_ms"] < 10300


def test_movie_picture_tail_does_not_reject_complete_audio_track(tmp_path):
    import subprocess
    source=tmp_path/'speech.wav';audio.write_audio(source,np.zeros(16000,dtype='<i2'))
    movie=tmp_path/'movie.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=s=64x64:r=25:d=1.5','-i',str(source),'-c:v','libx264','-c:a','aac',str(movie)],check=True)
    master=full.prepare(movie,tmp_path/'prepared')
    assert 900<master['duration_ms']<1200
    assert float(master['probe']['format']['duration'])>1.4


def test_delayed_first_picture_keeps_zero_origin_audio_clock(tmp_path):
    import subprocess
    source=tmp_path/'speech.wav';audio.write_audio(source,np.zeros(16000,dtype='<i2'))
    movie=tmp_path/'delayed.mp4'
    subprocess.run(['ffmpeg','-v','error','-itsoffset','0.08','-f','lavfi','-i','color=s=64x64:r=25:d=1.5','-i',str(source),'-c:v','libx264','-c:a','aac',str(movie)],check=True)
    master=full.prepare(movie,tmp_path/'prepared')
    assert master['source_clock_mapping']['container_start_s']==0
    assert master['source_clock_mapping']['video_start_s']>=.07
    assert 900<master['duration_ms']<1200
