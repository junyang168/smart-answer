"""The reviewer may review only as the owner calibrated it.

A review prompt or model change makes the reviewer a different reviewer: on
2026-10-24 the regrouped prompt still found every patterned owner finding but
missed K5 until a general rule was added. That calibration happened because
someone remembered to run it. Now each run of `scripts/draft-first-calibrate.py`
is recorded under the reviewer's fingerprint -- the prompt as sent, with the
shared two-way rules filled in, and the model -- and a review refuses to start
without a record for its fingerprint that agrees with the owner's key.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from backend.pipeline.matthew_exposition_authoring import sha256_text

PROMPT = Path(__file__).with_name("prompts") / "draft_first_two_way_review.md"


class CalibrationRequired(RuntimeError):
    pass


def records_path() -> Path:
    data = os.environ.get("DATA_BASE_DIR")
    if not data:
        raise CalibrationRequired("DATA_BASE_DIR is required to find the calibration records")
    return Path(data).expanduser() / "wang-knowledge-platform" / "staging" / "draft-first-calibration" / "records.json"


def reviewer_fingerprint(model: str) -> dict[str, str]:
    from backend.pipeline.draft_first_author_runner import prompt_text

    return {"prompt_sha256": sha256_text(prompt_text(PROMPT)), "model": model}


def agrees(rows: list[Mapping[str, Any]]) -> bool:
    """Every owner finding the key can score was found, blocking where the owner blocked.

    A `manual` row has no pattern and is read by the owner; it does not fail
    the gate but is listed with the record.
    """

    return all(row["result"] in ("hit", "manual") for row in rows)


def record(fingerprint: Mapping[str, str], key_id: str, rows: list[Mapping[str, Any]],
           review_dir: str, path: Path | None = None) -> dict[str, Any]:
    path = path or records_path()
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    entry = {**fingerprint, "key_id": key_id, "agrees": agrees(rows), "rows": list(rows),
             "review_dir": review_dir, "calibrated_at": datetime.now(timezone.utc).isoformat()}
    entries.append(entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return entry


def calibration_for(fingerprint: Mapping[str, str], path: Path | None = None) -> dict[str, Any] | None:
    """The latest calibration of exactly this reviewer, whether or not it agreed."""

    path = path or records_path()
    entries = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    matching = [e for e in entries if e["prompt_sha256"] == fingerprint["prompt_sha256"]
                and e["model"] == fingerprint["model"]]
    return matching[-1] if matching else None


def require_calibrated(model: str, path: Path | None = None) -> dict[str, Any]:
    fingerprint = reviewer_fingerprint(model)
    entry = calibration_for(fingerprint, path)
    if entry is None:
        raise CalibrationRequired(
            f"the reviewer ({model}, prompt {fingerprint['prompt_sha256'][:12]}) has not been calibrated; "
            "run scripts/draft-first-calibrate.py first")
    if not entry["agrees"]:
        missed = [row["id"] for row in entry["rows"] if row["result"] not in ("hit", "manual")]
        raise CalibrationRequired(
            f"the reviewer's last calibration disagrees with the owner's key on {', '.join(missed)}; "
            "the owner decides whether the prompt or the key changes")
    return entry
