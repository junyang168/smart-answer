"""Shared, provenance-bound semantic indexing primitives."""

from .embeddings import (
    EmbeddingGenerationPlan,
    EmbeddingIndexArtifact,
    EmbeddingProjection,
    EmbeddingProjectionManifest,
    EmbeddingProviderDescriptor,
    GoogleGeminiEmbeddingProvider,
    build_embedding_generation_plan,
    build_embedding_index_artifact,
    build_embedding_projection_manifest,
)
from .projections import (
    build_argument_route_embedding_projection,
    build_claim_embedding_projection,
    build_evidence_embedding_projection,
    build_viewpoint_embedding_projection,
)

__all__ = [
    "EmbeddingGenerationPlan",
    "EmbeddingIndexArtifact",
    "EmbeddingProjection",
    "EmbeddingProjectionManifest",
    "EmbeddingProviderDescriptor",
    "GoogleGeminiEmbeddingProvider",
    "build_argument_route_embedding_projection",
    "build_claim_embedding_projection",
    "build_embedding_generation_plan",
    "build_embedding_index_artifact",
    "build_embedding_projection_manifest",
    "build_evidence_embedding_projection",
    "build_viewpoint_embedding_projection",
]
