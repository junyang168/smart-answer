from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    sha256_json,
)
from backend.pipeline import viewpoint_scope_packet_runner as runner


class _Store:
    def __init__(self, statuses=None):
        statuses = statuses or {}
        self.claims = [
            {
                "claim_id": claim_id,
                "review_status": statuses.get(claim_id, "approved"),
                "revision": 1,
            }
            for claim_id in ("C1", "C2")
        ]

    def list_records(self, collection):
        return self.claims if collection == "claims" else []


def _fixture(monkeypatch, statuses=None):
    store = _Store(statuses)
    monkeypatch.setattr(runner, "PostgresKnowledgeStore", lambda _url: store)
    monkeypatch.setattr(
        runner.ClaimRecord,
        "model_validate",
        lambda raw: SimpleNamespace(
            **raw,
            model_dump=lambda mode: raw,
        ),
    )
    monkeypatch.setattr(runner, "semantic_record_sha", lambda claim: f"sha-{claim.claim_id}")
    monkeypatch.setattr(runner, "_pinned_claim_payloads", lambda *_args: {})
    monkeypatch.setattr(runner, "claim_evidence_integrity_findings", lambda **_kwargs: [])
    monkeypatch.setattr(
        runner,
        "compile_review_claim",
        lambda **kwargs: (
            SimpleNamespace(
                evidence=[SimpleNamespace(valid_for_identity_review=True)],
                model_dump=lambda mode: {
                    "claim_id": kwargs["claim"].claim_id,
                    "source_id": "S1",
                    "statement": "reviewed",
                    "scripture_refs": [],
                },
            ),
            [],
        ),
    )
    monkeypatch.setattr(
        runner, "RepositoryStore",
        lambda _root: SimpleNamespace(list_citations=lambda: []),
    )
    monkeypatch.setattr(runner, "registry_context", lambda *_args: [])
    monkeypatch.setattr(runner, "route_registry_context", lambda *_args: [])
    body = {
        "schema_version": CLAIM_MANIFEST_VERSION,
        "coverage_snapshot_id": "snapshot",
        "claims": [
            {
                "claim_id": claim_id,
                "pinned_claim_revision": 1,
                "claim_revision_sha256": f"sha-{claim_id}",
                "source_id": "S1",
            }
            for claim_id in ("C1", "C2")
        ],
    }
    manifest = body | {"manifest_sha256": sha256_json(body)}
    attestation = SimpleNamespace(
        claim_manifest_sha256=manifest["manifest_sha256"],
        artifact_sha256="attestation-sha",
        attestations=[
            SimpleNamespace(claim_id=claim_id, attestation_sha256="reviewed-sha")
            for claim_id in ("C1", "C2")
        ],
        exceptions=[],
    )
    return manifest, attestation


def test_all_corpus_packet_precedes_partition_plan(monkeypatch):
    manifest, attestation = _fixture(monkeypatch)
    packet = runner.build_scope_packet(
        scope=None,
        scope_label="all-corpus",
        passage_unit_ids=[],
        claim_manifest=manifest,
        source_attestation=attestation,
        repository_root=Path("/tmp/repository"),
        database_url=None,
        all_corpus=True,
    )
    assert packet["scope_kind"] == "all_corpus"
    assert [row["claim_id"] for row in packet["claims"]] == ["C1", "C2"]
    assert packet["scope_artifact_sha256"] == manifest["manifest_sha256"]
    assert packet["packet_sha256"] == sha256_json({
        key: value for key, value in packet.items() if key != "packet_sha256"
    })


def test_partition_packet_is_exactly_selected_and_bound(monkeypatch):
    manifest, attestation = _fixture(monkeypatch)
    packet = runner.build_scope_packet(
        scope=None,
        scope_label="p00001",
        passage_unit_ids=[],
        claim_manifest=manifest,
        source_attestation=attestation,
        repository_root=Path("/tmp/repository"),
        database_url=None,
        partition_claim_ids=["C1"],
        partition_manifest_sha256="a" * 64,
    )
    assert [row["claim_id"] for row in packet["claims"]] == ["C1"]
    assert packet["partition_manifest_sha256"] == "a" * 64

    _fixture(monkeypatch, statuses={"C1": "candidate"})
    with pytest.raises(ValueError, match="partition packet contains blocked"):
        runner.build_scope_packet(
            scope=None,
            scope_label="p00001",
            passage_unit_ids=[],
            claim_manifest=manifest,
            source_attestation=attestation,
            repository_root=Path("/tmp/repository"),
            database_url=None,
            partition_claim_ids=["C1"],
            partition_manifest_sha256="a" * 64,
        )
