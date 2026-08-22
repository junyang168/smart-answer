"""Object-specific text projections for shared semantic indexes."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..canonical_repository.knowledge_models import (
    ArgumentRouteRevisionRecord,
    ClaimRecord,
    EvidenceStepRecord,
    SourceFragmentRecord,
    ViewpointRevisionRecord,
    evidence_fragment_ids,
)
from ..canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from .embeddings import EMBEDDING_PROJECTION_VERSION, EmbeddingProjection


PROJECTION_VERSIONS = {
    "claim": "claim_embedding_projection_v1",
    "canonical_viewpoint": "viewpoint_embedding_projection_v1",
    "argument_route": "argument_route_embedding_projection_v1",
    "evidence": "evidence_embedding_projection_v1",
}


def _dump(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _sorted_text(values: Sequence[Any]) -> str:
    return "、".join(sorted({str(value) for value in values if str(value)}))


def _projection(
    *,
    object_kind: str,
    object_id: str,
    object_revision: int,
    source_record: Mapping[str, Any],
    dependency_records: Sequence[Mapping[str, Any]] = (),
    title: str,
    lines: Sequence[str],
) -> EmbeddingProjection:
    text = "\n".join(line for line in lines if line)
    payload = {
        "schema_version": EMBEDDING_PROJECTION_VERSION,
        "projection_version": PROJECTION_VERSIONS[object_kind],
        "object_kind": object_kind,
        "object_id": object_id,
        "object_revision": object_revision,
        "source_record_sha256": semantic_record_sha(source_record),
        "dependency_record_sha256s": sorted(
            {semantic_record_sha(value) for value in dependency_records}
        ),
        "title": title,
        "text": text,
        "text_sha256": sha256_json(text),
    }
    return EmbeddingProjection(**payload, projection_sha256=sha256_json(payload))


def build_claim_embedding_projection(
    value: Mapping[str, Any] | ClaimRecord,
) -> EmbeddingProjection:
    claim = value if isinstance(value, ClaimRecord) else ClaimRecord.model_validate(value)
    data = _dump(claim)
    return _projection(
        object_kind="claim",
        object_id=claim.claim_id,
        object_revision=claim.revision,
        source_record=data,
        title=claim.statement,
        lines=[
            f"主张：{claim.statement}",
            f"主张类型：{claim.claim_type}",
            f"经文范围：{_sorted_text(claim.scripture_refs)}" if claim.scripture_refs else "",
            f"归属：{claim.attribution}" if claim.attribution else "",
        ],
    )


def build_viewpoint_embedding_projection(
    value: Mapping[str, Any] | ViewpointRevisionRecord,
) -> EmbeddingProjection:
    revision = (
        value
        if isinstance(value, ViewpointRevisionRecord)
        else ViewpointRevisionRecord.model_validate(value)
    )
    data = _dump(revision)
    signature = revision.proposition_signature
    return _projection(
        object_kind="canonical_viewpoint",
        object_id=revision.viewpoint_id,
        object_revision=revision.revision,
        source_record=data,
        title=revision.core_proposition,
        lines=[
            f"规范观点：{revision.core_proposition}",
            f"命题结构：{signature.subject}；{signature.predicate}；{signature.object}",
            f"极性：{signature.polarity}；模态：{signature.modality}",
            f"条件：{_sorted_text(signature.conditions)}" if signature.conditions else "",
            f"群体范围：{_sorted_text(signature.population_scope)}"
            if signature.population_scope
            else "",
            f"经文范围：{_sorted_text(revision.scope.scripture_scope)}"
            if revision.scope.scripture_scope
            else "",
            f"编辑别名：{_sorted_text(revision.editorial_aliases)}"
            if revision.editorial_aliases
            else "",
        ],
    )


def build_argument_route_embedding_projection(
    value: Mapping[str, Any] | ArgumentRouteRevisionRecord,
    *,
    conclusion_viewpoint_revision: Mapping[str, Any] | ViewpointRevisionRecord,
) -> EmbeddingProjection:
    revision = (
        value
        if isinstance(value, ArgumentRouteRevisionRecord)
        else ArgumentRouteRevisionRecord.model_validate(value)
    )
    data = _dump(revision)
    conclusion = (
        conclusion_viewpoint_revision
        if isinstance(conclusion_viewpoint_revision, ViewpointRevisionRecord)
        else ViewpointRevisionRecord.model_validate(conclusion_viewpoint_revision)
    )
    conclusion_data = _dump(conclusion)
    if conclusion.viewpoint_id != revision.route_signature.conclusion_viewpoint_id:
        raise ValueError("argument route conclusion viewpoint mismatch")
    if (
        conclusion.viewpoint_revision_id
        != revision.validated_against_conclusion_viewpoint_revision_id
    ):
        raise ValueError("argument route conclusion viewpoint revision mismatch")
    signature = revision.route_signature
    return _projection(
        object_kind="argument_route",
        object_id=revision.argument_route_id,
        object_revision=revision.revision,
        source_record=data,
        dependency_records=[conclusion_data],
        title=revision.route_label,
        lines=[
            f"论证路线：{revision.route_label}",
            f"前提角色：{_sorted_text(signature.premise_roles)}",
            f"推理模式：{signature.inference_pattern}",
            f"结论观点：{conclusion.core_proposition}",
        ],
    )


def build_evidence_embedding_projection(
    value: Mapping[str, Any] | EvidenceStepRecord,
    *,
    source_fragment: Mapping[str, Any] | SourceFragmentRecord | None = None,
) -> EmbeddingProjection:
    evidence = (
        value
        if isinstance(value, EvidenceStepRecord)
        else EvidenceStepRecord.model_validate(value)
    )
    data = _dump(evidence)
    fragment = None
    if source_fragment is not None:
        fragment = (
            source_fragment
            if isinstance(source_fragment, SourceFragmentRecord)
            else SourceFragmentRecord.model_validate(source_fragment)
        )
        if fragment.fragment_id not in evidence_fragment_ids(evidence):
            raise ValueError("Evidence projection source fragment is not bound to EvidenceStep")
    return _projection(
        object_kind="evidence",
        object_id=evidence.evidence_step_id,
        object_revision=evidence.revision,
        source_record=data,
        dependency_records=[_dump(fragment)] if fragment is not None else [],
        title=evidence.statement,
        lines=[
            f"证据步骤：{evidence.statement}",
            f"步骤类型：{evidence.step_type}" if evidence.step_type else "",
            f"论证角色：{evidence.discourse_role}" if evidence.discourse_role else "",
            f"经文范围：{_sorted_text(evidence.scripture_refs)}"
            if evidence.scripture_refs
            else "",
            f"来源摘录：{fragment.verbatim_excerpt}" if fragment is not None else "",
        ],
    )
