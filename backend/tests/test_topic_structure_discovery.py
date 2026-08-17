from __future__ import annotations

import copy
from pathlib import Path

import pytest

from backend.pipeline.topic_structure_discovery import (
    SCOPE,
    build_incremental_package,
    discovery_input,
    graph_profile,
    pending_topic_identity_ids,
    resolve_topic_identity_package,
    validate_discovery,
)
from backend.pipeline.topic_structure_discovery_runner import (
    _apply_topic_package,
    _generation_inputs_match,
    existing_topic_index,
)


class _FakeStore:
    def __init__(self, compiled: dict | None = None) -> None:
        self.compiled = compiled or {}
        self.ingested: list[dict] = []

    def compile_package(self) -> dict:
        return self.compiled

    def ingest_package(self, package: dict, **kwargs: object) -> dict:
        self.ingested.append(copy.deepcopy(package))
        return {"status": "applied", "package_id": package.get("package_id")}


def test_reviewed_result_can_migrate_package_format_without_model_rerun() -> None:
    old = {
        "schema_version": "runner-v1", "source_sha256": "source",
        "prompt_sha256": {"discovery": "prompt"}, "openai_model": "openai",
        "claude_model": "claude", "openai_reasoning_effort": "medium",
        "response_schema_sha256": "schema", "fingerprint_sha256": "old",
    }
    current = {**old, "schema_version": "runner-v2", "fingerprint_sha256": "new"}
    assert _generation_inputs_match(old, current)
    assert not _generation_inputs_match(old, {**current, "prompt_sha256": {"discovery": "changed"}})


def _knowledge() -> dict:
    return {
        "batch": {"batch_id": "RB-TEST"},
        "claims": [
            {"claim_id": "C1", "title": "主张一", "topic_terms": ["约"], "occurrences": [{"transcript_id": "S1"}]},
            {"claim_id": "C2", "title": "主张二", "topic_terms": ["约"], "occurrences": [{"transcript_id": "S2"}]},
            {"claim_id": "C3", "title": "孤立问答", "topic_terms": ["问答"], "occurrences": [{"transcript_id": "S3"}]},
        ],
        "claim_relations": [
            {"claim_relation_id": "R1", "from_id": "C1", "to_id": "C2", "relation_type": "supports"},
            {"claim_relation_id": "R2", "source_id": "C2", "target_id": "C1", "relation_type": "explains"},
        ],
    }


def _discovery() -> dict:
    return {
        "scope_confirmation": SCOPE,
        "topic_families": [{
            "title": "约与关系",
            "organizing_question": "约如何组织关系？",
            "editorial_rationale": "两条互相支持的主张形成一条论证线。",
            "subtopics": [{
                "title": "约的结构",
                "central_question": "约如何运行？",
                "editorial_rationale": "先主旨后证据。",
                "sections": [
                    {"title": "核心判断", "role": "core_thesis", "purpose": "提出主旨", "claim_ids": ["C1"]},
                    {"title": "论证展开", "role": "reasoning", "purpose": "解释主旨", "claim_ids": ["C2"]},
                ],
            }],
        }],
        "unassigned_claim_ids": ["C3"],
        "summary": "候选结构",
    }


def test_graph_profile_accepts_legacy_and_canonical_relation_keys() -> None:
    profile = graph_profile(_knowledge())
    assert profile["relation_count"] == 2
    assert profile["relation_type_counts"] == {"explains": 1, "supports": 1}
    assert profile["high_connection_claims"][0]["degree"] == 2


def test_discovery_input_is_graph_first_and_preserves_sources() -> None:
    source = discovery_input(_knowledge())
    assert source["policy"]["processing_batch_is_not_a_topic"] is True
    assert source["claims"][0]["source_transcript_ids"] == ["S1"]
    assert source["claim_relations"][0]["source_claim_id"] == "C1"


def test_validate_discovery_requires_exactly_one_home_or_unassigned() -> None:
    source = discovery_input(_knowledge())
    validate_discovery(_discovery(), source)

    omitted = copy.deepcopy(_discovery())
    omitted["unassigned_claim_ids"] = []
    with pytest.raises(ValueError, match="omitted claims"):
        validate_discovery(omitted, source)

    duplicate = copy.deepcopy(_discovery())
    duplicate["topic_families"][0]["subtopics"][0]["sections"][1]["claim_ids"].append("C1")
    with pytest.raises(ValueError, match="repeated"):
        validate_discovery(duplicate, source)


