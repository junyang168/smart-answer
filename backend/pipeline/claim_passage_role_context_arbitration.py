"""Bounded subscription-only #409 arbitration with verified original context.

This produces proposals, never a replacement role ledger or database writes.
The two providers run independently and each raw answer is sealed before
validation. A failed request leaves a diagnostic and stops without skipping.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


MODEL = {"gpt": "gpt-6-sol", "opus": "claude-opus-5-5"}
PROMPT = """Classify each Claim's FUNCTION, not theological correctness. The supplied
source context is an exact verified window around its source fragments. Decide
passage_exegesis only if the Claim itself resolves what an identifiable passage
means, who its words refer to, a translation/original-language issue, or the
passage's structure/scope. The context may supply a missing passage reference,
but mere adjacency to a cited verse does not make a theological, practical,
historical, or methodological Claim exegesis. A quotation/paraphrase is other.
If the Claim directly interprets Scripture but the particular passage remains
unidentifiable in the context, use unresolved. Do not infer a verse from your
general Bible knowledge. Return a short source-grounded reason. For any
passage_exegesis decision, give a candidate_reference found in the source
context, the paragraph key, and a verbatim supporting excerpt. If the source
only states a book/chapter and not an exact verse, say so; do not invent it.
For other/unresolved, leave candidate_reference empty. Do not read or cite
either prior reviewer's decision; those are not in this payload."""


def schema(ids: list[str]) -> dict[str, Any]:
    item = {"type": "object", "additionalProperties": False,
            "required": ["role", "candidate_reference", "supporting_context_key",
                         "supporting_quote", "reason"],
            "properties": {
                "role": {"type": "string", "enum": ["other", "passage_exegesis", "unresolved"]},
                "candidate_reference": {"type": "string"},
                "supporting_context_key": {"type": "string"},
                "supporting_quote": {"type": "string"},
                "reason": {"type": "string"},
            }}
    return {"name": "wang_claim_role_source_context_arbitration_v1", "strict": True,
            "schema": {"type": "object", "additionalProperties": False,
                       "required": ["decisions"],
                       "properties": {"decisions": {"type": "object",
                                                   "additionalProperties": False,
                                                   "required": ids,
                                                   "properties": {cid: item for cid in ids}}}}}


def compact(row: dict) -> dict:
    return {key: row[key] for key in ("claim_id", "statement", "claim_scripture_refs",
                                     "evidence_steps", "anchor_indices", "context")}


def validate(answer: dict, batch: list[dict]) -> None:
    decisions = answer.get("decisions")
    ids = {row["claim_id"] for row in batch}
    if not isinstance(decisions, dict) or set(decisions) != ids:
        raise ValueError("answer does not cover exact Claim batch")
    for row in batch:
        cid = row["claim_id"]
        decision = decisions[cid]
        if decision["role"] not in {"other", "passage_exegesis", "unresolved"}:
            raise ValueError(f"invalid role: {cid}")
        key = decision["supporting_context_key"]
        quote = decision["supporting_quote"]
        if key or quote:
            matches = [part for part in row["context"] if part["paragraph_key"] == key]
            if len(matches) != 1 or not quote or quote not in matches[0]["text"]:
                raise ValueError(f"support quote does not match original source: {cid}")
        if decision["role"] == "passage_exegesis":
            if not (decision["candidate_reference"] and key and quote):
                raise ValueError(f"exegesis lacks source-grounded passage locator: {cid}")
        elif decision["candidate_reference"]:
            raise ValueError(f"non-exegesis has candidate reference: {cid}")


