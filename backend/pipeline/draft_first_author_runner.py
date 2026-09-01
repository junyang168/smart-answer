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
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
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


def structure_unresolved_items(packet: dict[str, Any]) -> list[str]:
    """The reviewed structure's own list of what the sources leave open.

    The charter without this list told every consumer to "keep unresolved
    relations unresolved" while hiding which relations those are; the
    church-foundation final harmonized three positive identifications into
    one referent and passed review blind (#291).
    """

    revision = (packet.get("structure") or {}).get("revision") or {}
    return [str(item) for item in revision.get("unresolved_items") or []]


def argument_route_charter(packet: dict[str, Any]) -> list[dict[str, Any]]:
    """The approved source-local argument routes, reduced to what a judge needs.

    Without this the editorial reviewer was forced to attest route hard
    failures with no route data in hand (#293): a route charter names, for
    every approved route, which sermon it lives in and which ordered steps
    the professor actually walked — the ground truth for judging whether an
    essay splices steps from different sermons into an argument he never made.
    """

    charter: list[dict[str, Any]] = []
    for bundle in packet.get("argument_routes") or []:
        revision = bundle.get("revision") or {}
        charter.append(
            {
                "route_revision_id": revision.get("argument_route_revision_id"),
                "route_label": revision.get("route_label"),
                "conclusion_viewpoint_revision_id": revision.get(
                    "validated_against_conclusion_viewpoint_revision_id"
                ),
                # Per-source attestation, not a flat source list: which steps
                # each sermon actually witnesses, and whether its witness is
                # full. A route only partially attested by a source must not
                # look wholly usable — that is the splice the judge exists to
                # catch, mechanically (#302).
                "source_attestations": [
                    {
                        "source_id": str(item.get("source_id") or ""),
                        "completeness": item.get("completeness"),
                        "attested_step_keys": [
                            str(binding.get("route_step_key") or "")
                            for binding in item.get("step_bindings") or []
                            if binding.get("attestation_status")
                            in (None, "attested", "full", "supported")
                        ],
                    }
                    for item in bundle.get("attestations") or []
                ],
                "steps": [
                    {
                        "step_key": node.get("route_step_key"),
                        "role": node.get("role"),
                        "proposition": node.get("normalized_proposition"),
                    }
                    for node in revision.get("ordered_inference_nodes") or []
                ],
            }
        )
    return charter


def source_texts(packet: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "source_id": str(item.get("source_id")),
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
    # Corner brackets around short runs are term mentions (「性」,「使徒和先知」)
    # — ordinary Chinese typography, not quotation. Only spans long enough to
    # be an actual quoted sentence are held to the verbatim standard; the
    # fabrication risk this gate exists for lives in long quotes.
    quotes = [
        value.strip()
        for value in re.findall(r"「([^」]+)」", manuscript)
        if len(value.strip()) >= 8
    ]
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
    # Readability rule the owner set after the keys article shipped a
    # full-verse quotation inline (the rock article had block-quoted its
    # verses by luck, not by rule): a quotation long enough to be a full
    # sentence or verse reads as a wall inline and must stand as a Markdown
    # blockquote. Short runs -- term mentions, the professor's pivot lines --
    # stay inline. The threshold is length, the one property code can judge.
    blockquoted = "\n".join(
        line for line in manuscript.splitlines() if line.startswith(">")
    )
    long_inline = [
        value.strip()
        for value in re.findall(r"「([^」]+)」", manuscript)
        if len(value.strip()) >= 40 and value.strip() not in blockquoted
    ]
    return {
        "quotes_checked": len(checked),
        "quotes_failing": [item["quote"] for item in checked if not item["verbatim"]],
        "long_quotes_not_blockquoted": long_inline,
    }


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--reader-question", required=True)
    parser.add_argument(
        "--audience",
        # A thin label made the author mirror its sources' seminary register
        # (bare verse numbers, untranslated Greek); the profile says what the
        # reader does not bring, and rule 13 tells the author to write to it.
        default=(
            "神学生和追求的老基督徒。他们有圣经常识——知道摩西五经、先知书、使徒行传是什么，"
            "认得利未记、认得拉比这类词，不需要从头解释；但不滚瓜烂熟，"
            "记不住某章某节具体讲了什么、上下文是什么。多数人不懂希腊文。"
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--provider",
        choices=("claude", "codex"),
        default="claude",
        help="subscription CLI used for the drafting call; never an API client",
    )
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
        "unresolved_items": structure_unresolved_items(packet),
        "argument_routes": argument_route_charter(packet),
        "source_originals": source_texts(packet),
    }
    payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)

    client_type = (
        CodexSubscriptionClient if args.provider == "codex" else ClaudeSubscriptionClient
    )
    client = client_type(model=args.model, reasoning_effort=args.reasoning_effort)
    result = client.generate_json(prompt, payload_json, DRAFT_SCHEMA)
    manuscript = str(result["manuscript_markdown"])
    # A model occasionally nests the envelope: the field's value is itself a
    # JSON object with the same key. Unwrap deterministically until markdown.
    while manuscript.lstrip().startswith("{"):
        try:
            inner = json.loads(manuscript)
        except ValueError:
            break
        if not isinstance(inner, dict) or "manuscript_markdown" not in inner:
            break
        manuscript = str(inner["manuscript_markdown"])

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