def test_incremental_package_keeps_candidates_out_of_canonical_collections() -> None:
    package = build_incremental_package(batch_id="RB-TEST", reviewed_payload=_discovery())
    assert package["topic_nodes"] == []
    assert package["product_plans"] == []
    assert package["knowledge_routes"] == []
    topics = package["candidate_topic_nodes"]
    family = next(row for row in topics if row["topic_level"] == "family")
    subtopic = next(row for row in topics if row["topic_level"] == "subtopic")
    assert subtopic["parent_topic_id"] == family["topic_id"]

    plan = package["candidate_product_plans"][0]
    assert plan["product_type"] == "topic_research"
    assert [row["section_role"] for row in plan["decisions"]] == ["core_thesis", "reasoning"]
    assert {row["claim_id"] for row in package["candidate_knowledge_routes"]} == {"C1", "C2"}
    assert all(row["review_status"] == "candidate" for row in topics)
    assert package["candidate_generation"]["unassigned_claim_ids"] == ["C3"]
    assert len(pending_topic_identity_ids(package)) == 2


def _with_extra_claim(discovery: dict) -> dict:
    grown = copy.deepcopy(discovery)
    grown["topic_families"][0]["subtopics"][0]["sections"][0]["claim_ids"].append("C3")
    grown["unassigned_claim_ids"] = []
    return grown


def _create_all(package: dict) -> dict:
    return resolve_topic_identity_package(
        package,
        {
            candidate_id: {"action": "create_new", "reviewed_by": "tester"}
            for candidate_id in pending_topic_identity_ids(package)
        },
    )


def test_resolving_new_topics_allocates_repeatable_opaque_canonical_ids() -> None:
    candidate = build_incremental_package(batch_id="RB-ONE", reviewed_payload=_discovery())
    first = _create_all(candidate)
    second = _create_all(candidate)
    ids = [row["topic_id"] for row in first["topic_nodes"]]
    assert ids == [row["topic_id"] for row in second["topic_nodes"]]
    assert all(value.startswith("TOPIC-") for value in ids)
    assert all("约" not in value and "RB-ONE" not in value for value in ids)
    assert all(not value.startswith("TCAND-") for value in ids)


def test_approving_a_candidate_label_does_not_mint_a_new_topic() -> None:
    candidate = copy.deepcopy(_discovery())
    candidate["topic_families"][0]["title"] = "候选母题：约与关系"
    approved = build_incremental_package(batch_id="RB-ONE", reviewed_payload=_discovery())
    proposed = build_incremental_package(batch_id="RB-ONE", reviewed_payload=candidate)
    assert [row["topic_id"] for row in approved["candidate_topic_nodes"]] == [
        row["topic_id"] for row in proposed["candidate_topic_nodes"]
    ]


def test_regrouped_topic_is_reported_for_human_merge_never_merged_automatically() -> None:
    renamed = copy.deepcopy(_discovery())
    renamed["topic_families"][0]["title"] = "盟约与人神关系"
    package = build_incremental_package(
        batch_id="RB-TWO",
        reviewed_payload=renamed,
        existing_topics={
            "TOPIC-FAMILY-legacy": {"label": "约与关系", "claim_ids": ["C1", "C2"]},
            "covenant-law-history": {"label": "无关主题", "claim_ids": ["C9"]},
        },
    )
    merges = [
        row for row in package["topic_identity_reconciliations"]
        if row["status"] == "pending_match"
    ]
    assert [row["label"] for row in merges] == ["盟约与人神关系"]
    match = merges[0]["candidate_matches"][0]
    assert match["existing_topic_id"] == "TOPIC-FAMILY-legacy"
    assert match["shared_claim_count"] == 2
    # The overlapping topic is offered as a decision, never applied as a merge.
    assert merges[0]["candidate_topic_id"] != "TOPIC-FAMILY-legacy"
    assert merges[0]["candidate_topic_id"] in pending_topic_identity_ids(package)
    assert package["topic_nodes"] == []

    with pytest.raises(ValueError, match="unresolved topic identity"):
        resolve_topic_identity_package(package)


def test_known_topic_identity_is_reused_without_creating_a_parallel_node() -> None:
    again = build_incremental_package(
        batch_id="RB-TWO",
        reviewed_payload=_discovery(),
        existing_topics={
            "TOPIC-FAMILY-EXISTING": {
                "label": "约与关系", "topic_level": "family",
                "parent_topic_id": None, "claim_ids": ["C1", "C2"],
            },
            "TOPIC-SUBTOPIC-EXISTING": {
                "label": "约的结构", "topic_level": "subtopic",
                "parent_topic_id": "TOPIC-FAMILY-EXISTING", "claim_ids": ["C1", "C2"],
            },
        },
    )
    assert pending_topic_identity_ids(again) == []
    resolved = resolve_topic_identity_package(again)
    assert resolved["topic_nodes"] == []
    assert resolved["product_plans"][0]["canonical_topic_id"] == "TOPIC-SUBTOPIC-EXISTING"
    assert resolved["knowledge_routes"][0]["canonical_topic_ids"] == [
        "TOPIC-FAMILY-EXISTING", "TOPIC-SUBTOPIC-EXISTING"
    ]


