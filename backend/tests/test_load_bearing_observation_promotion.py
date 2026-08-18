import pytest

from backend.pipeline.load_bearing_observation_promotion import (
    LoadBearingPromotionError,
    PromotionRequest,
    build_promotion_package,
)


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
                "statement": "太16:23的φρονέω意為關心、重視。",
                "observation_type": "original_language",
                "source_fragment_ids": ["FR-1"],
                "scripture_refs": ["太16:23"],
            }
        },
        "source_fragments": {
            "FR-1": {"fragment_id": "FR-1", "verbatim_excerpt": "φρονέω 意為關心、重視。"}
        },
        "claims": {},
        "evidence_steps": {},
    }
    for collection, updates in overrides.items():
        records[collection].update(updates)
    return _FakeStore(records)


def _request(**kwargs):
    defaults = dict(
        observation_id="OBS-1",
        claim_id="CL-NEW",
        evidence_step_id="E-NEW",
        claim_type="original_language_observation",
        rationale="刪掉這項觀察，責備的焦點就只剩語氣，論證站不住。",
    )
    defaults.update(kwargs)
    return PromotionRequest(**defaults)


def test_promotion_builds_a_claim_and_evidence_step_from_the_observation():
    package = build_promotion_package(_request(), store=_store())
    claim = package["claims"][0]
    step = package["evidence_steps"][0]
    assert claim["claim_id"] == "CL-NEW"
    assert claim["statement"] == "太16:23的φρονέω意為關心、重視。"
    assert claim["evidence_step_ids"] == ["E-NEW"]
    assert claim["promoted_from_observation_id"] == "OBS-1"
    assert step["evidence_step_id"] == "E-NEW"
    assert step["source_fragment_id"] == "FR-1"
    assert step["produced_claim_ids"] == ["CL-NEW"]


def test_promotion_carries_the_existing_fragment_so_package_validation_can_resolve_it():
    package = build_promotion_package(_request(), store=_store())
    assert package["source_fragments"][0]["fragment_id"] == "FR-1"


def test_promotion_refuses_an_empty_rationale():
    with pytest.raises(LoadBearingPromotionError, match="rationale"):
        build_promotion_package(_request(rationale="   "), store=_store())


def test_promotion_refuses_an_unknown_observation():
    with pytest.raises(LoadBearingPromotionError, match="OBS-MISSING"):
        build_promotion_package(_request(observation_id="OBS-MISSING"), store=_store())


def test_promotion_refuses_when_fragment_is_missing_from_the_store():
    store = _FakeStore(
        {
            "observations": {
                "OBS-1": {
                    "observation_id": "OBS-1", "statement": "x",
                    "source_fragment_ids": ["FR-GONE"], "scripture_refs": [],
                }
            },
            "source_fragments": {}, "claims": {}, "evidence_steps": {},
        }
    )
    with pytest.raises(LoadBearingPromotionError, match="FR-GONE"):
        build_promotion_package(_request(), store=store)


def test_promotion_refuses_to_overwrite_an_existing_claim_id():
    store = _store(claims={"CL-NEW": {"claim_id": "CL-NEW"}})
    with pytest.raises(LoadBearingPromotionError, match="CL-NEW"):
        build_promotion_package(_request(), store=store)


def test_promotion_refuses_to_overwrite_an_existing_evidence_step_id():
    store = _store(evidence_steps={"E-NEW": {"evidence_step_id": "E-NEW"}})
    with pytest.raises(LoadBearingPromotionError, match="E-NEW"):
        build_promotion_package(_request(), store=store)


def test_promotion_refuses_an_observation_with_ambiguous_fragments():
    store = _FakeStore(
        {
            "observations": {
                "OBS-1": {
                    "observation_id": "OBS-1", "statement": "x",
                    "source_fragment_ids": ["FR-1", "FR-2"], "scripture_refs": [],
                }
            },
            "source_fragments": {"FR-1": {}, "FR-2": {}},
            "claims": {}, "evidence_steps": {},
        }
    )
    with pytest.raises(LoadBearingPromotionError, match="exactly one"):
        build_promotion_package(_request(), store=store)


def test_promotion_accepts_an_explicit_statement_override():
    package = build_promotion_package(_request(statement="改寫過的陳述"), store=_store())
    assert package["claims"][0]["statement"] == "改寫過的陳述"
    assert package["evidence_steps"][0]["statement"] == "改寫過的陳述"
