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
    product_impact_keys,
    products_to_rebuild,
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

        def execute(self, _sql, params):
            self.collection = str(params[0])

        def fetchall(self):
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

    change_set, withdrawal, products = plan(
        Store(), package, source_kind="knowledge_package"
    )
    assert withdrawal.closure() == []
    assert products == []
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
