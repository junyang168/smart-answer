"""What falls when an anchor turns out to quote deleted text."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.postgres_store import (
    build_retirement_plan,
    normalize_package,
    record_content_sha,
)
from backend.pipeline.soft_deleted_anchor_audit import (
    audit,
    excerpt_is_deleted,
    segment_texts,
)
from backend.pipeline.record_withdrawal import closure_from_fragments

SEGMENTS = ["教會建立在信仰上。~~我昨天講過這個。~~所以我們繼續。"]


def test_an_excerpt_inside_a_struck_span_is_deleted() -> None:
    assert excerpt_is_deleted("我昨天講過這個", SEGMENTS, "S0001")


def test_an_excerpt_outside_the_struck_span_is_not() -> None:
    assert not excerpt_is_deleted("教會建立在信仰上", SEGMENTS, "S0001")
    assert not excerpt_is_deleted("所以我們繼續", SEGMENTS, "S0001")


def test_an_anchor_whose_key_no_longer_resolves_is_still_checked() -> None:
    """The question is whether the quoted text was deleted.

    Only 20% of claimed indices in the staged packages still resolve, so a key
    that points nowhere must not read as "not deleted".
    """

    assert excerpt_is_deleted("我昨天講過這個", SEGMENTS, "S0099")


def test_text_that_survives_anywhere_is_not_deleted() -> None:
    """`str.find` returns an arbitrary occurrence, and the professor repeats
    himself: 「為什麼？」 appears three times in one paragraph of 太16. Flagging
    on the first hit retires a record by guessing which occurrence the anchor
    meant -- it flagged six, and one of them was retired."""

    segments = ["~~為什麼？我昨天講過。~~所以我問你，為什麼？"]
    assert not excerpt_is_deleted("為什麼？", segments, "S0001")
    assert excerpt_is_deleted("我昨天講過", segments, "S0001")


def test_editorial_rows_do_not_make_deleted_source_excerpt_survive(
    tmp_path: Path,
) -> None:
    path = tmp_path / "sermon.json"
    path.write_text(
        json.dumps(
            [
                {"index": 1, "text": "教授說：~~這句已經刪除~~。"},
                {
                    "index": "subtitle-1",
                    "type": "subtitle",
                    "text": "## 這句已經刪除",
                    "user_id": "editor@example.org",
                },
                {
                    "index": "comment-1",
                    "type": "comment",
                    "text": "這句已經刪除",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    segments = segment_texts(path)

    assert segments == ["教授說：~~這句已經刪除~~。"]
    assert excerpt_is_deleted("這句已經刪除", segments, "S0001") is True


def _audit(**overrides):
    base = dict(
        fragments={
            "FR-live": {"source_id": "SRC-1", "paragraph_key": "S0001",
                        "verbatim_excerpt": "教會建立在信仰上"},
            "FR-dead": {"source_id": "SRC-1", "paragraph_key": "S0001",
                        "verbatim_excerpt": "我昨天講過這個"},
        },
        owners={
            "evidence_steps": {
                "E-gone": {"source_fragment_ids": ["FR-dead"]},
                "E-kept": {"source_fragment_ids": ["FR-dead", "FR-live"]},
            },
        },
        claims={
            "CL-gone": {"evidence_step_ids": ["E-gone"]},
            "CL-kept": {"evidence_step_ids": ["E-gone", "E-kept"]},
        },
        segments_by_source={"SRC-1": SEGMENTS},
        relations={"claim_relations": {"REL-1": {"from_id": "CL-gone", "to_id": "CL-kept"}}},
    )
    base.update(overrides)
    return audit(**base)


def test_a_record_keeping_one_live_anchor_is_weakened_not_retired() -> None:
    """It loses a citation, not its footing."""

    result = _audit()
    assert result.weakened_owners == [("evidence_steps", "E-kept")]
    assert result.orphaned_owners == [("evidence_steps", "E-gone")]


def test_legacy_singular_fragment_owner_is_in_the_withdrawal_closure() -> None:
    result = closure_from_fragments(
        {"FR-dead": "SRC-1"},
        owners={
            "evidence_steps": {
                "E-legacy": {"source_fragment_id": "FR-dead"},
            }
        },
        claims={},
    )

    assert result.orphaned_owners == [("evidence_steps", "E-legacy")]
    assert ("evidence_steps", "E-legacy") in result.closure()


def test_a_claim_keeps_standing_while_one_of_its_steps_does() -> None:
    result = _audit()
    assert result.orphaned_claims == ["CL-gone"]


def test_an_edge_to_a_retired_endpoint_joins_the_closure() -> None:
    """An edge whose endpoint is gone is not a weaker edge, it is an edge to nothing."""

    result = _audit()
    assert ("claim_relations", "REL-1") in result.closure()


def test_the_closure_is_fragments_then_owners_then_claims_then_edges() -> None:
    assert _audit().closure() == [
        ("source_fragments", "FR-dead"),
        ("evidence_steps", "E-gone"),
        ("claims", "CL-gone"),
        ("claim_relations", "REL-1"),
    ]


def test_a_fragment_whose_source_cannot_be_read_is_counted_not_judged() -> None:
    """"We could not check these" is a different statement from "these are clean"."""

    result = _audit(segments_by_source={})
    assert result.unresolved_fragments == 2
    assert result.withdrawn_fragments == {}


# ---------------------------------------------------------------------------
# planning the withdrawal
# ---------------------------------------------------------------------------

EXISTING = {
    ("claims", "CL-1"): {"revision": 3, "content_sha256": "sha-1", "payload": {"claim_id": "CL-1"}},
}


def test_a_retirement_leaves_the_record_saying_what_it_said() -> None:
    """Rewriting the payload to say "retired" would edit the evidence to record
    a decision about it. The withdrawal belongs to the store, not to the text."""

    plan = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="測試", package_id="RETIRE-TEST",
    )
    operation = plan.operations[0]
    assert operation.operation == "retire"
    assert operation.payload == {"claim_id": "CL-1"}
    assert operation.before_sha256 == operation.after_sha256 == "sha-1"
    assert (operation.before_revision, operation.after_revision) == (3, 4)


def test_retiring_what_is_already_gone_is_not_an_error() -> None:
    """Running it twice must not fail, or nobody will dare run it once."""

    plan = build_retirement_plan(
        [("claims", "CL-1"), ("claims", "CL-missing")], EXISTING,
        reason="測試", package_id="RETIRE-TEST",
    )
    assert [item.object_id for item in plan.operations] == ["CL-1"]
    assert plan.unchanged == 1


def test_the_same_retirement_plans_to_the_same_change_set() -> None:
    """The fingerprint is what makes a re-run idempotent rather than additive."""

    first = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="測試", package_id="RETIRE-TEST")
    second = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="測試", package_id="RETIRE-TEST")
    assert first.change_set_id == second.change_set_id
    other = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="別的理由", package_id="RETIRE-TEST")
    assert other.change_set_id != first.change_set_id


# ---------------------------------------------------------------------------
# what the store says when a package would revive something withdrawn
# ---------------------------------------------------------------------------


def test_a_retired_object_is_not_reported_as_a_concurrent_write() -> None:
    """It reaches the conflict check looking exactly like one.

    The planner reads only live rows, so a retired object is planned as a
    `create` with no `before_sha256` while its row is still there with a hash.
    "Concurrent change" would send the reader hunting for another writer.
    """

    from datetime import datetime, timezone

    from backend.api.canonical_repository.postgres_store import conflict_for

    retired = conflict_for(
        "claims", "CL-1", expected=None, found="sha-1",
        retired_at=datetime(2026, 8, 20, 9, 30, tzinfo=timezone.utc),
    )
    assert "was retired at 2026-08-20 09:30:00+0000" in str(retired)
    assert "concurrent" not in str(retired).lower()

    concurrent = conflict_for("claims", "CL-1", expected="sha-0", found="sha-1", retired_at=None)
    assert "Concurrent change" in str(concurrent)


# ---------------------------------------------------------------------------
# what a re-extraction replaces
# ---------------------------------------------------------------------------

from backend.pipeline.extraction_supersede import package_source_ids, superseded  # noqa: E402


def _package():
    return {
        "schema_version": "wang_shared_knowledge_v1.3",
        "package_id": "PKG-NEW",
        "source_documents": [
            {"source_id": "SRC-1", "source_type": "sermon_transcript", "title": "讲道"}
        ],
        "source_fragments": [
            {"fragment_id": "FR-new", "source_id": "SRC-1", "verbatim_excerpt": "新的原话"}
        ],
    }


LIVE = {
    "FR-old": {"source_id": "SRC-1"},
    "FR-kept": {"source_id": "SRC-1"},
    "FR-elsewhere": {"source_id": "SRC-2"},
}


def test_a_re_extraction_supersedes_only_its_own_sources() -> None:
    """A package that carries no document for a source is not claiming to
    replace that source's records."""

    result = superseded(_package(), live_fragments=LIVE, owners={}, claims={})
    assert set(result.withdrawn_fragments) == {"FR-old", "FR-kept"}
    assert "FR-elsewhere" not in result.withdrawn_fragments


