"""Read-only #397 preflight for legacy reviews crossing #368 locator repairs.

This produces a frozen, reason-coded report, not a migration plan or approval.
It intentionally excludes Claim/EvidenceStep link changes and adjudicated
patches.  A separate CAS-bound planner is required before any database write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore, _normalize_records, _substantive_payload, sha256_json,
)
from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.legacy_candidate_reconciliation import (
    _encode_report, classify_candidate, inspect_legacy_bundle, inventory,
)
from backend.pipeline.legacy_coordinate_review_proof import (
    coordinate_chain_proof, exact_current_anchor_count,
    related_package_content_unchanged,
)
from backend.pipeline.source_contract_cleanup import BodyLocatorIndex
from backend.pipeline.source_contract_cleanup_runner import _source_material
from backend.pipeline.source_projection import project_script


READY_REASON = "coordinate_only_historical_review_graph_verified"
HISTORICAL_DECISIONS = frozenset({
    "pass_ready_for_legacy_migration", "human_spot_check_required",
    "human_confirmation_required", "human_disagreement_required",
})


def _current_data(
    store: PostgresKnowledgeStore, claim_ids: list[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, dict[str, Any]],
           dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    versions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("SET TRANSACTION READ ONLY")
        cursor.execute(
            """SELECT v.object_id,v.revision,v.content_sha256,v.payload,
                      cs.source_kind
               FROM wang_knowledge.object_versions v
               JOIN wang_knowledge.change_sets cs
                 ON cs.change_set_id=v.change_set_id
               WHERE v.collection='claims' AND v.object_id=ANY(%s)
               ORDER BY v.object_id,v.revision""",
            (claim_ids,),
        )
        for claim_id, revision, content_sha, payload, source_kind in cursor:
            versions[str(claim_id)].append({
                "revision": int(revision), "content_sha256": str(content_sha),
                "payload": payload, "source_kind": str(source_kind),
            })
        cursor.execute(
            """SELECT object_id,revision,content_sha256,payload
               FROM wang_knowledge.objects
               WHERE collection='source_documents' AND retired_at IS NULL"""
        )
        sources = {
            str(source_id): {
                "object_id": str(source_id), "revision": int(revision),
                "content_sha256": str(content_sha), "payload": payload,
            }
            for source_id, revision, content_sha, payload in cursor
        }
        evidence_ids = sorted({
            str(evidence_id)
            for history in versions.values() if history
            for evidence_id in history[-1]["payload"].get("evidence_step_ids") or []
        })
        cursor.execute(
            """SELECT object_id,payload FROM wang_knowledge.objects
               WHERE collection='evidence_steps' AND retired_at IS NULL
                 AND object_id=ANY(%s)""", (evidence_ids,),
        )
        evidence = {str(object_id): payload for object_id, payload in cursor}
        fragment_ids = sorted({
            str(fragment_id)
            for step in evidence.values()
            for fragment_id in (
                list(step.get("source_fragment_ids") or [])
                + ([step["source_fragment_id"]]
                   if step.get("source_fragment_id") else [])
            )
        })
        cursor.execute(
            """SELECT object_id,payload FROM wang_knowledge.objects
               WHERE collection='source_fragments' AND retired_at IS NULL
                 AND object_id=ANY(%s)""", (fragment_ids,),
        )
        fragments = {str(object_id): payload for object_id, payload in cursor}
    return versions, sources, evidence, fragments


def _source_index(
    source: dict[str, Any], cache: dict[str, BodyLocatorIndex | None],
    data_base_path: Path,
) -> BodyLocatorIndex | None:
    source_id = str(source["object_id"])
    if source_id not in cache:
        try:
            payload, raw, _ = _source_material(source["payload"], data_base_path)
            if (
                hashlib.sha256(raw).hexdigest()
                != source["payload"].get("source_file_sha256")
                or project_script(payload.get("script")).body_sha256
                != source["payload"].get("source_body_sha256")
            ):
                cache[source_id] = None
            else:
                cache[source_id] = BodyLocatorIndex(payload.get("script"))
        except (OSError, ValueError, KeyError, TypeError):
            cache[source_id] = None
    return cache[source_id]


def _fragments_match_source(
    claim: dict[str, Any], *, source_id: str,
    evidence: dict[str, dict[str, Any]],
    fragments: dict[str, dict[str, Any]], index: BodyLocatorIndex,
) -> tuple[bool, int]:
    count = 0
    for evidence_id in claim.get("evidence_step_ids") or []:
        step = evidence.get(evidence_id)
        if step is None:
            return False, count
        fragment_ids = list(step.get("source_fragment_ids") or [])
        if step.get("source_fragment_id"):
            fragment_ids.append(step["source_fragment_id"])
        for fragment_id in fragment_ids:
            fragment = fragments.get(fragment_id)
            if fragment is None or str(fragment.get("source_id") or "") != source_id:
                return False, count
            row = index.by_locator.get(str(fragment.get("paragraph_key") or ""))
            excerpt = str(fragment.get("verbatim_excerpt") or "")
            if row is None or not excerpt or excerpt not in row.text:
                return False, count
            count += 1
    return bool(count), count


def dry_run(
    artifact_root: Path, output_root: Path, store: PostgresKnowledgeStore,
) -> dict[str, Any]:
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError("preflight output root must be empty")
    base = inventory(artifact_root, store)
    candidates = {
        str(row["claim_id"]): row for row in base["rows"]
        if row["reason"] == "claim_payload_changed"
    }
    versions, sources, evidence, fragments = _current_data(store, sorted(candidates))
    bundle_options: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    non_coordinate_history: dict[str, set[str]] = defaultdict(set)
    for path in sorted(artifact_root.rglob("*.reviewed-candidate.json")):
        bundle = inspect_legacy_bundle(path)
        if bundle.get("reason") is not None:
            continue
        selected = candidates.keys() & bundle.get("claims", {}).keys()
        if not selected:
            continue
        package, _ = _normalize_records(json.loads(path.read_text(encoding="utf-8")))
        for claim_id in selected:
            history = versions.get(claim_id, [])
            matches = [index for index, version in enumerate(history)
                       if _substantive_payload(bundle["claims"][claim_id])
                       == _substantive_payload(version["payload"])]
            proof = coordinate_chain_proof(
                bundle["claims"][claim_id], history,
            )
            if proof is not None:
                bundle_options[claim_id].append((bundle, {"proof": proof, "package": package}))
            elif matches:
                for before, after in zip(history[matches[-1]:], history[matches[-1] + 1:]):
                    non_coordinate_history[claim_id].update(
                        key for key in before["payload"].keys() | after["payload"].keys()
                        if key != "revision"
                        and before["payload"].get(key) != after["payload"].get(key)
                    )
    cache: dict[str, BodyLocatorIndex | None] = {}
    rows: list[dict[str, Any]] = []
    for claim_id, base_row in sorted(candidates.items()):
        row = dict(base_row)
        options = bundle_options.get(claim_id, [])
        if len(options) != 1:
            row["reason"] = (
                "historical_coordinate_review_ambiguous" if options
                else "historical_claim_evidence_link_changed"
                if "evidence_step_ids" in non_coordinate_history.get(claim_id, set())
                else "historical_noncoordinate_claim_change"
                if non_coordinate_history.get(claim_id)
                else "valid_historical_review_version_missing"
            )
            rows.append(row)
            continue
        bundle, data = options[0]
        proof = data["proof"]
        history = versions[claim_id]
        historical = next(item for item in history
                          if item["revision"] == proof["reviewed_claim_revision"])
        classified = classify_candidate(
            {"object_id": claim_id, "revision": historical["revision"],
             "content_sha256": historical["content_sha256"],
             "payload": historical["payload"]}, [bundle], sources,
        )
        if classified["reason"] not in HISTORICAL_DECISIONS:
            row["reason"] = "historical_" + classified["reason"]
            rows.append(row)
            continue
        current = history[-1]["payload"]
        package = data["package"]
        if not related_package_content_unchanged(
            claim_id, bundle["claims"][claim_id],
            package.get("evidence_steps", {}), package.get("source_fragments", {}),
            evidence, fragments,
        ):
            row["reason"] = "historical_evidence_graph_changed"
            rows.append(row)
            continue
        source = sources.get(classified["source_id"])
        if source is None:
            row["reason"] = "current_source_missing"
            rows.append(row)
            continue
        index = _source_index(source, cache, Path(os.environ["DATA_BASE_DIR"]))
        transcript_id = str(source["payload"].get("transcript_id") or "")
        anchor_count = (
            exact_current_anchor_count(
                current, source_id=classified["source_id"],
                transcript_id=transcript_id, index=index,
            ) if index is not None else None
        )
        fragment_ok, fragment_count = (
            _fragments_match_source(
                current, source_id=classified["source_id"],
                evidence=evidence, fragments=fragments, index=index,
            ) if index is not None else (False, 0)
        )
        if anchor_count is None or not fragment_ok:
            row["reason"] = "current_source_anchor_mismatch"
            rows.append(row)
            continue
        row.update({
            **classified, **proof,
            "revision": base_row["revision"],
            "content_sha256": base_row["content_sha256"],
            "reason": READY_REASON,
            "historical_decision_reason": classified["reason"],
            "current_anchor_count": anchor_count,
            "current_fragment_count": fragment_count,
            "reviewed_claim_substantive_sha256": sha256_json(
                _substantive_payload(bundle["claims"][claim_id])
            ),
        })
        rows.append(row)
    counts = dict(sorted(Counter(str(row["reason"]) for row in rows).items()))
    report = {
        "schema_version": "wang_legacy_coordinate_review_preflight_v1",
        "base_snapshot_sha256": base["snapshot_sha256"],
        "candidate_count": len(rows), "counts": counts, "rows": rows,
        "snapshot_sha256": sha256_json(rows),
        "note": "Read-only proof; no status migration is authorized by this report alone.",
    }
    output_root.mkdir(parents=True, exist_ok=True)
    _encode_report(output_root / "preflight.json", report)
    return {key: value for key, value in report.items() if key != "rows"}


def verify_frozen_rows(
    rows: list[dict[str, Any]], store: PostgresKnowledgeStore,
) -> None:
    """Recompute every coordinate/source/graph proof just before CAS apply."""

    if not rows or any(row.get("reason") != READY_REASON for row in rows):
        raise ValueError("coordinate replay requires frozen ready rows")
    versions, sources, evidence, fragments = _current_data(
        store, [str(row["claim_id"]) for row in rows],
    )
    bundles: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    indexes: dict[str, BodyLocatorIndex | None] = {}
    for row in rows:
        claim_id = str(row["claim_id"])
        history = versions.get(claim_id) or []
        if not history or (
            history[-1]["revision"] != row["revision"]
            or history[-1]["content_sha256"] != row["content_sha256"]
            or history[-1]["payload"].get("review_status") != "candidate"
        ):
            raise ValueError(f"frozen Claim changed: {claim_id}")
        path = str(row["reviewed_candidate_path"])
        if path not in bundles:
            bundle = inspect_legacy_bundle(Path(path))
            if bundle.get("reason") is not None:
                raise ValueError(f"legacy review is no longer valid: {path}")
            package, _ = _normalize_records(json.loads(Path(path).read_text(encoding="utf-8")))
            bundles[path] = (bundle, package)
        bundle, package = bundles[path]
        reviewed_claim = bundle.get("claims", {}).get(claim_id)
        if reviewed_claim is None or (
            sha256_json(_substantive_payload(reviewed_claim))
            != row["reviewed_claim_substantive_sha256"]
        ):
            raise ValueError(f"reviewed Claim changed: {claim_id}")
        proof = coordinate_chain_proof(reviewed_claim, history)
        if proof is None or any(row.get(key) != value for key, value in proof.items()):
            raise ValueError(f"coordinate history changed: {claim_id}")
        historical = next(item for item in history
                          if item["revision"] == proof["reviewed_claim_revision"])
        classified = classify_candidate(
            {"object_id": claim_id, "revision": historical["revision"],
             "content_sha256": historical["content_sha256"],
             "payload": historical["payload"]}, [bundle], sources,
        )
        if (
            classified["reason"] != row["historical_decision_reason"]
            or classified["reason"] not in HISTORICAL_DECISIONS
            or any(classified.get(key) != row.get(key) for key in (
                "target_review_status", "review_decision", "spot_check_selected",
                "adjudication_status", "source_id", "source_revision",
                "source_content_sha256", "package_sha256", "review_sha256",
                "adjudication_sha256", "reviewed_candidate_sha256",
            ))
        ):
            raise ValueError(f"historical decision changed: {claim_id}")
        if not related_package_content_unchanged(
            claim_id, reviewed_claim,
            package.get("evidence_steps", {}), package.get("source_fragments", {}),
            evidence, fragments,
        ):
            raise ValueError(f"evidence graph changed: {claim_id}")
        source = sources.get(str(row["source_id"]))
        if source is None:
            raise ValueError(f"source missing: {claim_id}")
        index = _source_index(source, indexes, Path(os.environ["DATA_BASE_DIR"]))
        if index is None:
            raise ValueError(f"source bytes changed: {claim_id}")
        anchor_count = exact_current_anchor_count(
            history[-1]["payload"], source_id=str(row["source_id"]),
            transcript_id=str(source["payload"].get("transcript_id") or ""),
            index=index,
        )
        fragment_ok, fragment_count = _fragments_match_source(
            history[-1]["payload"], source_id=str(row["source_id"]),
            evidence=evidence, fragments=fragments, index=index,
        )
        if (
            anchor_count != row["current_anchor_count"]
            or not fragment_ok
            or fragment_count != row["current_fragment_count"]
        ):
            raise ValueError(f"current anchors changed: {claim_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path,
                        default=wang_platform_paths().claim_layer_staging)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(dry_run(args.artifact_root, args.output_root,
                             PostgresKnowledgeStore()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
