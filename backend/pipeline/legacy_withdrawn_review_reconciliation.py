"""Conservatively reconcile legacy withdrawn review objections without model calls.

Only a withdrawal that left the Claim and its entire directly related package
graph untouched may qualify.  The live graph must match that package and is
frozen by revision/hash for a transactional compare-and-swap at apply time.
Other adjudication outcomes remain in the residual queue.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
    _contains_exact_value,
    _normalize_records,
    _substantive_payload,
    sha256_json,
)
from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.legacy_candidate_reconciliation import (
    _current_claims,
    _encode_report,
    apply_frozen,
    inspect_legacy_bundle,
    inventory,
    plan_review_migration,
)

SOURCE_KIND = "legacy_adjudicated_withdrawn_review_reconciliation_v1"
READY_REASON = "withdrawn_unchanged_graph_verified"


def _without_nulls(value: Any) -> Any:
    # Pydantic's package dump explicitly includes absent nullable fields; the
    # PostgreSQL importer preserves whether they were stated.  None and absent
    # are equivalent here, but no non-null value is normalized away.
    if isinstance(value, dict):
        return {key: _without_nulls(child) for key, child in value.items()
                if child is not None}
    if isinstance(value, list):
        return [_without_nulls(child) for child in value]
    return value


def _comparison_payload(
    collection: str, payload: Mapping[str, Any],
    source_hashes: Mapping[str, set[str]],
) -> dict[str, Any]:
    result = _without_nulls(_substantive_payload(payload))
    if collection == "source_fragments":
        source_id = str(result.get("source_id") or "")
        if result.get("source_sha256") in source_hashes.get(source_id, set()):
            # Historical fragment records used raw-file SHA; current records
            # may use canonical-body SHA for the same frozen SourceDocument.
            result.pop("source_sha256", None)
        if result.get("visual_facts") == []:
            result.pop("visual_facts")
    return result


def _related_records(
    collections: Mapping[str, Mapping[str, Mapping[str, Any]]], claim_id: str,
) -> dict[tuple[str, str], Mapping[str, Any]]:
    claim = collections["claims"][claim_id]
    evidence_ids = set(claim.get("evidence_step_ids") or [])
    steps = collections.get("evidence_steps", {})
    if any(
        evidence_id not in steps
        or claim_id not in (steps[evidence_id].get("produced_claim_ids") or [])
        for evidence_id in evidence_ids
    ):
        raise ValueError(f"Claim/EvidenceStep reciprocity is incomplete: {claim_id}")
    fragment_ids = {
        fragment_id
        for evidence_id in evidence_ids
        for fragment_id in (
            list(steps[evidence_id].get("source_fragment_ids") or [])
            + ([steps[evidence_id]["source_fragment_id"]]
               if steps[evidence_id].get("source_fragment_id") else [])
        )
    }
    fragments = collections.get("source_fragments", {})
    if any(fragment_id not in fragments for fragment_id in fragment_ids):
        raise ValueError(f"Claim evidence has a missing source fragment: {claim_id}")
    related = {
        (collection, object_id): payload
        for collection, records in collections.items()
        if collection != "claims"
        for object_id, payload in records.items()
        if (
            _contains_exact_value(payload, claim_id)
            or (collection == "evidence_steps" and object_id in evidence_ids)
            or (collection == "source_fragments" and object_id in fragment_ids)
            or (collection == "knowledge_relations" and any(
                _contains_exact_value(payload, evidence_id)
                for evidence_id in evidence_ids
            ))
        )
    }
    return related


def _package_graph_unchanged(
    original: Mapping[str, Mapping[str, Mapping[str, Any]]],
    reviewed: Mapping[str, Mapping[str, Mapping[str, Any]]],
    claim_id: str,
) -> bool:
    if original["claims"].get(claim_id) != reviewed["claims"].get(claim_id):
        return False
    try:
        return _related_records(original, claim_id) == _related_records(reviewed, claim_id)
    except ValueError:
        return False


def _live_graph_guard(
    expected: Mapping[tuple[str, str], Mapping[str, Any]],
    live: Mapping[tuple[str, str], tuple[int, str, Mapping[str, Any]]],
    source_hashes: Mapping[str, set[str]],
) -> list[dict[str, Any]] | None:
    if set(expected) != set(live):
        return None
    for key, payload in expected.items():
        if _comparison_payload(key[0], payload, source_hashes) != _comparison_payload(
            key[0], live[key][2], source_hashes
        ):
            return None
    return [
        {"collection": collection, "object_id": object_id,
         "revision": live[(collection, object_id)][0],
         "content_sha256": live[(collection, object_id)][1]}
        for collection, object_id in sorted(expected)
    ]


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
                  and row.get("adjudication_status") == "withdrawn"
                  and row.get("review_decision") == "changes_suggested"]
    bundles: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
    package_unchanged: list[tuple[dict[str, Any], dict[tuple[str, str], Mapping[str, Any]]]] = []
    for row in candidates:
        path = str(row["reviewed_candidate_path"])
        if path not in bundles:
            bundle = inspect_legacy_bundle(Path(path))
            if bundle["reason"] is not None:
                raise ValueError(f"validated bundle changed since inventory: {path}")
            original, _ = _normalize_records(json.loads(
                Path(bundle["package_path"]).read_text(encoding="utf-8")))
            reviewed, _ = _normalize_records(json.loads(
                Path(path).read_text(encoding="utf-8")))
            bundles[path] = bundle, original, reviewed
        _, original, reviewed = bundles[path]
        claim_id = row["claim_id"]
        if _package_graph_unchanged(original, reviewed, claim_id):
            package_unchanged.append((row, _related_records(reviewed, claim_id)))

    ids = [row["claim_id"] for row, _ in package_unchanged]
    evidence_ids = sorted({
        evidence_id
        for _, expected in package_unchanged
        for (collection, evidence_id) in expected
        if collection == "evidence_steps"
    })
    related_object_ids = sorted({
        object_id for _, expected in package_unchanged
        for _, object_id in expected
    })
    live: dict[str, dict[tuple[str, str], tuple[int, str, Mapping[str, Any]]]] = {
        claim_id: {} for claim_id in ids
    }
    source_hashes: dict[str, set[str]] = {}
    if ids:
        with store.connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                """SELECT object_id, payload FROM wang_knowledge.objects
                   WHERE collection='source_documents' AND retired_at IS NULL
                     AND object_id = ANY(%s)""",
                (sorted({row["source_id"] for row, _ in package_unchanged}),),
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
                ([f"%{value}%" for value in ids + evidence_ids], related_object_ids),
            )
            for collection, object_id, revision, content_sha, payload in cursor.fetchall():
                for row, expected in package_unchanged:
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
    for row, expected in package_unchanged:
        guard = _live_graph_guard(expected, live[row["claim_id"]], source_hashes)
        if guard is None:
            continue
        row.update({
            "reason": READY_REASON,
            "target_review_status": "ai_consensus_reviewed",
            "graph_guard_sha256": sha256_json(guard),
        })
        guards[row["claim_id"]] = guard

    report["counts"] = dict(sorted(Counter(row["reason"] for row in rows).items()))
    report["snapshot_sha256"] = sha256_json(rows)
    report["note"] = (
        "Read-only withdrawn-objection qualification. Status-only migration "
        "requires backup, frozen artifact/graph CAS, and review-event readback."
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
        "schema_version": "wang_legacy_withdrawn_review_migration_plans_v1",
        "freeze_sha256": report["snapshot_sha256"],
        "claim_related_graph_guards": guards,
        "plans": [asdict(plan) for plan in plans],
    }
    _encode_report(output_root / "preflight.json", report)
    _encode_report(output_root / "migration-plans.json", document)
    return {
        "withdrawn_residual": len(candidates),
        "package_graph_unchanged": len(package_unchanged),
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