def test_a_fragment_the_new_extraction_reproduces_is_an_update_not_a_casualty() -> None:
    package = _package()
    package["source_fragments"].append({"fragment_id": "FR-kept", "source_id": "SRC-1"})
    result = superseded(package, live_fragments=LIVE, owners={}, claims={})
    assert set(result.withdrawn_fragments) == {"FR-old"}


def test_a_legacy_source_id_for_the_same_transcript_is_retired_with_its_generation() -> None:
    from backend.pipeline.extraction_supersede_runner import transcript_source_aliases

    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-1"
    aliases = transcript_source_aliases(
        package,
        {
            "SRC-1": {
                "source_id": "SRC-1",
                "source_type": "sermon_transcript",
                "transcript_id": "SERMON-1",
            },
            "legacy-sermon-1": {
                "source_id": "legacy-sermon-1",
                "source_type": "sermon_transcript",
                "transcript_id": "SERMON-1",
            },
            "SRC-2": {
                "source_id": "SRC-2",
                "source_type": "sermon_transcript",
                "transcript_id": "SERMON-2",
            },
        },
    )
    result = superseded(
        package,
        live_fragments={
            "FR-alias": {"source_id": "legacy-sermon-1"},
            "FR-other": {"source_id": "SRC-2"},
        },
        owners={},
        claims={},
        source_alias_ids=aliases,
    )

    assert aliases == {"legacy-sermon-1"}
    assert result.withdrawn_fragments == {"FR-alias": "legacy-sermon-1"}
    assert ("source_documents", "legacy-sermon-1") in result.closure()
    assert ("source_documents", "SRC-2") not in result.closure()


def test_alias_matching_does_not_cross_source_types() -> None:
    from backend.pipeline.extraction_supersede_runner import transcript_source_aliases

    package = _package()
    package["source_documents"][0]["transcript_id"] = "SHARED-NAME"
    assert transcript_source_aliases(
        package,
        {
            "notes-with-same-name": {
                "source_type": "notes_manuscript",
                "transcript_id": "SHARED-NAME",
            }
        },
    ) == set()


