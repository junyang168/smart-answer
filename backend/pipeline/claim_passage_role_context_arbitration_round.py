"""One bounded GPT subscription arbitration of #409's already dual-reviewed holds.

Unlike a repeated blind review, the arbitrator sees both prior reasons and the
verified original context. Outputs are proposals only: no role-ledger or master
data mutation. Raw answers (including invalid ones) remain immutable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claim_passage_role_context_arbitration import compact
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


MODEL = "gpt-6-sol"
PROMPT = """This is ONE arbitration round, not a new blind first review. Two
independent reviewers already classified the Claim from its Claim/EvidenceStep
packet; their roles and reasons are supplied. You also have a SHA-verified
window from the original sermon or notes manuscript, which they did not see.

Decide the Claim's FUNCTION, not whether the professor's theology is right.
passage_exegesis requires that THIS Claim resolve a specific identifiable
passage's meaning, referent, original-language/translation issue, or literary
structure. A general theological conclusion, analogy, application, historical
claim, method, quotation/paraphrase, or a premise supporting another Claim's
exegesis is other. Do not copy a nearby verse onto a general Claim. If direct
interpretation is clear but the original source still does not identify which
passage, use unresolved/needs_human. If the Claim/ref conflicts with the
original source, use unresolved/repair_required. Never infer a verse from
general Bible knowledge.
The anchor_trail lists earlier source citation CUES with paragraph distances;
it does not prove that this Claim interprets those verses. Use a cue only when
the professor's spoken argument demonstrably continues from that cited passage
through the Claim. A new topic, illustration, or general summary breaks that
link even if the cue is close. Editorial subtitles are neither source evidence
nor mandatory topic boundaries.

Adjudicate each prior disagreement explicitly in the reason. Quote a SHORT,
CONTIGUOUS, EXACT original-source substring (roughly 10–30 characters) and
its paragraph key for any resolved classification. Do not use ellipses or
paraphrase. For passage_exegesis, give the identifiable passage reference;
if the source names only a chapter, do not invent a verse. If evidence is
insufficient after this single round, leave needs_human with a precise question.
An existing REVIEWED_HUMAN_DECISION_REQUIRED hold remains unresolved/needs_human;
an existing REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED hold remains
unresolved/repair_required. More context may explain the problem but cannot
silently override those prior decisions.
Do not count either reviewer's assertion as source evidence. No theological
judgment, no editing of professor's words."""


def schema(ids: list[str]) -> dict[str, Any]:
    item = {"type": "object", "additionalProperties": False,
            "required": ["role", "disposition", "candidate_reference",
                         "source_key", "source_quote", "reason"],
            "properties": {
                "role": {"type": "string", "enum": ["other", "passage_exegesis", "unresolved"]},
                "disposition": {"type": "string", "enum": ["resolved", "needs_human", "repair_required"]},
                "candidate_reference": {"type": "string"},
                "source_key": {"type": "string"},
                "source_quote": {"type": "string"},
                "reason": {"type": "string"},
            }}
    return {"name": "wang_claim_role_context_arbitration_round_v1", "strict": True,
            "schema": {"type": "object", "additionalProperties": False,
                       "required": ["decisions"],
                       "properties": {"decisions": {"type": "object",
                                                   "additionalProperties": False,
                                                   "required": ids,
                                                   "properties": {cid: item for cid in ids}}}}}


def validate(response: dict, rows: list[dict]) -> None:
    decisions = response.get("decisions")
    if not isinstance(decisions, dict) or set(decisions) != {row["claim_id"] for row in rows}:
        raise ValueError("arbitration Claim denominator differs")
    for row in rows:
        decision = decisions[row["claim_id"]]
        role, disposition = decision["role"], decision["disposition"]
        if row.get("reason_code") == "REVIEWED_HUMAN_DECISION_REQUIRED" and (
            role != "unresolved" or disposition != "needs_human"
        ):
            raise ValueError(f"prior human hold was overridden: {row['claim_id']}")
        if row.get("reason_code") == "REVIEWED_SOURCE_OR_CLAIM_REPAIR_REQUIRED" and (
            role != "unresolved" or disposition != "repair_required"
        ):
            raise ValueError(f"prior repair hold was overridden: {row['claim_id']}")
        if role == "unresolved":
            if disposition == "resolved" or decision["candidate_reference"]:
                raise ValueError(f"unresolved disposition/ref mismatch: {row['claim_id']}")
        elif disposition != "resolved":
            raise ValueError(f"resolved role has unresolved disposition: {row['claim_id']}")
        if role == "passage_exegesis" and not decision["candidate_reference"]:
            raise ValueError(f"exegesis lacks passage locator: {row['claim_id']}")
        if role == "other" and decision["candidate_reference"]:
            raise ValueError(f"other has passage locator: {row['claim_id']}")
        key, quote = decision["source_key"], decision["source_quote"]
        if disposition == "resolved" and (not key or not quote):
            raise ValueError(f"resolved decision lacks original-source quote: {row['claim_id']}")
        if key or quote:
            matches = [part for part in row["context"] if part["paragraph_key"] == key]
            if len(matches) != 1 or not quote or quote not in matches[0]["text"]:
                raise ValueError(f"source quote is not verbatim: {row['claim_id']}")
        if not str(decision["reason"]).strip():
            raise ValueError(f"arbitration reason is empty: {row['claim_id']}")


