"""Regression: compressed WAV packet seeking must not define playback origin."""
import json,subprocess
from pathlib import Path
import numpy as np
import pytest
from backend.pipeline.media_batch.stages import media,playback_anchors
from backend.pipeline.media_batch.runtime import file_sha


def test_adpcm_recording_checks_use_origin_decode_and_retain_strict_lag_gate(tmp_path):
    sr=22050;seconds=24
    # Nonperiodic speech-like source: a shifted clip cannot correlate by chance.
    rng=np.random.default_rng(685)
    a=rng.normal(size=sr*seconds).astype('float32')*.08
    source=tmp_path/'original.wav'
    subprocess.run(['ffmpeg','-nostdin','-v','error','-f','f32le','-ar',str(sr),'-ac','1','-i','-','-c:a','adpcm_ima_wav',str(source)],input=a.tobytes(),check=True)
    duration=float(json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(source)]))['format']['duration'])
    out=tmp_path/'out';out.mkdir()
    payload=dict(info=dict(source=str(source),source_sha=file_sha(source),video=False,bytes=source.stat().st_size,duration=duration,name='fixture'),config=dict(media_root=str(tmp_path/'media'),review_root=str(tmp_path/'review'),capacity_root=str(tmp_path/'capacity'),ssd_reserve_gib=0),item='S',out=str(out))
    result=media(payload)
    assert len(result['anchors'])==3 and all(x.get('correlation',0)>.9 and abs(x['lag_ms'])<10 for x in result['anchors'])
    assert file_sha(source)==payload['info']['source_sha']
    # Independent wrong playback must still fail, without relaxing thresholds.
    bad=tmp_path/'bad';bad.mkdir();badmedia=tmp_path/'badmedia';badmedia.mkdir()
    subprocess.run(['ffmpeg','-nostdin','-v','error','-i',str(source),'-af','adelay=400|400','-c:a','libmp3lame','-b:a','96k',str(badmedia/'.S.partial.mp3')],check=True)
    with pytest.raises(ValueError,match='clock mismatch'):
        playback_anchors(source,badmedia/'.S.partial.mp3',duration)
    assert not (badmedia/'S.mp3').exists()


def test_isolated_editor_link_keeps_correct_host_and_escapes_sermon_id():
    from backend.pipeline.media_batch.stages import editor_link
    assert editor_link('講道 1','http://127.0.0.1:3685')=='http://127.0.0.1:3685/admin/surmons/%E8%AC%9B%E9%81%93%201'
    assert editor_link('S')=='/admin/surmons/S'
    for origin in ('javascript:alert(1)','https://example.com/path','https://user:secret@example.com'):
        with pytest.raises(ValueError):editor_link('S',origin)
