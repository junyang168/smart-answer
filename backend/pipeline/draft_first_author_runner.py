"""Draft a theological topic essay from question + viewpoints + full sources.

One model call. The charter prompt carries functional rules only; there is no
Brief, no locked outline, and no author-declared provenance. This runner is the
generation half of the #283 experiment: can code reproduce the calibration
draft's quality from the same three inputs a careful human writer used? The
verification half (grounding, review) is deliberately out of scope here.

POC input is an existing TheologicalEvidencePacket, reused only as a SHA-bound
carrier of `focal_viewpoints` and `source_originals`; a production version
would read the registry directly.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.matthew_exposition_authoring import sha256_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = Path(__file__).with_name("prompts") / "draft_first_topic_author.md"

DRAFT_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_topic_essay_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {"manuscript_markdown": {"type": "string"}},
        "required": ["manuscript_markdown"],
    },
}


def viewpoint_charter(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """The approved position list, reduced to what a writer needs."""

    charter: list[dict[str, Any]] = []
    for focal in packet["focal_viewpoints"]:
        revision = focal["revision"]
        signature = revision.get("proposition_signature") or {}
        charter.append(
            {
                "structure_role": focal.get("structure_role"),
                "viewpoint_revision_id": revision.get("viewpoint_revision_id")
                or focal.get("viewpoint", {}).get("current_revision_id"),
                "core_proposition": revision.get("core_proposition"),
                "modality": signature.get("modality"),
                "polarity": signature.get("polarity"),
            }
        )
    return charter


def source_texts(packet: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "title": str(item.get("title")),
            "source_type": str(item.get("source_type")),
            "content": str(item.get("content")),
        }
        for item in packet["source_originals"]["originals"]
    ]


def verbatim_quote_report(manuscript: str, packet: dict[str, Any]) -> dict[str, Any]:
    """The one objective signal this card keeps: quotes must be verbatim."""

    corpus = "\n".join(
        str(item.get("content")) for item in packet["source_originals"]["originals"]
    )
    quotes = [value.strip() for value in re.findall(r"「([^」]+)」", manuscript)]
    quotes += [
        line[2:].strip()
        for line in manuscript.splitlines()
        if line.startswith("> ") and not line.startswith("> ——")
    ]
    checked = [
        {"quote": quote, "verbatim": quote in corpus}
        for quote in quotes
        if quote
    ]
    return {
        "quotes_checked": len(checked),
        "quotes_failing": [item["quote"] for item in checked if not item["verbatim"]],
    }


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--reader-question", required=True)
    parser.add_argument("--audience", default="神学生和追求的平信徒")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", default="claude-opus-5")
    parser.add_argument("--reasoning-effort", choices=("medium", "high", "xhigh"), default="high")
    args = parser.parse_args()

    raw = json.loads(args.packet.read_text(encoding="utf-8"))
    packet = raw.get("result", raw)
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    payload = {
        "reader_question": args.reader_question,
        "audience": args.audience,
        "approved_viewpoints": viewpoint_charter(packet),
        "source_originals": source_texts(packet),
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    client = ClaudeSubscriptionClient(
        model=args.model, reasoning_effort=args.reasoning_effort
    )
    result = client.generate_json(prompt, payload_json, DRAFT_SCHEMA)
    manuscript = str(result["manuscript_markdown"])

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "draft.md").write_text(manuscript, encoding="utf-8")
    record = {
        "schema_version": "wang_draft_first_author_run_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reader_question": args.reader_question,
        "audience": args.audience,
        "packet_path": str(args.packet),
        "evidence_packet_sha256": packet.get("evidence_packet_sha256"),
        "prompt_sha256": sha256_text(prompt),
        "payload_sha256": sha256_text(payload_json),
        "model": client.model,
        "backend": client.backend,
        "reasoning_effort": client.reasoning_effort,
        "manuscript_sha256": sha256_text(manuscript),
        "manuscript_chars": len(manuscript),
        "quote_report": verbatim_quote_report(manuscript, packet),
    }
    record["run_sha256"] = sha256_json(record)
    (args.output_dir / "run.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: record[k] for k in ("manuscript_chars", "quote_report", "manuscript_sha256")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