def input_row(context: dict, prior: dict) -> dict:
    return compact(context) | {
        "source_id": context["source_id"],
        "source_match": context["source_match"],
        "prior_primary": {"role": prior["primary_role"], "reason": prior["primary_reason"]},
        "prior_independent": {"role": prior["independent_role"],
                              "reason": prior["independent_reason"]},
        "prior_reason_code": prior["reason_code"],
    }


def run_batch(*, audit: dict, queue: dict, rows: list[dict], root: Path,
              client: Any, retry_invalid_once: bool) -> dict:
    ids = [row["claim_id"] for row in rows]
    digest = hashlib.sha256("\n".join(ids).encode()).hexdigest()[:16]
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"batch-{digest}.json"
    priors = {row["claim_id"]: row for row in queue["rows"]}
    payload = json.dumps({"audit_sha256": audit["artifact_sha256"],
                          "queue_sha256": queue["artifact_sha256"],
                          "claims": [input_row(row, priors[row["claim_id"]]) for row in rows]},
                         ensure_ascii=False, separators=(",", ":"))
    binding = {
        "schema_version": "wang_claim_role_context_arbitration_round_v1",
        "audit_sha256": audit["artifact_sha256"],
        "queue_sha256": queue["artifact_sha256"],
        "model": MODEL, "claim_ids": ids,
        "prompt_sha256": hashlib.sha256(PROMPT.encode()).hexdigest(),
        "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        "schema_sha256": base.sha256_json(schema(ids)["schema"]),
    }

    def checked(p: Path, expected: dict) -> dict:
        artifact = base._read_json(p)
        base._check_artifact(artifact)
        if any(artifact.get(key) != value for key, value in expected.items()):
            raise ValueError(f"cached arbitration binding differs: {p}")
        validate(artifact["response"], rows)
        return artifact

    def call(p: Path, expected: dict, prompt: str) -> dict:
        try:
            answer = client.generate_json(prompt, payload, schema(ids))
        except Exception as exc:
            failure = base._artifact(expected | {"status": "transport_failure",
                                                 "error_type": type(exc).__name__,
                                                 "error": str(exc)})
            base._write_immutable(p.with_suffix(".failure.json"), failure)
            raise
        artifact = base._artifact(expected | {"status": "raw_response_retained",
                                             "response": answer})
        base._write_immutable(p, artifact)
        validate(answer, rows)
        return artifact

    try:
        artifact = checked(path, binding) if path.exists() else call(path, binding, PROMPT)
        used_path = path
    except ValueError:
        if not retry_invalid_once or not path.exists():
            raise
        first = base._read_json(path)
        base._check_artifact(first)
        if any(first.get(key) != value for key, value in binding.items()):
            raise ValueError(f"cannot retry source-binding mismatch: {path}")
        retry_path = path.with_name(path.stem + ".retry-2.json")
        retry_binding = binding | {"attempt_number": 2,
                                   "retry_of_artifact_sha256": first["artifact_sha256"]}
        feedback = (PROMPT + "\nThe first raw answer failed a structural or exact-source-quote "
                    "check. Reconsider every answer and copy only a short contiguous "
                    "verbatim quote. This is the sole retry; uncertainty must remain unresolved.")
        artifact = (checked(retry_path, retry_binding) if retry_path.exists()
                    else call(retry_path, retry_binding, feedback))
        used_path = retry_path
    return {"path": str(used_path), "claim_count": len(ids),
            "artifact_sha256": artifact["artifact_sha256"]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=8)
    parser.add_argument("--stop", type=int)
    parser.add_argument("--retry-invalid-once", action="store_true")
    args = parser.parse_args()
    audit = base._read_json(args.audit)
    queue = base._read_json(args.queue)
    base._check_artifact(audit)
    base._check_artifact(queue)
    if (audit.get("queue_sha256") != queue["artifact_sha256"]
            or audit.get("claim_count") != len(queue["rows"])):
        raise ValueError("source context and prior review queue differ")
    stop = args.stop if args.stop is not None else args.start + args.count
    if args.start < 0 or args.count < 1 or stop > len(audit["rows"]) or stop <= args.start:
        raise ValueError("invalid bounded arbitration scope")
    client = CodexSubscriptionClient(model=MODEL, reasoning_effort="high")
    for start in range(args.start, stop, args.count):
        rows = audit["rows"][start:min(start + args.count, stop)]
        result = run_batch(audit=audit, queue=queue, rows=rows,
                           root=args.output_root, client=client,
                           retry_invalid_once=args.retry_invalid_once)
        print(json.dumps({"start": start, "stop": start + len(rows)} | result,
                         sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
