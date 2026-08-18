from __future__ import annotations

import pytest

from backend.pipeline.observation_argument_linking import (
    LinkingError,
    LinkingPlan,
    Link,
    Step,
    build_linking_package,
    load_plan,
)

EXCERPT = "陰間的門，其實意思是說陰間的門，就是陰間的權柄，不能夠勝過這個教會。"
SEGMENT = "耶穌對彼得說：我要把我的教會建造在這磐石上。" + EXCERPT + "很重要。"


class _FakeStore:
    def __init__(self, records):
        self._records = records

    def get_record(self, collection, object_id):
        return self._records.get(collection, {}).get(object_id)


def _store(**overrides):
    records = {
        "observations": {
            "OBS-1": {
                "observation_id": "OBS-1",
                "statement": "原來那個字是『陰間的門』。",
                "scripture_refs": ["太16:18"],
            }
        },
        "evidence_steps": {"E-1": {"evidence_step_id": "E-1"}},
        "claims": {},
        "source_fragments": {},
        "knowledge_relations": {},
    }
    for collection, updates in overrides.items():
        records.setdefault(collection, {}).update(updates)
    return _FakeStore(records)


def _transcripts():
    return {"SRC-1": {"script": [{"text": "無關。"}, {"text": SEGMENT}]}}


def _link(**overrides):
    values = dict(
        observation_id="OBS-1", evidence_step_id="E-1",
        relation_id="KR-1", reason="這條觀察是該步驟的根據。",
    )
    values.update(overrides)
    return Link(**values)


def _step(**overrides):
    values = dict(
        observation_id="OBS-1", source_id="SRC-1", segment_index="S0002",
        excerpt=EXCERPT,
        statement="教授說明「陰間的門」即陰間的權柄，不能勝過教會。",
        claim_statement="太16:18的「陰間的門」意指陰間的權柄，不能勝過基督的教會。",
        claim_type="interpretive_judgment",
        fragment_id="FR-NEW", evidence_step_id="E-NEW", claim_id="CL-NEW",
        relation_id="KR-NEW", reason="該推論從未被抽取。",
    )
    values.update(overrides)
    return Step(**values)


def test_a_link_records_the_edge_and_asserts_nothing_new():
    package = build_linking_package(
        LinkingPlan(links=[_link()]), store=_store(), transcripts={}
    )
    assert package["claims"] == []
    assert package["evidence_steps"] == []
    relation = package["knowledge_relations"][0]
    assert (relation["from_id"], relation["to_id"]) == ("OBS-1", "E-1")
    assert relation["relation_type"] == "supports"


def test_a_link_to_a_missing_evidence_step_is_refused():
    with pytest.raises(LinkingError, match="evidence step not found"):
        build_linking_package(
            LinkingPlan(links=[_link(evidence_step_id="E-9")]), store=_store(), transcripts={}
        )


def test_a_link_from_a_missing_observation_is_refused():
    with pytest.raises(LinkingError, match="observation not found"):
        build_linking_package(
            LinkingPlan(links=[_link(observation_id="OBS-9")]), store=_store(), transcripts={}
        )


def test_an_existing_relation_id_is_never_overwritten():
    store = _store(knowledge_relations={"KR-1": {"relation_id": "KR-1"}})
    with pytest.raises(LinkingError, match="refusing to overwrite"):
        build_linking_package(LinkingPlan(links=[_link()]), store=store, transcripts={})


def test_a_link_without_a_reason_is_refused():
    """The reason is the record of why this observation belongs to that step."""
    with pytest.raises(LinkingError, match="reason is required"):
        build_linking_package(
            LinkingPlan(links=[_link(reason="  ")]), store=_store(), transcripts={}
        )


def test_a_step_records_the_inference_quoted_from_the_transcript():
    package = build_linking_package(
        LinkingPlan(steps=[_step()]), store=_store(), transcripts=_transcripts()
    )
    fragment = package["source_fragments"][0]
    assert fragment["verbatim_excerpt"] == EXCERPT
    assert fragment["paragraph_key"] == "S0002"

    step = package["evidence_steps"][0]
    assert step["produced_claim_ids"] == ["CL-NEW"]
    assert step["source_fragment_id"] == "FR-NEW"
    assert step["discourse_role"] == "inference_recorded_for_observation:OBS-1"

    claim = package["claims"][0]
    assert claim["evidence_step_ids"] == ["E-NEW"]
    assert claim["scripture_refs"] == ["太16:18"]
    assert package["knowledge_relations"][0]["to_id"] == "E-NEW"


def test_a_claim_written_this_way_is_always_candidate():
    """Promoting the professor's exegesis is a human decision, not this tool's."""
    package = build_linking_package(
        LinkingPlan(steps=[_step()]), store=_store(), transcripts=_transcripts()
    )
    assert package["claims"][0]["maturity"] == "candidate"
    assert package["claims"][0]["review_status"] == "candidate"


