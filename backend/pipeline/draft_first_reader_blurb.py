"""The card intro and opening summary of a finished topic essay (#509).

One model call reads the final manuscript and writes two short texts readers
see: `reader_intro` on the repository card, to make a reader open the essay,
and `reader_summary` at the top of the essay, its core viewpoints in order.
Both are derived from the manuscript alone and bound to its SHA; the owner
sees them on the review page with the manuscript and publishes them together.

The checks are the mechanical ones: lengths, item count, and that every 「」
quotation is text the manuscript actually has. A failed attempt is retried
once with its problems; a second failure leaves no output.

    python -m backend.pipeline.draft_first_reader_blurb --manuscript final.md --output reader-blurb.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.matthew_exposition_authoring import sha256_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = Path(__file__).with_name("prompts") / "draft_first_reader_blurb.md"
SCHEMA_VERSION = "wang_draft_first_reader_blurb_v1"
INTRO_MAX = 80
SUMMARY_ITEMS = (3, 5)
SUMMARY_ITEM_MAX = 60

BLURB_SCHEMA: dict[str, Any] = {
    "name": SCHEMA_VERSION,
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "reader_intro": {"type": "string"},
            "reader_summary": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["reader_intro", "reader_summary"],
    },
}


def _length(text: str) -> int:
    return len(re.sub(r"\s", "", text))


def _plain(markdown: str) -> str:
    """Manuscript text a quotation is matched against: no markup, no whitespace."""

    text = re.sub(r"<!--.*?-->", "", markdown, flags=re.S)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[\s>*#_`]", "", text)


def blurb_problems(intro: str, summary: list[str], manuscript: str) -> list[str]:
    problems = []
    if not intro.strip():
        problems.append("reader_intro 是空的")
    elif _length(intro) > INTRO_MAX:
        problems.append(f"reader_intro 有 {_length(intro)} 字，超过 {INTRO_MAX} 字")
    if not SUMMARY_ITEMS[0] <= len(summary) <= SUMMARY_ITEMS[1]:
        problems.append(f"reader_summary 有 {len(summary)} 条，应为 {SUMMARY_ITEMS[0]}–{SUMMARY_ITEMS[1]} 条")
    for index, item in enumerate(summary, 1):
        if not item.strip():
            problems.append(f"reader_summary 第 {index} 条是空的")
        elif _length(item) > SUMMARY_ITEM_MAX:
            problems.append(f"reader_summary 第 {index} 条有 {_length(item)} 字，超过 {SUMMARY_ITEM_MAX} 字")
    body = _plain(manuscript)
    for text in [intro, *summary]:
        for quoted in re.findall(r"[「『]([^「」『』]+)[」』]", text):
            if re.sub(r"\s", "", quoted) not in body:
                problems.append(f"引号里的「{quoted}」不是文章里逐字出现的文字")
    return problems


def write_blurb(manuscript_path: Path, output: Path, client: Any) -> dict[str, Any]:
    manuscript = manuscript_path.read_text(encoding="utf-8")
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    attempts = []
    for _ in range(2):
        payload: dict[str, Any] = {"manuscript_markdown": manuscript}
        if attempts:
            payload["previous_attempt"] = attempts[-1]
        result = client.generate_json(prompt, json.dumps(payload, ensure_ascii=False), BLURB_SCHEMA)
        intro = str(result.get("reader_intro") or "").strip()
        summary = [str(item).strip() for item in result.get("reader_summary") or []]
        problems = blurb_problems(intro, summary, manuscript)
        attempts.append({"reader_intro": intro, "reader_summary": summary, "problems": problems})
        if not problems:
            break
    record = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manuscript_sha256": sha256_text(manuscript),
        "prompt_sha256": sha256_text(prompt),
        "model": client.model,
        "backend": client.backend,
        "reader_intro": attempts[-1]["reader_intro"],
        "reader_summary": attempts[-1]["reader_summary"],
        "attempts": attempts,
    }
    if attempts[-1]["problems"]:
        raise ValueError("; ".join(attempts[-1]["problems"]))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manuscript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--reasoning-effort", choices=("medium", "high", "xhigh"), default="medium")
    args = parser.parse_args()
    client = ClaudeSubscriptionClient(model=args.model, reasoning_effort=args.reasoning_effort, timeout_seconds=900)
    try:
        record = write_blurb(args.manuscript, args.output, client)
    except ValueError as error:
        print(f"reader blurb failed its checks twice: {error}", file=sys.stderr)
        return 1
    print(json.dumps({k: record[k] for k in ("reader_intro", "reader_summary")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
