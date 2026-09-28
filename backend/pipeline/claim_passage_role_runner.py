"""Read-only, dual-subscription Claim-level passage-exegesis role review.

This produces a role ledger for #409. It does not mutate Claim master data,
infer passage roles from references, or authorize #395/CVP by itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.sermon_search.bible_refs import normalize_ref
from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, sha256_json,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


SCHEMA_VERSION = "wang_claim_passage_role_packet_v2"
RESPONSE_VERSION = "wang_claim_passage_role_decisions_v2"
PROMPT = Path(__file__).with_name("prompts") / "claim_passage_role_v2.md"
ROLES = {"passage_exegesis", "other", "unresolved"}
MAX_ROLE_REQUEST_BYTES = 250_000
RESPONSE_SCHEMA: dict[str, Any] = {
    "name": RESPONSE_VERSION,
    "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "decisions"],
        "properties": {
            "schema_version": {"type": "string", "enum": [RESPONSE_VERSION]},
            "decisions": {
                "type": "array", "items": {
                    "type": "object", "additionalProperties": False,
                    "required": ["claim_id", "role", "interpreted_ref_indices", "interpreted_evidence_refs", "evidence_quote", "reason"],
                    "properties": {
                        "claim_id": {"type": "string"},
                        "role": {"type": "string", "enum": sorted(ROLES)},
                        "interpreted_ref_indices": {"type": "array", "items": {"type": "integer"}},
                        "interpreted_evidence_refs": {"type": "array", "items": {"type": "string"}},
                        "evidence_quote": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                },
            },
        },
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _write_immutable(path: Path, value: Mapping[str, Any]) -> None:
    if path.exists():
        if _read_json(path) != value:
            raise ValueError(f"immutable artifact differs: {path}")
        return
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.",
        suffix=".tmp", delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    try:
        if path.exists():
            if _read_json(path) != value:
                raise ValueError(f"immutable artifact differs: {path}")
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _artifact(body: Mapping[str, Any]) -> dict[str, Any]:
    return dict(body) | {"artifact_sha256": sha256_json(body)}


def _check_artifact(value: Mapping[str, Any]) -> None:
    body = {key: item for key, item in value.items() if key != "artifact_sha256"}
    if value.get("artifact_sha256") != sha256_json(body):
        raise ValueError("artifact SHA mismatch")


def _manifest_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _read_json(path)
    _check_artifact(manifest)
    if manifest.get("mode") != "preview":
        raise ValueError("#409 pilot expects a read-only #395 preview manifest")
    rows = [dict(row) for partition in manifest["partitions"] for row in partition["claims"]]
    ids = [str(row["claim_id"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("manifest primary Claim ownership is not unique")
    return manifest, rows


def select_pilot(rows: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    """Deterministically sample both referenced and non-referenced Claims."""

    if size < 1:
        raise ValueError("pilot size must be positive")
    ordered = sorted(rows, key=lambda row: hashlib.sha256(str(row["claim_id"]).encode()).hexdigest())
    with_refs = [row for row in ordered if row.get("scripture_refs")]
    without_refs = [row for row in ordered if not row.get("scripture_refs")]
    selected = with_refs[: min(len(with_refs), (size + 1) // 2)]
    selected += without_refs[: min(len(without_refs), size - len(selected))]
    selected_ids = {row["claim_id"] for row in selected}
    selected += [row for row in ordered if row["claim_id"] not in selected_ids][: size - len(selected)]
    return sorted(selected, key=lambda row: row["claim_id"])


def _records(cursor: Any, collection: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    if not ids:
        return {}
    cursor.execute(
        """SELECT object_id,revision,content_sha256,payload
           FROM wang_knowledge.objects
           WHERE collection=%s AND object_id=ANY(%s) AND retired_at IS NULL""",
        (collection, ids),
    )
    return {
        str(object_id): {"revision": int(revision), "content_sha256": str(content_sha), "payload": payload}
        for object_id, revision, content_sha, payload in cursor
    }


def _claim_matches_pin(claim: Mapping[str, Any], pin: Mapping[str, Any]) -> bool:
    payload = claim["payload"]
    return (
        claim["revision"] == pin["pinned_claim_revision"]
        and claim["content_sha256"] == pin["claim_revision_sha256"]
        and payload.get("statement") == pin["statement"]
        # The #395 preview sorts refs for stable routing; Claim master data
        # retains extraction order. Compare the same refs with multiplicity.
        and sorted(payload.get("scripture_refs") or []) == sorted(pin.get("scripture_refs") or [])
        and payload.get("review_status") in {"ai_consensus_reviewed", "approved"}
    )


def build_rows(store: PostgresKnowledgeStore, pins: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Freeze complete source-local evidence for selected current Claim pins."""

    ids = [str(pin["claim_id"]) for pin in pins]
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        claims = _records(cursor, "claims", ids)
        if set(claims) != set(ids):
            raise ValueError("Claim disappeared before role packet freeze")
        evidence_ids = sorted({str(eid) for row in claims.values() for eid in row["payload"].get("evidence_step_ids") or []})
        evidence = _records(cursor, "evidence_steps", evidence_ids)
        if set(evidence) != set(evidence_ids):
            raise ValueError("EvidenceStep disappeared before role packet freeze")
        fragment_ids = sorted({
            str(fid) for row in evidence.values() for fid in
            list(row["payload"].get("source_fragment_ids") or [])
            + ([row["payload"]["source_fragment_id"]] if row["payload"].get("source_fragment_id") else [])
        })
        fragments = _records(cursor, "source_fragments", fragment_ids)
        if set(fragments) != set(fragment_ids):
            raise ValueError("SourceFragment disappeared before role packet freeze")
        source_ids = sorted({str(pin["source_id"]) for pin in pins})
        sources = _records(cursor, "source_documents", source_ids)
        if set(sources) != set(source_ids):
            raise ValueError("SourceDocument disappeared before role packet freeze")
    output: list[dict[str, Any]] = []
    for pin in pins:
        claim_id = str(pin["claim_id"])
        claim = claims[claim_id]
        payload = claim["payload"]
        if not _claim_matches_pin(claim, pin):
            raise ValueError(f"Claim pin or review status changed: {claim_id}")
        steps: list[dict[str, Any]] = []
        for evidence_id in payload.get("evidence_step_ids") or []:
            step = evidence[evidence_id]
            ep = step["payload"]
            if claim_id not in ep.get("produced_claim_ids", []):
                raise ValueError(f"Claim/EvidenceStep not reciprocal: {claim_id}/{evidence_id}")
            fragment_rows = []
            for fragment_id in dict.fromkeys(
                list(ep.get("source_fragment_ids") or [])
                + ([ep["source_fragment_id"]] if ep.get("source_fragment_id") else [])
            ):
                fragment = fragments[fragment_id]
                fp = fragment["payload"]
                if fp.get("source_id") != pin["source_id"]:
                    raise ValueError(f"cross-source fragment: {claim_id}/{fragment_id}")
                fragment_rows.append({
                    "fragment_id": fragment_id,
                    "revision": fragment["revision"],
                    "content_sha256": fragment["content_sha256"],
                    "paragraph_key": fp.get("paragraph_key"),
                    "verbatim_excerpt": fp.get("verbatim_excerpt"),
                })
            steps.append({
                "evidence_step_id": evidence_id,
                "revision": step["revision"],
                "content_sha256": step["content_sha256"],
                "statement": ep.get("statement"),
                "scripture_refs": ep.get("scripture_refs") or [],
                "fragments": fragment_rows,
            })
        source = sources[str(pin["source_id"])]
        output.append({
            "claim_id": claim_id,
            "claim_revision": claim["revision"],
            "claim_content_sha256": claim["content_sha256"],
            "source_id": str(pin["source_id"]),
            "source_revision": source["revision"],
            "source_content_sha256": source["content_sha256"],
            "source_file_sha256": source["payload"].get("source_file_sha256"),
            "statement": payload["statement"],
            "claim_type": payload.get("claim_type"),
            "scripture_refs": payload.get("scripture_refs") or [],
            "evidence_steps": steps,
        })
    return output