def _retry_quote_failure(*, path: Path, original: dict, expected: dict,
                         batch: list[dict], payload: str, client: Any) -> dict:
    """One explicit, immutable retry after inspecting a retained invalid answer."""

    retry_path = path.with_name(path.stem + ".retry-2.json")
    binding = expected | {"attempt_number": 2,
                          "retry_of_artifact_sha256": original["artifact_sha256"]}
    if retry_path.exists():
        cached = base._read_json(retry_path)
        base._check_artifact(cached)
        if any(cached.get(k) != v for k, v in binding.items()):
            raise ValueError(f"retry binding differs: {retry_path}")
        validate(cached["response"], batch)
        return {"path": str(retry_path), "cached": True,
                "claim_count": len(batch), "retry_of": str(path)}
    feedback = (
        PROMPT + "\nYour previous answer was retained but failed a verbatim quote "
        "check. For supporting_quote, copy a SHORT CONTIGUOUS exact substring "
        "(roughly 10–30 characters) from supporting_context_key. Do not use "
        "ellipsis, paraphrase, or stitch nonadjacent text. Reconsider the role "
        "if the source does not support it."
    )
    try:
        response = client.generate_json(feedback, payload, schema(binding["claim_ids"]))
    except Exception as exc:
        failure = base._artifact(binding | {"status": "retry_transport_failure",
                                            "error_type": type(exc).__name__,
                                            "error": str(exc)})
        base._write_immutable(retry_path.with_suffix(".failure.json"), failure)
        raise
    artifact = base._artifact(binding | {"status": "retry_raw_response_retained",
                                         "response": response})
    base._write_immutable(retry_path, artifact)
    validate(response, batch)
    return {"path": str(retry_path), "cached": False,
            "claim_count": len(batch), "retry_of": str(path),
            "artifact_sha256": artifact["artifact_sha256"]}


def one_batch(audit: dict, batch: list[dict], *, provider: str, output_root: Path,
              client: Any, retry_invalid_once: bool = False) -> dict:
    ids = [row["claim_id"] for row in batch]
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
    path = output_root / provider / f"batch-{digest}.json"
    payload = json.dumps({"audit_sha256": audit["artifact_sha256"],
                          "claims": [compact(row) for row in batch]},
                         ensure_ascii=False, separators=(",", ":"))
    expected = {"schema_version": "wang_claim_role_source_context_arbitration_v1",
                "audit_sha256": audit["artifact_sha256"], "provider": provider,
                "model": MODEL[provider], "claim_ids": ids,
                "payload_sha256": hashlib.sha256(payload.encode()).hexdigest()}
    if path.exists():
        cached = base._read_json(path)
        base._check_artifact(cached)
        if any(cached.get(k) != v for k, v in expected.items()):
            raise ValueError(f"cached batch binding differs: {path}")
        try:
            validate(cached["response"], batch)
        except ValueError:
            if not retry_invalid_once:
                raise
            return _retry_quote_failure(path=path, original=cached, expected=expected,
                                        batch=batch, payload=payload, client=client)
        return {"path": str(path), "cached": True, "claim_count": len(ids)}
    try:
        response = client.generate_json(PROMPT, payload, schema(ids))
    except Exception as exc:
        failure = base._artifact(expected | {"status": "transport_or_structure_failure",
                                             "error_type": type(exc).__name__,
                                             "error": str(exc)})
        failure_path = output_root / provider / f"batch-{digest}.failure.json"
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        base._write_immutable(failure_path, failure)
        raise
    artifact = base._artifact(expected | {"status": "raw_response_retained",
                                          "response": response})
    path.parent.mkdir(parents=True, exist_ok=True)
    base._write_immutable(path, artifact)
    try:
        validate(response, batch)
    except ValueError:
        if not retry_invalid_once:
            raise
        return _retry_quote_failure(path=path, original=artifact, expected=expected,
                                    batch=batch, payload=payload, client=client)
    return {"path": str(path), "cached": False, "claim_count": len(ids),
            "artifact_sha256": artifact["artifact_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--provider", choices=sorted(MODEL), required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--stop", type=int, help="exclusive end for resumable campaign")
    parser.add_argument("--retry-invalid-once", action="store_true")
    args = parser.parse_args()
    audit = base._read_json(args.audit)
    base._check_artifact(audit)
    if audit.get("schema_version") != "wang_claim_role_source_context_audit_v1":
        raise ValueError("unsupported audit")
    rows = audit["rows"]
    stop = args.stop if args.stop is not None else args.start + args.count
    if args.start < 0 or args.count < 1 or stop > len(rows) or stop <= args.start:
        raise ValueError("invalid bounded batch")
    client = (CodexSubscriptionClient(model=MODEL["gpt"], reasoning_effort="high")
              if args.provider == "gpt" else
              ClaudeSubscriptionClient(model=MODEL["opus"], reasoning_effort="high"))
    for start in range(args.start, stop, args.count):
        batch = rows[start: min(start + args.count, stop)]
        result = one_batch(audit, batch, provider=args.provider,
                           output_root=args.output_root, client=client,
                           retry_invalid_once=args.retry_invalid_once)
        print(json.dumps({"start": start, "stop": min(start + args.count, stop)} | result,
                         sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
