"""Re-extracting a source that was already sectioned, twice.

The module's founding measurement — "0 of 185 and 0 of 317 incoming fragment
ids already existed" — was taken on a *first* sectioned re-extraction, where
the predecessor was a whole-document package. Its ids were `CL007` and the new
ones `P01-CL007`, so the two generations could not collide.

They collide from the second sectioned re-extraction onward: same sections,
same numbering. The first source to reach that point shared 82 of its 93
evidence-step ids with its predecessor, and the ingest aborted with
`ChangeSetConflict` on `DK-3d012c24a542-P01-E012` — a record the arrival wrote
and the retirement then expected to find unchanged.
"""

from __future__ import annotations

from backend.pipeline.extraction_supersede import arriving_keys, superseded
from backend.pipeline.relation_id_namespace import source_namespace
from backend.pipeline.extraction_supersede_runner import (
    plan,
    transcript_predecessor_namespaces,
)


def _package(fragment_ids, evidence_ids, claim_ids=("CL001",)):
    return {
        "source_documents": [{"source_id": "SRC-A"}],
        "source_fragments": [{"fragment_id": f, "source_id": "SRC-A"} for f in fragment_ids],
        "evidence_steps": [
            {"evidence_step_id": e, "source_fragment_ids": [fragment_ids[0]]}
            for e in evidence_ids
        ],
        "claims": [{"claim_id": c, "evidence_step_ids": list(evidence_ids)} for c in claim_ids],
    }


def _live(package):
    """The store, holding exactly what this package would put there."""

    fragments = {
        row["fragment_id"]: {"source_id": "SRC-A"} for row in package["source_fragments"]
    }
    owners = {
        "evidence_steps": {
            row["evidence_step_id"]: {"source_fragment_ids": row["source_fragment_ids"]}
            for row in package["evidence_steps"]
        }
    }
    claims = {
        row["claim_id"]: {"evidence_step_ids": row["evidence_step_ids"]}
        for row in package["claims"]
    }
    return fragments, owners, claims


def test_legacy_predecessor_namespace_uses_the_old_compilers_actual_source_key() -> None:
    transcript_id = "2017 NYSC 專題：馬太福音釋經（六）3"
    source_id = "SRC-2017_NYSC_3-acde12345678"
    incoming = {
        "source_documents": [{
            "source_id": source_id,
            "source_type": "sermon_transcript",
            "transcript_id": transcript_id,
        }]
    }
    live = {
        source_id: {
            "source_id": source_id,
            "source_type": "sermon_transcript",
            "transcript_id": transcript_id,
        }
    }

    namespaces = transcript_predecessor_namespaces(incoming, live)

    assert source_namespace(transcript_id) in namespaces
    assert source_namespace(source_id) in namespaces


def test_legacy_transcript_namespace_fails_closed_when_source_types_share_the_name() -> None:
    transcript_id = "shared-name"
    incoming = {
        "source_documents": [{
            "source_id": "SRC-SERMON",
            "source_type": "sermon_transcript",
            "transcript_id": transcript_id,
        }]
    }
    live = {
        "SRC-SERMON": {
            "source_id": "SRC-SERMON",
            "source_type": "sermon_transcript",
            "transcript_id": transcript_id,
        },
        "SRC-NOTES": {
            "source_id": "SRC-NOTES",
            "source_type": "notes_manuscript",
            "transcript_id": transcript_id,
        },
    }

    import pytest

    with pytest.raises(ValueError, match="shared by multiple source types"):
        transcript_predecessor_namespaces(incoming, live)


