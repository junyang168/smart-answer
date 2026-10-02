from __future__ import annotations

from copy import deepcopy

from backend.pipeline.legacy_coordinate_review_proof import (
    coordinate_chain_proof,
    exact_current_anchor_count,
    related_package_content_unchanged,
)
from backend.pipeline.legacy_coordinate_review_preflight import _fragments_match_source
from backend.pipeline.source_contract_cleanup import BodyLocatorIndex


def _claim() -> dict:
    return {
        "claim_id": "CL-1", "title": "教授的主张", "review_status": "candidate",
        "revision": 1, "evidence_step_ids": ["E-1"],
        "occurrences": [{
            "source_id": "SRC-1", "transcript_id": "T-1",
            "anchors": [{
                "paragraph_key": "S0002", "evidence_id": "E-1",
                "proposed_highlight": {"text": "教授原话", "status": "proposed"},
            }],
        }],
    }


def _versions(old: dict, new: dict) -> list[dict]:
    return [
        {"revision": 1, "content_sha256": "a" * 64,
         "source_kind": "knowledge_package", "payload": old},
        {"revision": 2, "content_sha256": "b" * 64,
         "source_kind": "wkp368_source_contract_cleanup", "payload": new},
    ]


def test_coordinate_chain_accepts_only_paragraph_key_change() -> None:
    old = _claim()
    new = deepcopy(old)
    new["revision"] = 2
    new["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0001"
    proof = coordinate_chain_proof(old, _versions(old, new))
    assert proof is not None
    assert proof["reviewed_claim_revision"] == 1
    assert proof["current_claim_revision"] == 2


def test_coordinate_chain_rejects_relation_and_statement_changes() -> None:
    old = _claim()
    new = deepcopy(old)
    new["revision"] = 2
    new["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0001"
    new["evidence_step_ids"].append("E-2")
    assert coordinate_chain_proof(old, _versions(old, new)) is None
    del new["evidence_step_ids"][-1]
    new["title"] = "另一主张"
    assert coordinate_chain_proof(old, _versions(old, new)) is None


def test_coordinate_chain_rejects_untrusted_change_kind() -> None:
    old = _claim()
    new = deepcopy(old)
    new["revision"] = 2
    new["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0001"
    versions = _versions(old, new)
    versions[1]["source_kind"] = "knowledge_package"
    assert coordinate_chain_proof(old, versions) is None


def test_current_anchor_requires_exact_excerpt_at_locator() -> None:
    claim = _claim()
    index = BodyLocatorIndex([{"index": 1, "text": "教授原话"}])
    claim["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0001"
    assert exact_current_anchor_count(
        claim, source_id="SRC-1", transcript_id="T-1", index=index,
    ) == 1
    claim["occurrences"][0]["anchors"][0]["paragraph_key"] = "S0002"
    assert exact_current_anchor_count(
        claim, source_id="SRC-1", transcript_id="T-1", index=index,
    ) is None


def test_graph_requires_same_evidence_and_excerpt() -> None:
    claim = _claim()
    evidence = {"E-1": {"evidence_step_id": "E-1", "statement": "证据",
                          "produced_claim_ids": ["CL-1"],
                          "source_fragment_ids": ["FR-1"], "review_status": "candidate"}}
    fragment = {"FR-1": {"fragment_id": "FR-1", "source_id": "SRC-1",
                             "verbatim_excerpt": "教授原话", "paragraph_key": "S0002",
                             "source_sha256": "old", "review_status": "candidate"}}
    current_fragment = deepcopy(fragment)
    current_fragment["FR-1"]["paragraph_key"] = "S0001"
    current_fragment["FR-1"]["source_sha256"] = "new"
    assert related_package_content_unchanged(
        "CL-1", claim, evidence, fragment, evidence, current_fragment,
    )
    altered = deepcopy(evidence)
    altered["E-1"]["statement"] = "不同证据"
    assert not related_package_content_unchanged(
        "CL-1", claim, evidence, fragment, altered, current_fragment,
    )
    altered = deepcopy(evidence)
    altered["E-1"]["produced_claim_ids"].append("CL-2")
    assert not related_package_content_unchanged(
        "CL-1", claim, evidence, fragment, altered, current_fragment,
    )


def test_fragment_must_bind_to_current_source_and_body_locator() -> None:
    claim = _claim()
    index = BodyLocatorIndex([{"index": 1, "text": "教授原话"}])
    evidence = {"E-1": {"source_fragment_ids": ["FR-1"]}}
    fragments = {"FR-1": {"source_id": "SRC-1", "paragraph_key": "S0001",
                          "verbatim_excerpt": "教授原话"}}
    assert _fragments_match_source(
        claim, source_id="SRC-1", evidence=evidence,
        fragments=fragments, index=index,
    ) == (True, 1)
    fragments["FR-1"]["paragraph_key"] = "S0002"
    assert _fragments_match_source(
        claim, source_id="SRC-1", evidence=evidence,
        fragments=fragments, index=index,
    ) == (False, 0)
