from __future__ import annotations

import json

import pytest

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.semantic_index.embeddings import (
    EmbeddingProviderDescriptor,
    build_embedding_generation_plan,
    build_embedding_projection_manifest,
)
from backend.api.semantic_index.projections import build_claim_embedding_projection
from backend.pipeline.viewpoint_embedding_execution_runner import execute_embedding_plan


class CountingProvider:
    descriptor = EmbeddingProviderDescriptor(
        provider="fake", model="fixture", dimensions=3,
        provider_contract_version="fixture_v1",
    )

    def __init__(self, fail_on_call: int | None = None):
        self.calls = 0
        self.fail_on_call = fail_on_call

    def prepare_document(self, projection, use_case):
        return projection.text

    def embed_documents(self, projections, use_case):
        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("simulated interruption")
        return [[1.0, float(index + 1), 0.0] for index, _ in enumerate(projections)]


def _inputs():
    claims = [
        ClaimRecord(claim_id=f"C{index}", statement=f"观点 {index}",
                    claim_type="interpretive_judgment", attribution="professor")
        for index in range(1, 6)
    ]
    projections = [build_claim_embedding_projection(claim) for claim in claims]
    provider = CountingProvider()
    manifest = build_embedding_projection_manifest(projections)
    plan = build_embedding_generation_plan(
        projections=projections, provider=provider, use_case="candidate_recall",
        max_batch_size=2,
    )
    return manifest, plan


def test_embedding_execution_resumes_without_recalling_valid_batches(tmp_path):
    manifest, plan = _inputs()
    interrupted = CountingProvider(fail_on_call=2)
    with pytest.raises(RuntimeError, match="interruption"):
        execute_embedding_plan(
            projection_manifest=manifest, plan=plan, provider=interrupted,
            batch_dir=tmp_path,
        )
    assert interrupted.calls == 2
    assert len(list(tmp_path.glob("*.json"))) == 1

    resumed = CountingProvider()
    index, executed, reused = execute_embedding_plan(
        projection_manifest=manifest, plan=plan, provider=resumed, batch_dir=tmp_path,
    )
    assert resumed.calls == 2
    assert executed == 2
    assert reused == 1
    assert len(index.records) == 5

    final = CountingProvider()
    same_index, executed, reused = execute_embedding_plan(
        projection_manifest=manifest, plan=plan, provider=final, batch_dir=tmp_path,
    )
    assert final.calls == 0
    assert executed == 0
    assert reused == 3
    assert same_index.artifact_sha256 == index.artifact_sha256


def test_embedding_execution_fails_closed_on_tampered_batch(tmp_path):
    manifest, plan = _inputs()
    execute_embedding_plan(
        projection_manifest=manifest, plan=plan, provider=CountingProvider(),
        batch_dir=tmp_path,
    )
    path = next(tmp_path.glob("*.json"))
    payload = json.loads(path.read_text())
    payload["batch_fingerprint_sha256"] = "tampered"
    path.write_text(json.dumps(payload))

    with pytest.raises(ValueError):
        execute_embedding_plan(
            projection_manifest=manifest, plan=plan, provider=CountingProvider(),
            batch_dir=tmp_path,
        )
