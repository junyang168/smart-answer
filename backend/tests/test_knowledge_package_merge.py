import json
from pathlib import Path

import pytest

from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    merge_packages,
    validate_merged_package,
)

def _package(index: int) -> dict:
    source_id = f"SOURCE-{index}"
    fragment_id = f"FRAGMENT-{index}"
    evidence_id = f"EVIDENCE-{index}"
    claim_id = f"CLAIM-{index}"
    target_claim_id = f"CLAIM-TARGET-{index}"
    return {
        "schema_version": "wang_shared_knowledge_v1.2",
        "package_id": f"SYNTHETIC-PACKAGE-{index}",
        "source_documents": [{"source_id": source_id}],
        "source_fragments": [{"fragment_id": fragment_id, "source_id": source_id}],
        "questions": [],
        "position_nodes": [],
        "observations": [],
        "evidence_steps": [
            {
                "evidence_step_id": evidence_id,
                "source_fragment_ids": [fragment_id],
                "produced_claim_ids": [claim_id, target_claim_id],
            }
        ],
        "claims": [
            {
                "claim_id": claim_id,
                "evidence_step_ids": [evidence_id],
                "opposed_position_ids": [],
            },
            {
                "claim_id": target_claim_id,
                "evidence_step_ids": [evidence_id],
                "opposed_position_ids": [],
            },
        ],
        "knowledge_relations": [],
        "claim_relations": [
            {
                "claim_relation_id": f"RELATION-{index}",
                "from_id": claim_id,
                "to_id": target_claim_id,
                "relation_type": "supports",
            }
        ],
    }


def _write_packages(tmp_path: Path, count: int = 3) -> list[Path]:
    paths = []
    for index in range(1, count + 1):
        path = tmp_path / f"synthetic-package-{index}.json"
        path.write_text(json.dumps(_package(index)), encoding="utf-8")
        paths.append(path)
    return paths


def test_merge_packages_preserves_all_objects(tmp_path: Path) -> None:
    paths = _write_packages(tmp_path)
    merged = merge_packages(paths, package_id="SYNTHETIC-MERGE")
    assert len(merged["source_documents"]) == 3
    assert len(merged["claims"]) == 6
    assert len(merged["evidence_steps"]) == 3
    assert len(merged["claim_relations"]) == 3


def test_merge_validation_rejects_unknown_relation_endpoint(tmp_path: Path) -> None:
    paths = _write_packages(tmp_path)
    merged = merge_packages(paths, package_id="SYNTHETIC-MERGE")
    merged["claim_relations"][0]["to_id"] = "missing"
    with pytest.raises(KnowledgePackageMergeError, match="unknown endpoints"):
        validate_merged_package(merged)


def test_merge_accepts_observation_to_evidence_knowledge_relation() -> None:
    package = _package(1)
    package["observations"] = [{
        "observation_id": "OBSERVATION-1",
        "source_fragment_ids": ["FRAGMENT-1"],
    }]
    package["knowledge_relations"] = [{
        "relation_id": "EVIDENCE-RELATION-1",
        "from_id": "OBSERVATION-1",
        "to_id": "EVIDENCE-1",
        "relation_type": "supports",
    }]

    validate_merged_package(package)


def test_merge_rejects_missing_legacy_singular_fragment_reference() -> None:
    package = _package(1)
    package["evidence_steps"][0].pop("source_fragment_ids")
    package["evidence_steps"][0]["source_fragment_id"] = "FRAGMENT-MISSING"

    with pytest.raises(KnowledgePackageMergeError, match="unknown fragments"):
        validate_merged_package(package)


def test_merge_rejects_an_id_reused_by_different_collections() -> None:
    package = _package(1)
    package["knowledge_relations"] = [{
        "relation_id": "RELATION-1",
        "from_id": "EVIDENCE-1",
        "to_id": "EVIDENCE-1",
        "relation_type": "supports",
    }]

    with pytest.raises(KnowledgePackageMergeError, match="globally unique"):
        validate_merged_package(package)


def test_merge_rejects_self_edges_and_duplicate_semantic_edges() -> None:
    package = _package(1)
    package["claim_relations"][0]["to_id"] = "CLAIM-1"
    with pytest.raises(KnowledgePackageMergeError, match="point to itself"):
        validate_merged_package(package)

    package = _package(1)
    package["claim_relations"].append({
        **package["claim_relations"][0],
        "claim_relation_id": "RELATION-SECOND",
    })
    with pytest.raises(KnowledgePackageMergeError, match="duplicate semantic relation"):
        validate_merged_package(package)


@pytest.mark.parametrize("direction", ["claim_only", "evidence_only"])
def test_merge_rejects_nonreciprocal_claim_evidence_bindings(
    direction: str,
) -> None:
    package = _package(1)
    if direction == "claim_only":
        package["evidence_steps"][0]["produced_claim_ids"].remove(
            "CLAIM-TARGET-1"
        )
    else:
        package["claims"][1]["evidence_step_ids"] = []

    with pytest.raises(
        KnowledgePackageMergeError,
        match="claim/evidence bindings must be reciprocal",
    ):
        validate_merged_package(package)


def test_merge_rejects_duplicate_reference_edges() -> None:
    package = _package(1)
    package["claims"][0]["evidence_step_ids"].append("EVIDENCE-1")

    with pytest.raises(KnowledgePackageMergeError, match="duplicate references"):
        validate_merged_package(package)


def test_merge_rejects_linked_visual_fragment_without_exact_svg_path() -> None:
    package = _package(1)
    package["source_documents"][0].update({
        "source_sha256": "a" * 64,
        "source_body_sha256": "a" * 64,
        "visual_source_assets": [{
            "source_path": "/source/diagram.svg",
            "source_sha256": "b" * 64,
        }],
        "visual_sources": [{
            "locator": "S0001/V01",
            "raw_sha256": "b" * 64,
            "binding_kind": "linked_svg_asset",
            "source_path": "/source/diagram.svg",
            "source_file_sha256": "b" * 64,
        }],
    })
    package["source_fragments"][0].update({
        "source_sha256": "a" * 64,
        "source_modality": "visual",
        "paragraph_key": "S0001/V01",
        "visual_locator": "S0001/V01",
        "visual_block_sha256": "b" * 64,
        "visual_source_file_sha256": "b" * 64,
    })

    with pytest.raises(
        KnowledgePackageMergeError,
        match="does not point to its exact SVG source file",
    ):
        validate_merged_package(package)

    package["source_fragments"][0]["visual_source_path"] = (
        "/source/diagram.svg"
    )
    validate_merged_package(package)


def test_merge_can_record_neutral_comparison_scope(tmp_path: Path) -> None:
    paths = _write_packages(tmp_path, count=1)
    merged = merge_packages(
        paths[:1],
        package_id="M16-NEUTRAL-TEST",
        batch={
            "batch_id": "RB-M16-TEST",
            "purpose": "Compare one bounded passage without assuming equivalence.",
            "semantic_assumption": "none",
            "selection_is_not_classification": True,
        },
    )

    assert merged["batch"]["batch_id"] == "RB-M16-TEST"
    assert merged["batch"]["semantic_assumption"] == "none"
    assert merged["batch"]["selection_is_not_classification"] is True