def test_legacy_source_id_is_used_as_transcript_identity() -> None:
    from backend.pipeline.extraction_supersede_runner import transcript_source_aliases

    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-1"
    assert transcript_source_aliases(
        package,
        {
            "SERMON-1": {
                "source_id": "SERMON-1",
                "source_type": "sermon_transcript",
            }
        },
    ) == {"SERMON-1"}


def test_ambiguous_legacy_alias_without_source_type_fails_closed() -> None:
    from backend.pipeline.extraction_supersede_runner import transcript_source_aliases

    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-1"
    with pytest.raises(ValueError, match="missing source_type"):
        transcript_source_aliases(package, {"SERMON-1": {"source_id": "SERMON-1"}})


def test_incoming_package_cannot_name_one_transcript_twice() -> None:
    from backend.pipeline.extraction_supersede_runner import transcript_source_aliases

    package = _package()
    package["source_documents"][0]["transcript_id"] = "SERMON-1"
    package["source_documents"].append({
        "source_id": "SRC-alias",
        "source_type": "sermon_transcript",
        "transcript_id": "SERMON-1",
    })
    with pytest.raises(ValueError, match="multiple source IDs"):
        transcript_source_aliases(package, {})


def test_alias_identity_includes_source_type() -> None:
    from backend.pipeline.extraction_supersede_runner import transcript_source_aliases

    package = {
        "source_documents": [
            {
                "source_id": "SERMON-NEW",
                "source_type": "sermon_transcript",
                "transcript_id": "SHARED-NAME",
            },
            {
                "source_id": "NOTES-NEW",
                "source_type": "notes_manuscript",
                "transcript_id": "SHARED-NAME",
            },
        ]
    }
    live = {
        "SERMON-OLD": {
            "source_type": "sermon_transcript",
            "transcript_id": "SHARED-NAME",
        },
        "NOTES-OLD": {
            "source_type": "notes_manuscript",
            "transcript_id": "SHARED-NAME",
        },
    }

    assert transcript_source_aliases(package, live) == {"SERMON-OLD", "NOTES-OLD"}


def test_predecessor_namespaces_include_exact_and_legacy_source_generations() -> None:
    from backend.pipeline.extraction_supersede_runner import (
        transcript_predecessor_namespaces,
    )
    from backend.pipeline.relation_id_namespace import source_namespace

    package = {
        "source_documents": [{
            "source_id": "SRC-NEW",
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-1",
        }]
    }
    live = {
        "SRC-OLD": {
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-1",
            "extraction_record_namespace": "DK-111111111111",
        },
        "UNRELATED": {
            "source_type": "sermon_transcript",
            "transcript_id": "SERMON-2",
            "extraction_record_namespace": "DK-222222222222",
        },
    }

    assert transcript_predecessor_namespaces(package, live) == {
        "DK-111111111111",
        source_namespace("SRC-OLD"),
        # Direct sermon extraction historically names its generated records
        # from transcript_id, while manifest extraction names them from
        # source_id. A legacy SourceDocument does not say which path produced
        # it, so both exact candidates must be retired.
        source_namespace("SERMON-1"),
    }


def test_the_sources_come_from_the_documents_not_the_fragments() -> None:
    assert package_source_ids(_package()) == {"SRC-1"}
    assert package_source_ids({"source_fragments": [{"source_id": "SRC-9"}]}) == set()


def test_arrival_and_withdrawal_plan_as_one_change_set() -> None:
    """Two change sets would leave a window in which the store holds both
    extractions, or neither, and nothing to say which state it is in."""

    from backend.api.canonical_repository.postgres_store import (
        build_change_set_plan,
        build_retirement_plan,
        combined_plan,
    )

    arrival = build_change_set_plan(_package(), {})
    withdrawal = build_retirement_plan(
        [("claims", "CL-1")], EXISTING, reason="superseded by PKG-NEW", package_id="PKG-NEW",
    )
    merged = combined_plan(arrival, withdrawal)
    assert merged.as_dict()["summary"]["retired"] == 1
    assert merged.as_dict()["summary"]["created"] == arrival.as_dict()["summary"]["created"]
    assert [item.operation for item in merged.operations][0] == "retire"
    assert merged.change_set_id not in {arrival.change_set_id, withdrawal.change_set_id}


# ---------------------------------------------------------------------------
# which written articles a withdrawal invalidates
# ---------------------------------------------------------------------------

from backend.pipeline.extraction_supersede_runner import (  # noqa: E402
    no_op_result,
    obsolete_candidate_batch_retirement,
    product_impact_keys,
    products_to_rebuild,
    stale_ai_cross_sermon_constraint_retirement,
    stale_candidate_projection_retirement,
    stale_pending_topic_identity_retirement,
    validate_obsolete_retirement_plan,
    validate_stale_ai_cross_sermon_constraint_retirement_plan,
    validate_stale_candidate_projection_retirement_plan,
    validate_stale_topic_identity_retirement_plan,
)

DEPENDENCIES = {
    "PD-draft": {
        "consumer_kind": "matthew_draft",
        "consumer_id": "DRAFT-matthew-16-13-20",
        "claim_id": "CL-old",
        "status": "current",
        "dependency_manifest": [],
    },
    "PD-qa": {
        "consumer_kind": "evidence_qa",
        "consumer_id": "QA-kingdom-keys",
        "claim_id": "CL-kept",
        "status": "current",
        "dependency_manifest": [
            {"collection": "source_fragments", "record_id": "SF-old"}
        ],
    },
    "PD-already-stale": {
        "consumer_kind": "matthew_draft",
        "consumer_id": "DRAFT-old",
        "claim_id": "CL-old",
        "status": "invalidated",
    },
}


