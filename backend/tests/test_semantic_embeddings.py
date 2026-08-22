from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.knowledge_models import (
    ArgumentRouteRevisionRecord,
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
    ViewpointRevisionRecord,
)
from backend.api.semantic_index.embeddings import (
    EmbeddingProviderDescriptor,
    GoogleGeminiEmbeddingProvider,
    build_embedding_generation_plan,
    build_embedding_index_artifact,
)
from backend.api.semantic_index.projections import (
    build_argument_route_embedding_projection,
    build_claim_embedding_projection,
    build_evidence_embedding_projection,
    build_viewpoint_embedding_projection,
)


class FakeProvider:
    descriptor = EmbeddingProviderDescriptor(
        provider="fake",
        model="fake-embedding-v1",
        dimensions=3,
        provider_contract_version="fake_exact_v1",
    )

    def prepare_document(self, projection, use_case):
        return f"{use_case}:{projection.object_kind}:{projection.text}"


def _claim(claim_id: str, statement: str) -> ClaimRecord:
    return ClaimRecord(
        claim_id=claim_id,
        statement=statement,
        claim_type="interpretive_judgment",
        attribution="professor",
        scripture_refs=["Matt.16.18"],
    )


def _viewpoint() -> ViewpointRevisionRecord:
    return ViewpointRevisionRecord(
        viewpoint_revision_id="CVR-1",
        viewpoint_id="CV-1",
        revision=1,
        revision_number=1,
        core_proposition="磐石指向彼得所宣认的基督身份",
        proposition_signature={
            "subject": "磐石",
            "predicate": "指向",
            "object": "彼得所宣认的基督身份",
            "polarity": "affirmed",
            "modality": "asserted",
        },
        scope={"scripture_scope": ["Matt.16.18"]},
        provenance={
            "basis_identity_decision_ids": ["VID-1"],
            "review_artifact_sha256": "review-sha",
        },
        editorial_aliases=["彼得与磐石"],
    )


def _route() -> ArgumentRouteRevisionRecord:
    return ArgumentRouteRevisionRecord(
        argument_route_revision_id="ARR-1",
        argument_route_id="AR-1",
        revision=1,
        revision_number=1,
        validated_against_conclusion_viewpoint_revision_id="CVR-1",
        route_label="从天父启示的认信理解磐石",
        route_signature={
            "premise_roles": ["revelation", "confession"],
            "inference_pattern": "由启示所产生的认信辨明教会根基",
            "conclusion_viewpoint_id": "CV-1",
        },
        review_artifact_sha256="route-review-sha",
    )


def test_projection_and_plan_are_stable_sha_bound_and_batched_exactly_once():
    projections = [
        build_claim_embedding_projection(_claim("C2", "磐石不是彼得个人")),
        build_claim_embedding_projection(_claim("C1", "彼得本人是磐石")),
        build_claim_embedding_projection(_claim("C3", "磐石是彼得所承认的基督")),
    ]
    first = build_embedding_generation_plan(
        projections=projections,
        provider=FakeProvider(),
        use_case="candidate_recall",
        max_batch_size=2,
    )
    second = build_embedding_generation_plan(
        projections=list(reversed(projections)),
        provider=FakeProvider(),
        use_case="candidate_recall",
        max_batch_size=2,
    )

    assert first == second
    assert first.object_kind == "claim"
    assert [item.object_id for item in first.items] == ["C1", "C2", "C3"]
    assert [batch.item_ids for batch in first.batches] == [["C1", "C2"], ["C3"]]
    assert first.statistics["projection_count"] == 3
    assert first.statistics["estimated_input_tokens"] > 0


def test_plan_rejects_mixed_object_collections():
    claim = build_claim_embedding_projection(_claim("C1", "彼得本人是磐石"))
    viewpoint_shaped = claim.model_copy(
        update={"object_kind": "canonical_viewpoint", "object_id": "CV-1"}
    )

    with pytest.raises(ValueError, match="one object kind"):
        build_embedding_generation_plan(
            projections=[claim, viewpoint_shaped],
            provider=FakeProvider(),
            use_case="semantic_search",
        )


def test_object_specific_projections_keep_boundaries_and_dependencies():
    viewpoint = build_viewpoint_embedding_projection(_viewpoint())
    route = build_argument_route_embedding_projection(
        _route(), conclusion_viewpoint_revision=_viewpoint()
    )
    evidence = build_evidence_embedding_projection(
        EvidenceStepRecord(
            evidence_step_id="E1",
            source_fragment_id="F1",
            statement="彼得的认信来自天父启示",
            step_type="scriptural_observation",
            discourse_role="premise",
            scripture_refs=["Matt.16.17"],
        ),
        source_fragment=SourceFragmentRecord(
            fragment_id="F1",
            source_id="S1",
            verbatim_excerpt="这是我父在天上指示的。",
        ),
    )

    assert viewpoint.object_kind == "canonical_viewpoint"
    assert "规范观点" in viewpoint.text
    assert route.object_kind == "argument_route"
    assert route.dependency_record_sha256s == [viewpoint.source_record_sha256]
    assert "结论观点" in route.text
    assert evidence.object_kind == "evidence"
    assert "彼得的认信来自天父启示" in evidence.text
    assert "这是我父在天上指示的" in evidence.text
    assert len(evidence.dependency_record_sha256s) == 1


