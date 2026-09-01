"""A ruled wording revision drags exactly what the owner confirmed."""

from __future__ import annotations

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_ruling_revision_runner import (
    REVISION_RULING_SCHEMA_VERSION,
    RevisionRulingError,
    _validated_ruling,
    build_revision_package,
)


def _records():
    return {
        "viewpoint_revisions": [
            {
                "viewpoint_revision_id": "CVR-OLD",
                "viewpoint_id": "CV-1",
                "revision": 2,
                "revision_number": 2,
                "core_proposition": "捆绑、释放、赦罪、留罪的标准已先由神决定。",
                "proposition_signature": {"subject": "旧主语"},
                "scope": {"scripture_scope": ["太16:19"]},
                "provenance": {
                    "basis_identity_decision_ids": ["VID-1"],
                    "review_artifact_sha256": "old-review-sha",
                },
                "review_status": "system_approved",
            }
        ],
        "canonical_viewpoints": [
            {"viewpoint_id": "CV-1", "current_revision_id": "CVR-OLD"}
        ],
        "viewpoint_claim_links": [
            {
                "viewpoint_claim_link_id": "VCL-1",
                "validated_against_viewpoint_revision_id": "CVR-OLD",
                "effective_state": "active",
            }
        ],
        "viewpoint_relations": [],
        "argument_routes": [
            {"argument_route_id": "AR-1", "current_revision_id": "ARR-OLD"}
        ],
        "argument_route_revisions": [
            {
                "argument_route_revision_id": "ARR-OLD",
                "argument_route_id": "AR-1",
                "revision": 1,
                "revision_number": 1,
                "validated_against_conclusion_viewpoint_revision_id": "CVR-OLD",
                "ordered_inference_nodes": [
                    {"route_step_key": "C1", "conclusion_viewpoint_revision_id": "CVR-OLD"}
                ],
            }
        ],
        "argument_route_attestations": [
            {
                "argument_route_attestation_id": "ARA-OLD",
                "argument_route_id": "AR-1",
                "validated_against_route_revision_id": "ARR-OLD",
                "source_id": "SRC-1",
                "source_revision_sha256": "src-sha",
                "claim_ids": ["C-1"],
                "step_bindings": [],
                "terminal_claim_link_id": "VCL-1",
                "effective_state": "active",
            }
        ],
        "viewpoint_structures": [
            {"structure_id": "VS-1", "current_revision_id": "VSR-OLD"}
        ],
        "viewpoint_structure_revisions": [
            {
                "structure_revision_id": "VSR-OLD",
                "structure_id": "VS-1",
                "revision": 1,
                "revision_number": 1,
                "focal_viewpoints": [
                    {"viewpoint_revision_id": "CVR-OLD", "structure_role": "central_claim"}
                ],
            }
        ],
    }


def _ruling(**overrides):
    payload = {
        "schema_version": REVISION_RULING_SCHEMA_VERSION,
        "decided_by": "junyang",
        "decided_at": "2026-09-01",
        "recorded_at_urls": ["https://github.com/junyang168/smart-answer/issues/232"],
        "target_viewpoint_revision_id": "CVR-OLD",
        "expected_core_proposition": "捆绑、释放、赦罪、留罪的标准已先由神决定。",
        "new_core_proposition": "捆绑、释放的标准已先由神决定。",
        "new_proposition_signature": {"subject": "新主语"},
        "reason": "摘。判定者：junyang。",
        "expected_dependents": {
            "viewpoint_claim_links": ["VCL-1"],
            "argument_route_revisions": ["ARR-OLD"],
            "argument_route_attestations": ["ARA-OLD"],
            "viewpoint_structure_revisions": ["VSR-OLD"],
        },
    }
    payload.update(overrides)
    payload["artifact_sha256"] = sha256_json(payload)
    return _validated_ruling(payload)


def test_supersede_drags_the_confirmed_dependents_by_succession():
    package, retiring, manifest = build_revision_package(
        _ruling(), store_records=_records()
    )
    new_id = manifest["new_viewpoint_revision_id"]
    assert new_id != "CVR-OLD"
    assert package["viewpoint_revisions"][0]["supersedes_revision_id"] == "CVR-OLD"
    assert package["canonical_viewpoints"][0]["current_revision_id"] == new_id
    assert package["viewpoint_claim_links"][0][
        "validated_against_viewpoint_revision_id"
    ] == new_id
    arr = package["argument_route_revisions"][0]
    assert arr["supersedes_revision_id"] == "ARR-OLD"
    assert arr["argument_route_revision_id"] != "ARR-OLD"
    ara = package["argument_route_attestations"][0]
    assert ara["argument_route_attestation_id"] != "ARA-OLD"
    assert ara["validated_against_route_revision_id"] == arr["argument_route_revision_id"]
    assert retiring == [("argument_route_attestations", "ARA-OLD")]
    vsr = package["viewpoint_structure_revisions"][0]
    assert vsr["supersedes_revision_id"] == "VSR-OLD"
    assert vsr["focal_viewpoints"][0]["viewpoint_revision_id"] == new_id

    ruling_sha = _ruling()["artifact_sha256"]
    assert arr["review_artifact_sha256"] == ruling_sha
    assert arr["approved_by"] == "junyang"
    assert ara["review_artifact_sha256"] == ruling_sha


