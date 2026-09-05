from __future__ import annotations

import copy

import pytest

from backend.api.canonical_repository.postgres_store import sha256_json
from backend.pipeline.relation_id_namespace import (
    RelationIdNamespaceError,
    is_source_extraction_relation_id,
    migrate_legacy_cross_section_relation_ids,
    source_namespace,
)


def _legacy_package() -> dict:
    return {
        "source_documents": [{"source_id": "SRC-A", "transcript_id": "讲道甲"}],
        "knowledge_relations": [
            {
                "relation_id": "XER001",
                "from_id": "OBS-A",
                "to_id": "E-A",
                "relation_type": "supports",
            }
        ],
        "claim_relations": [
            {
                "claim_relation_id": "XCR001",
                "from_id": "CL-A",
                "to_id": "CL-B",
                "relation_type": "qualifies",
            }
        ],
        "consensus_application": {
            "removed_claim_relation_ids": ["CR-OLD", "XCR002"],
            "dissolved_claim_relation_ids": ["XCR001"],
        },
    }


def test_migration_changes_only_declared_ids_and_proves_round_trip() -> None:
    package = _legacy_package()
    before = copy.deepcopy(package)

    migrated, manifest = migrate_legacy_cross_section_relation_ids(package)

    namespace = source_namespace("讲道甲")
    assert migrated["knowledge_relations"][0]["relation_id"] == f"{namespace}-XER001"
    assert migrated["claim_relations"][0]["claim_relation_id"] == f"{namespace}-XCR001"
    assert migrated["consensus_application"]["removed_claim_relation_ids"] == [
        "CR-OLD",
        f"{namespace}-XCR002",
    ]
    assert migrated["consensus_application"]["dissolved_claim_relation_ids"] == [
        f"{namespace}-XCR001"
    ]
    assert package == before, "the archived reviewed package must stay immutable"
    assert manifest["status"] == "applied"
    assert manifest["semantic_change"] == "none_relation_identifiers_only"
    assert manifest["round_trip_verified"] is True
    assert manifest["input_canonical_sha256"] == sha256_json(package)
    assert manifest["output_canonical_sha256"] == sha256_json(migrated)
    assert manifest["summary"] == {"identifiers_mapped": 3, "references_rewritten": 4}


def test_already_namespaced_package_is_a_verified_no_op() -> None:
    package = _legacy_package()
    migrated, _ = migrate_legacy_cross_section_relation_ids(package)

    second, manifest = migrate_legacy_cross_section_relation_ids(migrated)

    assert second == migrated
    assert manifest["status"] == "not_required"
    assert manifest["input_canonical_sha256"] == manifest["output_canonical_sha256"]
    assert manifest["round_trip_verified"] is True


def test_unknown_legacy_id_reference_is_rejected_instead_of_left_dangling() -> None:
    package = _legacy_package()
    package["unexpected"] = {"relation": "XER009"}

    with pytest.raises(RelationIdNamespaceError, match="unsupported paths"):
        migrate_legacy_cross_section_relation_ids(package)


def test_extraction_ownership_comes_from_id_dialect_not_endpoints() -> None:
    namespace = source_namespace("讲道甲")
    assert is_source_extraction_relation_id("讲道甲", f"{namespace}-ER001")
    assert is_source_extraction_relation_id("讲道甲", f"{namespace}-P03-CR004")
    assert is_source_extraction_relation_id("讲道甲", f"{namespace}-XER002")
    assert not is_source_extraction_relation_id("讲道甲", "XER002")
    assert not is_source_extraction_relation_id("讲道甲", f"{namespace}-CURATED-1")
    assert not is_source_extraction_relation_id("别的讲道", f"{namespace}-ER001")