def test_match_existing_resolution_reuses_selected_identity() -> None:
    package = build_incremental_package(batch_id="RB-ONE", reviewed_payload=_discovery())
    family = next(
        row for row in package["topic_identity_reconciliations"]
        if row["topic_level"] == "family"
    )
    subtopic = next(
        row for row in package["topic_identity_reconciliations"]
        if row["topic_level"] == "subtopic"
    )
    resolved = resolve_topic_identity_package(package, {
        family["candidate_topic_id"]: {
            "action": "match_existing", "canonical_topic_id": "covenant-family"
        },
        subtopic["candidate_topic_id"]: {
            "action": "match_existing", "canonical_topic_id": "covenant-structure"
        },
    })
    assert resolved["topic_nodes"] == []
    assert resolved["product_plans"][0]["canonical_topic_id"] == "covenant-structure"


def test_subtopic_exact_match_requires_the_matched_parent() -> None:
    package = build_incremental_package(
        batch_id="RB-TWO", reviewed_payload=_discovery(), existing_topics={
            "FAMILY-A": {"label": "约与关系", "topic_level": "family", "claim_ids": ["C1"]},
            "FAMILY-B": {"label": "另一母题", "topic_level": "family", "claim_ids": []},
            "SUB-WRONG": {
                "label": "约的结构", "topic_level": "subtopic",
                "parent_topic_id": "FAMILY-B", "claim_ids": ["C1", "C2"],
            },
        },
    )
    rows = {row["topic_level"]: row for row in package["topic_identity_reconciliations"]}
    assert rows["family"]["status"] == "matched_existing"
    assert rows["subtopic"]["status"] == "pending_match"


def test_existing_topic_index_preserves_level_parent_and_claim_routes() -> None:
    store = _FakeStore({
        "topic_nodes": [
            {"topic_id": "FAMILY", "label": "约", "topic_level": "family"},
            {
                "topic_id": "SUB", "label": "约的结构", "topic_level": "subtopic",
                "parent_topic_id": "FAMILY",
            },
        ],
        "knowledge_routes": [
            {"claim_id": "C1", "canonical_topic_ids": ["FAMILY", "SUB"]},
        ],
    })
    index = existing_topic_index(store)  # type: ignore[arg-type]
    assert index["FAMILY"] == {
        "label": "约", "parent_topic_id": None, "topic_level": "family",
        "claim_ids": ["C1"],
    }
    assert index["SUB"] == {
        "label": "约的结构", "parent_topic_id": "FAMILY",
        "topic_level": "subtopic", "claim_ids": ["C1"],
    }


def test_apply_persists_identity_queue_but_not_canonical_candidates(
    tmp_path: Path,
) -> None:
    candidate = build_incremental_package(batch_id="RB-ONE", reviewed_payload=_discovery())
    store = _FakeStore()
    result = _apply_topic_package(
        store=store,  # type: ignore[arg-type]
        package=candidate,
        package_path=tmp_path / "candidate.json",
        output_dir=tmp_path,
        identity_resolutions=None,
    )
    assert result["status"] == "identity_review_required"
    assert len(store.ingested) == 1
    assert store.ingested[0]["topic_identity_reconciliations"]
    assert not store.ingested[0].get("topic_nodes")


def test_apply_writes_canonical_topics_only_after_explicit_resolution(
    tmp_path: Path,
) -> None:
    candidate = build_incremental_package(batch_id="RB-ONE", reviewed_payload=_discovery())
    resolutions = {
        candidate_id: {"action": "create_new", "reviewed_by": "tester"}
        for candidate_id in pending_topic_identity_ids(candidate)
    }
    store = _FakeStore()
    result = _apply_topic_package(
        store=store,  # type: ignore[arg-type]
        package=candidate,
        package_path=tmp_path / "candidate.json",
        output_dir=tmp_path,
        identity_resolutions={"resolutions": resolutions},
    )
    assert result["status"] == "canonical_applied"
    assert len(store.ingested) == 2
    canonical = store.ingested[1]
    assert canonical["topic_nodes"]
    assert all(not row["topic_id"].startswith("TCAND-") for row in canonical["topic_nodes"])
    assert all(
        not topic_id.startswith("TCAND-")
        for route in canonical["knowledge_routes"]
        for topic_id in route["canonical_topic_ids"]
    )