def _obsolete_candidate_live() -> dict[str, dict[str, dict]]:
    batch_key = "TEST-OBSOLETE"
    plan_id = f"CP-{batch_key}-S-abcdef123456"
    candidate = {"review_status": "candidate", "visibility": "internal", "revision": 1}
    return {
        "composition_plans": {
            plan_id: {**candidate, "plan_id": plan_id},
        },
        "composition_decisions": {
            "CD-TEST": {**candidate, "decision_id": "CD-TEST", "plan_id": plan_id},
        },
        "knowledge_routes": {
            "KR-TEST": {**candidate, "route_id": "KR-TEST", "target_id": plan_id},
        },
        "editorial_syntheses": {
            "SYN-TEST-OBSOLETE-S-123456abcdef": {
                **candidate,
                "synthesis_id": "SYN-TEST-OBSOLETE-S-123456abcdef",
                "corpus_scope": f"RB-{batch_key}",
            },
        },
        "topic_identity_reconciliations": {
            "TIR-TEST": {
                **candidate,
                "reconciliation_id": "TIR-TEST",
                "origin_batch_id": f"RB-{batch_key}",
                "status": "pending_new",
            },
        },
    }


def _live_rows(live: dict[str, dict[str, dict]]) -> list[tuple[str, str, dict]]:
    return [
        (collection, object_id, payload)
        for collection, rows in live.items()
        for object_id, payload in rows.items()
    ]


def test_obsolete_candidate_retirement_is_explicit_and_snapshot_bound() -> None:
    live = _obsolete_candidate_live()
    sibling_id = "CP-TEST-OBSOLETE-EXTRA-S-abcdef123456"
    live["composition_plans"][sibling_id] = {
        "plan_id": sibling_id,
        "review_status": "candidate",
        "visibility": "internal",
        "revision": 1,
    }
    keys, audit = obsolete_candidate_batch_retirement(
        batch_id="RB-TEST-OBSOLETE",
        live=live,
        all_live_rows=_live_rows(live),
        known_plan_ids=set(live["composition_plans"]),
    )

    assert len(keys) == audit["summary"]["total"] == 4
    assert ("composition_plans", sibling_id) not in keys
    assert ("topic_identity_reconciliations", "TIR-TEST") not in keys
    assert audit["status"] == "planned"
    assert audit["reason_code"] == "composition_plan_candidate_retired_by_draft_first"
    assert len(audit["scope_sha256"]) == 64
    assert all(row["expected_revision"] == 1 for row in audit["records"])
    assert all(len(row["expected_content_sha256"]) == 64 for row in audit["records"])


def test_obsolete_candidate_retirement_refuses_authority_and_external_refs() -> None:
    live = _obsolete_candidate_live()
    live["composition_plans"][
        "CP-TEST-OBSOLETE-S-abcdef123456"
    ]["review_status"] = "approved"
    with pytest.raises(ValueError, match="non-candidate authority"):
        obsolete_candidate_batch_retirement(
            batch_id="RB-TEST-OBSOLETE",
            live=live,
            all_live_rows=_live_rows(live),
            known_plan_ids=set(live["composition_plans"]),
        )

    live = _obsolete_candidate_live()
    rows = _live_rows(live) + [
        ("product_dependencies", "PD-OUTSIDE", {"route_ids": ["KR-TEST"]})
    ]
    with pytest.raises(ValueError, match="external references"):
        obsolete_candidate_batch_retirement(
            batch_id="RB-TEST-OBSOLETE",
            live=live,
            all_live_rows=rows,
            known_plan_ids=set(live["composition_plans"]),
        )

    live = _obsolete_candidate_live()
    topic_synthesis_id = "SYN-FAMILY-topic-discovery"
    live["editorial_syntheses"][topic_synthesis_id] = {
        "synthesis_id": topic_synthesis_id,
        "corpus_scope": "RB-TEST-OBSOLETE",
        "review_status": "candidate",
        "visibility": "internal",
        "revision": 1,
    }
    keys, _audit = obsolete_candidate_batch_retirement(
        batch_id="RB-TEST-OBSOLETE",
        live=live,
        all_live_rows=_live_rows(live),
        known_plan_ids=set(live["composition_plans"]),
    )
    assert ("editorial_syntheses", topic_synthesis_id) not in keys


def test_obsolete_candidate_retirement_replay_requires_known_history() -> None:
    keys, audit = obsolete_candidate_batch_retirement(
        batch_id="RB-TEST-OBSOLETE",
        live={},
        all_live_rows=[],
        known_plan_ids={"CP-TEST-OBSOLETE-S-abcdef123456"},
    )

    assert keys == []
    assert audit["status"] == "already_retired"
    lingering = _obsolete_candidate_live()
    lingering["composition_plans"] = {}
    with pytest.raises(ValueError, match="partially retired"):
        obsolete_candidate_batch_retirement(
            batch_id="RB-TEST-OBSOLETE",
            live=lingering,
            all_live_rows=_live_rows(lingering),
            known_plan_ids={"CP-TEST-OBSOLETE-S-abcdef123456"},
        )
    with pytest.raises(ValueError, match="no known CompositionPlan"):
        obsolete_candidate_batch_retirement(
            batch_id="RB-TYPO",
            live={},
            all_live_rows=[],
            known_plan_ids={"CP-TEST-OBSOLETE-S-abcdef123456"},
        )


