import subprocess
import sys
import pytest
from backend.pipeline.media_batch.retirement import require_supported,RetiredPipeline

@pytest.mark.parametrize('module',['full_video_transcript','transcript_audio_pilot'])
def test_legacy_cli_cannot_start_models_or_change_source(module,tmp_path):
    result=subprocess.run([sys.executable,'-m','backend.pipeline.'+module,'run','--out',str(tmp_path)],capture_output=True,text=True)
    assert result.returncode!=0 and '已退役' in result.stderr
    assert list(tmp_path.iterdir())==[]


def test_only_supported_generation_or_read_only_inventory_is_launchable():
    for cfg in ({},{'mode':'rough'},{'profile':'wang-sermon-v1'}):
        with pytest.raises(RetiredPipeline):require_supported(cfg)
    require_supported({'pipeline':'gemini-transcript-v1'})
    require_supported({'mode':'inventory'})
