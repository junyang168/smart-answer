from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("existing", [True, False])
def test_replay_refuses_existing_or_production_output_before_loading_credentials(tmp_path, existing):
    script = Path(__file__).resolve().parents[2] / "scripts/simulate-sunday-form.py"
    output = tmp_path if existing else Path("/opt/homebrew/var/www/not-a-real-simulation-output")
    result = subprocess.run([sys.executable, str(script), "--scenario", str(tmp_path / "absent.json"), "--output", str(output), "--model-config", str(tmp_path / "absent.env")], capture_output=True, text=True)
    assert result.returncode == 2
    assert ("Output must be a new directory" if existing else "Production paths are not permitted") in result.stderr
