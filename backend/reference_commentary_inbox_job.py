"""launchd entry point for the reference-commentary scan inbox (WKP-F11.02).

`scripts/deploy.sh` points the LaunchAgent at this file in each release, the
same way it rebinds the fellowship reminder. The work is in
`backend.reference_commentary.inbox`.
"""

from __future__ import annotations

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / "backend" / ".env")
load_dotenv(REPO_ROOT / ".env")

from backend.reference_commentary.inbox import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
