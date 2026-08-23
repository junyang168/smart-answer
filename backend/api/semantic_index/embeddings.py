"""Versioned embedding plans, provider adapter, and immutable index artifacts.

Similarity is a retrieval signal only.  Nothing in this module creates a
CanonicalViewpoint identity, relation, membership, or approval decision.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..canonical_repository.viewpoint_foundation import sha256_json


EMBEDDING_PROJECTION_VERSION = "wang_semantic_embedding_projection_v1"
EMBEDDING_PROJECTION_MANIFEST_VERSION = "wang_semantic_embedding_projection_manifest_v1"
EMBEDDING_PLAN_VERSION = "wang_semantic_embedding_plan_v1"
EMBEDDING_INDEX_VERSION = "wang_semantic_embedding_index_v1"
TOKEN_ESTIMATION_METHOD = "utf8_bytes_div_2_ceiling_v1"
DEFAULT_GEMINI_MODEL = "gemini-embedding-2"
DEFAULT_DIMENSIONS = 768
DEFAULT_BATCH_SIZE = 64
MAX_BATCH_SIZE = 100

EmbeddingObjectKind = Literal[
    "canonical_viewpoint", "claim", "claim_signature", "argument_route", "evidence"
]
EmbeddingUseCase = Literal["semantic_search", "question_answering", "candidate_recall"]


class StrictEmbeddingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmbeddingProjection(StrictEmbeddingModel):
    schema_version: Literal["wang_semantic_embedding_projection_v1"] = (
        EMBEDDING_PROJECTION_VERSION
    )
    projection_version: str
    object_kind: EmbeddingObjectKind
    object_id: str
    object_revision: int = Field(ge=1)
    source_record_sha256: str
    dependency_record_sha256s: list[str] = Field(default_factory=list)
    title: str
    text: str = Field(min_length=1)
    text_sha256: str
    projection_sha256: str

    @model_validator(mode="after")
    def validate_hashes(self) -> "EmbeddingProjection":
        if self.dependency_record_sha256s != sorted(
            set(self.dependency_record_sha256s)
        ):
            raise ValueError("embedding dependency SHAs must be sorted and unique")
        if self.text_sha256 != sha256_json(self.text):
            raise ValueError("embedding projection text SHA mismatch")
        payload = self.model_dump(mode="json", exclude={"projection_sha256"})
        if self.projection_sha256 != sha256_json(payload):
            raise ValueError("embedding projection SHA mismatch")
        return self


class EmbeddingProviderDescriptor(StrictEmbeddingModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    dimensions: int = Field(ge=1, le=3072)
    provider_contract_version: str = Field(min_length=1)
    transport_mode: str = "provider_defined"
    endpoint_location: str = "provider_default"


class EmbeddingProjectionManifest(StrictEmbeddingModel):
    schema_version: Literal["wang_semantic_embedding_projection_manifest_v1"] = (
        EMBEDDING_PROJECTION_MANIFEST_VERSION
    )
    object_kind: EmbeddingObjectKind
    projections: list[EmbeddingProjection] = Field(min_length=1)
    statistics: dict[str, int]
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_manifest(self) -> "EmbeddingProjectionManifest":
        ids = [item.object_id for item in self.projections]
        if ids != sorted(set(ids)):
            raise ValueError("embedding projections must use canonical unique object order")
        if any(item.object_kind != self.object_kind for item in self.projections):
            raise ValueError("projection manifest can contain only one object kind")
        expected = {
            "projection_count": len(self.projections),
            "text_bytes": sum(len(item.text.encode("utf-8")) for item in self.projections),
        }
        if self.statistics != expected:
            raise ValueError("embedding projection manifest statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("embedding projection manifest SHA mismatch")
        return self


class EmbeddingPlanItem(StrictEmbeddingModel):
    object_kind: EmbeddingObjectKind
    object_id: str
    projection_sha256: str
    provider_input_sha256: str
    provider_input_bytes: int = Field(gt=0)
    estimated_input_tokens: int = Field(gt=0)


class EmbeddingPlanBatch(StrictEmbeddingModel):
    batch_id: str
    item_ids: list[str] = Field(min_length=1)
    input_bytes: int = Field(gt=0)
    estimated_input_tokens: int = Field(gt=0)
    batch_fingerprint_sha256: str

    @model_validator(mode="after")
    def validate_batch(self) -> "EmbeddingPlanBatch":
        if self.item_ids != sorted(set(self.item_ids)):
            raise ValueError("embedding batch item ids must be sorted and unique")
        if self.batch_id != f"EMB-{self.batch_fingerprint_sha256[:20]}":
            raise ValueError("embedding batch id does not match fingerprint")
        return self


class EmbeddingGenerationPlan(StrictEmbeddingModel):
    schema_version: Literal["wang_semantic_embedding_plan_v1"] = EMBEDDING_PLAN_VERSION
    object_kind: EmbeddingObjectKind
    use_case: EmbeddingUseCase
    provider: EmbeddingProviderDescriptor
    token_estimation_method: Literal["utf8_bytes_div_2_ceiling_v1"] = (
        TOKEN_ESTIMATION_METHOD
    )
    max_batch_size: int = Field(ge=1, le=MAX_BATCH_SIZE)
    projection_manifest_sha256: str
    items: list[EmbeddingPlanItem] = Field(min_length=1)
    batches: list[EmbeddingPlanBatch] = Field(min_length=1)
    statistics: dict[str, int]
    plan_sha256: str

    @model_validator(mode="after")
    def validate_plan(self) -> "EmbeddingGenerationPlan":
        item_ids = [item.object_id for item in self.items]
        if item_ids != sorted(set(item_ids)):
            raise ValueError("embedding plan items must use canonical unique object order")
        if any(item.object_kind != self.object_kind for item in self.items):
            raise ValueError("one embedding plan can contain only one object kind")
        scheduled = [item_id for batch in self.batches for item_id in batch.item_ids]
        if sorted(scheduled) != item_ids or len(scheduled) != len(set(scheduled)):
            raise ValueError("embedding batches must cover every item exactly once")
        if any(len(batch.item_ids) > self.max_batch_size for batch in self.batches):
            raise ValueError("embedding batch exceeds item limit")
        item_index = {item.object_id: item for item in self.items}
        for batch in self.batches:
            batch_items = [item_index[item_id] for item_id in batch.item_ids]
            if batch.input_bytes != sum(item.provider_input_bytes for item in batch_items):
                raise ValueError("embedding batch byte total mismatch")
            if batch.estimated_input_tokens != sum(
                item.estimated_input_tokens for item in batch_items
            ):
                raise ValueError("embedding batch token estimate mismatch")
            expected_fingerprint = sha256_json(
                {
                    "provider": self.provider.model_dump(mode="json"),
                    "use_case": self.use_case,
                    "item_projection_sha256s": [
                        item.projection_sha256 for item in batch_items
                    ],
                    "provider_input_sha256s": [
                        item.provider_input_sha256 for item in batch_items
                    ],
                }
            )
            if batch.batch_fingerprint_sha256 != expected_fingerprint:
                raise ValueError("embedding batch fingerprint mismatch")
        expected = {
            "projection_count": len(self.items),
            "batch_count": len(self.batches),
            "input_bytes": sum(item.provider_input_bytes for item in self.items),
            "estimated_input_tokens": sum(
                item.estimated_input_tokens for item in self.items
            ),
        }
        if self.statistics != expected:
            raise ValueError("embedding plan statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"plan_sha256"})
        if self.plan_sha256 != sha256_json(payload):
            raise ValueError("embedding plan SHA mismatch")
        return self


class EmbeddingVectorRecord(StrictEmbeddingModel):
    object_kind: EmbeddingObjectKind
    object_id: str
    projection_sha256: str
    vector: list[float] = Field(min_length=1)
    vector_sha256: str

    @model_validator(mode="after")
    def validate_vector(self) -> "EmbeddingVectorRecord":
        if not all(math.isfinite(value) for value in self.vector):
            raise ValueError("embedding vector values must be finite")
        if not any(value != 0 for value in self.vector):
            raise ValueError("embedding vector cannot be zero")
        if self.vector_sha256 != sha256_json(self.vector):
            raise ValueError("embedding vector SHA mismatch")
        return self


class EmbeddingIndexArtifact(StrictEmbeddingModel):
    schema_version: Literal["wang_semantic_embedding_index_v1"] = EMBEDDING_INDEX_VERSION
    plan_sha256: str
    object_kind: EmbeddingObjectKind
    use_case: EmbeddingUseCase
    provider: EmbeddingProviderDescriptor
    records: list[EmbeddingVectorRecord] = Field(min_length=1)
    statistics: dict[str, int]
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "EmbeddingIndexArtifact":
        ids = [item.object_id for item in self.records]
        if ids != sorted(set(ids)):
            raise ValueError("embedding index records must use canonical unique object order")
        if any(len(item.vector) != self.provider.dimensions for item in self.records):
            raise ValueError("embedding vector dimensions do not match provider descriptor")
        if any(item.object_kind != self.object_kind for item in self.records):
            raise ValueError("embedding records do not match index object kind")
        expected = {
            "record_count": len(self.records),
            "dimensions": self.provider.dimensions,
        }
        if self.statistics != expected:
            raise ValueError("embedding index statistics mismatch")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("embedding index artifact SHA mismatch")
        return self


class EmbeddingProvider(Protocol):
    @property
    def descriptor(self) -> EmbeddingProviderDescriptor: ...

    def prepare_document(
        self, projection: EmbeddingProjection, use_case: EmbeddingUseCase
    ) -> str: ...

    def prepare_query(self, text: str, use_case: EmbeddingUseCase) -> str: ...

    def embed_documents(
        self, projections: Sequence[EmbeddingProjection], use_case: EmbeddingUseCase
    ) -> list[list[float]]: ...

    def embed_queries(
        self, texts: Sequence[str], use_case: EmbeddingUseCase
    ) -> list[list[float]]: ...

    def embed_document_texts(
        self,
        texts: Sequence[str],
        *,
        titles: Sequence[str] | None = None,
        use_case: EmbeddingUseCase,
    ) -> list[list[float]]: ...


def _estimated_tokens(value: str) -> int:
    return max(1, math.ceil(len(value.encode("utf-8")) / 2))


def build_embedding_projection_manifest(
    projections: Sequence[EmbeddingProjection],
) -> EmbeddingProjectionManifest:
    ordered = sorted(projections, key=lambda item: item.object_id)
    if not ordered:
        raise ValueError("embedding projection manifest cannot be empty")
    object_kinds = {item.object_kind for item in ordered}
    if len(object_kinds) != 1:
        raise ValueError("projection manifest can contain only one object kind")
    payload: dict[str, Any] = {
        "schema_version": EMBEDDING_PROJECTION_MANIFEST_VERSION,
        "object_kind": next(iter(object_kinds)),
        "projections": [item.model_dump(mode="json") for item in ordered],
        "statistics": {
            "projection_count": len(ordered),
            "text_bytes": sum(len(item.text.encode("utf-8")) for item in ordered),
        },
    }
    return EmbeddingProjectionManifest(
        **payload, artifact_sha256=sha256_json(payload)
    )


def build_embedding_generation_plan(
    *,
    projections: Sequence[EmbeddingProjection],
    provider: EmbeddingProvider,
    use_case: EmbeddingUseCase,
    max_batch_size: int = DEFAULT_BATCH_SIZE,
) -> EmbeddingGenerationPlan:
    if not 1 <= max_batch_size <= MAX_BATCH_SIZE:
        raise ValueError(f"max_batch_size must be between 1 and {MAX_BATCH_SIZE}")
    ordered = sorted(projections, key=lambda item: item.object_id)
    if not ordered:
        raise ValueError("embedding generation requires at least one projection")
    object_kinds = {item.object_kind for item in ordered}
    if len(object_kinds) != 1:
        raise ValueError("one embedding plan can contain only one object kind")
    object_kind = next(iter(object_kinds))
    ids = [item.object_id for item in ordered]
    if len(ids) != len(set(ids)):
        raise ValueError("embedding projections contain duplicate object ids")
    projection_manifest = build_embedding_projection_manifest(ordered)
    inputs = [provider.prepare_document(item, use_case) for item in ordered]
    items = [
        EmbeddingPlanItem(
            object_kind=projection.object_kind,
            object_id=projection.object_id,
            projection_sha256=projection.projection_sha256,
            provider_input_sha256=sha256_json(provider_input),
            provider_input_bytes=len(provider_input.encode("utf-8")),
            estimated_input_tokens=_estimated_tokens(provider_input),
        )
        for projection, provider_input in zip(ordered, inputs, strict=True)
    ]
    batches: list[EmbeddingPlanBatch] = []
    for start in range(0, len(items), max_batch_size):
        batch_items = items[start : start + max_batch_size]
        fingerprint = sha256_json(
            {
                "provider": provider.descriptor.model_dump(mode="json"),
                "use_case": use_case,
                "item_projection_sha256s": [item.projection_sha256 for item in batch_items],
                "provider_input_sha256s": [item.provider_input_sha256 for item in batch_items],
            }
        )
        batches.append(
            EmbeddingPlanBatch(
                batch_id=f"EMB-{fingerprint[:20]}",
                item_ids=sorted(item.object_id for item in batch_items),
                input_bytes=sum(item.provider_input_bytes for item in batch_items),
                estimated_input_tokens=sum(
                    item.estimated_input_tokens for item in batch_items
                ),
                batch_fingerprint_sha256=fingerprint,
            )
        )
    statistics = {
        "projection_count": len(items),
        "batch_count": len(batches),
        "input_bytes": sum(item.provider_input_bytes for item in items),
        "estimated_input_tokens": sum(item.estimated_input_tokens for item in items),
    }
    payload: dict[str, Any] = {
        "schema_version": EMBEDDING_PLAN_VERSION,
        "object_kind": object_kind,
        "use_case": use_case,
        "provider": provider.descriptor.model_dump(mode="json"),
        "token_estimation_method": TOKEN_ESTIMATION_METHOD,
        "max_batch_size": max_batch_size,
        "projection_manifest_sha256": projection_manifest.artifact_sha256,
        "items": [item.model_dump(mode="json") for item in items],
        "batches": [batch.model_dump(mode="json") for batch in batches],
        "statistics": statistics,
    }
    return EmbeddingGenerationPlan(**payload, plan_sha256=sha256_json(payload))


def build_embedding_index_artifact(
    *,
    plan: EmbeddingGenerationPlan,
    projections: Sequence[EmbeddingProjection],
    vectors_by_object_id: Mapping[str, Sequence[float]],
) -> EmbeddingIndexArtifact:
    ordered = sorted(projections, key=lambda item: item.object_id)
    ordered_ids = [item.object_id for item in ordered]
    if ordered_ids != [item.object_id for item in plan.items]:
        raise ValueError("embedding result projections do not match plan item ids")
    if [item.projection_sha256 for item in ordered] != [
        item.projection_sha256 for item in plan.items
    ]:
        raise ValueError("embedding result projections do not match plan SHAs")
    if sorted(vectors_by_object_id) != ordered_ids:
        raise ValueError("embedding provider result ids do not match plan")
    records = [
        EmbeddingVectorRecord(
            object_kind=projection.object_kind,
            object_id=projection.object_id,
            projection_sha256=projection.projection_sha256,
            vector=list(vectors_by_object_id[projection.object_id]),
            vector_sha256=sha256_json(list(vectors_by_object_id[projection.object_id])),
        )
        for projection in ordered
    ]
    payload: dict[str, Any] = {
        "schema_version": EMBEDDING_INDEX_VERSION,
        "plan_sha256": plan.plan_sha256,
        "object_kind": plan.object_kind,
        "use_case": plan.use_case,
        "provider": plan.provider.model_dump(mode="json"),
        "records": [item.model_dump(mode="json") for item in records],
        "statistics": {
            "record_count": len(records),
            "dimensions": plan.provider.dimensions,
        },
    }
    return EmbeddingIndexArtifact(**payload, artifact_sha256=sha256_json(payload))


class GoogleGeminiEmbeddingProvider:
    """Gemini embedding adapter with exact-per-input semantics.

    Gemini Embedding 2 aggregates multiple raw parts into one vector.  This
    adapter wraps every requested item in its own Content object and validates
    result count and dimensions so aggregation cannot silently drop records.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_GEMINI_MODEL,
        dimensions: int = DEFAULT_DIMENSIONS,
        batch_size: int = DEFAULT_BATCH_SIZE,
        transport_mode: Literal[
            "gemini_developer_multi_content", "vertex_single_content"
        ] = "gemini_developer_multi_content",
        endpoint_location: str = "global",
        client: Any | None = None,
    ) -> None:
        if not 128 <= dimensions <= 3072:
            raise ValueError("Gemini embedding dimensions must be between 128 and 3072")
        if not 1 <= batch_size <= MAX_BATCH_SIZE:
            raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size
        self.transport_mode = transport_mode
        self.endpoint_location = endpoint_location
        self._client = client

    @property
    def descriptor(self) -> EmbeddingProviderDescriptor:
        return EmbeddingProviderDescriptor(
            provider="google",
            model=self.model,
            dimensions=self.dimensions,
            provider_contract_version=(
                "google_gemini_embedding_2_exact_content_v2"
                if self.model == DEFAULT_GEMINI_MODEL
                else "google_gemini_embedding_1_task_type_v1"
            ),
            transport_mode=self.transport_mode,
            endpoint_location=self.endpoint_location,
        )

    def prepare_document(
        self, projection: EmbeddingProjection, use_case: EmbeddingUseCase
    ) -> str:
        if self.model == DEFAULT_GEMINI_MODEL:
            if use_case == "candidate_recall":
                return f"task: sentence similarity | query: {projection.text}"
            return f"title: {projection.title or 'none'} | text: {projection.text}"
        return projection.text

    def prepare_query(self, text: str, use_case: EmbeddingUseCase) -> str:
        if self.model != DEFAULT_GEMINI_MODEL:
            return text
        if use_case == "candidate_recall":
            return f"task: sentence similarity | query: {text}"
        task = {
            "question_answering": "question answering",
            "semantic_search": "search result",
        }[use_case]
        return f"task: {task} | query: {text}"

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            api_key = os.getenv("GEMINI_API_KEY") or None
            if api_key:
                self._client = genai.Client(api_key=api_key)
            elif self.transport_mode == "vertex_single_content":
                discovered = genai.Client()
                if not bool(getattr(discovered, "vertexai", False)):
                    raise ValueError("embedding plan requires a Vertex AI client")
                project = getattr(discovered._api_client, "project", None)
                self._client = genai.Client(
                    vertexai=True,
                    project=project,
                    location=self.endpoint_location,
                )
            else:
                self._client = genai.Client()
        is_vertex = bool(getattr(self._client, "vertexai", False))
        actual_location = getattr(
            getattr(self._client, "_api_client", None), "location", None
        )
        if (
            self.model == DEFAULT_GEMINI_MODEL
            and self.transport_mode == "vertex_single_content"
            and not is_vertex
        ):
            raise ValueError("embedding plan requires a Vertex AI client")
        if (
            self.model == DEFAULT_GEMINI_MODEL
            and self.transport_mode == "vertex_single_content"
            and actual_location is not None
            and actual_location != self.endpoint_location
        ):
            raise ValueError("embedding client location differs from the pinned plan")
        if (
            self.model == DEFAULT_GEMINI_MODEL
            and self.transport_mode == "gemini_developer_multi_content"
            and is_vertex
        ):
            raise ValueError(
                "embedding plan requires Gemini Developer API; Vertex AI accepts "
                "only one gemini-embedding-2 Content per sync request"
            )
        return self._client

    def embed_documents(
        self,
        projections: Sequence[EmbeddingProjection],
        use_case: EmbeddingUseCase,
    ) -> list[list[float]]:
        prepared = [self.prepare_document(item, use_case) for item in projections]
        legacy_task = "RETRIEVAL_DOCUMENT"
        return self._embed_prepared(prepared, legacy_task=legacy_task)

    def embed_document_texts(
        self,
        texts: Sequence[str],
        *,
        titles: Sequence[str] | None = None,
        use_case: EmbeddingUseCase,
    ) -> list[list[float]]:
        if titles is not None and len(titles) != len(texts):
            raise ValueError("embedding document titles must match text count")
        actual_titles = list(titles) if titles is not None else ["none"] * len(texts)
        if self.model == DEFAULT_GEMINI_MODEL:
            prepared = [
                (
                    f"task: sentence similarity | query: {text}"
                    if use_case == "candidate_recall"
                    else f"title: {title or 'none'} | text: {text}"
                )
                for text, title in zip(texts, actual_titles, strict=True)
            ]
        else:
            prepared = list(texts)
        return self._embed_prepared(prepared, legacy_task="RETRIEVAL_DOCUMENT")

    def embed_queries(
        self, texts: Sequence[str], use_case: EmbeddingUseCase
    ) -> list[list[float]]:
        prepared = [self.prepare_query(text, use_case) for text in texts]
        legacy_task = (
            "QUESTION_ANSWERING" if use_case == "question_answering" else "RETRIEVAL_QUERY"
        )
        return self._embed_prepared(prepared, legacy_task=legacy_task)

    def _embed_prepared(
        self, prepared: Sequence[str], *, legacy_task: str
    ) -> list[list[float]]:
        if not prepared:
            return []
        from google.genai import types

        vectors: list[list[float]] = []
        client = self._get_client()
        request_batch_size = (
            1
            if self.model == DEFAULT_GEMINI_MODEL
            and self.transport_mode == "vertex_single_content"
            else self.batch_size
        )
        for start in range(0, len(prepared), request_batch_size):
            batch = prepared[start : start + request_batch_size]
            if self.model == DEFAULT_GEMINI_MODEL:
                contents = [
                    types.Content(parts=[types.Part.from_text(text=text)]) for text in batch
                ]
                config = types.EmbedContentConfig(
                    output_dimensionality=self.dimensions
                )
            else:
                contents = list(batch)
                config = types.EmbedContentConfig(
                    task_type=legacy_task,
                    output_dimensionality=self.dimensions,
                )
            response = client.models.embed_content(
                model=self.model,
                contents=contents,
                config=config,
            )
            batch_vectors = [
                list(embedding.values) for embedding in (response.embeddings or [])
            ]
            if len(batch_vectors) != len(batch):
                raise ValueError(
                    "embedding provider returned an unexpected item count; "
                    "possible multi-input aggregation"
                )
            if any(len(vector) != self.dimensions for vector in batch_vectors):
                raise ValueError("embedding provider returned unexpected dimensions")
            vectors.extend(batch_vectors)
        return vectors