def test_a_record_the_new_package_carries_is_never_also_retired() -> None:
    previous = _package(["FR-1", "FR-2"], ["P01-E012", "P01-E013"])
    fragments, owners, claims = _live(previous)

    # The second sectioned extraction: new fragment ids, but the same evidence
    # step ids, which is what the section numbering guarantees.
    incoming = _package(["FR-9", "FR-8"], ["P01-E012", "P01-E013"])

    withdrawal = superseded(
        incoming, live_fragments=fragments, owners=owners, claims=claims, relations={}
    )

    retired = set(withdrawal.closure())
    written = arriving_keys(incoming)
    assert not (retired & written), f"arrives and is withdrawn in one change set: {retired & written}"

    # The predecessor's fragments still go: the new package does not carry them.
    assert ("source_fragments", "FR-1") in retired
    assert ("source_fragments", "FR-2") in retired


def test_a_record_the_new_package_drops_is_still_retired() -> None:
    """The exclusion must not become an excuse to leave dead records live."""

    previous = _package(["FR-1"], ["P01-E012", "P01-E099"])
    fragments, owners, claims = _live(previous)

    # `P01-E099` is gone from this generation, and nothing anchors it any more.
    incoming = _package(["FR-9"], ["P01-E012"])

    withdrawal = superseded(
        incoming, live_fragments=fragments, owners=owners, claims=claims, relations={}
    )
    retired = set(withdrawal.closure())

    assert ("evidence_steps", "P01-E099") in retired
    assert ("evidence_steps", "P01-E012") not in retired


def test_the_first_sectioned_re_extraction_still_behaves_as_it_did() -> None:
    """Whole-document predecessor, sectioned arrival: no shared ids, nothing changes."""

    previous = _package(["FR-1"], ["E012"])
    fragments, owners, claims = _live(previous)
    incoming = _package(["FR-9"], ["P01-E012"])

    retired = set(
        superseded(
            incoming, live_fragments=fragments, owners=owners, claims=claims, relations={}
        ).closure()
    )
    assert ("evidence_steps", "E012") in retired


def test_a_source_local_relation_omitted_by_the_new_package_is_retired() -> None:
    namespace = source_namespace("SRC-A")
    incoming = _package(["FR-1"], [f"{namespace}-E1", f"{namespace}-E2"])
    incoming["knowledge_relations"] = [{
        "relation_id": f"{namespace}-XER001",
        "from_id": f"{namespace}-E1",
        "to_id": f"{namespace}-E2",
    }]
    relations = {
        "knowledge_relations": {
            f"{namespace}-ER099": {
                "relation_id": f"{namespace}-ER099",
                "from_id": f"{namespace}-E1",
                "to_id": f"{namespace}-E2",
            },
        },
    }

    withdrawal = superseded(
        incoming,
        live_fragments={"FR-1": {"source_id": "SRC-A"}},
        owners={},
        claims={},
        relations=relations,
    )

    assert withdrawal.superseded_relations == [
        ("knowledge_relations", f"{namespace}-ER099")
    ]
    assert ("knowledge_relations", f"{namespace}-ER099") in withdrawal.closure()
    assert withdrawal.as_dict()["relations_superseded"] == 1


def test_same_source_curated_relation_is_not_inferred_to_belong_to_extraction() -> None:
    namespace = source_namespace("SRC-A")
    incoming = _package(["FR-1"], [f"{namespace}-E1", f"{namespace}-E2"])
    relations = {
        "knowledge_relations": {
            f"{namespace}-CURATED-1": {
                "relation_id": f"{namespace}-CURATED-1",
                "from_id": f"{namespace}-E1",
                "to_id": f"{namespace}-E2",
            },
            "XER001": {
                "relation_id": "XER001",
                "from_id": f"{namespace}-E1",
                "to_id": f"{namespace}-E2",
            },
        }
    }

    withdrawal = superseded(
        incoming,
        live_fragments={"FR-1": {"source_id": "SRC-A"}},
        owners={},
        claims={},
        relations=relations,
    )

    assert withdrawal.superseded_relations == []