def test_obsolete_retirement_audit_must_match_change_set_snapshot() -> None:
    live = _obsolete_candidate_live()
    _, audit = obsolete_candidate_batch_retirement(
        batch_id="RB-TEST-OBSOLETE",
        live=live,
        all_live_rows=_live_rows(live),
        known_plan_ids=set(live["composition_plans"]),
    )
    operations = tuple(
        SimpleNamespace(
            collection=row["collection"],
            object_id=row["object_id"],
            operation="retire",
            before_revision=row["expected_revision"],
            before_sha256=row["expected_content_sha256"],
        )
        for row in audit["records"]
    )
    validate_obsolete_retirement_plan(SimpleNamespace(operations=operations), audit)

    drifted = list(operations)
    drifted[0] = SimpleNamespace(
        **{**vars(drifted[0]), "before_revision": 2}
    )
    with pytest.raises(ValueError, match="revision drifted"):
        validate_obsolete_retirement_plan(
            SimpleNamespace(operations=tuple(drifted)), audit
        )
    drifted = list(operations)
    drifted[0] = SimpleNamespace(
        **{**vars(drifted[0]), "before_sha256": "different"}
    )
    with pytest.raises(ValueError, match="content drifted"):
        validate_obsolete_retirement_plan(
            SimpleNamespace(operations=tuple(drifted)), audit
        )


def test_stale_topic_identity_retirement_is_exact_and_snapshot_bound() -> None:
    extraction = SimpleNamespace(
        collection="claims", object_id="CL-OLD", operation="retire"
    )
    stale = {
        "reconciliation_id": "TIR-STALE",
        "origin_batch_id": "RB-TOPIC",
        "claim_ids": ["CL-OLD", "CL-KEPT"],
        "status": "pending_new",
        "review_status": "candidate",
        "visibility": "internal",
        "revision": 3,
    }
    untouched = {
        **stale,
        "reconciliation_id": "TIR-UNTOUCHED",
        "claim_ids": ["CL-KEPT"],
    }
    other_batch = {
        **stale,
        "reconciliation_id": "TIR-OTHER",
        "origin_batch_id": "RB-OTHER",
    }
    rows = [
        ("topic_identity_reconciliations", "TIR-STALE", stale),
        ("topic_identity_reconciliations", "TIR-UNTOUCHED", untouched),
        ("topic_identity_reconciliations", "TIR-OTHER", other_batch),
    ]
    keys, audit = stale_pending_topic_identity_retirement(
        batch_id="RB-TOPIC",
        change_set=SimpleNamespace(operations=(extraction,)),
        all_live_rows=rows,
    )

    assert keys == [("topic_identity_reconciliations", "TIR-STALE")]
    assert audit["status"] == "planned"
    assert audit["records"][0]["stale_claim_ids"] == ["CL-OLD"]
    assert len(audit["scope_sha256"]) == 64

    retirement = SimpleNamespace(
        collection="topic_identity_reconciliations",
        object_id="TIR-STALE",
        operation="retire",
        before_revision=3,
        before_sha256=record_content_sha(stale),
    )
    validate_stale_topic_identity_retirement_plan(
        SimpleNamespace(operations=(extraction, retirement)), audit
    )


def test_stale_topic_identity_retirement_refuses_authority_and_external_refs() -> None:
    extraction = SimpleNamespace(
        collection="claims", object_id="CL-OLD", operation="retire"
    )
    stale = {
        "reconciliation_id": "TIR-STALE",
        "origin_batch_id": "RB-TOPIC",
        "claim_ids": ["CL-OLD"],
        "status": "pending_new",
        "review_status": "candidate",
        "visibility": "internal",
        "revision": 1,
    }
    with pytest.raises(ValueError, match="resolved or approved"):
        stale_pending_topic_identity_retirement(
            batch_id="RB-TOPIC",
            change_set=SimpleNamespace(operations=(extraction,)),
            all_live_rows=[(
                "topic_identity_reconciliations",
                "TIR-STALE",
                {**stale, "review_status": "approved"},
            )],
        )
    with pytest.raises(ValueError, match="external references"):
        stale_pending_topic_identity_retirement(
            batch_id="RB-TOPIC",
            change_set=SimpleNamespace(operations=(extraction,)),
            all_live_rows=[
                ("topic_identity_reconciliations", "TIR-STALE", stale),
                ("product_dependencies", "PD-STALE", {"record_ids": ["TIR-STALE"]}),
            ],
        )


def _retiring_claim_plan() -> SimpleNamespace:
    return SimpleNamespace(operations=(
        SimpleNamespace(
            collection="claims", object_id="CL-OLD", operation="retire"
        ),
    ))


def _stale_projection_rows() -> list[tuple[str, str, dict]]:
    plan_id = "CP-OLD-BATCH-S-abcdef123456"
    return [
        (
            "composition_plans",
            plan_id,
            {
                "plan_id": plan_id,
                "review_status": "candidate",
                "visibility": "internal",
            },
        ),
        (
            "composition_decisions",
            "CD-OLD",
            {
                "decision_id": "CD-OLD",
                "plan_id": plan_id,
                "review_status": "candidate",
                "visibility": "internal",
            },
        ),
        (
            "knowledge_routes",
            "KR-STALE",
            {
                "route_id": "KR-STALE",
                "claim_id": "CL-OLD",
                "target_id": plan_id,
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 2,
            },
        ),
        (
            "knowledge_routes",
            "KR-UNTOUCHED",
            {
                "route_id": "KR-UNTOUCHED",
                "claim_id": "CL-KEPT",
                "target_id": plan_id,
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 1,
            },
        ),
        (
            "editorial_syntheses",
            "SYN-OLD-BATCH-S-abcdef123456",
            {
                "synthesis_id": "SYN-OLD-BATCH-S-abcdef123456",
                "corpus_scope": "RB-OLD-BATCH",
                "claim_ids": ["CL-OLD", "CL-KEPT"],
                "review_status": "candidate",
                "visibility": "internal",
                "revision": 3,
            },
        ),
    ]


