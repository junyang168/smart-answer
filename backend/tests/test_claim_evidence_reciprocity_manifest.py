from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

import backend.pipeline.claim_evidence_reciprocity_manifest as manifest_module
from backend.pipeline.claim_evidence_reciprocity_authority import (
    AUTHORITY_MANIFEST_SCHEMA_VERSION,
    validate_authority_artifact,
)
from backend.pipeline.claim_evidence_reciprocity_manifest import (
    ClaimEvidenceReciprocityManifestError,
    build_authority_manifest,
    build_prerequisites_manifest,
    main,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    PREREQUISITES_MANIFEST_SCHEMA_VERSION,
    validate_sealed_artifact,
)


FINGERPRINT_A = "a" * 64
FINGERPRINT_B = "b" * 64


class _Store:
    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = rows
        self.requested: list[str] | None = None

    def list_change_set_states(self, change_set_ids: list[str]) -> list[dict[str, str]]:
        self.requested = change_set_ids
        return deepcopy(self.rows)


def _authority_spec(
    *,
    authority_unit_id: str = "AUTH-ONE",
    change_set_id: str = "KCS-HISTORICAL-ONE",
) -> dict[str, Any]:
    return {
        "authority_unit_id": authority_unit_id,
        # Manifest creation is intentionally structural; bind-authority is the
        # boundary that must read and authenticate these bytes.
        "path": "/does/not/need/to/exist/yet/reviewed.json",
        "raw_sha256": "1" * 64,
        "input_canonical_sha256": "2" * 64,
        "effective_canonical_sha256": "3" * 64,
        "upstream_reviewed_candidate_artifact_sha256": "4" * 64,
        "effective_reviewed_candidate_artifact_sha256": "5" * 64,
        "relation_id_namespace_migration": {
            "schema_version": "wang_relation_id_namespace_migration_v1",
            "input_canonical_sha256": "2" * 64,
            "output_canonical_sha256": "3" * 64,
        },
        "historical_change_set": {
            "change_set_id": change_set_id,
            "fingerprint_sha256": "6" * 64,
            "source_kind": "knowledge_package",
            "source_sha256": "3" * 64,
            "status": "applied",
        },
        "source_generations": [
            {
                "source_type": "sermon_transcript",
                "row_key": "SERMON-ONE",
                "active_source_document_id": "SRC-ONE",
                "expected_revision": 7,
                "expected_content_sha256": "7" * 64,
                "source_body_sha256": "8" * 64,
                "extraction_record_namespace": "SERMON-ONE",
            }
        ],
    }


def test_prerequisites_verifies_exact_applied_states_and_seals_canonical_order() -> None:
    store = _Store(
        [
            {
                "change_set_id": "KCS-B",
                "fingerprint_sha256": FINGERPRINT_B,
                "status": "applied",
            },
            {
                "change_set_id": "KCS-A",
                "fingerprint_sha256": FINGERPRINT_A,
                "status": "applied",
            },
        ]
    )

    result = build_prerequisites_manifest(
        [f"KCS-B={FINGERPRINT_B}", f"KCS-A={FINGERPRINT_A}"],
        store=store,
    )

    assert store.requested == ["KCS-A", "KCS-B"]
    assert [row["change_set_id"] for row in result["change_sets"]] == [
        "KCS-A",
        "KCS-B",
    ]
    assert "writer_inventory_complete" not in result
    assert validate_sealed_artifact(
        result, expected_schema_version=PREREQUISITES_MANIFEST_SCHEMA_VERSION
    ) == result


@pytest.mark.parametrize(
    ("requested", "rows", "message"),
    [
        (
            [f"KCS-A={FINGERPRINT_A}", f"KCS-A={FINGERPRINT_A}"],
            [],
            "repeats KCS-A",
        ),
        (
            [f"KCS-A={FINGERPRINT_A}"],
            [],
            "missing=KCS-A",
        ),
        (
            [f"KCS-A={FINGERPRINT_A}"],
            [
                {
                    "change_set_id": "KCS-A",
                    "fingerprint_sha256": FINGERPRINT_A,
                    "status": "planned",
                }
            ],
            "is not applied",
        ),
        (
            [f"KCS-A={FINGERPRINT_A}"],
            [
                {
                    "change_set_id": "KCS-A",
                    "fingerprint_sha256": FINGERPRINT_B,
                    "status": "applied",
                }
            ],
            "fingerprint differs",
        ),
    ],
)
def test_prerequisites_rejects_duplicate_missing_non_applied_or_drifted_kcs(
    requested: list[str], rows: list[dict[str, str]], message: str
) -> None:
    with pytest.raises(ClaimEvidenceReciprocityManifestError, match=message):
        build_prerequisites_manifest(requested, store=_Store(rows))


def test_prerequisites_requires_explicit_id_equals_sha_syntax() -> None:
    with pytest.raises(
        ClaimEvidenceReciprocityManifestError,
        match="CHANGE_SET_ID=FINGERPRINT_SHA256",
    ):
        build_prerequisites_manifest(["KCS-A"], store=_Store([]))


