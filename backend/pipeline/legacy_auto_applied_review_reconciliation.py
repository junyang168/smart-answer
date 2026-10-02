"""Read-only historical replay and guarded migration for legacy accepted patches.

The 2026-09-04 reviewed candidates were generated before the current full-package
reciprocity/seal contract.  This one-time verifier executes the exact, SHA-pinned
historical consensus function in memory, requires a byte-for-byte JSON-object
replay of the reviewed package, then checks each Claim's current local evidence
graph.  It never relaxes the current ingestion validator or calls a model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import types
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    _contains_exact_value,
    _normalize_records,
    sha256_json,
)
from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.corpus_ai_adjudication_runner import _compile_overrides
from backend.pipeline.corpus_ai_review_runner import _normalize_claim_layer
from backend.pipeline.knowledge_source import markdown_source_document
from backend.pipeline.legacy_candidate_reconciliation import (
    _current_claims,
    _encode_report,
    apply_frozen,
    inspect_legacy_bundle,
    inventory,
    plan_review_migration,
)
from backend.pipeline.legacy_withdrawn_review_reconciliation import (
    _live_graph_guard,
    _related_records,
)

SOURCE_KIND = "legacy_adjudicated_auto_applied_review_reconciliation_v1"
READY_REASON = "auto_applied_historical_replay_graph_verified"
HISTORICAL_COMMIT = "ef3fbf9eab071707b7a62a9a43047a1cb3ab01c7"
HISTORICAL_PATH = "backend/pipeline/knowledge_consensus_applier.py"
HISTORICAL_CODE_SHA256 = (
    "147397b660af6f35109b5eaaa710bebf57c227970e173459d749b926557853d9"
)
_historical_function: Any = None


def _historical_applier() -> Any:
    global _historical_function
    if _historical_function is not None:
        return _historical_function
    repo = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        ["git", "show", f"{HISTORICAL_COMMIT}:{HISTORICAL_PATH}"],
        cwd=repo, capture_output=True, check=True,
    )
    if hashlib.sha256(result.stdout).hexdigest() != HISTORICAL_CODE_SHA256:
        raise ValueError("historical consensus code SHA changed")
    module = types.ModuleType("wang_legacy_consensus_replay_readonly")
    exec(  # noqa: S102 - execute only our immutable, SHA-checked Git source
        compile(result.stdout, f"{HISTORICAL_COMMIT}:{HISTORICAL_PATH}", "exec"),
        module.__dict__,
    )
    _historical_function = module.apply_consensus_overrides
    return _historical_function


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_historical_replay(reviewed_path: Path) -> dict[str, str] | None:
    """Prove the exact old package came from its original, adjudicated inputs."""

    bundle = inspect_legacy_bundle(reviewed_path)
    if bundle["reason"] is not None:
        return None
    package = json.loads(Path(bundle["package_path"]).read_text(encoding="utf-8"))
    adjudication = json.loads(
        Path(bundle["adjudication_path"]).read_text(encoding="utf-8")
    )
    stem = reviewed_path.name.removesuffix(".reviewed-candidate.json")
    overrides_path = reviewed_path.parent.parent / "overrides" / f"{stem}.consensus-overrides.json"
    if not overrides_path.is_file():
        return None
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    survey = _normalize_claim_layer(package)
    claims = {str(row["claim_id"]): row for row in survey["candidate_claims"]}
    compiled = _compile_overrides(
        outcome=adjudication, claims_by_id=claims,
        fingerprint=adjudication["adjudicator"],
        generated_at=str(overrides["generated_at"]),
    )
    unsealed = dict(compiled)
    unsealed.pop("artifact_sha256", None)
    if overrides != compiled and overrides != unsealed:
        return None
    transcripts: dict[str, dict[str, Any]] = {}
    for source in package.get("source_documents") or []:
        source_id = str(source["source_id"])
        transcript_id = str(source.get("transcript_id") or source_id)
        if source.get("source_type") == "notes_manuscript":
            payload, _, _ = markdown_source_document(source)
        else:
            source_path = Path(bundle["review_transcript_paths"][source_id])
            raw = json.loads(source_path.read_text(encoding="utf-8"))
            payload = {"script": raw} if isinstance(raw, list) else raw
        if not isinstance(payload, dict):
            return None
        transcripts[transcript_id] = payload
    replayed = _historical_applier()(package, overrides, transcripts)
    reviewed = json.loads(reviewed_path.read_text(encoding="utf-8"))
    if replayed != reviewed:
        return None
    return {
        "historical_replay_sha256": sha256_json(replayed),
        "historical_replay_code_sha256": HISTORICAL_CODE_SHA256,
        "overrides_sha256": _sha(overrides_path),
    }


def dry_run(
    artifact_root: Path, output_root: Path, store: PostgresKnowledgeStore,
) -> dict[str, Any]:
    output_root.mkdir(parents=True, exist_ok=True)
    if any(output_root.iterdir()):
        raise ValueError("dry-run output root must be empty")
    report = inventory(artifact_root, store)
    rows = report["rows"]
    candidates = [row for row in rows if
                  row["reason"] == "legacy_adjudicated_claim_needs_patch_replay"
                  and row.get("adjudication_status") == "auto_applied"
                  and row.get("review_decision") == "changes_suggested"]
    replay: dict[str, tuple[dict[str, str] | None, dict[str, Any] | None]] = {}
    items: list[tuple[dict[str, Any], dict[tuple[str, str], dict[str, Any]], dict[str, str]]] = []
    for row in candidates:
        path = str(row["reviewed_candidate_path"])
        if path not in replay:
            proof = verify_historical_replay(Path(path))
            normalized = None
            if proof is not None:
                normalized, _ = _normalize_records(json.loads(
                    Path(path).read_text(encoding="utf-8")))
            replay[path] = proof, normalized
        proof, normalized = replay[path]
        if proof is None or normalized is None:
            continue
        try:
            expected = _related_records(normalized, row["claim_id"])
        except ValueError:
            continue
        items.append((row, expected, proof))

    ids = [row["claim_id"] for row, _, _ in items]
    evidence_ids = sorted({
        evidence_id for _, expected, _ in items
        for collection, evidence_id in expected if collection == "evidence_steps"
    })
    related_ids = sorted({
        object_id for _, expected, _ in items for _, object_id in expected
    })
    live: dict[str, dict[tuple[str, str], tuple[int, str, dict[str, Any]]]] = {
        claim_id: {} for claim_id in ids
    }
    source_hashes: dict[str, set[str]] = {}
    if ids:
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT object_id, payload FROM wang_knowledge.objects
                   WHERE collection='source_documents' AND retired_at IS NULL
                     AND object_id = ANY(%s)""",
                (sorted({row["source_id"] for row, _, _ in items}),),
            )
            for source_id, payload in cursor.fetchall():
                source_hashes[str(source_id)] = {
                    str(value) for value in (
                        payload.get("source_file_sha256"),
                        payload.get("source_body_sha256"),
                    ) if value
                }
            cursor.execute(
                """SELECT collection, object_id, revision, content_sha256, payload
                   FROM wang_knowledge.objects
                   WHERE retired_at IS NULL AND collection <> 'claims'
                     AND (payload::text LIKE ANY(%s) OR object_id = ANY(%s))""",
                ([f"%{value}%" for value in ids + evidence_ids], related_ids),
            )
            for collection, object_id, revision, content_sha, payload in cursor.fetchall():
                for row, expected, _ in items:
                    claim_id = row["claim_id"]
                    own_evidence_ids = {
                        evidence_id for coll, evidence_id in expected
                        if coll == "evidence_steps"
                    }
                    if (
                        _contains_exact_value(payload, claim_id)
                        or (collection, object_id) in expected
                        or (collection == "knowledge_relations" and any(
                            _contains_exact_value(payload, evidence_id)
                            for evidence_id in own_evidence_ids
                        ))
                    ):
                        live[claim_id][(str(collection), str(object_id))] = (
                            int(revision), str(content_sha), payload
                        )

    guards: dict[str, list[dict[str, Any]]] = {}
    for row, expected, proof in items:
        guard = _live_graph_guard(expected, live[row["claim_id"]], source_hashes)
        if guard is None:
            continue
        row.update({
            "reason": READY_REASON,
            "target_review_status": "ai_consensus_reviewed",
            "graph_guard_sha256": sha256_json(guard),
            **proof,
        })
        guards[row["claim_id"]] = guard

    report["counts"] = dict(sorted(Counter(row["reason"] for row in rows).items()))
    report["snapshot_sha256"] = sha256_json(rows)
    report["note"] = (
        "Read-only historical replay and current local graph qualification. "
        "Migration still requires backup, artifact/graph CAS and readback."
    )
    ready: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["reason"] == READY_REASON:
            ready[row["source_id"]].append(row)
    current = _current_claims(store, list(guards))
    plans = [
        plan_review_migration(group, current,
                              freeze_sha256=report["snapshot_sha256"],
                              source_kind=SOURCE_KIND)
        for _, group in sorted(ready.items())
    ]
    document = {
        "schema_version": "wang_legacy_auto_applied_replay_migration_plans_v1",
        "freeze_sha256": report["snapshot_sha256"],
        "claim_related_graph_guards": guards,
        "plans": [asdict(plan) for plan in plans],
    }
    _encode_report(output_root / "preflight.json", report)
    _encode_report(output_root / "migration-plans.json", document)
    return {
        "auto_applied_residual": len(candidates),
        "exact_historical_replay_bundles": sum(
            proof is not None for proof, _ in replay.values()),
        "local_graph_valid": len(items),
        "qualified": len(guards),
        "source_units": len(plans),
        "snapshot_sha256": report["snapshot_sha256"],
        "output_root": str(output_root),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path,
                        default=wang_platform_paths().claim_layer_staging)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup-path", type=Path)
    parser.add_argument("--max-source-units", type=int)
    args = parser.parse_args()
    store = PostgresKnowledgeStore()
    if args.apply:
        if args.backup_path is None:
            parser.error("--apply requires --backup-path")
        result = apply_frozen(args.output_root, args.backup_path, store,
                              max_source_units=args.max_source_units)
    else:
        result = dry_run(args.artifact_root, args.output_root, store)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