def test_stale_projection_retirement_is_narrow_and_retires_mixed_synthesis() -> None:
    rows = _stale_projection_rows()
    keys, audit = stale_candidate_projection_retirement(
        batch_ids=["RB-OLD-BATCH"],
        change_set=_retiring_claim_plan(),
        all_live_rows=rows,
        known_plan_ids={"CP-OLD-BATCH-S-abcdef123456"},
    )

    assert keys == [
        ("editorial_syntheses", "SYN-OLD-BATCH-S-abcdef123456"),
        ("knowledge_routes", "KR-STALE"),
    ]
    assert ("knowledge_routes", "KR-UNTOUCHED") not in keys
    assert audit["summary"] == {
        "knowledge_routes": 1,
        "editorial_syntheses": 1,
        "total": 2,
    }
    synthesis = next(
        row for row in audit["records"]
        if row["collection"] == "editorial_syntheses"
    )
    assert synthesis["stale_claim_ids"] == ["CL-OLD"]
    assert synthesis["owner_plan_id"] == "CP-OLD-BATCH-S-abcdef123456"
    assert len(audit["scope_sha256"]) == 64


def test_stale_projection_retirement_rejects_bad_scope_authority_and_refs() -> None:
    rows = _stale_projection_rows()
    with pytest.raises(ValueError, match="unknown stale projection batch"):
        stale_candidate_projection_retirement(
            batch_ids=["RB-TYPO"],
            change_set=_retiring_claim_plan(),
            all_live_rows=rows,
            known_plan_ids={"CP-OLD-BATCH-S-abcdef123456"},
        )

    bad_scope = [
        (
            collection,
            object_id,
            {**payload, "corpus_scope": "RB-OTHER"}
            if collection == "editorial_syntheses"
            else payload,
        )
        for collection, object_id, payload in rows
    ]
    with pytest.raises(ValueError, match="mismatched batch ownership"):
        stale_candidate_projection_retirement(
            batch_ids=["RB-OLD-BATCH"],
            change_set=_retiring_claim_plan(),
            all_live_rows=bad_scope,
            known_plan_ids={"CP-OLD-BATCH-S-abcdef123456"},
        )

    approved_plan = [
        (
            collection,
            object_id,
            {**payload, "review_status": "approved"}
            if collection == "composition_plans"
            else payload,
        )
        for collection, object_id, payload in rows
    ]
    with pytest.raises(ValueError, match="current plan authority"):
        stale_candidate_projection_retirement(
            batch_ids=["RB-OLD-BATCH"],
            change_set=_retiring_claim_plan(),
            all_live_rows=approved_plan,
            known_plan_ids={"CP-OLD-BATCH-S-abcdef123456"},
        )

    external = [*rows, ("product_dependencies", "PD-1", {"ids": ["KR-STALE"]})]
    with pytest.raises(ValueError, match="external references"):
        stale_candidate_projection_retirement(
            batch_ids=["RB-OLD-BATCH"],
            change_set=_retiring_claim_plan(),
            all_live_rows=external,
            known_plan_ids={"CP-OLD-BATCH-S-abcdef123456"},
        )


def test_stale_projection_audit_is_bound_to_change_set_snapshot() -> None:
    rows = _stale_projection_rows()
    keys, audit = stale_candidate_projection_retirement(
        batch_ids=["RB-OLD-BATCH"],
        change_set=_retiring_claim_plan(),
        all_live_rows=rows,
        known_plan_ids={"CP-OLD-BATCH-S-abcdef123456"},
    )
    payloads = {(collection, object_id): payload for collection, object_id, payload in rows}
    operations = [
        SimpleNamespace(
            collection="claims",
            object_id="CL-OLD",
            operation="retire",
            before_revision=1,
            before_sha256="claim-sha",
        ),
        *[
            SimpleNamespace(
                collection=collection,
                object_id=object_id,
                operation="retire",
                before_revision=payloads[(collection, object_id)]["revision"],
                before_sha256=record_content_sha(payloads[(collection, object_id)]),
            )
            for collection, object_id in keys
        ],
    ]
    plan = SimpleNamespace(operations=tuple(operations))
    validate_stale_candidate_projection_retirement_plan(plan, audit)
    operations[-1] = SimpleNamespace(
        **{**vars(operations[-1]), "before_revision": 99}
    )
    with pytest.raises(ValueError, match="revision drifted"):
        validate_stale_candidate_projection_retirement_plan(
            SimpleNamespace(operations=tuple(operations)), audit
        )


def _stale_constraint() -> dict:
    return {
        "constraint_id": "CRC-XSR-0123456789abcdef",
        "source_id": "CL-OLD",
        "target_id": "CL-OTHER",
        "reason": "the reviewed comparison answered different questions",
        "review_artifact_id": "XSR-0123456789abcdef",
        "review_status": "ai_consensus",
        "visibility": "internal",
        "revision": 4,
    }


def test_stale_cross_sermon_constraint_requires_exact_ai_authority() -> None:
    constraint = _stale_constraint()
    rows = [("claim_relation_constraints", constraint["constraint_id"], constraint)]
    keys, audit = stale_ai_cross_sermon_constraint_retirement(
        constraint_ids=[constraint["constraint_id"]],
        change_set=_retiring_claim_plan(),
        all_live_rows=rows,
        known_constraint_ids={constraint["constraint_id"]},
    )

    assert keys == [("claim_relation_constraints", constraint["constraint_id"])]
    assert audit["records"][0]["source_id"] == "CL-OLD"
    assert audit["records"][0]["target_id"] == "CL-OTHER"
    assert audit["records"][0]["reason"] == constraint["reason"]
    assert audit["records"][0]["stale_claim_ids"] == ["CL-OLD"]

    for replacement, match in [
        ({"review_status": "approved"}, "eligible AI judgment"),
        ({"visibility": "public"}, "eligible AI judgment"),
        ({"source_id": "CL-KEPT"}, "eligible AI judgment"),
        ({"review_artifact_id": "XSR-wrong"}, "eligible AI judgment"),
    ]:
        with pytest.raises(ValueError, match=match):
            stale_ai_cross_sermon_constraint_retirement(
                constraint_ids=[constraint["constraint_id"]],
                change_set=_retiring_claim_plan(),
                all_live_rows=[(
                    "claim_relation_constraints",
                    constraint["constraint_id"],
                    {**constraint, **replacement},
                )],
                known_constraint_ids={constraint["constraint_id"]},
            )


