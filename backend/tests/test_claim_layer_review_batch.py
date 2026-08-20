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


def test_every_batch_can_name_the_claims_it_does_not_review() -> None:
    """The twin of a duplicate lands wherever the partition put it.

    Section extraction states one conclusion once per section, so a duplicate
    pair is as likely to be split across batches as to share one.  Without the
    other batches' ids the reviewer has nothing to write in
    `duplicate_of_claim_id`, and the finding cannot be made at all.
    """
    package = _package()
    batches = split_claim_layer_package(package, batch_size=3)

    for batch in batches:
        in_batch = {row["claim_id"] for row in batch["claims"]}
        elsewhere = batch["review_batch"]["other_batch_claims"]
        assert {row["claim_id"] for row in elsewhere} == {
            row["claim_id"] for row in package["claims"]
        } - in_batch
        assert all(row["statement"] for row in elsewhere)


def test_split_leaves_out_claims_a_merge_retired() -> None:
    package = _package()
    package["claims"][1]["superseded_by"] = "CLAIM-1"

    batches = split_claim_layer_package(package, batch_size=3)
    by_source = split_claim_layer_package_by_source(package)

    reviewed = [row["claim_id"] for batch in batches for row in batch["claims"]]
    assert "CLAIM-2" not in reviewed and len(reviewed) == 5
    assert "CLAIM-2" not in [
        row["claim_id"] for batch in by_source for row in batch["claims"]
    ]


def test_merge_expects_coverage_of_the_live_claims_only(tmp_path: Path) -> None:
    """A retired claim is never reviewed, so demanding a review for it fails."""
    package = _package()
    package["claims"][1]["superseded_by"] = "CLAIM-1"
    package_path = _write_package(tmp_path, package)
    batches = split_claim_layer_package(package, batch_size=3)

    combined = merge_review_artifacts(
        [_artifact(batch, index) for index, batch in enumerate(batches, start=1)],
        source_package=package,
        source_package_path=package_path,
    )

    assert [row["claim_id"] for row in combined["claim_reviews"]] == [
        "CLAIM-1", "CLAIM-3", "CLAIM-4", "CLAIM-5", "CLAIM-6",
    ]
    assert combined["source"]["claim_count"] == 5


def test_merge_carries_every_batch_bill(tmp_path: Path) -> None:
    """The batched path is the only one a package this size can take.

    Dropping the per-batch rows left it with no answer to what the review cost
    -- the number those rows were added to produce.
    """
    package = _package()
    package_path = _write_package(tmp_path, package)
    batches = split_claim_layer_package(package, batch_size=3)
    artifacts = []
    for index, batch in enumerate(batches, start=1):
        artifact = _artifact(batch, index)
        artifact["usage"] = [
            {
                "attempt": 1, "prompt_tokens": 1000 * index, "cached_tokens": 400 * index,
                "cache_write_tokens": None, "completion_tokens": 100, "total_tokens": 1000 * index + 100,
            }
        ]
        artifacts.append(artifact)

    combined = merge_review_artifacts(
        artifacts, source_package=package, source_package_path=package_path,
    )

    assert [row["batch_index"] for row in combined["usage"]] == [1, 2]
    assert sum(row["prompt_tokens"] for row in combined["usage"]) == 3000