def test_same_source_curated_claim_is_not_inferred_to_belong_to_extraction() -> None:
    namespace = source_namespace("SRC-A")
    incoming = _package(["FR-1"], [f"{namespace}-E1"])
    withdrawal = superseded(
        incoming,
        live_fragments={"FR-1": {"source_id": "SRC-A"}},
        owners={},
        claims={
            f"{namespace}-MERGED-001": {
                "claim_id": f"{namespace}-MERGED-001",
                "evidence_step_ids": [],
            }
        },
        relations={},
    )

    assert ("claims", f"{namespace}-MERGED-001") not in withdrawal.closure()


def test_supersede_refuses_an_invalid_graph_before_connecting_to_the_store() -> None:
    import pytest

    package = {
        "source_documents": [{"source_id": "SRC-A"}],
        "source_fragments": [{"fragment_id": "FR-1", "source_id": "SRC-A"}],
        "evidence_steps": [{
            "evidence_step_id": "E-1",
            "source_fragment_ids": ["FR-1"],
            "produced_claim_ids": ["CL-1"],
        }],
        "claims": [{"claim_id": "CL-1", "evidence_step_ids": []}],
    }

    class Store:
        def connect(self):
            raise AssertionError("invalid packages must fail before any DB access")

    with pytest.raises(ValueError, match="supersede package violates graph integrity"):
        plan(Store(), package, source_kind="knowledge_package")


def test_an_omitted_cross_source_relation_is_not_retired() -> None:
    incoming = _package(["FR-1"], ["DK-A-E1"])
    relations = {
        "knowledge_relations": {
            "CROSS-1": {
                "relation_id": "CROSS-1",
                "from_id": "DK-A-E1",
                "to_id": "DK-B-E1",
            },
        },
    }

    withdrawal = superseded(
        incoming,
        live_fragments={"FR-1": {"source_id": "SRC-A"}},
        owners={},
        claims={},
        relations=relations,
    )

    assert withdrawal.superseded_relations == []


def test_predecessor_namespace_retires_unanchored_records_and_old_cross_edges() -> None:
    """Fragment closure is a proof, not the only way to find a generation."""

    old = "DK-111111111111"
    new = "DK-222222222222"
    incoming = _package(
        [f"FR-SRC-A-{new}-E1-01"], [f"{new}-E1"], [f"{new}-CL1"]
    )
    incoming["extraction"] = {"record_namespace": new}
    owners = {
        "questions": {
            f"{old}-Q1": {
                "question_id": f"{old}-Q1",
                "source_fragment_ids": [],
            }
        }
    }
    relations = {
        "knowledge_relations": {
            "DK-333333333333-XER001": {
                "relation_id": "DK-333333333333-XER001",
                "record_namespace": "DK-333333333333",
                "parent_extraction_record_namespace": old,
                "from_id": f"{old}-E1",
                "to_id": f"{old}-E2",
            }
        }
    }

    withdrawal = superseded(
        incoming,
        live_fragments={},
        owners=owners,
        claims={},
        relations=relations,
        predecessor_namespaces={old},
    )

    assert ("questions", f"{old}-Q1") in withdrawal.closure()
    assert (
        "knowledge_relations",
        "DK-333333333333-XER001",
    ) in withdrawal.closure()


def test_predecessor_namespace_retires_observed_letter_suffixed_legacy_ids() -> None:
    old = "DK-a26f0a7e9ba4"
    new = "DK-222222222222"
    incoming = _package(
        [f"FR-SRC-A-{new}-E1-01"], [f"{new}-E1"], [f"{new}-CL1"]
    )
    incoming["extraction"] = {"record_namespace": new}
    owners = {
        "evidence_steps": {
            f"{old}-E020H": {
                "evidence_step_id": f"{old}-E020H",
                "source_fragment_ids": [],
            }
        }
    }
    claims = {
        f"{old}-CL020H": {
            "claim_id": f"{old}-CL020H",
            "evidence_step_ids": [f"{old}-E020H"],
        }
    }

    retired = set(
        superseded(
            incoming,
            live_fragments={},
            owners=owners,
            claims=claims,
            relations={},
            predecessor_namespaces={old},
        ).closure()
    )

    assert ("evidence_steps", f"{old}-E020H") in retired
    assert ("claims", f"{old}-CL020H") in retired


