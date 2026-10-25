#!/usr/bin/env python3
"""Run the two-way reviewer on a fixed draft and score it against the owner's findings.

Re-run whenever a reviewer prompt or model changes: a reviewer that stops
finding what the owner found, or starts blocking what the owner did not, is
no longer the reviewer the owner calibrated. One reviewer call; nothing is
revised, written to the knowledge layer, or published.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from backend.pipeline.draft_first_checklist import build_checklist
from backend.pipeline.draft_first_calibration import record, reviewer_fingerprint
from backend.pipeline.draft_first_review_agent import CodexReviewAgent
from backend.pipeline.draft_first_review_runner import REVIEW_MODEL, run_two_way_review
from backend.pipeline.matthew_exposition_authoring import sha256_text


def score(key: dict, findings: list[dict]) -> list[dict]:
    rows = []
    for item in key["items"]:
        if not item.get("pattern"):
            rows.append({"id": item["id"], "result": "manual", "finding": item["finding"]})
            continue
        pattern = re.compile(item["pattern"])
        hits = [f for f in findings if pattern.search(f["anchor"]) or pattern.search(f["summary"])]
        blocking = any(f["blocking"] for f in hits)
        result = "miss" if not hits else "hit" if blocking or not item["expect_blocking"] else "hit_nonblocking"
        rows.append({"id": item["id"], "result": result, "anchors": [f["anchor"][:40] for f in hits]})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, default=ROOT / "backend/config/draft_first_calibration/wkp439-son-of-man-draft-a.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    key = json.loads(args.key.read_text(encoding="utf-8"))
    data = Path(os.environ["DATA_BASE_DIR"])
    texts = {}
    for name in ("draft", "packet"):
        text = (data / key[name]["path"]).read_text(encoding="utf-8")
        if sha256_text(text) != key[name]["sha256"]:
            raise SystemExit(f"calibration {name} changed since the owner scored it: {key[name]['path']}")
        texts[name] = text
    raw = json.loads(texts["packet"])
    packet = raw.get("result", raw)
    profile = json.loads((ROOT / "backend/config/editorial_quality_profiles/WQ-theological-topic-essay-v1.json").read_text())
    review, receipt = run_two_way_review(
        agent=CodexReviewAgent(model=REVIEW_MODEL), manuscript=texts["draft"], packet=packet, profile=profile,
        checklist=build_checklist(packet), task={"round": 0, "self_check": [], "previous_findings": [], "author_responses": []},
        round_dir=args.output_dir, disputed_ids=[],
    )
    rows = score(key, review["findings"])
    entry = record(reviewer_fingerprint(REVIEW_MODEL), key["key_id"], rows, str(args.output_dir))
    report = {"key_id": key["key_id"], "model": receipt["model"], "rows": rows, "agrees": entry["agrees"],
              "findings": len(review["findings"]), "blocking": sum(f["blocking"] for f in review["findings"])}
    (args.output_dir / "calibration.json").write_text(json.dumps({**report, "review": review}, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
