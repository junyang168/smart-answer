from __future__ import annotations

import pytest

from backend.api.canonical_repository.matthew16_viewpoint_pilot import PASSAGE_UNITS
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.passage_scope_attestation import (
    passage_units_sha256,
    validate_passage_scope_attestation,
)


def _claims():
    return [
        {
            "claim_id": "C-1",
            "pinned_claim_revision": 3,
            "claim_revision_sha256": "claim-sha",
            "scripture_refs": ["太16:18", "弗2:20"],
        }
    ]


def _payload(*, role="primary_passage", review_status="human_approved", references=True):
    body = {
        "schema_version": "wang_passage_scope_attestation_v1",
        "claim_manifest_sha256": "manifest-sha",
        "passage_units_sha256": passage_units_sha256(PASSAGE_UNITS),
        "references": (
            [
                {
                    "claim_id": "C-1",
                    "claim_revision": 3,
                    "claim_revision_sha256": "claim-sha",
                    "source_ref_index": 0,
                    "scripture_ref": "太16:18",
                    "passage_unit_ids": ["16:13-18"],
                    "role": role,
                    "role_reason": "The occurrence is classified from its evidence context.",
                    "review_status": review_status,
                }
            ]
            if references
            else []
        ),
    }
    return body | {"artifact_sha256": sha256_json(body)}


def test_only_reviewed_primary_passage_can_seed_scope():
    admissions = validate_passage_scope_attestation(
        _payload(),
        claims=_claims(),
        claim_manifest_sha256="manifest-sha",
        passage_units=PASSAGE_UNITS,
    )
    assert admissions["C-1"][0]["signal"] == "primary_scripture_exegesis"
    assert admissions["C-1"][0]["passage_unit_ids"] == ["16:13-18"]

    support_only = validate_passage_scope_attestation(
        _payload(role="theological_support"),
        claims=_claims(),
        claim_manifest_sha256="manifest-sha",
        passage_units=PASSAGE_UNITS,
    )
    assert support_only == {}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_payload(references=False), "missing scripture-use roles"),
        (_payload(role="unclassified"), "is unclassified"),
        (_payload(review_status="candidate"), "is not approved"),
    ],
)
def test_unresolved_scripture_use_blocks_scope(payload, message):
    with pytest.raises(ValueError, match=message):
        validate_passage_scope_attestation(
            payload,
            claims=_claims(),
            claim_manifest_sha256="manifest-sha",
            passage_units=PASSAGE_UNITS,
        )


def test_recomputed_artifact_sha_does_not_hide_claim_drift():
    payload = _payload()
    payload["references"][0]["scripture_ref"] = "太16:19"
    payload["artifact_sha256"] = sha256_json(
        {key: value for key, value in payload.items() if key != "artifact_sha256"}
    )
    with pytest.raises(ValueError, match="scripture reference text drift"):
        validate_passage_scope_attestation(
            payload,
            claims=_claims(),
            claim_manifest_sha256="manifest-sha",
            passage_units=PASSAGE_UNITS,
        )
