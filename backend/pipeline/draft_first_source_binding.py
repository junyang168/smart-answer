"""Derive per-paragraph source bindings for a draft-first essay.

Draft-first manuscripts carry no author-declared provenance, so the admin
review preview's per-paragraph source disclosure has nothing to read. This
module derives that mapping after the fact: one subscription-CLI call proposes
verbatim source spans for every reader paragraph, and every span is then
verified character-for-character against the scoped source originals —
a span that does not survive verification is dropped with a recorded finding,
never silently kept. The same discipline as texture anchors (#281) and the
alignment gate (#285): models propose, string comparison decides.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.draft_first_author_runner import source_texts
from backend.pipeline.matthew_exposition_authoring import sha256_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = Path(__file__).with_name("prompts") / "draft_first_source_binding.md"
MIN_EXCERPT_CHARS = 20
MAX_SPANS_PER_PARAGRAPH = 3

BINDING_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_source_binding_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "bindings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "paragraph_index": {"type": "integer"},
                        "spans": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "properties": {
                                    "source_id": {"type": "string"},
                                    "excerpt": {"type": "string"},
                                },
                                "required": ["source_id", "excerpt"],
                            },
                        },
                    },
                    "required": ["paragraph_index", "spans"],
                },
            }
        },
        "required": ["bindings"],
    },
}


def reader_paragraphs(markdown: str) -> list[dict[str, Any]]:
    """Reader-visible paragraphs in document order, headings excluded.

    This split is the shared contract between binding generation and the
    review read model — both sides must segment the manuscript identically
    or the paragraph SHAs stop matching.
    """

    paragraphs: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", markdown):
        text = block.strip()
        if not text or re.fullmatch(r"#{1,6}\s+.*", text.splitlines()[0]) and len(text.splitlines()) == 1:
            continue
        paragraphs.append(
            {
                "paragraph_index": len(paragraphs),
                "paragraph_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
            }
        )
    return paragraphs


def verify_bindings(
    proposed: Mapping[str, Any],
    *,
    paragraphs: list[dict[str, Any]],
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only spans that are verbatim in their source; record the rest."""

    originals = (packet.get("source_originals") or {}).get("originals") or []
    content_by_source = {
        str(item.get("source_id") or ""): str(item.get("content") or "")
        for item in originals
    }
    rows = {int(item["paragraph_index"]): item for item in proposed["bindings"]}
    findings: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        index = paragraph["paragraph_index"]
        row = rows.get(index) or {"spans": []}
        spans: list[dict[str, str]] = []
        for span in list(row.get("spans") or [])[:MAX_SPANS_PER_PARAGRAPH]:
            source_id = str(span.get("source_id") or "")
            excerpt = str(span.get("excerpt") or "").strip()
            content = content_by_source.get(source_id)
            if content is None:
                findings.append(
                    {"paragraph_index": index, "code": "unknown_source", "source_id": source_id}
                )
                continue
            if len(excerpt) < MIN_EXCERPT_CHARS:
                findings.append(
                    {"paragraph_index": index, "code": "excerpt_too_short", "excerpt": excerpt}
                )
                continue
            if excerpt not in content:
                findings.append(
                    {
                        "paragraph_index": index,
                        "code": "excerpt_not_verbatim",
                        "source_id": source_id,
                        "excerpt": excerpt[:80],
                    }
                )
                continue
            spans.append({"source_id": source_id, "excerpt": excerpt})
        verified.append(
            {
                "paragraph_index": index,
                "paragraph_sha256": paragraph["paragraph_sha256"],
                "spans": spans,
            }
        )
    missing = sorted(set(rows) - {p["paragraph_index"] for p in paragraphs})
    for index in missing:
        findings.append({"paragraph_index": index, "code": "unknown_paragraph"})
    return {"bindings": verified, "findings": findings}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", choices=("medium", "high", "xhigh"), default="high")
    args = parser.parse_args()

    raw = json.loads(args.packet.read_text(encoding="utf-8"))
    packet = raw.get("result", raw)
    manuscript = args.draft.read_text(encoding="utf-8")
    paragraphs = reader_paragraphs(manuscript)

    client = CodexSubscriptionClient(model=args.model, reasoning_effort=args.reasoning_effort)
    proposed = client.generate_json(
        PROMPT_PATH.read_text(encoding="utf-8"),
        json.dumps(
            {
                "paragraphs": [
                    {"paragraph_index": p["paragraph_index"], "text": p["text"]}
                    for p in paragraphs
                ],
                "source_originals": source_texts(dict(packet)),
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        BINDING_SCHEMA,
    )
    result = verify_bindings(proposed, paragraphs=paragraphs, packet=packet)
    bound = sum(1 for item in result["bindings"] if item["spans"])
    record = {
        "schema_version": "wang_draft_first_source_bindings_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manuscript_sha256": sha256_text(manuscript),
        "evidence_packet_sha256": packet.get("evidence_packet_sha256"),
        "model": client.model,
        "backend": client.backend,
        "paragraphs_total": len(paragraphs),
        "paragraphs_bound": bound,
        "bindings": result["bindings"],
        "findings": result["findings"],
    }
    record["bindings_sha256"] = sha256_json(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "paragraphs_total": len(paragraphs),
                "paragraphs_bound": bound,
                "findings": len(result["findings"]),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
