"""Conformance of scope artifacts is checked where they are consumed (#327).

The artifact SHA proves the bytes were not altered; only the model proves the
file is the thing its schema_version claims. A hand-derived v5 scope carried
extra fields, a miscounted revision note and stale statistics through every
SHA check and into resolution.
"""

from __future__ import annotations

import pytest

from backend.api.canonical_repository.matthew16_viewpoint_pilot import (
    Matthew16PilotScope,
    PilotClaim,
    PilotSource,
    validate_pilot_scope_artifact,
)
from backend.api.canonical_repository.viewpoint_foundation import sha256_json


def _signed(payload: dict) -> dict:
    """Canonical dump + SHA, the exact recipe the model validator enforces."""

    unsigned = dict(payload)
    unsigned.pop("artifact_sha256", None)
    scope = Matthew16PilotScope.model_construct(
        **unsigned
        | {
            "sources": [PilotSource.model_validate(row) for row in unsigned["sources"]],
            "claims": [PilotClaim.model_validate(row) for row in unsigned["claims"]],
            "article_acceptance_fixtures": [],
        }
    )
    body = scope.model_dump(mode="json", exclude={"artifact_sha256"})
    return body | {"artifact_sha256": sha256_json(body)}


def _scope_payload() -> dict:
    claims = [
        {
            "claim_id": "DK-src1-CL001",
            "pinned_claim_revision": 1,
            "claim_revision_sha256": "sha-1",
            "source_id": "SRC-1",
            "statement": "磐石不是彼得本人。",
            "claim_type": "interpretive",
            "review_status": "candidate",
            "lane": "core",
            "passage_unit_ids": ["16:13-20"],
        },
        {
            "claim_id": "DK-src1-CL002",
            "pinned_claim_revision": 1,
            "claim_revision_sha256": "sha-2",
            "source_id": "SRC-1",
            "statement": "背景观察。",
            "claim_type": "observational",
            "review_status": "candidate",
            "lane": "source_context_candidate",
        },
    ]
    payload = {
        "schema_version": "wang_matthew16_viewpoint_pilot_scope_v3",
        "chapter": 16,
        "passage_units": ["16:13-20"],
        "source_catalog_sha256": "catalog-sha",
        "source_map_sha256": "map-sha",
        "source_selection_sha256": "selection-sha",
        "parent_claim_manifest_sha256": "manifest-sha",
        "sources": [
            {
                "catalog_source_id": "SRC-1",
                "title": "讲道一",
                "source_type": "sermon",
                "processing_phase": "passage_exegesis",
                "status": "latest_detailed_available",
            }
        ],
        "claims": claims,
        "article_acceptance_fixtures": [],
        "scope_selection_sha256": "scope-selection-sha",
        "relation_type_allowlist": ["supports"],
        "occurrence_signal_status": "unavailable",
        "statistics": {
            "claim_total": 2,
            "core_claim_total": 1,
            "source_context_candidate_total": 1,
        },
    }
    return _signed(payload)


def test_conforming_scope_artifact_passes():
    scope = validate_pilot_scope_artifact(_scope_payload())
    assert scope.statistics["core_claim_total"] == 1


def test_extra_fields_are_refused_even_with_a_valid_sha():
    payload = _scope_payload()
    payload["revision_note"] = "手工派生说明。"
    del payload["artifact_sha256"]
    payload["artifact_sha256"] = sha256_json(payload)
    with pytest.raises(Exception, match="revision_note"):
        validate_pilot_scope_artifact(payload)


def test_statistics_are_recomputed_not_trusted():
    payload = _scope_payload()
    payload["statistics"]["core_claim_total"] = 2
    payload = _signed(payload)
    with pytest.raises(ValueError, match="statistics disagree"):
        validate_pilot_scope_artifact(payload)


def test_wrong_schema_version_is_refused():
    payload = _scope_payload()
    payload["schema_version"] = "wang_matthew16_viewpoint_pilot_scope_v1"
    with pytest.raises(Exception, match="schema_version"):
        validate_pilot_scope_artifact(payload)