def test_repointed_relation_carries_the_ruling_as_provenance():
    records = _records()
    records["viewpoint_relations"] = [
        {
            "viewpoint_relation_id": "VREL-1",
            "source_viewpoint_id": "CV-1",
            "target_viewpoint_id": "CV-2",
            "validated_source_viewpoint_revision_id": "CVR-OLD",
            "validated_target_viewpoint_revision_id": "CVR-OTHER",
            "relation_type": "entails",
            "effective_state": "active",
            "review_provenance": {
                "review_artifact_sha256": "stale-review-sha",
                "basis_identity_decision_ids": [],
            },
        }
    ]
    ruling = _ruling(
        expected_dependents={
            "viewpoint_claim_links": ["VCL-1"],
            "viewpoint_relations": ["VREL-1"],
            "argument_route_revisions": ["ARR-OLD"],
            "argument_route_attestations": ["ARA-OLD"],
            "viewpoint_structure_revisions": ["VSR-OLD"],
        }
    )
    package, _, _ = build_revision_package(ruling, store_records=records)
    moved = package["viewpoint_relations"][0]
    assert moved["review_provenance"]["review_artifact_sha256"] == ruling["artifact_sha256"]


def test_successor_records_survive_the_store_round_trip():
    """The store writes a NEW object at store revision 1 and readback carries
    that revision; a successor whose bookkeeping said old+1 validated inside
    the package and corrupted the registry at read time (#326 live fire).
    The vaccine: every successor must validate under its strict model with
    the store's revision of 1 applied."""

    from backend.api.canonical_repository.knowledge_models import (
        ArgumentRouteRevisionRecord,
        ViewpointRevisionRecord,
        ViewpointStructureRevisionRecord,
    )

    records = _records()
    records["viewpoint_revisions"][0] |= {
        "schema_version": "wang_viewpoint_revision_v1",
        "proposition_signature": {
            "subject": "旧主语", "predicate": "已先取决于", "object": "神的先在决定",
            "polarity": "affirmed", "modality": "断言",
            "conditions": [], "temporal_scope": [], "population_scope": ["教会"],
        },
        "scope": {"scripture_scope": ["馬太福音16:19"], "audience_scope": [], "historical_scope": []},
        "review_status": "system_approved",
        "approved_by": "batch", "approved_at": "2026-08-25",
    }
    records["argument_route_revisions"][0] |= {
        "schema_version": "wang_argument_route_revision_v2",
        "route_label": "未来完成式→天上先定",
        "route_signature": {
            "conclusion_viewpoint_id": "CV-1",
            "inference_method_codes": ["grammatical_argument"],
        },
        "ordered_inference_nodes": [
            {
                "route_step_key": "P1",
                "role": "premise",
                "required_for_full_attestation": True,
                "normalized_proposition": "未来完成式表示天上已先决定。",
            },
            {
                "route_step_key": "C1",
                "role": "conclusion",
                "required_for_full_attestation": True,
                "conclusion_viewpoint_revision_id": "CVR-OLD",
            },
        ],
        "review_status": "system_approved",
        "approved_by": "route-batch", "approved_at": "2026-08-25",
        "review_artifact_sha256": "stale-review-sha",
    }
    records["viewpoint_structure_revisions"][0] |= {
        "central_synthesis": "标准先定于天上。",
        "scope_manifest_sha256": "scope-sha",
        "focal_viewpoints": [
            {"viewpoint_revision_id": "CVR-OLD", "structure_role": "central_claim"}
        ],
    }
    ruling = _ruling(
        new_proposition_signature={
            "subject": "新主语", "predicate": "已先取决于", "object": "神的先在决定",
            "polarity": "affirmed", "modality": "断言",
            "conditions": [], "temporal_scope": [], "population_scope": ["教会"],
        },
    )
    package, _, _ = build_revision_package(ruling, store_records=records)
    models = {
        "viewpoint_revisions": ViewpointRevisionRecord,
        "argument_route_revisions": ArgumentRouteRevisionRecord,
        "viewpoint_structure_revisions": ViewpointStructureRevisionRecord,
    }
    checked = 0
    for collection, model in models.items():
        for row in package.get(collection) or []:
            model.model_validate(dict(row) | {"revision": 1})
            checked += 1
    assert checked == 3

    # And the successors carry THIS ruling's credentials, not the stale ones.
    arr = package["argument_route_revisions"][0]
    assert arr["review_artifact_sha256"] == ruling["artifact_sha256"]