def test_stale_cross_sermon_constraint_rejects_unknown_and_external_refs() -> None:
    constraint = _stale_constraint()
    with pytest.raises(ValueError, match="unknown stale cross-sermon"):
        stale_ai_cross_sermon_constraint_retirement(
            constraint_ids=["CRC-XSR-fedcba9876543210"],
            change_set=_retiring_claim_plan(),
            all_live_rows=[],
            known_constraint_ids={constraint["constraint_id"]},
        )
    with pytest.raises(ValueError, match="external references"):
        stale_ai_cross_sermon_constraint_retirement(
            constraint_ids=[constraint["constraint_id"]],
            change_set=_retiring_claim_plan(),
            all_live_rows=[
                ("claim_relation_constraints", constraint["constraint_id"], constraint),
                (
                    "product_dependencies",
                    "PD-1",
                    {"ids": [constraint["constraint_id"]]},
                ),
            ],
            known_constraint_ids={constraint["constraint_id"]},
        )


def test_stale_cross_sermon_constraint_audit_is_snapshot_bound() -> None:
    constraint = _stale_constraint()
    rows = [("claim_relation_constraints", constraint["constraint_id"], constraint)]
    _keys, audit = stale_ai_cross_sermon_constraint_retirement(
        constraint_ids=[constraint["constraint_id"]],
        change_set=_retiring_claim_plan(),
        all_live_rows=rows,
        known_constraint_ids={constraint["constraint_id"]},
    )
    plan = SimpleNamespace(operations=(
        SimpleNamespace(
            collection="claims", object_id="CL-OLD", operation="retire"
        ),
        SimpleNamespace(
            collection="claim_relation_constraints",
            object_id=constraint["constraint_id"],
            operation="retire",
            before_revision=4,
            before_sha256=record_content_sha(constraint),
        ),
    ))
    validate_stale_ai_cross_sermon_constraint_retirement_plan(plan, audit)
    drifted = SimpleNamespace(operations=(
        plan.operations[0],
        SimpleNamespace(**{**vars(plan.operations[1]), "before_sha256": "changed"}),
    ))
    with pytest.raises(ValueError, match="content drifted"):
        validate_stale_ai_cross_sermon_constraint_retirement_plan(drifted, audit)


def test_supersede_plan_merges_explicit_obsolete_candidate_retirements() -> None:
    from backend.pipeline.extraction_supersede_runner import plan

    package = _package()
    candidate_live = _obsolete_candidate_live()
    all_rows = _live_rows(candidate_live)
    obsolete_rows = [
        row for row in all_rows
        if row[0] != "topic_identity_reconciliations"
    ]

    class Cursor:
        def __init__(self) -> None:
            self.collection = ""
            self.mode = "collection"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            if "SELECT object_id FROM" in sql:
                self.mode = "known_plans"
            elif "SELECT collection, object_id, revision" in sql and params is None:
                self.mode = "all_live"
            else:
                self.mode = "collection"
                self.collection = str(params[0])

        def fetchall(self):
            if self.mode == "known_plans":
                return [(object_id,) for object_id in candidate_live["composition_plans"]]
            if self.mode == "all_live":
                return [
                    (
                        collection,
                        object_id,
                        payload["revision"],
                        record_content_sha(payload),
                        payload,
                    )
                    for collection, object_id, payload in all_rows
                ]
            return list((candidate_live.get(self.collection) or {}).items())

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    class Store:
        def connect(self):
            return Connection()

        def plan_package(self, _incoming, *, source_kind, retiring_keys=()):
            assert source_kind == "knowledge_package"
            assert set(retiring_keys) == {
                (collection, object_id)
                for collection, rows in candidate_live.items()
                if collection != "topic_identity_reconciliations"
                for object_id in rows
            }
            operations = tuple(
                SimpleNamespace(
                    collection=collection,
                    object_id=object_id,
                    operation="retire",
                    before_revision=payload["revision"],
                    before_sha256=record_content_sha(payload),
                )
                for collection, object_id, payload in obsolete_rows
            )
            return SimpleNamespace(operations=operations)

    (
        change_set,
        withdrawal,
        products,
        blockers,
        audit,
        stale_audit,
        stale_projection_audit,
        stale_constraint_audit,
    ) = plan(
        Store(),
        package,
        source_kind="knowledge_package",
        retire_obsolete_candidate_batch="RB-TEST-OBSOLETE",
    )

    assert len(change_set.operations) == 4
    assert withdrawal.closure() == []
    assert products == []
    assert blockers == []
    assert audit["summary"]["total"] == 4
    assert stale_audit is None
    assert stale_projection_audit is None
    assert stale_constraint_audit is None


def test_only_current_product_dependencies_are_reported() -> None:
    products = products_to_rebuild(
        {("claims", "CL-old")}, dependencies=DEPENDENCIES
    )
    assert products == [{
        "consumer_kind": "matthew_draft",
        "consumer_id": "DRAFT-matthew-16-13-20",
        "affected_dependency_ids": ["PD-draft"],
        "changed_records": [{"collection": "claims", "record_id": "CL-old"}],
    }]


