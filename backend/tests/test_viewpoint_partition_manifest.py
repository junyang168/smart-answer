from copy import deepcopy

import pytest

from backend.api.canonical_repository.viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    canonical_json,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_production_safety import (
    CVP_FREEZE_VERSION,
    CvpProductionBlocked,
)
from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
from backend.api.canonical_repository.viewpoint_resolution import structured_json_request
from backend.pipeline.viewpoint_partition_manifest import (
    GROUPING_PROMPT,
    GROUPING_SCHEMA_NAME,
    build_partition_manifest,
    grouping_payload,
    grouping_request_bytes,
)
from backend.pipeline.viewpoint_partition_validation import validate_partition_manifest
from backend.pipeline.passage_scope_attestation import passage_units_sha256
from backend.pipeline.passage_knowledge_slice import Passage


POLICY = {
    "schema_version": "wang_cvp_partition_policy_v1",
    "target_request_bytes": 4500,
    "max_request_bytes": 5000,
    "max_claims_per_partition": 300,
}


def _inputs():
    claims = [
        {
            "claim_id": f"C{index}",
            "pinned_claim_revision": 2,
            "claim_revision_sha256": f"sha-{index}",
            "source_id": f"S{index}",
            "statement": f"教授解释这段经文与信心的关系，第 {index} 项。",
            "scripture_refs": [f"太 16:{index}"],
        }
        for index in range(1, 6)
    ]
    pins = [
        {key: row[key] for key in (
            "claim_id", "pinned_claim_revision", "claim_revision_sha256", "source_id"
        )}
        for row in claims
    ]
    pins.append({
        "claim_id": "C6", "pinned_claim_revision": 1,
        "claim_revision_sha256": "sha-6", "source_id": "S 230205",
    })
    claim_manifest = {
        "schema_version": CLAIM_MANIFEST_VERSION,
        "coverage_snapshot_id": "snapshot-1",
        "claims": pins,
    }
    claim_manifest["manifest_sha256"] = sha256_json(claim_manifest)
    freeze = {
        "schema_version": CVP_FREEZE_VERSION,
        "status": "frozen",
        "claim_manifest_sha256": claim_manifest["manifest_sha256"],
        "cvp_policy_sha256": "cvp-policy",
        "corpus_fingerprint_sha256": "corpus-fingerprint",
        "global_lock_path": "/tmp/test-cvp-global.lock",
        "prerequisites": {
            "source_state_reconciliation": {"sha256": "source-reconciliation"},
            "independent_audit": {"sha256": "independent-audit"},
        },
    }
    freeze["artifact_sha256"] = sha256_json(freeze)
    dispositions = [{
        "claim_id": "C6", "disposition": "excluded",
        "reason_code": "source_repair_deferred_by_owner",
    }]
    return claim_manifest, freeze, claims, dispositions


def _build(**overrides):
    claim_manifest, freeze, claims, dispositions = _inputs()
    units = {"matthew-16": [Passage("Matt", 16, 1, 28)]}
    role_body = {
        "schema_version": "wang_passage_scope_attestation_v2",
        "claim_manifest_sha256": claim_manifest["manifest_sha256"],
        "passage_units_sha256": passage_units_sha256(units),
        "references": [
            {
                "claim_id": row["claim_id"],
                "claim_revision": row["pinned_claim_revision"],
                "claim_revision_sha256": row["claim_revision_sha256"],
                "source_ref_index": 0,
                "scripture_ref": row["scripture_refs"][0],
                "passage_unit_ids": ["matthew-16"],
                "role": "theological_support",
                "role_reason": "Evidence context marks this as a citation, not exegesis.",
                "review_status": "human_approved",
            }
            for row in claims
        ],
    }
    role = role_body | {"artifact_sha256": sha256_json(role_body)}
    args = dict(
        claim_manifest=claim_manifest,
        claims=claims,
        dispositions=dispositions,
        freeze=freeze,
        cvp_policy_sha256="cvp-policy",
        partition_policy=POLICY,
        runner_commit="commit-395",
        passage_role_attestation=role,
        passage_units=units,
        mode="final",
    )
    args.update(overrides)
    if args["mode"] == "preview" and "passage_role_attestation" not in overrides:
        args["passage_role_attestation"] = None
        args["passage_units"] = None
    return build_partition_manifest(**args)


def _resign(payload):
    payload["artifact_sha256"] = sha256_json({
        key: value for key, value in payload.items() if key != "artifact_sha256"
    })
    return payload


def test_exact_once_determinism_and_split_lineage():
    claim_manifest, freeze, claims, _ = _inputs()
    first = _build()
    reordered = _build(claims=list(reversed(claims)))
    assert canonical_json(first) == canonical_json(reordered)
    assert first["artifact_sha256"] == reordered["artifact_sha256"]
    assert len(first["partitions"]) > 1
    assert sorted(
        row["claim_id"] for part in first["partitions"] for row in part["claims"]
    ) == ["C1", "C2", "C3", "C4", "C5"]
    assert all(part["grouping_request_bytes"] <= POLICY["target_request_bytes"] for part in first["partitions"])
    assert validate_partition_manifest(
        first, claim_manifest=claim_manifest, freeze=freeze, cvp_policy_sha256="cvp-policy"
    )["missing_count"] == 0


