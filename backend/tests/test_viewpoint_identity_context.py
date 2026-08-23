from __future__ import annotations

import hashlib
import json

import pytest

from backend.api.canonical_repository.viewpoint_identity_context import (
    build_identity_context_packet,
)
from backend.tests.test_viewpoint_resolution import _fixture


def _source_file(tmp_path, source_id: str, marker: str):
    path = tmp_path / f"{source_id}.json"
    payload = {
        "metadata": {"title": source_id},
        "script": [
            {"index": 1, "end_index": 1, "text": f"{marker} 前文"},
            {"index": 2, "end_index": 2, "text": f"{marker} 锚点原文"},
            {"index": 3, "end_index": 3, "text": f"{marker} 后文"},
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def test_context_expansion_is_source_bound_and_bounded(tmp_path):
    packet, _ = _fixture(source_count=2)
    descriptors = {}
    for claim in packet.claims:
        path, source_sha = _source_file(tmp_path, claim.source_id, claim.claim_id)
        for evidence in claim.evidence:
            evidence.source_sha256 = source_sha
            evidence.paragraph_key = "S0002"
        descriptors[claim.source_id] = {
            "source_id": claim.source_id,
            "source_path": str(path),
            "source_sha256": source_sha,
        }
    packet_payload = packet.model_dump(mode="json")
    packet_payload.pop("packet_sha256")
    from backend.api.canonical_repository.viewpoint_foundation import sha256_json

    packet_payload["packet_sha256"] = sha256_json(packet_payload)
    packet = type(packet).model_validate(packet_payload)
    result = build_identity_context_packet(
        hypothesis_id="VIH-CONTEXT",
        parent_packet=packet,
        source_documents=descriptors,
        source_fragment_indexes={
            evidence.source_fragment_id: 2
            for claim in packet.claims
            for evidence in claim.evidence
        },
        expansion_reason="boundary_disagreement",
        window_before_items=1,
        window_after_items=1,
    )

    assert result.expansion_ordinal == 1
    assert len(result.source_context_windows) == 2
    assert all(len(item.segments) == 3 for item in result.source_context_windows)
    assert all(
        segment.locator_kind == "source_segment_index"
        for item in result.source_context_windows
        for segment in item.segments
    )
    assert result.master_data_mutations == 0
    assert result.apply_allowed is False


def test_context_expansion_rejects_source_sha_drift(tmp_path):
    packet, _ = _fixture(source_count=2)
    descriptors = {}
    for claim in packet.claims:
        path, _ = _source_file(tmp_path, claim.source_id, claim.claim_id)
        descriptors[claim.source_id] = {
            "source_id": claim.source_id,
            "source_path": str(path),
            "source_sha256": "stale-sha",
        }
    with pytest.raises(ValueError, match="source document SHA mismatch"):
        build_identity_context_packet(
            hypothesis_id="VIH-CONTEXT",
            parent_packet=packet,
            source_documents=descriptors,
            source_fragment_indexes={},
            expansion_reason="boundary_disagreement",
        )