def test_exact_extraction_replay_does_not_retire_its_cross_section_child() -> None:
    generation = "DK-111111111111"
    child = "DK-333333333333"
    incoming = _package(
        [f"FR-SRC-A-{generation}-E1-01"],
        [f"{generation}-E1"],
        [f"{generation}-CL1"],
    )
    incoming["extraction"] = {"record_namespace": generation}
    relations = {
        "knowledge_relations": {
            f"{child}-XER001": {
                "relation_id": f"{child}-XER001",
                "record_namespace": child,
                "parent_extraction_record_namespace": generation,
                "from_id": f"{generation}-E1",
                "to_id": f"{generation}-E2",
            }
        }
    }

    withdrawal = superseded(
        incoming,
        live_fragments={},
        owners={},
        claims={},
        relations=relations,
        predecessor_namespaces={generation},
    )

    assert ("knowledge_relations", f"{child}-XER001") not in withdrawal.closure()


def test_exact_extraction_replay_preserves_a_legacy_parent_namespaced_cross_edge() -> None:
    generation = "DK-111111111111"
    incoming = _package(
        [f"FR-SRC-A-{generation}-E1-01"],
        [f"{generation}-E1"],
        [f"{generation}-CL1"],
    )
    incoming["extraction"] = {"record_namespace": generation}
    legacy_edge = f"{generation}-XER001"
    relations = {
        "knowledge_relations": {
            legacy_edge: {
                "relation_id": legacy_edge,
                "from_id": f"{generation}-E1",
                "to_id": f"{generation}-E2",
                "discovered_by": "wang_cross_section_relation_v2",
            }
        }
    }

    withdrawal = superseded(
        incoming,
        live_fragments={},
        owners={},
        claims={},
        relations=relations,
        predecessor_namespaces={generation},
    )

    assert ("knowledge_relations", legacy_edge) not in withdrawal.closure()


def test_new_cross_section_child_replaces_a_legacy_parent_namespaced_cross_edge() -> None:
    generation = "DK-111111111111"
    child = "DK-222222222222"
    incoming = _package(
        [f"FR-SRC-A-{generation}-E1-01"],
        [f"{generation}-E1"],
        [f"{generation}-CL1"],
    )
    incoming["extraction"] = {"record_namespace": generation}
    incoming["cross_section_relations"] = {
        "record_namespace": child,
        "parent_extraction_record_namespace": generation,
    }
    legacy_edge = f"{generation}-XER001"

    withdrawal = superseded(
        incoming,
        live_fragments={},
        owners={},
        claims={},
        relations={
            "knowledge_relations": {
                legacy_edge: {
                    "relation_id": legacy_edge,
                    "from_id": f"{generation}-E1",
                    "to_id": f"{generation}-E2",
                    "discovered_by": "wang_cross_section_relation_v2",
                }
            }
        },
        predecessor_namespaces={generation},
    )

    assert ("knowledge_relations", legacy_edge) in withdrawal.closure()


