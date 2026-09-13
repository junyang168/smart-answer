"""Create sealed operator manifests for the #364 reciprocity workflow.

This helper only removes hand-computed artifact seals from the operational
path.  It does not decide whether the operator's writer inventory is complete,
and it does not authenticate historical package bytes or source authority.
Those checks remain owned by ``freeze`` and ``bind-authority`` respectively.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import (
    PostgresKnowledgeStore,
)
from backend.pipeline.claim_evidence_reciprocity_authority import (
    AUTHORITY_MANIFEST_SCHEMA_VERSION,
    seal_authority_artifact,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    PREREQUISITES_MANIFEST_SCHEMA_VERSION,
    seal_artifact,
)


SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


class ClaimEvidenceReciprocityManifestError(ValueError):
    """An operator manifest is malformed or differs from PostgreSQL."""


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ClaimEvidenceReciprocityManifestError(
            "manifest input must contain only JSON values"
        ) from exc


def _required_string(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ClaimEvidenceReciprocityManifestError(f"{field} is required")
    return text


def _required_sha256(value: Any, field: str) -> str:
    text = _required_string(value, field)
    if not SHA256_PATTERN.fullmatch(text):
        raise ClaimEvidenceReciprocityManifestError(
            f"{field} must be a lowercase SHA-256 digest"
        )
    return text


def _validate_named_sha_fields(value: Any, *, path: str) -> None:
    """Reject malformed SHA fields even inside migration/proof metadata."""

    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).endswith("_sha256"):
                _required_sha256(child, child_path)
            else:
                _validate_named_sha_fields(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_named_sha_fields(child, path=f"{path}[{index}]")


def _parse_change_set(value: str) -> dict[str, str]:
    change_set_id, separator, fingerprint = value.partition("=")
    if not separator:
        raise ClaimEvidenceReciprocityManifestError(
            "--change-set must use CHANGE_SET_ID=FINGERPRINT_SHA256"
        )
    return {
        "change_set_id": _required_string(change_set_id, "change_set_id"),
        "fingerprint_sha256": _required_sha256(
            fingerprint, "change_set fingerprint_sha256"
        ),
    }


def build_prerequisites_manifest(
    change_sets: Sequence[str], *, store: Any
) -> dict[str, Any]:
    """Verify an explicitly supplied KCS cohort and seal it.

    Deliberately, this function cannot infer or attest that the cohort names
    every running or registered writer.  That remains an operator prerequisite.
    """

    if isinstance(change_sets, (str, bytes)) or not change_sets:
        raise ClaimEvidenceReciprocityManifestError(
            "prerequisites require at least one --change-set"
        )
    expected: dict[str, str] = {}
    for raw in change_sets:
        row = _parse_change_set(str(raw))
        change_set_id = row["change_set_id"]
        if change_set_id in expected:
            raise ClaimEvidenceReciprocityManifestError(
                f"--change-set repeats {change_set_id}"
            )
        expected[change_set_id] = row["fingerprint_sha256"]

    reader = getattr(store, "list_change_set_states", None)
    if not callable(reader):
        raise ClaimEvidenceReciprocityManifestError(
            "store lacks list_change_set_states"
        )
    observed_value = reader(sorted(expected))
    if isinstance(observed_value, (str, bytes)) or not isinstance(
        observed_value, Sequence
    ):
        raise ClaimEvidenceReciprocityManifestError(
            "PostgreSQL returned malformed ChangeSet states"
        )
    observed: dict[str, dict[str, str]] = {}
    for index, raw in enumerate(observed_value):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityManifestError(
                f"ChangeSet state {index} must be an object"
            )
        change_set_id = _required_string(
            raw.get("change_set_id"), f"ChangeSet state {index}.change_set_id"
        )
        if change_set_id in observed:
            raise ClaimEvidenceReciprocityManifestError(
                f"PostgreSQL repeated ChangeSet {change_set_id}"
            )
        observed[change_set_id] = {
            "change_set_id": change_set_id,
            "fingerprint_sha256": _required_sha256(
                raw.get("fingerprint_sha256"),
                f"ChangeSet {change_set_id}.fingerprint_sha256",
            ),
            "status": _required_string(
                raw.get("status"), f"ChangeSet {change_set_id}.status"
            ),
        }

    if set(observed) != set(expected):
        missing = sorted(set(expected) - set(observed))
        unexpected = sorted(set(observed) - set(expected))
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ClaimEvidenceReciprocityManifestError(
            "PostgreSQL ChangeSet cohort differs: " + " | ".join(details)
        )
    for change_set_id in sorted(expected):
        row = observed[change_set_id]
        if row["status"] != "applied":
            raise ClaimEvidenceReciprocityManifestError(
                f"ChangeSet {change_set_id} is not applied"
            )
        if row["fingerprint_sha256"] != expected[change_set_id]:
            raise ClaimEvidenceReciprocityManifestError(
                f"ChangeSet {change_set_id} fingerprint differs from PostgreSQL"
            )

    return seal_artifact(
        {
            "schema_version": PREREQUISITES_MANIFEST_SCHEMA_VERSION,
            "change_sets": [
                {
                    "change_set_id": change_set_id,
                    "fingerprint_sha256": expected[change_set_id],
                }
                for change_set_id in sorted(expected)
            ],
        }
    )


def _normalize_authority_package(
    raw: Mapping[str, Any], *, index: int
) -> dict[str, Any]:
    package = _json_copy(raw)
    label = f"packages[{index}]"
    for field in ("authority_unit_id", "path"):
        package[field] = _required_string(package.get(field), f"{label}.{field}")
    for field in (
        "raw_sha256",
        "input_canonical_sha256",
        "effective_canonical_sha256",
        "upstream_reviewed_candidate_artifact_sha256",
        "effective_reviewed_candidate_artifact_sha256",
    ):
        package[field] = _required_sha256(package.get(field), f"{label}.{field}")

    migration = package.get("relation_id_namespace_migration")
    if not isinstance(migration, Mapping):
        raise ClaimEvidenceReciprocityManifestError(
            f"{label}.relation_id_namespace_migration must be an object"
        )
    historical = package.get("historical_change_set")
    if not isinstance(historical, Mapping):
        raise ClaimEvidenceReciprocityManifestError(
            f"{label}.historical_change_set must be an object"
        )
    historical = _json_copy(historical)
    for field in ("change_set_id", "source_kind"):
        historical[field] = _required_string(
            historical.get(field), f"{label}.historical_change_set.{field}"
        )
    for field in ("fingerprint_sha256", "source_sha256"):
        historical[field] = _required_sha256(
            historical.get(field), f"{label}.historical_change_set.{field}"
        )
    if historical.get("status") != "applied":
        raise ClaimEvidenceReciprocityManifestError(
            f"{label}.historical_change_set.status must be applied"
        )
    package["historical_change_set"] = historical

    generations_value = package.get("source_generations")
    if not isinstance(generations_value, list) or not generations_value:
        raise ClaimEvidenceReciprocityManifestError(
            f"{label}.source_generations must be a non-empty list"
        )
    generations: list[dict[str, Any]] = []
    identities: set[tuple[str, str]] = set()
    physical_ids: set[str] = set()
    for generation_index, generation_raw in enumerate(generations_value):
        generation_label = f"{label}.source_generations[{generation_index}]"
        if not isinstance(generation_raw, Mapping):
            raise ClaimEvidenceReciprocityManifestError(
                f"{generation_label} must be an object"
            )
        generation = _json_copy(generation_raw)
        for field in (
            "source_type",
            "row_key",
            "active_source_document_id",
            "extraction_record_namespace",
        ):
            generation[field] = _required_string(
                generation.get(field), f"{generation_label}.{field}"
            )
        for field in ("expected_content_sha256", "source_body_sha256"):
            generation[field] = _required_sha256(
                generation.get(field), f"{generation_label}.{field}"
            )
        revision = generation.get("expected_revision")
        if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ClaimEvidenceReciprocityManifestError(
                f"{generation_label}.expected_revision must be a positive integer"
            )
        identity = (generation["source_type"], generation["row_key"])
        if identity in identities:
            raise ClaimEvidenceReciprocityManifestError(
                f"{label}.source_generations repeats {identity[0]}/{identity[1]}"
            )
        object_id = generation["active_source_document_id"]
        if object_id in physical_ids:
            raise ClaimEvidenceReciprocityManifestError(
                f"{label}.source_generations repeats SourceDocument {object_id}"
            )
        identities.add(identity)
        physical_ids.add(object_id)
        generations.append(generation)
    package["source_generations"] = sorted(
        generations, key=lambda row: (row["source_type"], row["row_key"])
    )
    _validate_named_sha_fields(package, path=label)
    return package


def build_authority_manifest(specs: Mapping[str, Any]) -> dict[str, Any]:
    """Seal human-supplied package specs after structural validation only."""

    value = _json_copy(specs)
    if not isinstance(value, Mapping) or set(value) != {"packages"}:
        raise ClaimEvidenceReciprocityManifestError(
            "authority input must be an object containing only packages"
        )
    packages_value = value.get("packages")
    if not isinstance(packages_value, list):
        raise ClaimEvidenceReciprocityManifestError(
            "authority input packages must be a list"
        )
    packages: list[dict[str, Any]] = []
    authority_ids: set[str] = set()
    change_set_ids: set[str] = set()
    for index, raw in enumerate(packages_value):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityManifestError(
                f"packages[{index}] must be an object"
            )
        package = _normalize_authority_package(raw, index=index)
        authority_id = package["authority_unit_id"]
        change_set_id = package["historical_change_set"]["change_set_id"]
        if authority_id in authority_ids:
            raise ClaimEvidenceReciprocityManifestError(
                f"authority input repeats authority_unit_id {authority_id}"
            )
        if change_set_id in change_set_ids:
            raise ClaimEvidenceReciprocityManifestError(
                f"authority input repeats historical ChangeSet {change_set_id}"
            )
        authority_ids.add(authority_id)
        change_set_ids.add(change_set_id)
        packages.append(package)
    packages.sort(key=lambda row: row["authority_unit_id"])
    return seal_authority_artifact(
        {
            "schema_version": AUTHORITY_MANIFEST_SCHEMA_VERSION,
            "packages": packages,
        }
    )


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClaimEvidenceReciprocityManifestError(
            f"cannot load authority specs {path}: {exc}"
        ) from exc
    if not isinstance(value, dict):
        raise ClaimEvidenceReciprocityManifestError(
            "authority specs must contain a JSON object"
        )
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _command_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    prerequisites = commands.add_parser(
        "prerequisites", help="verify and seal an explicit applied ChangeSet cohort"
    )
    prerequisites.add_argument(
        "--change-set",
        action="append",
        required=True,
        metavar="CHANGE_SET_ID=FINGERPRINT_SHA256",
        help=(
            "repeat for every operator-confirmed writer ChangeSet; this command "
            "does not infer completeness"
        ),
    )
    prerequisites.add_argument("--database-url")
    prerequisites.add_argument("--output", required=True, type=Path)

    authority = commands.add_parser(
        "authority", help="structurally validate and seal human package specs"
    )
    authority.add_argument("--input", "--specs", dest="input", required=True, type=Path)
    authority.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_dotenv()
    args = _command_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "prerequisites":
        artifact = build_prerequisites_manifest(
            args.change_set,
            store=PostgresKnowledgeStore(args.database_url),
        )
        _write_json(args.output, artifact)
        print(
            json.dumps(
                {
                    "status": "sealed",
                    "artifact": str(args.output),
                    "verified_change_sets": len(artifact["change_sets"]),
                }
            )
        )
        return 0
    if args.command == "authority":
        artifact = build_authority_manifest(_load_json(args.input))
        _write_json(args.output, artifact)
        print(
            json.dumps(
                {
                    "status": "sealed",
                    "artifact": str(args.output),
                    "package_specs": len(artifact["packages"]),
                    "authority_authentication": "deferred_to_bind_authority",
                }
            )
        )
        return 0
    raise AssertionError(f"unhandled command {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
