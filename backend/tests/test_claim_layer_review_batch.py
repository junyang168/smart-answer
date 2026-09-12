import copy
import json
from pathlib import Path

import pytest

from backend.pipeline import corpus_ai_adjudication_runner
from backend.pipeline.claim_layer_review_batch import (
    ClaimLayerReviewBatchError,
    merge_review_artifacts,
    split_claim_layer_package,
    split_claim_layer_package_by_source,
    validate_split_claim_layer_package,
)
from backend.pipeline.corpus_ai_review import AI_REVIEW_VERSION, apply_risk_routing
from backend.pipeline.corpus_ai_review_runner import (
    _matching_review_artifact,
    _normalize_claim_layer,
    _review_artifact_sha256,
)
from backend.pipeline.claim_layer_review_batch_runner import (
    _assert_review_batch_binding,
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
    for batch in batches:
        validate_split_claim_layer_package(batch)


def test_split_package_rejects_any_change_after_partitioning() -> None:
    batch = split_claim_layer_package(_package(), batch_size=3)[0]
    batch["claims"][0]["title"] = "silently changed"

    with pytest.raises(ClaimLayerReviewBatchError, match="SHA"):
        validate_split_claim_layer_package(batch)


def test_runner_never_rebinds_an_old_review_to_a_new_partition() -> None:
    batch = split_claim_layer_package(_package(), batch_size=3)[0]
    artifact = _artifact(batch, 1)
    artifact["source"].pop("review_batch")

    with pytest.raises(ValueError, match="not bound"):
        _assert_review_batch_binding(artifact, batch)

    assert "review_batch" not in artifact["source"]


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
    claims = _normalize_claim_layer(batch)["candidate_claims"]
    response = {
        "sermon_assessment": {
            "summary": f"batch {index}",
            "systemic_risks": [],
        },
        "claim_reviews": [
        {
            "claim_id": row["claim_id"],
            "decision": "pass",
            "issues": [],
            "proposed_statement": "",
            "proposed_claim_kind": "",
            "proposed_route_type": "unchanged",
            "rationale": "source supports claim",
            "confidence": "high",
            "human_review_reason": "",
        }
        for row in batch["claims"]
        ],
    }
    fingerprint = f"review-{index}"
    routed = apply_risk_routing(
        response,
        reviewer_fingerprint_sha256=fingerprint,
        spot_check_percent=0,
    )
    artifact = {
        "schema_version": AI_REVIEW_VERSION,
        "source": {"batch": index, "review_batch": batch["review_batch"]},
        "reviewer": {"fingerprint_sha256": fingerprint},
        "spot_check_percent": 0,
        "sermon_assessment": response["sermon_assessment"],
        "reviewed_claims": claims,
        **routed,
    }
    artifact["reviewer"]["artifact_sha256"] = _review_artifact_sha256(artifact)
    return artifact


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
    assert combined["routing_summary"] == {
        "ai_reviewed": 6,
        "awaiting_openai_adjudication": 0,
        "human_spot_check": 0,
    }
    assert combined["reviewer"]["fingerprint_sha256"]
    survey = _normalize_claim_layer(package)
    assert _matching_review_artifact(
        combined,
        survey=survey,
        expected_fingerprint=combined["reviewer"]["fingerprint_sha256"],
        spot_check_percent=combined["spot_check_percent"],
    )


def test_adjudication_accepts_the_canonical_combined_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _package()
    package_path = _write_package(tmp_path, package)
    survey = _normalize_claim_layer(package)
    batches = split_claim_layer_package(package, batch_size=3)
    combined = merge_review_artifacts(
        [_artifact(batch, index) for index, batch in enumerate(batches, start=1)],
        source_package=package,
        source_package_path=package_path,
    )
    review_path = tmp_path / "combined-review.json"
    review_path.write_text(json.dumps(combined), encoding="utf-8")
    monkeypatch.setattr(
        corpus_ai_adjudication_runner,
        "_load_context",
        lambda *_args: (
            survey,
            {row["claim_id"]: row for row in survey["candidate_claims"]},
            [],
            {},
        ),
    )

    *_, accepted, review_bytes = (
        corpus_ai_adjudication_runner._validated_review_context(
            package_path, review_path, []
        )
    )

    assert accepted == combined
    assert review_bytes == review_path.read_bytes()


def test_merge_rejects_review_snapshot_changed_from_source_package(
    tmp_path: Path,
) -> None:
    package = _package()
    package_path = _write_package(tmp_path, package)
    batches = split_claim_layer_package(package, batch_size=3)
    artifacts = [_artifact(batch, index) for index, batch in enumerate(batches, start=1)]
    artifacts[0]["reviewed_claims"][0]["statement"] = "coherently but wrongly changed"
    artifacts[0]["reviewer"]["artifact_sha256"] = _review_artifact_sha256(
        artifacts[0]
    )

    with pytest.raises(ClaimLayerReviewBatchError, match="differs from the source"):
        merge_review_artifacts(
            artifacts,
            source_package=package,
            source_package_path=package_path,
        )


def test_merge_rejects_duplicate_review(tmp_path: Path) -> None:
    package = _package()
    package_path = _write_package(tmp_path, package)
    batches = split_claim_layer_package(package, batch_size=3)
    artifacts = [_artifact(batch, index) for index, batch in enumerate(batches, start=1)]
    artifacts[1]["claim_reviews"][0] = copy.deepcopy(artifacts[0]["claim_reviews"][0])
    artifacts[1]["reviewer"]["artifact_sha256"] = _review_artifact_sha256(
        artifacts[1]
    )
    with pytest.raises(ClaimLayerReviewBatchError, match="modified"):
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
        artifact["reviewer"]["artifact_sha256"] = _review_artifact_sha256(
            artifact
        )
        artifacts.append(artifact)

    combined = merge_review_artifacts(
        artifacts, source_package=package, source_package_path=package_path,
    )

    assert [row["batch_index"] for row in combined["usage"]] == [1, 2]
    assert sum(row["prompt_tokens"] for row in combined["usage"]) == 3000
