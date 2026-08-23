"""Build identity-review source attestations from reviewed extraction artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha
from backend.api.canonical_repository.viewpoint_source_attestation import (
    build_source_eligibility_artifact,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_attestations(
    *, claim_manifest_path: Path, research_batches_root: Path,
    output_path: Path, database_url: str | None = None,
) -> dict[str, Any]:
    manifest = _read(claim_manifest_path)
    manifest_claim_ids = {
        str(row["claim_id"]) for row in manifest.get("claims") or []
    }
    store = PostgresKnowledgeStore(database_url)
    current_claims = {
        row["claim_id"]: ClaimRecord.model_validate(row)
        for row in store.list_records("claims")
    }
    candidates: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
    for path in sorted(research_batches_root.glob("*/reviewed/*.reviewed-candidate.json")):
        payload = _read(path)
        if not {
            str(row.get("claim_id") or "") for row in payload.get("claims") or []
        } & manifest_claim_ids:
            continue
        slug = path.name.removesuffix(".reviewed-candidate.json")
        review_path = path.parent.parent / "reviews" / f"{slug}.independent-review.json"
        if not review_path.is_file():
            continue
        review_payload = _read(review_path)
        source = review_payload.get("source") or {}
        review_input_path = Path(str(source.get("package_path") or ""))
        stated_input_sha = str(source.get("package_sha256") or "")
        if (
            not review_input_path.is_file()
            or not stated_input_sha
            or _file_sha(review_input_path) != stated_input_sha
        ):
            raise ValueError(f"{review_path}: independent review input package does not bind")
        package_binding = {
            "payload": payload,
            "artifact_sha256": _file_sha(path),
            "path": str(path),
        }
        reviews = {
            str(row.get("claim_id") or ""): row
            for row in review_payload.get("claim_reviews") or []
        }
        for claim in payload.get("claims") or []:
            claim_id = str(claim.get("claim_id") or "")
            current = current_claims.get(claim_id)
            review_row = reviews.get(claim_id)
            try:
                package_claim = ClaimRecord.model_validate(claim)
            except Exception:
                continue
            if (
                current is None
                or review_row is None
                or semantic_record_sha(package_claim) != semantic_record_sha(current)
            ):
                continue
            review_binding = {
                "payload": review_payload,
                "claim_review": review_row,
                "review_input_artifact_sha256": stated_input_sha,
                "artifact_sha256": _file_sha(review_path),
                "path": str(review_path),
            }
            candidates.setdefault(claim_id, []).append(
                (package_binding, review_binding)
            )
    package_bindings = {}
    review_bindings = {}
    for claim_id, rows in sorted(candidates.items()):
        package_binding, review_binding = min(
            rows,
            key=lambda row: (
                row[0]["artifact_sha256"], row[1]["artifact_sha256"]
            ),
        )
        package_bindings[claim_id] = package_binding
        review_bindings[claim_id] = review_binding
    artifact = build_source_eligibility_artifact(
        claim_manifest=manifest,
        claims=[item.model_dump(mode="json") for item in current_claims.values()],
        evidence_steps=store.list_records("evidence_steps"),
        source_fragments=store.list_records("source_fragments"),
        reviewed_packages_by_claim_id=package_bindings,
        reviews_by_claim_id=review_bindings,
    )
    payload = artifact.model_dump(mode="json")
    _write_immutable(output_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--research-batches-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    payload = build_attestations(
        claim_manifest_path=args.claim_manifest,
        research_batches_root=args.research_batches_root,
        output_path=args.output,
        database_url=args.database_url,
    )
    print(json.dumps({
        "output": str(args.output),
        "artifact_sha256": payload["artifact_sha256"],
        **payload["statistics"],
        "model_calls_executed": 0,
        "master_data_mutations": 0,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