def test_repeated_supersede_with_no_operations_performs_no_database_write() -> None:
    from backend.api.canonical_repository.postgres_store import build_change_set_plan

    package = _package()
    current = {}
    for collection, rows in normalize_package(package).items():
        for object_id, payload in rows.items():
            stored = {**payload, "revision": 1}
            current[(collection, object_id)] = {
                "revision": 1,
                "content_sha256": record_content_sha(stored),
                "payload": stored,
            }
    repeated = build_change_set_plan(package, current)

    assert repeated.operations == ()
    assert no_op_result(repeated)["status"] == "unchanged"
    assert no_op_result(repeated)["change_set_id"] is None


def test_full_supersede_replan_after_apply_has_zero_operations() -> None:
    from backend.api.canonical_repository.postgres_store import build_change_set_plan
    from backend.pipeline.extraction_supersede_runner import plan

    package = _package()
    normalized = normalize_package(package)
    existing = {}
    live: dict[str, dict[str, dict]] = {}
    for collection, rows in normalized.items():
        live[collection] = {}
        for object_id, payload in rows.items():
            stored = {**payload, "revision": 1}
            live[collection][object_id] = stored
            existing[(collection, object_id)] = {
                "revision": 1,
                "content_sha256": record_content_sha(stored),
                "payload": stored,
            }

    class Cursor:
        def __init__(self) -> None:
            self.collection = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params=None):
            if params is None:
                self.collection = "__all__"
            else:
                self.collection = str(params[0])

        def fetchall(self):
            if self.collection == "__all__":
                return [
                    (
                        collection,
                        object_id,
                        payload["revision"],
                        record_content_sha(payload),
                        payload,
                    )
                    for collection, rows in live.items()
                    for object_id, payload in rows.items()
                ]
            return list((live.get(self.collection) or {}).items())

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def cursor(self):
            return Cursor()

    class Store:
        def connect(self):
            return Connection()

        def plan_package(self, incoming, *, source_kind, retiring_keys=()):
            assert retiring_keys == []
            return build_change_set_plan(incoming, existing, source_kind=source_kind)

    (
        change_set,
        withdrawal,
        products,
            semantic_blockers,
            obsolete_retirement,
            stale_topic_identity_retirement,
            stale_projection_retirement,
            stale_constraint_retirement,
        ) = plan(
        Store(), package, source_kind="knowledge_package"
    )
    assert withdrawal.closure() == []
    assert products == []
    assert semantic_blockers == []
    assert obsolete_retirement is None
    assert stale_topic_identity_retirement is None
    assert stale_projection_retirement is None
    assert stale_constraint_retirement is None
    assert change_set.operations == ()
    assert no_op_result(change_set)["status"] == "unchanged"


def test_dependency_manifest_reports_non_claim_changes() -> None:
    products = products_to_rebuild(
        {("source_fragments", "SF-old")}, dependencies=DEPENDENCIES
    )
    assert products[0]["consumer_kind"] == "evidence_qa"
    assert products[0]["affected_dependency_ids"] == ["PD-qa"]
    assert products[0]["changed_records"] == [
        {"collection": "source_fragments", "record_id": "SF-old"}
    ]


def test_updated_records_are_part_of_the_product_impact_preview() -> None:
    change_set = SimpleNamespace(operations=[
        SimpleNamespace(operation="create", collection="claims", object_id="CL-new"),
        SimpleNamespace(operation="update", collection="claims", object_id="CL-old"),
        SimpleNamespace(operation="retire", collection="source_fragments", object_id="SF-old"),
    ])
    assert product_impact_keys(change_set) == {
        ("claims", "CL-old"),
        ("source_fragments", "SF-old"),
    }


def test_an_id_appearing_only_in_prose_is_not_a_dependency() -> None:
    """Traced through the citation fields, not by searching the payload text."""

    products = products_to_rebuild(
        {("claims", "CL-old")},
        dependencies={
            "PD-prose": {
                "consumer_kind": "matthew_draft",
                "consumer_id": "DRAFT-1",
                "claim_id": "CL-kept",
                "notes": "參見 CL-old 的討論",
            }
        },
    )
    assert products == []


# ---------------------------------------------------------------------------
# putting back what should not have gone
# ---------------------------------------------------------------------------

RETIRED = {
    ("questions", "Q-1"): {"revision": 5, "content_sha256": "sha-q", "payload": {"question_id": "Q-1"}},
}


def test_a_revival_leaves_the_record_saying_what_it_said() -> None:
    """Retirement is a judgement and judgements are sometimes wrong. What
    changes on the way back is the store's assertion, not the record."""

    from backend.api.canonical_repository.postgres_store import build_revival_plan

    plan = build_revival_plan(
        [("questions", "Q-1")], RETIRED, reason="錯判", package_id="REVIVE-TEST",
    )
    operation = plan.operations[0]
    assert operation.operation == "revive"
    assert operation.payload == {"question_id": "Q-1"}
    assert operation.before_sha256 == operation.after_sha256 == "sha-q"
    assert (operation.before_revision, operation.after_revision) == (5, 6)
    assert plan.as_dict()["summary"] == {
        "created": 0, "updated": 0, "retired": 0, "revived": 1,
        "unchanged": 0, "operations": 1,
        "fields_removed": 0, "removals": [],
    }


def test_reviving_something_that_is_not_retired_is_not_an_error() -> None:
    from backend.api.canonical_repository.postgres_store import build_revival_plan

    plan = build_revival_plan(
        [("questions", "Q-1"), ("questions", "Q-live")], RETIRED,
        reason="錯判", package_id="REVIVE-TEST",
    )
    assert [item.object_id for item in plan.operations] == ["Q-1"]
    assert plan.unchanged == 1