def test_route_projection_rejects_wrong_conclusion_revision():
    wrong = _viewpoint().model_copy(
        update={"viewpoint_revision_id": "CVR-2", "revision": 2, "revision_number": 2}
    )
    with pytest.raises(ValueError, match="revision mismatch"):
        build_argument_route_embedding_projection(
            _route(), conclusion_viewpoint_revision=wrong
        )


class FakeModels:
    def __init__(self, *, dimensions: int, aggregate: bool = False):
        self.dimensions = dimensions
        self.aggregate = aggregate
        self.calls = []

    def embed_content(self, **kwargs):
        self.calls.append(kwargs)
        count = 1 if self.aggregate else len(kwargs["contents"])
        return SimpleNamespace(
            embeddings=[
                SimpleNamespace(values=[float(index + 1)] * self.dimensions)
                for index in range(count)
            ]
        )


def test_gemini_2_wraps_each_text_as_content_and_uses_prompt_instruction():
    models = FakeModels(dimensions=128)
    provider = GoogleGeminiEmbeddingProvider(
        model="gemini-embedding-2",
        dimensions=128,
        batch_size=2,
        client=SimpleNamespace(models=models),
    )
    projections = [
        build_claim_embedding_projection(_claim("C1", "彼得本人是磐石")),
        build_claim_embedding_projection(_claim("C2", "磐石不是彼得个人")),
    ]

    vectors = provider.embed_documents(projections, "candidate_recall")

    assert len(vectors) == 2
    call = models.calls[0]
    assert len(call["contents"]) == 2
    assert call["contents"][0].parts[0].text.startswith(
        "task: sentence similarity | query:"
    )
    assert call["config"].task_type is None
    assert call["config"].output_dimensionality == 128


def test_gemini_2_fails_closed_if_provider_aggregates_batch():
    models = FakeModels(dimensions=128, aggregate=True)
    provider = GoogleGeminiEmbeddingProvider(
        dimensions=128,
        client=SimpleNamespace(models=models),
    )
    projections = [
        build_claim_embedding_projection(_claim("C1", "彼得本人是磐石")),
        build_claim_embedding_projection(_claim("C2", "磐石不是彼得个人")),
    ]

    with pytest.raises(ValueError, match="possible multi-input aggregation"):
        provider.embed_documents(projections, "candidate_recall")


def test_legacy_gemini_keeps_task_type_contract():
    models = FakeModels(dimensions=128)
    provider = GoogleGeminiEmbeddingProvider(
        model="gemini-embedding-001",
        dimensions=128,
        client=SimpleNamespace(models=models),
    )

    vectors = provider.embed_document_texts(
        ["document one", "document two"], use_case="semantic_search"
    )

    assert len(vectors) == 2
    call = models.calls[0]
    assert call["contents"] == ["document one", "document two"]
    assert call["config"].task_type == "RETRIEVAL_DOCUMENT"


def test_index_builder_rejects_missing_or_wrong_dimension_vectors():
    projection = build_claim_embedding_projection(_claim("C1", "彼得本人是磐石"))
    plan = build_embedding_generation_plan(
        projections=[projection],
        provider=FakeProvider(),
        use_case="candidate_recall",
    )

    with pytest.raises(ValueError, match="result ids"):
        build_embedding_index_artifact(
            plan=plan, projections=[projection], vectors_by_object_id={}
        )
    with pytest.raises(ValueError, match="dimensions"):
        build_embedding_index_artifact(
            plan=plan,
            projections=[projection],
            vectors_by_object_id={"C1": [1.0, 0.0]},
        )
    changed_projection = build_claim_embedding_projection(
        _claim("C1", "磐石不是彼得个人")
    )
    with pytest.raises(ValueError, match="plan SHAs"):
        build_embedding_index_artifact(
            plan=plan,
            projections=[changed_projection],
            vectors_by_object_id={"C1": [1.0, 0.0, 0.0]},
        )

    artifact = build_embedding_index_artifact(
        plan=plan,
        projections=[projection],
        vectors_by_object_id={"C1": [1.0, 0.0, 0.0]},
    )
    assert artifact.records[0].object_id == "C1"