def validate_response(response: Mapping[str, Any], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if response.get("schema_version") != RESPONSE_VERSION:
        raise ValueError("unsupported role response")
    expected = {row["claim_id"]: row for row in rows}
    decisions = response.get("decisions")
    if not isinstance(decisions, list) or len(decisions) != len(expected):
        raise ValueError("role response denominator mismatch")
    result: dict[str, dict[str, Any]] = {}
    for raw in decisions:
        claim_id = raw.get("claim_id")
        if claim_id not in expected or claim_id in result:
            raise ValueError(f"duplicate or foreign Claim decision: {claim_id}")
        row = expected[claim_id]
        role = raw.get("role")
        indices = raw.get("interpreted_ref_indices")
        evidence_refs = raw.get("interpreted_evidence_refs")
        quote = str(raw.get("evidence_quote") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        if role not in ROLES or not isinstance(indices, list) or not isinstance(evidence_refs, list) or not quote or not reason:
            raise ValueError(f"invalid role decision: {claim_id}")
        if any(not isinstance(index, int) or isinstance(index, bool) or index < 0
               or index >= len(row["scripture_refs"]) for index in indices):
            raise ValueError(f"invalid interpreted reference index: {claim_id}")
        allowed_evidence_refs = {
            str(ref) for step in row["evidence_steps"] for ref in step["scripture_refs"]
        }
        if any(not isinstance(ref, str) or ref not in allowed_evidence_refs for ref in evidence_refs):
            raise ValueError(f"interpreted EvidenceStep reference is not exact: {claim_id}")
        if (
            len(indices) != len(set(indices))
            or len(evidence_refs) != len(set(evidence_refs))
            or (role == "passage_exegesis") != bool(indices or evidence_refs)
        ):
            raise ValueError(f"interpreted references disagree with role: {claim_id}")
        texts = [row["statement"]] + [
            str(fragment.get("verbatim_excerpt") or "")
            for step in row["evidence_steps"] for fragment in step["fragments"]
        ]
        if not any(quote in text for text in texts):
            raise ValueError(f"role evidence quote is not exact: {claim_id}")
        result[claim_id] = {
            "claim_id": claim_id, "role": role,
            "interpreted_ref_indices": sorted(indices),
            "interpreted_evidence_refs": sorted(evidence_refs),
            "evidence_quote": quote, "reason": reason,
        }
    return [result[claim_id] for claim_id in sorted(expected)]


def _passage_keys(decision: Mapping[str, Any], row: Mapping[str, Any]) -> list[str]:
    raw_refs = [row["scripture_refs"][index] for index in decision["interpreted_ref_indices"]]
    raw_refs += decision["interpreted_evidence_refs"]
    return sorted({(normalized.osis if (normalized := normalize_ref(str(ref))) else f"raw:{ref}")
                   for ref in raw_refs})


def reconcile(primary: list[dict[str, Any]], independent: list[dict[str, Any]],
              rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if [row["claim_id"] for row in primary] != [row["claim_id"] for row in independent]:
        raise ValueError("independent review denominator differs")
    row_index = {row["claim_id"]: row for row in rows}
    if set(row_index) != {row["claim_id"] for row in primary}:
        raise ValueError("review source rows differ")
    output = []
    for a, b in zip(primary, independent, strict=True):
        a_keys = _passage_keys(a, row_index[a["claim_id"]])
        b_keys = _passage_keys(b, row_index[b["claim_id"]])
        role_agrees = a["role"] == b["role"] != "unresolved"
        passage_agrees = role_agrees and a_keys == b_keys
        output.append({
            "claim_id": a["claim_id"],
            "role": a["role"] if role_agrees else "unresolved",
            "interpreted_passage_keys": a_keys if passage_agrees else [],
            "passage_identity_status": (
                "agreed" if passage_agrees and a_keys else
                "not_applicable" if role_agrees and a["role"] == "other" else
                "disputed"
            ),
            "decision_basis": "dual_model_consensus" if role_agrees else "review_disagreement_or_uncertainty",
            "primary": a,
            "independent": b,
        })
    return output


def prepare(manifest_path: Path, output_root: Path, store: PostgresKnowledgeStore, pilot_size: int) -> dict[str, Any]:
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("output root must be empty for prepare")
    manifest, pins = _manifest_rows(manifest_path)
    if pilot_size:
        pins = select_pilot(pins, pilot_size)
    rows = build_rows(store, pins)
    body = {
        "schema_version": SCHEMA_VERSION,
        "mode": "pilot" if pilot_size else "all_eligible",
        "manifest_sha256": manifest["artifact_sha256"],
        "prompt_sha256": hashlib.sha256(PROMPT.read_bytes()).hexdigest(),
        "runner_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "claims": rows,
    }
    packet = _artifact(body)
    output_root.mkdir(parents=True, exist_ok=True)
    _write_immutable(output_root / "role-packet.json", packet)
    return {"claim_count": len(rows), "packet_sha256": packet["artifact_sha256"], "output_root": str(output_root)}


def review(output_root: Path, store: PostgresKnowledgeStore, *, batch_size: int,
           primary_model: str, independent_model: str) -> dict[str, Any]:
    packet = _read_json(output_root / "role-packet.json")
    _check_artifact(packet)
    if packet.get("schema_version") != SCHEMA_VERSION or not 1 <= batch_size <= 40:
        raise ValueError("invalid role packet or batch size")
    if packet["prompt_sha256"] != hashlib.sha256(PROMPT.read_bytes()).hexdigest():
        raise ValueError("role prompt changed")
    if packet["runner_code_sha256"] != hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError("role runner code changed")
    rows = packet["claims"]
    pins = [{
        "claim_id": row["claim_id"], "pinned_claim_revision": row["claim_revision"],
        "claim_revision_sha256": row["claim_content_sha256"], "source_id": row["source_id"],
        "statement": row["statement"], "scripture_refs": row["scripture_refs"],
    } for row in rows]
    if build_rows(store, pins) != rows:
        raise ValueError("role packet source/Claim graph changed")
    primary_client = CodexSubscriptionClient(model=primary_model, reasoning_effort="high")
    independent_client = ClaudeSubscriptionClient(model=independent_model, reasoning_effort="high")
    prompt = PROMPT.read_text(encoding="utf-8")
    results = []
    for offset in range(0, len(rows), batch_size):
        batch = rows[offset:offset + batch_size]
        batch_id = f"{offset // batch_size + 1:05d}"
        user_payload = json.dumps({"claims": batch}, ensure_ascii=False, sort_keys=True)
        if len((prompt + user_payload + json.dumps(RESPONSE_SCHEMA, ensure_ascii=False)).encode("utf-8")) > MAX_ROLE_REQUEST_BYTES:
            raise ValueError(f"role review request exceeds byte ceiling: {batch_id}")
        primary_path = output_root / f"primary-{batch_id}.json"
        independent_path = output_root / f"independent-{batch_id}.json"
        if primary_path.exists():
            primary_artifact = _read_json(primary_path)
            _check_artifact(primary_artifact)
            if (
                primary_artifact.get("packet_sha256") != packet["artifact_sha256"]
                or primary_artifact.get("model") != primary_model
                or primary_artifact.get("batch_id") != batch_id
                or primary_artifact.get("claim_ids") != [row["claim_id"] for row in batch]
            ):
                raise ValueError("primary review belongs to another packet")
            primary = primary_artifact["decisions"]
        else:
            response = primary_client.generate_json(prompt, user_payload, RESPONSE_SCHEMA)
            primary = validate_response(response, batch)
            primary_artifact = _artifact({
                "schema_version": RESPONSE_VERSION, "role": "primary",
                "model": primary_model, "packet_sha256": packet["artifact_sha256"],
                "batch_id": batch_id, "claim_ids": [row["claim_id"] for row in batch],
                "decisions": primary,
            })
            _write_immutable(primary_path, primary_artifact)
        if independent_path.exists():
            independent_artifact = _read_json(independent_path)
            _check_artifact(independent_artifact)
            if (
                independent_artifact.get("packet_sha256") != packet["artifact_sha256"]
                or independent_artifact.get("model") != independent_model
                or independent_artifact.get("batch_id") != batch_id
                or independent_artifact.get("claim_ids") != [row["claim_id"] for row in batch]
            ):
                raise ValueError("independent review belongs to another packet")
            independent = independent_artifact["decisions"]
        else:
            # The independent reviewer receives the same source packet, never the proposal.
            response = independent_client.generate_json(prompt, user_payload, RESPONSE_SCHEMA)
            independent = validate_response(response, batch)
            independent_artifact = _artifact({
                "schema_version": RESPONSE_VERSION, "role": "independent",
                "model": independent_model, "packet_sha256": packet["artifact_sha256"],
                "batch_id": batch_id, "claim_ids": [row["claim_id"] for row in batch],
                "decisions": independent,
            })
            _write_immutable(independent_path, independent_artifact)
        # Validate cached outputs as strictly as fresh model outputs.
        primary = validate_response({"schema_version": RESPONSE_VERSION, "decisions": primary}, batch)
        independent = validate_response({"schema_version": RESPONSE_VERSION, "decisions": independent}, batch)
        results.extend(reconcile(primary, independent, batch))
    if build_rows(store, pins) != rows:
        raise ValueError("role packet changed during model review")
    body = {
        "schema_version": "wang_claim_passage_role_ledger_v4",
        "status": "pilot_reviewed" if packet["mode"] == "pilot" else "all_eligible_reviewed",
        "packet_sha256": packet["artifact_sha256"],
        "primary_model": primary_model, "independent_model": independent_model,
        "batch_size": batch_size, "decisions": results,
        "counts": dict(sorted(Counter(row["role"] for row in results).items())),
    }
    ledger = _artifact(body)
    _write_immutable(output_root / "role-ledger-v4.json", ledger)
    return {"claim_count": len(results), "counts": ledger["counts"],
            "ledger_sha256": ledger["artifact_sha256"], "output_root": str(output_root)}


def reconcile_existing(output_root: Path, store: PostgresKnowledgeStore) -> dict[str, Any]:
    """Re-evaluate immutable completed reviews without another model call."""

    packet = _read_json(output_root / "role-packet.json")
    _check_artifact(packet)
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported existing packet")
    rows = packet["claims"]
    pins = [{
        "claim_id": row["claim_id"], "pinned_claim_revision": row["claim_revision"],
        "claim_revision_sha256": row["claim_content_sha256"], "source_id": row["source_id"],
        "statement": row["statement"], "scripture_refs": row["scripture_refs"],
    } for row in rows]
    if build_rows(store, pins) != rows:
        raise ValueError("existing role packet is stale")
    pairs: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    batch_ids = sorted(path.stem.removeprefix("primary-") for path in output_root.glob("primary-*.json"))
    if not batch_ids:
        raise ValueError("no completed model reviews")
    for batch_id in batch_ids:
        primary = _read_json(output_root / f"primary-{batch_id}.json")
        independent = _read_json(output_root / f"independent-{batch_id}.json")
        for artifact, role in ((primary, "primary"), (independent, "independent")):
            _check_artifact(artifact)
            if (
                artifact.get("role") != role
                or artifact.get("packet_sha256") != packet["artifact_sha256"]
                or artifact.get("batch_id") != batch_id
            ):
                raise ValueError(f"{role} review binding differs: {batch_id}")
        if primary["claim_ids"] != independent["claim_ids"]:
            raise ValueError(f"batch Claim IDs differ: {batch_id}")
        pairs.append((batch_id, primary, independent))
    by_id = {row["claim_id"]: row for row in rows}
    decisions: list[dict[str, Any]] = []
    primary_model = independent_model = None
    for _, primary, independent in pairs:
        ids = primary["claim_ids"]
        if any(claim_id not in by_id for claim_id in ids):
            raise ValueError("review includes foreign Claim")
        batch = [by_id[claim_id] for claim_id in ids]
        a = validate_response({"schema_version": RESPONSE_VERSION, "decisions": primary["decisions"]}, batch)
        b = validate_response({"schema_version": RESPONSE_VERSION, "decisions": independent["decisions"]}, batch)
        decisions.extend(reconcile(a, b, batch))
        if primary_model not in (None, primary["model"]) or independent_model not in (None, independent["model"]):
            raise ValueError("review model changed between batches")
        primary_model, independent_model = primary["model"], independent["model"]
    if sorted(row["claim_id"] for row in decisions) != sorted(by_id):
        raise ValueError("completed reviews do not cover packet exactly once")
    decisions.sort(key=lambda row: row["claim_id"])
    body = {
        "schema_version": "wang_claim_passage_role_ledger_v4",
        "status": "pilot_reviewed" if packet["mode"] == "pilot" else "all_eligible_reviewed",
        "packet_sha256": packet["artifact_sha256"],
        "reconciliation_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "primary_model": primary_model, "independent_model": independent_model,
        "decisions": decisions,
        "counts": dict(sorted(Counter(row["role"] for row in decisions).items())),
    }
    ledger = _artifact(body)
    _write_immutable(output_root / "role-ledger-v4.json", ledger)
    return {"claim_count": len(decisions), "counts": ledger["counts"],
            "ledger_sha256": ledger["artifact_sha256"], "output_root": str(output_root)}


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--pilot-size", type=int, default=0)
    parser.add_argument("--review", action="store_true")
    parser.add_argument("--reconcile-only", action="store_true")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--primary-model", default="gpt-6-sol")
    parser.add_argument("--independent-model", default="claude-fable-5-1")
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.review and args.reconcile_only:
        parser.error("choose --review or --reconcile-only")
    if args.reconcile_only:
        if args.manifest is not None or args.pilot_size:
            parser.error("--reconcile-only consumes completed model reviews")
        result = reconcile_existing(args.output_root, store)
    elif args.review:
        if args.manifest is not None or args.pilot_size:
            parser.error("--review consumes an already prepared packet")
        result = review(args.output_root, store, batch_size=args.batch_size,
                        primary_model=args.primary_model,
                        independent_model=args.independent_model)
    else:
        if args.manifest is None:
            parser.error("--manifest is required for prepare")
        result = prepare(args.manifest, args.output_root, store, args.pilot_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
