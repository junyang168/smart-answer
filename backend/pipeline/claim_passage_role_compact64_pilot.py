"""Isolated 64-Claim compact-payload experiment for issue #409.

This reads the existing frozen packet and completed 16-Claim reviews. It never
changes either one, reads no database, and writes only to its own output root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

from backend.pipeline.claim_passage_role_runner import (
    MAX_ROLE_REQUEST_BYTES,
    PROMPT,
    _artifact,
    _check_artifact,
    _read_json,
    _write_immutable,
    reconcile,
    response_schema,
    validate_cached_decisions,
    validate_response,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


PILOT_SIZE = 64
BASELINE_BATCH_SIZE = 16


def compact_row(row: dict[str, Any]) -> dict[str, Any]:
    """Keep every semantic evidence string, in its original order."""

    return {
        "claim_id": row["claim_id"],
        "statement": row["statement"],
        "scripture_refs": row["scripture_refs"],
        "evidence_steps": [
            {
                "statement": step["statement"],
                "scripture_refs": step["scripture_refs"],
                "verbatim_excerpts": [
                    fragment["verbatim_excerpt"] for fragment in step["fragments"]
                ],
            }
            for step in row["evidence_steps"]
        ],
    }


def _baseline(packet_root: Path, rows: list[dict[str, Any]], packet_sha: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for batch_number in range(1, PILOT_SIZE // BASELINE_BATCH_SIZE + 1):
        start = (batch_number - 1) * BASELINE_BATCH_SIZE
        batch = rows[start:start + BASELINE_BATCH_SIZE]
        artifacts = []
        for role in ("primary", "independent"):
            artifact = _read_json(packet_root / f"{role}-{batch_number:05d}.json")
            _check_artifact(artifact)
            if (
                artifact.get("packet_sha256") != packet_sha
                or artifact.get("role") != role
                or artifact.get("claim_ids") != [row["claim_id"] for row in batch]
            ):
                raise ValueError(f"baseline {role} batch {batch_number} has different ownership")
            artifacts.append(validate_cached_decisions(artifact["decisions"], batch))
        result.extend(reconcile(artifacts[0], artifacts[1], batch))
    return result


def run(packet_root: Path, output_root: Path) -> dict[str, Any]:
    packet = _read_json(packet_root / "role-packet.json")
    _check_artifact(packet)
    if packet.get("mode") != "all_eligible" or len(packet["claims"]) < PILOT_SIZE:
        raise ValueError("expected an all-eligible frozen packet with at least 64 Claims")
    rows = packet["claims"][:PILOT_SIZE]
    ids = [row["claim_id"] for row in rows]
    if len(set(ids)) != PILOT_SIZE:
        raise ValueError("duplicate Claim in pilot")
    baseline = _baseline(packet_root, rows, packet["artifact_sha256"])
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("pilot output root must be new and empty")

    projection = {"claims": [compact_row(row) for row in rows]}
    payload = json.dumps(projection, ensure_ascii=False, sort_keys=True)
    schema = response_schema(ids)
    prompt = PROMPT.read_text(encoding="utf-8")
    request_bytes = len((prompt + payload + json.dumps(schema, ensure_ascii=False)).encode("utf-8"))
    if request_bytes > MAX_ROLE_REQUEST_BYTES:
        raise ValueError(f"64-Claim compact request exceeds byte ceiling: {request_bytes}")
    output_root.mkdir(parents=True, exist_ok=False)
    manifest = _artifact({
        "schema_version": "wang_claim_passage_role_compact64_pilot_v1",
        "packet_sha256": packet["artifact_sha256"],
        "claim_ids": ids,
        "selection": "first_64_claims_in_frozen_packet",
        "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
        "projection_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "schema_sha256": hashlib.sha256(json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
        "request_bytes": request_bytes,
        "baseline_roles": dict(sorted(Counter(row["role"] for row in baseline).items())),
    })
    _write_immutable(output_root / "pilot-manifest.json", manifest)

    clients = (
        ("primary", CodexSubscriptionClient(model="gpt-6-sol", reasoning_effort="high")),
        ("independent", ClaudeSubscriptionClient(model="claude-fable-5-1", reasoning_effort="high")),
    )
    decisions: dict[str, list[dict[str, Any]]] = {}
    for role, client in clients:
        started = time.monotonic()
        response = client.generate_json(prompt, payload, schema)
        elapsed = time.monotonic() - started
        validated = validate_response(response, rows)
        artifact = _artifact({
            "schema_version": "wang_claim_passage_role_compact64_response_v1",
            "role": role,
            "model": client.model,
            "manifest_sha256": manifest["artifact_sha256"],
            "elapsed_seconds": round(elapsed, 3),
            "decisions": validated,
        })
        _write_immutable(output_root / f"{role}.json", artifact)
        decisions[role] = validated
        print(json.dumps({"role": role, "elapsed_seconds": round(elapsed, 3),
                          "artifact_sha256": artifact["artifact_sha256"]}), flush=True)

    pilot = reconcile(decisions["primary"], decisions["independent"], rows)
    old = {row["claim_id"]: row for row in baseline}
    changes = [
        {
            "claim_id": row["claim_id"],
            "baseline_role": old[row["claim_id"]]["role"],
            "pilot_role": row["role"],
            "baseline_passage_identity": old[row["claim_id"]]["passage_identity_status"],
            "pilot_passage_identity": row["passage_identity_status"],
        }
        for row in pilot
        if (row["role"], row["passage_identity_status"])
        != (old[row["claim_id"]]["role"], old[row["claim_id"]]["passage_identity_status"])
    ]
    report = _artifact({
        "schema_version": "wang_claim_passage_role_compact64_comparison_v1",
        "manifest_sha256": manifest["artifact_sha256"],
        "baseline_roles": manifest["baseline_roles"],
        "pilot_roles": dict(sorted(Counter(row["role"] for row in pilot).items())),
        "changed_claim_count": len(changes),
        "changes": changes,
        "primary_seconds": _read_json(output_root / "primary.json")["elapsed_seconds"],
        "independent_seconds": _read_json(output_root / "independent.json")["elapsed_seconds"],
    })
    _write_immutable(output_root / "comparison.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.packet_root, args.output_root), ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