def test_new_cross_section_child_supersedes_only_the_previous_child() -> None:
    generation = "DK-111111111111"
    old_child = "DK-333333333333"
    new_child = "DK-444444444444"
    incoming = _package(
        [f"FR-SRC-A-{generation}-E1-01"],
        [f"{generation}-E1"],
        [f"{generation}-CL1"],
    )
    incoming["extraction"] = {"record_namespace": generation}
    incoming["cross_section_relations"] = {"record_namespace": new_child}
    incoming["knowledge_relations"] = [{
        "relation_id": f"{new_child}-XER001",
        "record_namespace": new_child,
        "parent_extraction_record_namespace": generation,
        "from_id": f"{generation}-E1",
        "to_id": f"{generation}-E2",
    }]
    relations = {
        "knowledge_relations": {
            f"{old_child}-XER001": {
                "relation_id": f"{old_child}-XER001",
                "record_namespace": old_child,
                "parent_extraction_record_namespace": generation,
                "from_id": f"{generation}-E1",
                "to_id": f"{generation}-E2",
            },
            f"{new_child}-XER001": incoming["knowledge_relations"][0],
        }
    }

    withdrawal = superseded(
        incoming,
        live_fragments={},
        owners={},
        claims={},
        relations=relations,
        predecessor_namespaces={generation},
    )

    assert ("knowledge_relations", f"{old_child}-XER001") in withdrawal.closure()
    assert ("knowledge_relations", f"{new_child}-XER001") not in withdrawal.closure()


def test_cross_section_parent_declaration_is_enough_to_replace_an_old_child() -> None:
    generation = "DK-111111111111"
    old_child = "DK-333333333333"
    new_child = "DK-444444444444"
    incoming = _package(["FR-1"], [f"{generation}-E1"], [f"{generation}-CL1"])
    incoming["cross_section_relations"] = {
        "record_namespace": new_child,
        "parent_extraction_record_namespace": generation,
    }
    relations = {
        "knowledge_relations": {
            f"{old_child}-XER001": {
                "relation_id": f"{old_child}-XER001",
                "record_namespace": old_child,
                "parent_extraction_record_namespace": generation,
                "from_id": f"{generation}-E1",
                "to_id": f"{generation}-E2",
            }
        }
    }

    withdrawal = superseded(
        incoming,
        live_fragments={},
        owners={},
        claims={},
        relations=relations,
    )

    assert ("knowledge_relations", f"{old_child}-XER001") in withdrawal.closure()


def test_generation_and_dependency_closure_retire_a_record_only_once() -> None:
    old = "DK-111111111111"
    incoming = _package(["FR-NEW"], ["DK-NEW-E1"])
    withdrawal = superseded(
        incoming,
        live_fragments={"FR-OLD": {"source_id": "SRC-A"}},
        owners={
            "evidence_steps": {
                f"{old}-E1": {"source_fragment_ids": ["FR-OLD"]}
            }
        },
        claims={},
        relations={},
        predecessor_namespaces={old},
    )

    assert withdrawal.closure().count(("evidence_steps", f"{old}-E1")) == 1


def test_every_stage_names_a_sermon_the_way_extraction_does() -> None:
    """One source, one row -- whichever stage is filing.

    Extraction files under the transcript id. The stages after it read
    `source_documents` and filed under `source_id`, which for a sermon is
    `SRC-2016_NYSC_3-3d012c24a542` against extraction's
    `2016 NYSC 專題：馬太福音釋經（四）3`. Five stages landed on a row nothing
    else used, so a sermon whose whole chain had just succeeded showed
    cross_section 未跑 and everything after it 舊.

    A 母本 hides it: both fields hold the same string there, which is why the
    first two sources through the chain looked right.
    """

    from backend.pipeline.source_keys import package_row_key

    sermon = {
        "source_documents": [{
            "source_id": "SRC-2016_NYSC_3-3d012c24a542",
            "source_type": "sermon_transcript",
            "transcript_id": "2016 NYSC 專題：馬太福音釋經（四）3",
        }]
    }
    assert package_row_key(sermon) == "2016 NYSC 專題：馬太福音釋經（四）3"

    notes = {
        "source_documents": [{
            "source_id": "notes_manuscript:16_章_-_生命",
            "source_type": "notes_manuscript",
            "transcript_id": "notes_manuscript:16_章_-_生命",
            "project_id": "16_章_-_生命",
        }]
    }
    assert package_row_key(notes) == "16_章_-_生命"

    # A merged package covers several sources and has no single subject.
    assert package_row_key({"source_documents": [{"source_id": "A"}, {"source_id": "B"}]}) == ""
