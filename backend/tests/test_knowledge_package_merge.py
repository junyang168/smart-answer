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
                "produced_claim_ids": [claim_id],
            }
        ],
        "claims": [
            {
                "claim_id": claim_id,
                "evidence_step_ids": [evidence_id],
                "opposed_position_ids": [],
            }
        ],
        "knowledge_relations": [],
        "claim_relations": [
            {
                "claim_relation_id": f"RELATION-{index}",
                "from_id": claim_id,
                "to_id": claim_id,
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
    assert len(merged["claims"]) == 3
    assert len(merged["evidence_steps"]) == 3
    assert len(merged["claim_relations"]) == 3


def test_merge_validation_rejects_unknown_relation_endpoint(tmp_path: Path) -> None:
    paths = _write_packages(tmp_path)
    merged = merge_packages(paths, package_id="SYNTHETIC-MERGE")
    merged["claim_relations"][0]["to_id"] = "missing"
    with pytest.raises(KnowledgePackageMergeError, match="unknown endpoints"):
        validate_merged_package(merged)


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
