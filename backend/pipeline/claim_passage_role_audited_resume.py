"""Audited #409 continuation: preserve Fable batches 1-68, use Opus 5.5 from 69.

The original frozen packet, prompt, runner, and response artifacts remain
immutable. This runner writes to a separate root and persists each structured
model response before semantic validation, including rejected responses.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline import claim_passage_role_runner as base
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


BATCH_SIZE = 16
CUTOVER_BATCH = 69
PRIMARY_MODEL = "gpt-6-sol"
OLD_INDEPENDENT_MODEL = "claude-fable-5-1"
NEW_INDEPENDENT_MODEL = "claude-opus-5-5"
MAX_INVALID_RETRIES = 1


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rows_for_batch(packet: dict[str, Any], batch_id: int) -> list[dict[str, Any]]:
    if batch_id < 1 or batch_id > math.ceil(len(packet["claims"]) / BATCH_SIZE):
        raise ValueError("batch ID outside frozen denominator")
    start = (batch_id - 1) * BATCH_SIZE
    return packet["claims"][start:start + BATCH_SIZE]


def _check_packet(root: Path) -> dict[str, Any]:
    packet = base._read_json(root / "role-packet.json")
    base._check_artifact(packet)
    if packet.get("mode") != "all_eligible":
        raise ValueError("expected all-eligible frozen packet")
    if packet["runner_code_sha256"] != _sha(Path(base.__file__).read_bytes()):
        raise ValueError("original runner SHA changed")
    if packet["prompt_sha256"] != _sha(base.PROMPT.read_bytes()):
        raise ValueError("original prompt SHA changed")
    return packet


def _check_graph(packet: dict[str, Any], store: PostgresKnowledgeStore) -> None:
    pins = [{
        "claim_id": row["claim_id"], "pinned_claim_revision": row["claim_revision"],
        "claim_revision_sha256": row["claim_content_sha256"],
        "source_id": row["source_id"], "statement": row["statement"],
        "scripture_refs": row["scripture_refs"],
    } for row in packet["claims"]]
    if base.build_rows(store, pins) != packet["claims"]:
        raise ValueError("Claim/source graph drifted from frozen packet")


def _checked_decisions(path: Path, packet: dict[str, Any], batch_id: int,
                       role: str, model: str) -> tuple[list[dict[str, Any]], str]:
    artifact = base._read_json(path)
    base._check_artifact(artifact)
    batch = _rows_for_batch(packet, batch_id)
    if (
        artifact.get("schema_version") != base.RESPONSE_VERSION
        or artifact.get("packet_sha256") != packet["artifact_sha256"]
        or artifact.get("role") != role or artifact.get("model") != model
        or artifact.get("batch_id") != f"{batch_id:05d}"
        or artifact.get("claim_ids") != [row["claim_id"] for row in batch]
    ):
        raise ValueError(f"review artifact ownership mismatch: {path}")
    return base.validate_cached_decisions(artifact["decisions"], batch), artifact["artifact_sha256"]


def _original_artifacts(original_root: Path, packet: dict[str, Any]) -> dict[str, str]:
    """Freeze exactly the already-completed 68 pairs and GPT batch 69."""

    expected = {f"primary-{index:05d}.json" for index in range(1, CUTOVER_BATCH + 1)}
    expected |= {f"independent-{index:05d}.json" for index in range(1, CUTOVER_BATCH)}
    actual = {path.name for path in original_root.glob("primary-*.json")}
    actual |= {path.name for path in original_root.glob("independent-*.json")}
    if actual != expected:
        raise ValueError("original checkpoint changed from completed 68 pairs plus GPT 69")
    shas: dict[str, str] = {}
    for batch_id in range(1, CUTOVER_BATCH + 1):
        roles = (("primary", PRIMARY_MODEL),)
        if batch_id < CUTOVER_BATCH:
            roles += (("independent", OLD_INDEPENDENT_MODEL),)
        for role, model in roles:
            name = f"{role}-{batch_id:05d}.json"
            _, shas[name] = _checked_decisions(original_root / name, packet, batch_id, role, model)
    return shas


def prepare(original_root: Path, output_root: Path, store: PostgresKnowledgeStore) -> dict[str, Any]:
    if output_root.exists():
        raise ValueError("new audited output root already exists")
    packet = _check_packet(original_root)
    _check_graph(packet, store)
    original_shas = _original_artifacts(original_root, packet)
    manifest = base._artifact({
        "schema_version": "wang_claim_passage_role_audited_resume_v1",
        "packet_sha256": packet["artifact_sha256"],
        "original_root": str(original_root.resolve()),
        "original_artifact_shas": original_shas,
        "resume_code_sha256": _sha(Path(__file__).read_bytes()),
        "prompt_sha256": packet["prompt_sha256"],
        "batch_size": BATCH_SIZE,
        "cutover_batch": CUTOVER_BATCH,
        "primary_model": PRIMARY_MODEL,
        "independent_model_before_cutover": OLD_INDEPENDENT_MODEL,
        "independent_model_from_cutover": NEW_INDEPENDENT_MODEL,
        "max_invalid_retries": MAX_INVALID_RETRIES,
    })
    output_root.mkdir(parents=True, exist_ok=False)
    base._write_immutable(output_root / "resume-manifest.json", manifest)
    return {"manifest_sha256": manifest["artifact_sha256"], "output_root": str(output_root)}


def _check_resume(original_root: Path, output_root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    packet = _check_packet(original_root)
    manifest = base._read_json(output_root / "resume-manifest.json")
    base._check_artifact(manifest)
    if (
        manifest.get("schema_version") != "wang_claim_passage_role_audited_resume_v1"
        or manifest.get("packet_sha256") != packet["artifact_sha256"]
        or manifest.get("original_root") != str(original_root.resolve())
        or manifest.get("resume_code_sha256") != _sha(Path(__file__).read_bytes())
        or manifest.get("original_artifact_shas") != _original_artifacts(original_root, packet)
        or manifest.get("prompt_sha256") != packet["prompt_sha256"]
        or manifest.get("batch_size") != BATCH_SIZE
        or manifest.get("cutover_batch") != CUTOVER_BATCH
        or manifest.get("primary_model") != PRIMARY_MODEL
        or manifest.get("independent_model_before_cutover") != OLD_INDEPENDENT_MODEL
        or manifest.get("independent_model_from_cutover") != NEW_INDEPENDENT_MODEL
        or manifest.get("max_invalid_retries") != MAX_INVALID_RETRIES
    ):
        raise ValueError("audited resume manifest or original checkpoint changed")
    return packet, manifest


def _response_for_batch(role: str, batch: list[dict[str, Any]], *, client: Any,
                        packet_sha: str, manifest_sha: str, batch_id: int,
                        output_root: Path) -> list[dict[str, Any]]:
    """Persist raw structured output before validating; never promote invalid output."""

    ids = [row["claim_id"] for row in batch]
    prompt = base.PROMPT.read_text(encoding="utf-8")
    payload = json.dumps({"claims": batch}, ensure_ascii=False, sort_keys=True)
    schema = base.response_schema(ids)
    if len((prompt + payload + json.dumps(schema, ensure_ascii=False)).encode("utf-8")) > base.MAX_ROLE_REQUEST_BYTES:
        raise ValueError("role review request exceeds frozen byte ceiling")
    attempts_root = output_root / "attempts"
    attempts_root.mkdir(exist_ok=True)
    batch_name = f"{batch_id:05d}"
    for number in range(1, MAX_INVALID_RETRIES + 2):
        raw_path = attempts_root / f"{role}-{batch_name}-attempt-{number}.json"
        if raw_path.exists():
            raw = base._read_json(raw_path)
            base._check_artifact(raw)
            if (raw.get("packet_sha256") != packet_sha
                    or raw.get("schema_version") != "wang_claim_passage_role_raw_attempt_v1"
                    or raw.get("manifest_sha256") != manifest_sha
                    or raw.get("role") != role
                    or raw.get("model") != client.model
                    or raw.get("batch_id") != batch_name
                    or raw.get("attempt_number") != number
                    or raw.get("claim_ids") != ids
                    or raw.get("prompt_sha256") != _sha(base.PROMPT.read_bytes())
                    or raw.get("payload_sha256") != _sha(payload.encode("utf-8"))
                    or raw.get("schema_sha256") != _sha(json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8"))):
                raise ValueError("existing raw attempt binding differs")
        else:
            response = client.generate_json(prompt, payload, schema)
            raw = base._artifact({
                "schema_version": "wang_claim_passage_role_raw_attempt_v1",
                "packet_sha256": packet_sha, "manifest_sha256": manifest_sha,
                "role": role, "model": client.model, "batch_id": batch_name,
                "attempt_number": number, "claim_ids": ids,
                "prompt_sha256": _sha(base.PROMPT.read_bytes()),
                "payload_sha256": _sha(payload.encode("utf-8")),
                "schema_sha256": _sha(json.dumps(schema, ensure_ascii=False, sort_keys=True).encode("utf-8")),
                "response": response,
            })
            base._write_immutable(raw_path, raw)
        try:
            validated = base.validate_response(raw["response"], batch)
        except ValueError as exc:
            event = base._artifact({
                "schema_version": "wang_claim_passage_role_validation_event_v1",
                "raw_attempt_sha256": raw["artifact_sha256"],
                "status": "rejected", "error": f"{type(exc).__name__}: {exc}",
            })
            base._write_immutable(attempts_root / f"{role}-{batch_name}-attempt-{number}-validation.json", event)
            if number == MAX_INVALID_RETRIES + 1:
                raise ValueError(f"{role} batch {batch_name} invalid after bounded retries; raw answers retained") from exc
            continue
        event = base._artifact({
            "schema_version": "wang_claim_passage_role_validation_event_v1",
            "raw_attempt_sha256": raw["artifact_sha256"], "status": "accepted",
        })
        base._write_immutable(attempts_root / f"{role}-{batch_name}-attempt-{number}-validation.json", event)
        artifact = base._artifact({
            "schema_version": base.RESPONSE_VERSION, "role": role,
            "model": client.model, "packet_sha256": packet_sha,
            "batch_id": batch_name, "claim_ids": ids,
            "decisions": validated,
        })
        base._write_immutable(output_root / f"{role}-{batch_name}.json", artifact)
        return validated
    raise AssertionError("unreachable retry state")


def review(original_root: Path, output_root: Path, store: PostgresKnowledgeStore,
           *, last_batch: int | None = None) -> dict[str, Any]:
    packet, manifest = _check_resume(original_root, output_root)
    _check_graph(packet, store)
    total_batches = math.ceil(len(packet["claims"]) / BATCH_SIZE)
    stop_at = total_batches if last_batch is None else last_batch
    if not CUTOVER_BATCH <= stop_at <= total_batches:
        raise ValueError("last batch outside new reviewer range")
    with (output_root / ".audited-resume.lock").open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        primary_client = CodexSubscriptionClient(model=PRIMARY_MODEL, reasoning_effort="high")
        independent_client = ClaudeSubscriptionClient(model=NEW_INDEPENDENT_MODEL, reasoning_effort="high")
        for batch_id in range(CUTOVER_BATCH, stop_at + 1):
            batch = _rows_for_batch(packet, batch_id)
            if batch_id == CUTOVER_BATCH:
                _checked_decisions(original_root / f"primary-{batch_id:05d}.json", packet, batch_id,
                                   "primary", PRIMARY_MODEL)
            elif (output_root / f"primary-{batch_id:05d}.json").exists():
                _checked_decisions(output_root / f"primary-{batch_id:05d}.json", packet, batch_id,
                                   "primary", PRIMARY_MODEL)
            else:
                _response_for_batch("primary", batch, client=primary_client,
                                    packet_sha=packet["artifact_sha256"],
                                    manifest_sha=manifest["artifact_sha256"],
                                    batch_id=batch_id, output_root=output_root)
            if (output_root / f"independent-{batch_id:05d}.json").exists():
                _checked_decisions(output_root / f"independent-{batch_id:05d}.json", packet, batch_id,
                                   "independent", NEW_INDEPENDENT_MODEL)
            else:
                _response_for_batch("independent", batch, client=independent_client,
                                    packet_sha=packet["artifact_sha256"],
                                    manifest_sha=manifest["artifact_sha256"],
                                    batch_id=batch_id, output_root=output_root)
            print(json.dumps({"batch_id": batch_id, "status": "complete"}), flush=True)
        _check_graph(packet, store)
        if stop_at != total_batches:
            return {"completed_through_batch": stop_at, "total_batches": total_batches}

        decisions: list[dict[str, Any]] = []
        review_shas: dict[str, str] = {}
        for batch_id in range(1, total_batches + 1):
            batch = _rows_for_batch(packet, batch_id)
            primary_root = original_root if batch_id <= CUTOVER_BATCH else output_root
            independent_root = original_root if batch_id < CUTOVER_BATCH else output_root
            a, a_sha = _checked_decisions(primary_root / f"primary-{batch_id:05d}.json",
                                          packet, batch_id, "primary", PRIMARY_MODEL)
            model = OLD_INDEPENDENT_MODEL if batch_id < CUTOVER_BATCH else NEW_INDEPENDENT_MODEL
            b, b_sha = _checked_decisions(independent_root / f"independent-{batch_id:05d}.json",
                                          packet, batch_id, "independent", model)
            review_shas[f"primary-{batch_id:05d}"] = a_sha
            review_shas[f"independent-{batch_id:05d}"] = b_sha
            decisions.extend(base.reconcile(a, b, batch))
        if len(decisions) != len(packet["claims"]) or len({row["claim_id"] for row in decisions}) != len(decisions):
            raise ValueError("final review denominator or ownership differs")
        ledger = base._artifact({
            "schema_version": "wang_claim_passage_role_ledger_v5",
            "status": "all_eligible_reviewed",
            "packet_sha256": packet["artifact_sha256"],
            "resume_manifest_sha256": manifest["artifact_sha256"],
            "primary_model": PRIMARY_MODEL,
            "independent_model_before_batch_69": OLD_INDEPENDENT_MODEL,
            "independent_model_from_batch_69": NEW_INDEPENDENT_MODEL,
            "review_artifact_shas": review_shas,
            "batch_size": BATCH_SIZE,
            "decisions": decisions,
            "counts": dict(sorted(Counter(row["role"] for row in decisions).items())),
        })
        base._write_immutable(output_root / "role-ledger-v5.json", ledger)
        return {"completed_through_batch": stop_at, "total_batches": total_batches,
                "ledger_sha256": ledger["artifact_sha256"], "counts": ledger["counts"]}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--last-batch", type=int)
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.prepare:
        if args.last_batch is not None:
            parser.error("--prepare cannot limit batches")
        result = prepare(args.original_root, args.output_root, store)
    else:
        result = review(args.original_root, args.output_root, store, last_batch=args.last_batch)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
