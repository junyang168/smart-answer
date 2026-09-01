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


def test_wording_that_moved_since_the_ruling_returns_to_the_owner():
    records = _records()
    records["viewpoint_revisions"][0]["core_proposition"] = "别的措辞。"
    with pytest.raises(RevisionRulingError, match="wording moved"):
        build_revision_package(_ruling(), store_records=records)


def test_a_dependent_the_owner_never_saw_refuses_to_follow_silently():
    ruling = _ruling(
        expected_dependents={
            "viewpoint_claim_links": ["VCL-1"],
            "argument_route_revisions": ["ARR-OLD"],
            "viewpoint_structure_revisions": ["VSR-OLD"],
        }
    )
    with pytest.raises(RevisionRulingError, match="dependents diverge"):
        build_revision_package(ruling, store_records=_records())


def test_non_current_target_returns_to_the_owner():
    records = _records()
    records["canonical_viewpoints"][0]["current_revision_id"] = "CVR-NEWER"
    with pytest.raises(RevisionRulingError, match="not the current revision"):
        build_revision_package(_ruling(), store_records=records)
