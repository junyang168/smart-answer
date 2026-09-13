"""Build identity-review source attestations from reviewed extraction artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_source_attestation import (
    build_source_eligibility_artifact,
)
from backend.api.canonical_repository.reviewed_candidate_contract import (
    ConsensusApplicationError,
    validate_reviewed_candidate_artifact,
)
from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    validate_merged_package,
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


def _validated_lineage_inputs(
    *, manifest_claim_ids: set[str], lineage_manifest_path: Path
) -> dict[str, dict[str, Any]]:
    """Authenticate the exact selected lineage before the first database read."""

    lineage_manifest = _read(lineage_manifest_path)
    lineage_body = {
        key: value for key, value in lineage_manifest.items() if key != "artifact_sha256"
    }
    if lineage_manifest.get("artifact_sha256") != sha256_json(lineage_body):
        raise ValueError("lineage manifest SHA mismatch")
    rows_by_claim: dict[str, dict[str, Any]] = {}
    for row in lineage_manifest.get("claims") or []:
        claim_id = str(row.get("claim_id") or "")
        if not claim_id or claim_id in rows_by_claim:
            raise ValueError(f"lineage manifest duplicate/empty Claim: {claim_id}")
        rows_by_claim[claim_id] = dict(row)
    if set(rows_by_claim) != manifest_claim_ids:
        raise ValueError(
            "lineage manifest Claim set differs: "
            f"missing={sorted(manifest_claim_ids - set(rows_by_claim))}, "
            f"extra={sorted(set(rows_by_claim) - manifest_claim_ids)}"
        )

    validated: dict[str, dict[str, Any]] = {}
    for claim_id, lineage in sorted(rows_by_claim.items()):
        paths = {
            "path": Path(str(lineage.get("reviewed_candidate_path") or "")),
            "review_path": Path(str(lineage.get("independent_review_path") or "")),
            "adjudication_path": Path(str(lineage.get("adjudication_path") or "")),
            "overrides_path": Path(str(lineage.get("overrides_path") or "")),
        }
        if not all(path.is_file() for path in paths.values()):
            raise ValueError(f"{claim_id}: selected lineage artifact is missing")
        expected_shas = {
            "path": lineage.get("reviewed_candidate_sha256"),
            "review_path": lineage.get("independent_review_sha256"),
            "adjudication_path": lineage.get("adjudication_sha256"),
            "overrides_path": lineage.get("overrides_sha256"),
        }
        for role, path in paths.items():
            if _file_sha(path) != expected_shas[role]:
                raise ValueError(f"{claim_id}: selected {role} SHA drift")
        payload = _read(paths["path"])
        try:
            validate_reviewed_candidate_artifact(payload)
            validate_merged_package(payload)
        except (ConsensusApplicationError, KnowledgePackageMergeError) as exc:
            raise ValueError(
                f"{claim_id}: selected reviewed candidate does not authenticate: {exc}"
            ) from exc
        application = payload["consensus_application"]
        if (
            application.get("review_artifact_sha256")
            != expected_shas["review_path"]
            or application.get("adjudication_artifact_sha256")
            != expected_shas["adjudication_path"]
            or application.get("overrides_artifact_sha256")
            != expected_shas["overrides_path"]
        ):
            raise ValueError(
                f"{claim_id}: selected consensus chain does not bind lineage artifacts"
            )
        review_payload = _read(paths["review_path"])
        source = review_payload.get("source") or {}
        review_input_path = Path(str(source.get("package_path") or ""))
        review_input_sha = str(source.get("package_sha256") or "")
        if (
            not review_input_path.is_file()
            or not review_input_sha
            or _file_sha(review_input_path) != review_input_sha
        ):
            raise ValueError(
                f"{paths['review_path']}: independent review input package does not bind"
            )
        validated[claim_id] = {
            **paths,
            "payload": payload,
            "application": application,
            "review_payload": review_payload,
            "review_input_payload": _read(review_input_path),
            "review_input_sha256": review_input_sha,
            "adjudication_payload": _read(paths["adjudication_path"]),
        }
    return validated


def build_attestations(
    *,
    claim_manifest_path: Path,
    lineage_manifest_path: Path,
    output_path: Path,
    database_url: str | None = None,
) -> dict[str, Any]:
    manifest = _read(claim_manifest_path)
    manifest_claim_ids = {
        str(row["claim_id"]) for row in manifest.get("claims") or []
    }
    lineage_inputs = _validated_lineage_inputs(
        manifest_claim_ids=manifest_claim_ids,
        lineage_manifest_path=lineage_manifest_path,
    )
    store = PostgresKnowledgeStore(database_url)
    current_claims = {
        row["claim_id"]: ClaimRecord.model_validate(row)
        for row in store.list_records("claims")
    }
    package_bindings: dict[str, dict[str, Any]] = {}
    review_bindings: dict[str, dict[str, Any]] = {}
    for claim_id, item in sorted(lineage_inputs.items()):
        path = item["path"]
        review_path = item["review_path"]
        adjudication_path = item["adjudication_path"]
        overrides_path = item["overrides_path"]
        payload = item["payload"]
        application = item["application"]
        review_payload = item["review_payload"]
        adjudication_payload = item["adjudication_payload"]
        adjudication_results = {
            str(row.get("claim_id") or ""): row
            for row in ((adjudication_payload or {}).get("results") or [])
        }
        stated_input_sha = item["review_input_sha256"]
        review_input_payload = item["review_input_payload"]
        package_binding = {
            "payload": payload,
            "artifact_sha256": application["artifact_sha256"],
            "file_sha256": _file_sha(path),
            "path": str(path),
        }
        reviews = {
            str(row.get("claim_id") or ""): row
            for row in review_payload.get("claim_reviews") or []
        }
        package_claims = {
            str(row.get("claim_id") or ""): row for row in payload.get("claims") or []
        }
        current = current_claims.get(claim_id)
        review_row = reviews.get(claim_id)
        try:
            package_claim = ClaimRecord.model_validate(package_claims[claim_id])
        except (KeyError, ValueError) as exc:
            raise ValueError(
                f"{claim_id}: selected reviewed candidate lacks the pinned Claim"
            ) from exc
        if (
            current is None
            or review_row is None
            or semantic_record_sha(package_claim) != semantic_record_sha(current)
        ):
            raise ValueError(f"{claim_id}: selected lineage does not bind current Claim")
        if str(review_row.get("decision") or "") == "pass":
            review_input_claims = {
                str(row.get("claim_id") or ""): row
                for row in review_input_payload.get("claims") or []
            }
            try:
                reviewed_claim = ClaimRecord.model_validate(
                    review_input_claims[claim_id]
                )
            except (KeyError, ValueError) as exc:
                raise ValueError(
                    f"{claim_id}: independent review input lacks the selected Claim"
                ) from exc
            if semantic_record_sha(reviewed_claim) != semantic_record_sha(current):
                raise ValueError(
                    f"{claim_id}: passing review covers different Claim semantics"
                )
        package_bindings[claim_id] = package_binding
        review_bindings[claim_id] = {
            "payload": review_payload,
            "claim_review": review_row,
            "review_input_artifact_sha256": stated_input_sha,
            "artifact_sha256": _file_sha(review_path),
            "path": str(review_path),
            "adjudication_payload": adjudication_payload,
            "adjudication_result": adjudication_results.get(claim_id),
            "adjudication_artifact_sha256": (
                _file_sha(adjudication_path)
            ),
            "overrides_artifact_sha256": _file_sha(overrides_path),
        }
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
    parser.add_argument(
        "--lineage-manifest",
        type=Path,
        required=True,
        help="content-addressed exact Claim-to-reviewed-package/review lineage selection",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    payload = build_attestations(
        claim_manifest_path=args.claim_manifest,
        lineage_manifest_path=args.lineage_manifest,
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