def test_utf8_byte_count_matches_actual_adapter_arguments():
    _, _, claims, _ = _inputs()
    payload = grouping_payload("p00001", claims[:2])
    actual = structured_json_request(
        payload,
        prompt=GROUPING_PROMPT.read_text(encoding="utf-8"),
        response_model=ClaimGroupingResponse,
        schema_name=GROUPING_SCHEMA_NAME,
    )
    assert grouping_request_bytes("p00001", claims[:2]) == len(
        canonical_json(actual).encode("utf-8")
    )
    assert len(actual["user_prompt"].encode("utf-8")) > len(actual["user_prompt"])


def test_399kb_request_passes_and_oversized_claim_cannot_be_truncated():
    claim_manifest, freeze, claims, dispositions = _inputs()
    policy = {
        **POLICY,
        "target_request_bytes": 400000,
        "max_request_bytes": 500000,
        "max_claims_per_partition": 1,
    }
    # Chinese content is three UTF-8 bytes per character. Find a size just
    # below 400 KB without assuming JSON or schema formatting overhead.
    low, high, best = 1, 150000, 0
    while low <= high:
        middle = (low + high) // 2
        probe = {**claims[0], "statement": "信" * middle}
        size = grouping_request_bytes("p00001", [probe])
        if size <= 400000:
            best, low = middle, middle + 1
        else:
            high = middle - 1
    near_limit = [{**claims[0], "statement": "信" * best}, *claims[1:]]
    manifest = _build(
        claim_manifest=claim_manifest, freeze=freeze,
        claims=near_limit, dispositions=dispositions,
        partition_policy=policy,
    )
    assert 399000 <= manifest["partitions"][0]["grouping_request_bytes"] <= 400000
    assert all(len(part["claims"]) == 1 for part in manifest["partitions"])
    too_large = [{**claims[0], "statement": "信" * 170000}, *claims[1:]]
    with pytest.raises(ValueError, match="one Claim exceeds target bytes"):
        _build(
            claim_manifest=claim_manifest, freeze=freeze,
            claims=too_large, dispositions=dispositions,
            partition_policy=policy,
        )


@pytest.mark.parametrize("change,expected", [
    ("missing", "missing Claim ownership"),
    ("duplicate", "duplicate primary ownership"),
    ("foreign", "foreign primary Claim"),
    ("pin", "claim_revision_sha256 drift"),
    ("oversize", "grouping request exceeds partition policy"),
    ("residual", "unexplained residual"),
])
def test_validator_fails_closed_even_with_resigned_manifest(change, expected):
    claim_manifest, freeze, _, _ = _inputs()
    manifest = deepcopy(_build())
    first = manifest["partitions"][0]
    if change == "missing":
        first["claims"].pop()
    elif change == "duplicate":
        first["claims"].append(deepcopy(first["claims"][0]))
    elif change == "foreign":
        row = deepcopy(first["claims"][0])
        row["claim_id"] = "FOREIGN"
        first["claims"].append(row)
    elif change == "pin":
        first["claims"][0]["claim_revision_sha256"] = "wrong"
    elif change == "oversize":
        first["claims"][0]["statement"] = "中文字" * 300
        first["grouping_request_bytes"] = grouping_request_bytes(first["partition_id"], first["claims"])
    elif change == "residual":
        manifest["dispositions"][0]["reason_code"] = ""
    with pytest.raises(CvpProductionBlocked, match=expected):
        validate_partition_manifest(
            _resign(manifest),
            claim_manifest=claim_manifest,
            freeze=freeze,
            cvp_policy_sha256="cvp-policy",
        )


def test_stale_freeze_and_packet_authorization_fail_before_execution():
    claim_manifest, freeze, _, _ = _inputs()
    manifest = _build()
    selected = manifest["partitions"][0]
    packet = {
        "scope_label": selected["partition_id"],
        "packet_sha256": "packet-sha",
        "partition_manifest_sha256": manifest["artifact_sha256"],
        "claims": [deepcopy(row) for row in selected["claims"]],
    }
    local_freeze = {
        "scope_packet_sha256": "packet-sha",
        "claim_manifest_sha256": claim_manifest["manifest_sha256"],
        "corpus_fingerprint_sha256": freeze["corpus_fingerprint_sha256"],
        "global_lock_path": freeze["global_lock_path"],
    }
    assert validate_partition_manifest(
        manifest,
        claim_manifest=claim_manifest,
        freeze=freeze,
        cvp_policy_sha256="cvp-policy",
        partition_id=selected["partition_id"],
        scope_packet=packet,
        partition_freeze=local_freeze,
    )["status"] == "valid"
    packet["claims"][0]["statement"] = "altered"
    with pytest.raises(CvpProductionBlocked, match="scope packet differs"):
        validate_partition_manifest(
            manifest,
            claim_manifest=claim_manifest,
            freeze=freeze,
            cvp_policy_sha256="cvp-policy",
            partition_id=selected["partition_id"],
            scope_packet=packet,
            partition_freeze=local_freeze,
        )
    altered_freeze = _resign(deepcopy(freeze))
    altered_freeze["corpus_fingerprint_sha256"] = "changed"
    _resign(altered_freeze)
    with pytest.raises(CvpProductionBlocked, match="global freeze binding mismatch"):
        validate_partition_manifest(
            manifest,
            claim_manifest=claim_manifest,
            freeze=altered_freeze,
            cvp_policy_sha256="cvp-policy",
        )