def test_authority_accepts_explicit_empty_packages_without_claiming_authority() -> None:
    result = build_authority_manifest({"packages": []})

    assert result["packages"] == []
    assert validate_authority_artifact(
        result, schema_version=AUTHORITY_MANIFEST_SCHEMA_VERSION
    ) == result


def test_authority_structurally_validates_and_sorts_human_specs() -> None:
    second = _authority_spec(
        authority_unit_id="AUTH-Z", change_set_id="KCS-HISTORICAL-Z"
    )
    first = _authority_spec(
        authority_unit_id="AUTH-A", change_set_id="KCS-HISTORICAL-A"
    )
    first["source_generations"].append(
        {
            "source_type": "notes_manuscript",
            "row_key": "NOTES-ONE",
            "active_source_document_id": "SRC-NOTES-ONE",
            "expected_revision": 2,
            "expected_content_sha256": "9" * 64,
            "source_body_sha256": "a" * 64,
            "extraction_record_namespace": "NOTES-ONE",
        }
    )

    result = build_authority_manifest({"packages": [second, first]})

    assert [row["authority_unit_id"] for row in result["packages"]] == [
        "AUTH-A",
        "AUTH-Z",
    ]
    assert [
        (row["source_type"], row["row_key"])
        for row in result["packages"][0]["source_generations"]
    ] == [
        ("notes_manuscript", "NOTES-ONE"),
        ("sermon_transcript", "SERMON-ONE"),
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda specs: specs["packages"][0].__setitem__(
                "raw_sha256", "NOT-A-SHA"
            ),
            "raw_sha256 must be a lowercase SHA-256",
        ),
        (
            lambda specs: specs["packages"].append(
                deepcopy(specs["packages"][0])
            ),
            "repeats authority_unit_id",
        ),
        (
            lambda specs: specs["packages"].append(
                {
                    **deepcopy(specs["packages"][0]),
                    "authority_unit_id": "AUTH-TWO",
                }
            ),
            "repeats historical ChangeSet",
        ),
        (
            lambda specs: specs["packages"][0]["source_generations"].append(
                deepcopy(specs["packages"][0]["source_generations"][0])
            ),
            "source_generations repeats sermon_transcript/SERMON-ONE",
        ),
        (
            lambda specs: specs["packages"][0]["source_generations"][0].__setitem__(
                "expected_revision", "7"
            ),
            "expected_revision must be a positive integer",
        ),
    ],
)
def test_authority_rejects_bad_sha_or_ambiguous_identity(
    mutate: Any, message: str
) -> None:
    specs = {"packages": [_authority_spec()]}
    mutate(specs)

    with pytest.raises(ClaimEvidenceReciprocityManifestError, match=message):
        build_authority_manifest(specs)


def test_authority_rejects_presealed_or_extra_top_level_input() -> None:
    with pytest.raises(
        ClaimEvidenceReciprocityManifestError,
        match="containing only packages",
    ):
        build_authority_manifest(
            {"schema_version": AUTHORITY_MANIFEST_SCHEMA_VERSION, "packages": []}
        )


def test_cli_loads_env_queries_store_and_atomically_writes_prerequisites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[Any] = []

    class Store:
        def __init__(self, database_url: str | None) -> None:
            events.append(("database_url", database_url))

        def list_change_set_states(
            self, change_set_ids: list[str]
        ) -> list[dict[str, str]]:
            events.append(("requested", change_set_ids))
            return [
                {
                    "change_set_id": "KCS-A",
                    "fingerprint_sha256": FINGERPRINT_A,
                    "status": "applied",
                }
            ]

    monkeypatch.setattr(manifest_module, "PostgresKnowledgeStore", Store)
    monkeypatch.setattr(
        manifest_module, "load_dotenv", lambda: events.append(("dotenv", True))
    )
    output = tmp_path / "nested" / "prerequisites.json"

    assert main(
        [
            "prerequisites",
            "--change-set",
            f"KCS-A={FINGERPRINT_A}",
            "--database-url",
            "postgresql://example/knowledge",
            "--output",
            str(output),
        ]
    ) == 0

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert validate_sealed_artifact(
        artifact, expected_schema_version=PREREQUISITES_MANIFEST_SCHEMA_VERSION
    ) == artifact
    assert events == [
        ("dotenv", True),
        ("database_url", "postgresql://example/knowledge"),
        ("requested", ["KCS-A"]),
    ]
    assert list(output.parent.glob(f".{output.name}.*.tmp")) == []


def test_authority_cli_seals_specs_without_opening_package_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(manifest_module, "load_dotenv", lambda: None)
    specs_path = tmp_path / "specs.json"
    output = tmp_path / "authority.json"
    specs_path.write_text(json.dumps({"packages": [_authority_spec()]}), encoding="utf-8")

    assert main(
        ["authority", "--input", str(specs_path), "--output", str(output)]
    ) == 0

    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert validate_authority_artifact(
        artifact, schema_version=AUTHORITY_MANIFEST_SCHEMA_VERSION
    ) == artifact
    assert artifact["packages"][0]["path"].startswith("/does/not/need/to/exist")