def test_an_excerpt_that_is_not_verbatim_is_refused():
    """The conclusion may be recorded, never composed."""
    with pytest.raises(LinkingError, match="not verbatim"):
        build_linking_package(
            LinkingPlan(steps=[_step(excerpt="陰間的門就是撒但的權勢，教會必然得勝。")]),
            store=_store(), transcripts=_transcripts(),
        )


def test_an_excerpt_verbatim_in_a_different_segment_is_still_refused():
    """Anchoring has to name the segment the words are actually in."""
    with pytest.raises(LinkingError, match="not verbatim"):
        build_linking_package(
            LinkingPlan(steps=[_step(segment_index="S0001")]),
            store=_store(), transcripts=_transcripts(),
        )


def test_an_unknown_segment_is_refused():
    with pytest.raises(LinkingError, match="unknown segment"):
        build_linking_package(
            LinkingPlan(steps=[_step(segment_index="S0099")]),
            store=_store(), transcripts=_transcripts(),
        )


def test_a_step_without_its_transcript_cannot_be_verified_so_is_refused():
    with pytest.raises(LinkingError, match="transcript not supplied"):
        build_linking_package(LinkingPlan(steps=[_step()]), store=_store(), transcripts={})


@pytest.mark.parametrize(
    "collection,object_id",
    [
        ("source_fragments", "FR-NEW"),
        ("evidence_steps", "E-NEW"),
        ("claims", "CL-NEW"),
        ("knowledge_relations", "KR-NEW"),
    ],
)
def test_a_step_never_overwrites_an_existing_record(collection, object_id):
    store = _store(**{collection: {object_id: {"id": object_id}}})
    with pytest.raises(LinkingError, match="refusing to overwrite"):
        build_linking_package(
            LinkingPlan(steps=[_step()]), store=store, transcripts=_transcripts()
        )


def test_a_plan_round_trips_from_json():
    plan = load_plan({
        "links": [{
            "observation_id": "OBS-1", "evidence_step_id": "E-1",
            "relation_id": "KR-1", "reason": "根據。",
        }],
        "steps": [],
    })
    assert plan.links[0].relation_type == "supports"
    assert plan.steps == []


def _attachment(**overrides):
    values = dict(
        observation_id="OBS-1", claim_id="CL-EXISTING", source_id="SRC-1",
        segment_index="S0002", excerpt=EXCERPT,
        statement="該城在黑門山下，居民多為外邦人。",
        fragment_id="FR-A", evidence_step_id="E-A", relation_id="KR-A",
        reason="教授以地理說明這是外邦城市。",
    )
    values.update(overrides)
    from backend.pipeline.observation_argument_linking import Attachment

    return Attachment(**values)


def _store_with_claim():
    return _store(claims={"CL-EXISTING": {
        "claim_id": "CL-EXISTING",
        "statement": "耶穌在強調皇帝權柄的外邦城市提問。",
        "claim_type": "interpretive_judgment",
        "evidence_step_ids": ["E-OLD"],
        "maturity": "candidate",
    }})


def test_an_attachment_adds_evidence_to_a_claim_without_restating_it():
    from backend.pipeline.observation_argument_linking import LinkingPlan as Plan

    package = build_linking_package(
        Plan(attachments=[_attachment()]),
        store=_store_with_claim(), transcripts=_transcripts(),
    )
    claim = package["claims"][0]
    assert claim["evidence_step_ids"] == ["E-OLD", "E-A"]
    assert claim["statement"] == "耶穌在強調皇帝權柄的外邦城市提問。"
    assert package["evidence_steps"][0]["produced_claim_ids"] == ["CL-EXISTING"]
    assert package["knowledge_relations"][0]["to_id"] == "E-A"


def test_an_attachment_to_a_missing_claim_is_refused():
    from backend.pipeline.observation_argument_linking import LinkingPlan as Plan

    with pytest.raises(LinkingError, match="claim not found"):
        build_linking_package(
            Plan(attachments=[_attachment(claim_id="CL-9")]),
            store=_store_with_claim(), transcripts=_transcripts(),
        )


def test_an_attachment_excerpt_must_also_be_verbatim():
    from backend.pipeline.observation_argument_linking import LinkingPlan as Plan

    with pytest.raises(LinkingError, match="not verbatim"):
        build_linking_package(
            Plan(attachments=[_attachment(excerpt="教授說這城在山上。")]),
            store=_store_with_claim(), transcripts=_transcripts(),
        )


def test_attaching_the_same_step_twice_does_not_duplicate_it():
    from backend.pipeline.observation_argument_linking import LinkingPlan as Plan

    store = _store(claims={"CL-EXISTING": {
        "claim_id": "CL-EXISTING", "statement": "既有結論。",
        "evidence_step_ids": ["E-A"], "maturity": "candidate",
    }})
    package = build_linking_package(
        Plan(attachments=[_attachment()]), store=store, transcripts=_transcripts()
    )
    assert package["claims"][0]["evidence_step_ids"] == ["E-A"]