def test_multi_passage_claim_has_one_owner_and_read_only_context():
    claim_manifest, freeze, claims, dispositions = _inputs()
    manifest = _build(
        claim_manifest=claim_manifest,
        freeze=freeze,
        claims=claims,
        dispositions=dispositions,
        exegesis_units={"C1": ["matthew-16", "matthew-17"], "C2": ["matthew-17"]},
        partition_policy={**POLICY, "target_request_bytes": 4200},
        mode="preview",
    )
    ownership = [
        part["partition_id"] for part in manifest["partitions"]
        for row in part["claims"] if row["claim_id"] == "C1"
    ]
    assert len(ownership) == 1
    assert any(
        ref["claim_id"] == "C1" and ref["primary_owner"] == ownership[0]
        for part in manifest["partitions"] if part["partition_id"] != ownership[0]
        for ref in part["context_refs"]
    )


def test_preview_residual_cannot_authorize_execution():
    claim_manifest, freeze, claims, _ = _inputs()
    preview = _build(
        claim_manifest=claim_manifest,
        freeze=freeze,
        claims=claims[:2],
        dispositions=[],
        mode="preview",
    )
    assert any(row["disposition"] == "residual" for row in preview["dispositions"])
    with pytest.raises(CvpProductionBlocked, match="preview manifest cannot authorize"):
        validate_partition_manifest(
            preview,
            claim_manifest=claim_manifest,
            freeze=freeze,
            cvp_policy_sha256="cvp-policy",
            partition_id=preview["partitions"][0]["partition_id"],
            scope_packet={},
            partition_freeze={},
        )


def test_zero_claim_excluded_source_is_recorded():
    claim_manifest, freeze, claims, dispositions = _inputs()
    manifest = _build(
        claim_manifest=claim_manifest, freeze=freeze,
        claims=claims, dispositions=dispositions,
        source_exclusions=["S 230205"],
    )
    assert manifest["source_exclusions"] == [{
        "source_id": "S 230205", "reason_code": "source_repair_deferred_by_owner",
    }]


def test_only_reviewed_claim_level_exegesis_routes_to_matthew():
    citation_only = _build()
    assert not any(
        row["primary_route"].startswith("passage:")
        for part in citation_only["partitions"] for row in part["claims"]
    )
    roles = deepcopy(citation_only["passage_role_attestation"])
    roles["references"][0]["role"] = "passage_exegesis"
    roles["references"][1]["role"] = "primary_passage"
    _resign(roles)
    manifest = _build(passage_role_attestation=roles)
    routes = {
        row["claim_id"]: row["primary_route"]
        for part in manifest["partitions"] for row in part["claims"]
    }
    assert routes["C1"] == "passage:matthew-16"
    assert not routes["C2"].startswith("passage:")


def test_matthew16_sized_scope_stays_whole_before_semantic_grouping():
    claim_manifest, freeze, _, _ = _inputs()
    claims = [
        {
            "claim_id": f"M{index:03d}",
            "pinned_claim_revision": 1,
            "claim_revision_sha256": f"m-sha-{index}",
            "source_id": f"SERMON-{index % 10}",
            "statement": f"马太十六章讲论中的主张 {index}",
            "scripture_refs": ["太16:18"],
        }
        for index in range(190)
    ]
    body = {
        "schema_version": CLAIM_MANIFEST_VERSION,
        "coverage_snapshot_id": "matthew16-preview",
        "claims": [
            {key: row[key] for key in (
                "claim_id", "pinned_claim_revision", "claim_revision_sha256", "source_id"
            )}
            for row in claims
        ],
    }
    claim_manifest = body | {"manifest_sha256": sha256_json(body)}
    freeze["claim_manifest_sha256"] = claim_manifest["manifest_sha256"]
    _resign(freeze)
    manifest = _build(
        claim_manifest=claim_manifest,
        claims=claims,
        dispositions=[],
        freeze=freeze,
        cvp_policy_sha256="cvp-policy",
        partition_policy={
            **POLICY,
            "target_request_bytes": 380000,
            "max_request_bytes": 500000,
        },
        exegesis_units={row["claim_id"]: ["matthew-16"] for row in claims},
        mode="preview",
    )
    assert len(manifest["partitions"]) == 1
    assert len(manifest["partitions"][0]["claims"]) == 190
    assert manifest["partitions"][0]["routing_reasons"] == ["passage:matthew-16"]
