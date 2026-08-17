import copy
import json
from pathlib import Path

import pytest

from backend.pipeline.claim_layer_review_batch import (
    ClaimLayerReviewBatchError,
    merge_review_artifacts,
    split_claim_layer_package,
    split_claim_layer_package_by_source,
)


def _package() -> dict:
    source_ids = ["SOURCE-1", "SOURCE-2", "SOURCE-3"]
    return {
        "schema_version": "wang_shared_knowledge_v1.2",
        "package_id": "SYNTHETIC-REVIEW-PACKAGE",
        "source_documents": [{"source_id": source_id} for source_id in source_ids],
        "claims": [
            {
                "claim_id": f"CLAIM-{index}",
                "title": f"Synthetic claim {index}",
                "occurrences": [{"source_id": source_ids[(index - 1) // 2]}],
            }
            for index in range(1, 7)
        ],
        "claim_relations": [],
    }


def _write_package(tmp_path: Path, package: dict) -> Path:
    path = tmp_path / "synthetic-review-package.json"
    path.write_text(json.dumps(package), encoding="utf-8")
    return path


def test_split_retains_every_source_and_partitions_claims() -> None:
    package = _package()
    batches = split_claim_layer_package(package, batch_size=3)
    assert [len(batch["claims"]) for batch in batches] == [3, 3]
    assert [len(batch["source_documents"]) for batch in batches] == [3, 3]
    ids = [claim["claim_id"] for batch in batches for claim in batch["claims"]]
    assert len(ids) == len(set(ids)) == 6
    assert all(
        batch["review_batch"]["partition_policy"]
        == "claims_only_all_sources_and_relations_retained"
        for batch in batches
    )


def test_split_by_source_matches_pre_synthesis_responsibility() -> None:
    package = _package()
    batches = split_claim_layer_package_by_source(package)
    assert [len(batch["claims"]) for batch in batches] == [2, 2, 2]
    assert [len(batch["source_documents"]) for batch in batches] == [1, 1, 1]
    for batch in batches:
        source_id = batch["source_documents"][0]["source_id"]
        assert all(
            {
                occurrence.get("source_id") or occurrence.get("transcript_id")
                for occurrence in claim["occurrences"]
            }
            == {source_id}
            for claim in batch["claims"]
        )
        assert (
            batch["review_batch"]["partition_policy"]
            == "source_scoped_review_before_cross_source_synthesis"
        )


def _artifact(batch: dict, index: int) -> dict:
    claims = [
        {"claim_id": row["claim_id"], "statement": row.get("title", ""), "anchors": []}
        for row in batch["claims"]
    ]
    reviews = [
        {
            "claim_id": row["claim_id"],
            "decision": "pass",
            "routing_status": "ai_reviewed",
        }
        for row in batch["claims"]
    ]
    return {
        "source": {"batch": index, "review_batch": batch["review_batch"]},
        "reviewer": {"fingerprint_sha256": f"review-{index}"},
        "sermon_assessment": {"summary": f"batch {index}", "systemic_risks": []},
        "reviewed_claims": claims,
        "claim_reviews": reviews,
        "routing_summary": {"ai_reviewed": len(reviews)},
    }


def test_merge_proves_exact_claim_coverage(tmp_path: Path) -> None:
    package = _package()
    package_path = _write_package(tmp_path, package)
    batches = split_claim_layer_package(package, batch_size=3)
    combined = merge_review_artifacts(
        [_artifact(batch, index) for index, batch in enumerate(batches, start=1)],
        source_package=package,
        source_package_path=package_path,
    )
    assert len(combined["claim_reviews"]) == 6
    assert combined["review_strategy"]["exact_claim_coverage_verified"] is True
    assert (
        combined["review_strategy"]["partition_policy"]
        == "claims_only_all_sources_and_relations_retained"
    )
    assert combined["routing_summary"] == {"ai_reviewed": 6}
    assert combined["reviewer"]["fingerprint_sha256"]


def test_merge_rejects_duplicate_review(tmp_path: Path) -> None:
    package = _package()
    package_path = _write_package(tmp_path, package)
    batches = split_claim_layer_package(package, batch_size=3)
    artifacts = [_artifact(batch, index) for index, batch in enumerate(batches, start=1)]
    artifacts[1]["claim_reviews"][0] = copy.deepcopy(artifacts[0]["claim_reviews"][0])
    with pytest.raises(ClaimLayerReviewBatchError, match="duplicate"):
        merge_review_artifacts(
            artifacts,
            source_package=package,
            source_package_path=package_path,
        )
