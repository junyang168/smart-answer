from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.postgres_store import record_content_sha
from backend.pipeline.extraction_supersede_runner import stale_source_cvr_retirement


def _retirement_plan(*ids: str):
    return SimpleNamespace(
        operations=tuple(
            SimpleNamespace(collection="claims", object_id=object_id, operation="retire")
            for object_id in ids
        )
    )


def test_stale_source_cvr_retirement_keeps_master_and_records_regroup_scope():
    source_id = "SRC-sermon-123456789abc"
    retiring = "DK-old-CL001"
    rows = [
        (
            "viewpoint_claim_links",
            "VCL-one",
            {
                "claim_id": retiring,
                "visibility": "internal",
                "review_status": "system_approved",
            },
        ),
        (
            "argument_route_attestations",
            "ARA-one",
            {
                "source_id": source_id,
                "claim_ids": [retiring],
                "terminal_claim_link_id": "VCL-one",
                "visibility": "internal",
                "review_status": "system_approved",
            },
        ),
        (
            "viewpoint_identity_candidates",
            "VIC-one",
            {
                "candidate_claim_ids": [retiring, "DK-other-CL001"],
                "visibility": "internal",
                "review_status": "candidate",
            },
        ),
        (
            "canonical_viewpoints",
            "CV-one",
            {"visibility": "internal", "review_status": "system_approved"},
        ),
    ]
    states = {
        (collection, object_id): {
            "revision": 1,
            "content_sha256": record_content_sha(payload),
        }
        for collection, object_id, payload in rows
    }

    keys, audit = stale_source_cvr_retirement(
        source_id=source_id,
        change_set=_retirement_plan(retiring),
        semantic_rows=rows,
        record_states=states,
    )

    assert set(keys) == {
        ("argument_route_attestations", "ARA-one"),
        ("viewpoint_claim_links", "VCL-one"),
        ("viewpoint_identity_candidates", "VIC-one"),
    }
    assert ("canonical_viewpoints", "CV-one") not in keys
    candidate = next(
        row for row in audit["records"] if row["collection"] == "viewpoint_identity_candidates"
    )
    assert candidate["other_claim_ids_requiring_regroup"] == ["DK-other-CL001"]


def test_stale_source_cvr_retirement_refuses_public_link():
    payload = {
        "claim_id": "DK-old-CL001",
        "visibility": "public",
        "review_status": "system_approved",
    }
    with pytest.raises(ValueError, match="absent or public"):
        stale_source_cvr_retirement(
            source_id="SRC-sermon-123456789abc",
            change_set=_retirement_plan("DK-old-CL001"),
            semantic_rows=[("viewpoint_claim_links", "VCL-one", payload)],
            record_states={
                ("viewpoint_claim_links", "VCL-one"): {
                    "revision": 1,
                    "content_sha256": record_content_sha(payload),
                }
            },
        )
