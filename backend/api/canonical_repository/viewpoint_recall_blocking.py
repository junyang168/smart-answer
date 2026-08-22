"""Deterministic recall blocking for CanonicalViewpoint identity review.

Blocking decides which claims deserve semantic comparison.  It never decides
that two claims are equivalent and never creates registry membership.
"""

from __future__ import annotations

import itertools
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from opencc import OpenCC
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..sermon_search.bible_refs import normalize_ref
from .knowledge_models import (
    ClaimRecord,
    ClaimRelationRecord,
    ViewpointClaimLinkRecord,
)
from .viewpoint_foundation import semantic_record_sha, sha256_json


RECALL_BLOCKING_VERSION = "viewpoint_recall_blocking_v1"
RECALL_ARTIFACT_VERSION = "wang_viewpoint_recall_blocking_v1"
NORMALIZATION_VERSION = "unicode_nfkc_opencc_s2t_v1"
DEFAULT_MAX_NEIGHBORS = 12
DEFAULT_MAX_BLOCK_CLAIMS = 64
INELIGIBLE_REVIEW_STATUSES = frozenset(
    {"superseded", "rejected", "retired", "withdrawn"}
)
REVIEWED_DUPLICATE_STATUSES = frozenset(
    {"approved", "human_approved", "system_approved", "ai_consensus"}
)
APPROVED_LINK_STATUSES = frozenset(
    {"approved", "human_approved", "system_approved"}
)

_traditionalizer = OpenCC("s2t")


class StrictRecallModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecallNeighbor(StrictRecallModel):
    claim_id: str
    claim_revision_sha256: str
    statement: str
    score: int = Field(ge=1)
    signals: list[str]
    shared_topic_terms: list[str] = Field(default_factory=list)
    shared_scripture_chapters: list[str] = Field(default_factory=list)
    candidate_viewpoint_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sorted_fields(self) -> "RecallNeighbor":
        for field_name in (
            "signals",
            "shared_topic_terms",
            "shared_scripture_chapters",
            "candidate_viewpoint_ids",
        ):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        return self


class RecallNeighborhood(StrictRecallModel):
    focal_claim_id: str
    focal_claim_revision_sha256: str
    focal_statement: str
    claim_role: str
    normalized_topic_terms: list[str]
    scripture_chapter_keys: list[str]
    neighbors: list[RecallNeighbor] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_neighborhood(self) -> "RecallNeighborhood":
        for field_name in ("normalized_topic_terms", "scripture_chapter_keys"):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        neighbor_ids = [item.claim_id for item in self.neighbors]
        if self.focal_claim_id in neighbor_ids:
            raise ValueError("recall neighborhood cannot contain its focal Claim")
        if neighbor_ids != sorted(set(neighbor_ids)):
            raise ValueError("recall neighbors must use canonical Claim order")
        return self


class SuppressedRecallBlock(StrictRecallModel):
    block_key: str
    signal_kind: Literal["topic_term", "scripture_chapter"]
    claim_count: int = Field(ge=1)
    reason_code: Literal["block_exceeds_claim_budget"] = "block_exceeds_claim_budget"


class KnownPositiveRecall(StrictRecallModel):
    eligible_pair_count: int = Field(ge=0)
    found_pair_count: int = Field(ge=0)
    recall: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_ratio(self) -> "KnownPositiveRecall":
        expected = (
            self.found_pair_count / self.eligible_pair_count
            if self.eligible_pair_count
            else None
        )
        if self.found_pair_count > self.eligible_pair_count or self.recall != expected:
            raise ValueError("known-positive recall does not match pair counts")
        return self


class ViewpointRecallBlockingArtifact(StrictRecallModel):
    schema_version: Literal["wang_viewpoint_recall_blocking_v1"] = (
        RECALL_ARTIFACT_VERSION
    )
    blocking_version: Literal["viewpoint_recall_blocking_v1"] = (
        RECALL_BLOCKING_VERSION
    )
    normalization_version: Literal["unicode_nfkc_opencc_s2t_v1"] = (
        NORMALIZATION_VERSION
    )
    claim_manifest_sha256: str
    max_neighbors_per_claim: int = Field(ge=1)
    max_block_claims: int = Field(ge=2)
    neighborhoods: list[RecallNeighborhood]
    suppressed_blocks: list[SuppressedRecallBlock] = Field(default_factory=list)
    uncovered_claim_ids: list[str] = Field(default_factory=list)
    source_ineligible_claim_ids: list[str] = Field(default_factory=list)
    unparsed_scripture_refs: list[str] = Field(default_factory=list)
    known_positive_recall: KnownPositiveRecall
    statistics: dict[str, int]
    artifact_sha256: str

    @model_validator(mode="after")
    def validate_artifact(self) -> "ViewpointRecallBlockingArtifact":
        focal_ids = [item.focal_claim_id for item in self.neighborhoods]
        if focal_ids != sorted(set(focal_ids)):
            raise ValueError("recall neighborhoods must cover each focal Claim once")
        for field_name in (
            "uncovered_claim_ids",
            "source_ineligible_claim_ids",
            "unparsed_scripture_refs",
        ):
            values = getattr(self, field_name)
            if values != sorted(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        if self.suppressed_blocks != sorted(
            self.suppressed_blocks, key=lambda item: item.block_key
        ):
            raise ValueError("suppressed blocks must use canonical key order")
        expected_uncovered = sorted(
            item.focal_claim_id for item in self.neighborhoods if not item.neighbors
        )
        if self.uncovered_claim_ids != expected_uncovered:
            raise ValueError("uncovered Claim ids do not match empty neighborhoods")
        directed_links = sum(len(item.neighbors) for item in self.neighborhoods)
        unique_pairs = {
            tuple(sorted((item.focal_claim_id, neighbor.claim_id)))
            for item in self.neighborhoods
            for neighbor in item.neighbors
        }
        expected_statistics = {
            "input_claim_count": len(self.neighborhoods)
            + len(self.source_ineligible_claim_ids),
            "eligible_claim_count": len(self.neighborhoods),
            "source_ineligible_claim_count": len(self.source_ineligible_claim_ids),
            "covered_claim_count": len(self.neighborhoods) - len(self.uncovered_claim_ids),
            "uncovered_claim_count": len(self.uncovered_claim_ids),
            "directed_neighbor_count": directed_links,
            "unique_candidate_pair_count": len(unique_pairs),
            "suppressed_block_count": len(self.suppressed_blocks),
        }
        if self.statistics != expected_statistics:
            raise ValueError("recall statistics do not match artifact contents")
        payload = self.model_dump(mode="json", exclude={"artifact_sha256"})
        if self.artifact_sha256 != sha256_json(payload):
            raise ValueError("recall blocking artifact SHA mismatch")
        return self


def normalize_recall_term(value: str) -> str:
    """Canonicalize a recall key without changing reader-visible source text."""

    normalized = _traditionalizer.convert(unicodedata.normalize("NFKC", value)).casefold()
    return "".join(character for character in normalized if character.isalnum())


def claim_role(claim_type: str) -> str:
    return {
        "interpretive_judgment": "passage_interpretation",
        "interpretive_method": "interpretive_method",
        "reasoning_conclusion": "theological_judgment",
        "explicit_claim": "theological_judgment",
        "application": "application",
    }.get(claim_type, f"other:{claim_type or 'unknown'}")


def _scripture_chapter_keys(
    values: Sequence[Any], unparsed: set[str]
) -> list[str]:
    keys: set[str] = set()
    for raw_value in values:
        value = raw_value if isinstance(raw_value, str) else str(raw_value)
        ref = normalize_ref(value)
        if not ref:
            if value:
                unparsed.add(value)
            continue
        end = ref.chapter_end or ref.chapter_start
        keys.update(
            f"scripture:{ref.book}.{chapter}"
            for chapter in range(ref.chapter_start, end + 1)
        )
    return sorted(keys)


def _as_claim(value: Mapping[str, Any] | ClaimRecord) -> ClaimRecord:
    return value if isinstance(value, ClaimRecord) else ClaimRecord.model_validate(value)


def _as_relation(
    value: Mapping[str, Any] | ClaimRelationRecord,
) -> ClaimRelationRecord:
    return (
        value
        if isinstance(value, ClaimRelationRecord)
        else ClaimRelationRecord.model_validate(value)
    )


def _as_link(
    value: Mapping[str, Any] | ViewpointClaimLinkRecord,
) -> ViewpointClaimLinkRecord:
    return (
        value
        if isinstance(value, ViewpointClaimLinkRecord)
        else ViewpointClaimLinkRecord.model_validate(value)
    )


def build_viewpoint_recall_blocking(
    *,
    claim_manifest: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any] | ClaimRecord],
    claim_relations: Sequence[Mapping[str, Any] | ClaimRelationRecord] = (),
    existing_links: Sequence[Mapping[str, Any] | ViewpointClaimLinkRecord] = (),
    max_neighbors_per_claim: int = DEFAULT_MAX_NEIGHBORS,
    max_block_claims: int = DEFAULT_MAX_BLOCK_CLAIMS,
) -> ViewpointRecallBlockingArtifact:
    """Build bounded, deterministic recall neighborhoods for one pinned manifest."""

    if max_neighbors_per_claim < 1 or max_block_claims < 2:
        raise ValueError("recall budgets must be positive")
    manifest_payload = dict(claim_manifest)
    manifest_sha = str(manifest_payload.pop("manifest_sha256", ""))
    if not manifest_sha or manifest_sha != sha256_json(manifest_payload):
        raise ValueError("Claim manifest SHA mismatch")
    manifest_rows = {
        str(item["claim_id"]): item for item in claim_manifest.get("claims") or []
    }
    claim_index = {
        item.claim_id: item for item in (_as_claim(raw) for raw in claims)
        if item.claim_id in manifest_rows
    }
    if set(claim_index) != set(manifest_rows):
        missing = sorted(set(manifest_rows) - set(claim_index))
        raise ValueError(f"recall blocking is missing pinned Claims: {', '.join(missing)}")
    for claim_id, row in manifest_rows.items():
        claim = claim_index[claim_id]
        if (
            int(row.get("pinned_claim_revision") or 0) != claim.revision
            or row.get("claim_revision_sha256") != semantic_record_sha(claim)
        ):
            raise ValueError(f"{claim_id}: Claim revision changed after manifest freeze")

    ineligible = sorted(
        claim_id
        for claim_id, claim in claim_index.items()
        if claim.review_status in INELIGIBLE_REVIEW_STATUSES
    )
    eligible_ids = sorted(set(claim_index) - set(ineligible))
    unparsed: set[str] = set()
    terms_by_claim: dict[str, list[str]] = {}
    scriptures_by_claim: dict[str, list[str]] = {}
    role_by_claim: dict[str, str] = {}
    source_by_claim = {
        str(item["claim_id"]): str(item.get("source_id") or "")
        for item in claim_manifest.get("claims") or []
    }
    for claim_id in eligible_ids:
        claim = claim_index[claim_id]
        raw_terms = getattr(claim, "topic_terms", []) or []
        terms_by_claim[claim_id] = sorted(
            {
                normalized
                for value in raw_terms
                if (normalized := normalize_recall_term(str(value)))
                and len(normalized) >= 2
            }
        )
        scriptures_by_claim[claim_id] = _scripture_chapter_keys(
            claim.scripture_refs, unparsed
        )
        role_by_claim[claim_id] = claim_role(claim.claim_type)

    buckets: dict[tuple[str, str], list[str]] = defaultdict(list)
    for claim_id in eligible_ids:
        for term in terms_by_claim[claim_id]:
            buckets[("topic_term", f"term:{term}")].append(claim_id)
        for scripture in scriptures_by_claim[claim_id]:
            buckets[("scripture_chapter", scripture)].append(claim_id)

    suppressed: list[SuppressedRecallBlock] = []
    block_claim_counts = {
        key: len(set(claim_ids)) for (_, key), claim_ids in buckets.items()
    }
    pair_terms: dict[tuple[str, str], set[str]] = defaultdict(set)
    pair_scriptures: dict[tuple[str, str], set[str]] = defaultdict(set)
    for (kind, key), claim_ids in sorted(buckets.items()):
        members = sorted(set(claim_ids))
        if len(members) > max_block_claims:
            suppressed.append(
                SuppressedRecallBlock(
                    block_key=key,
                    signal_kind=kind,
                    claim_count=len(members),
                )
            )
            continue
        for left, right in itertools.combinations(members, 2):
            target = pair_terms if kind == "topic_term" else pair_scriptures
            target[(left, right)].add(key.removeprefix("term:"))

    reviewed_duplicate_pairs: set[tuple[str, str]] = set()
    for raw in claim_relations:
        relation = _as_relation(raw)
        pair = tuple(sorted((relation.from_id, relation.to_id)))
        if (
            relation.relation_type == "duplicate"
            and relation.review_status in REVIEWED_DUPLICATE_STATUSES
            and set(pair).issubset(eligible_ids)
        ):
            reviewed_duplicate_pairs.add(pair)

    owner_by_claim: dict[str, set[str]] = defaultdict(set)
    for raw in existing_links:
        link = _as_link(raw)
        if (
            link.effective_state == "active"
            and link.link_type == "equivalent_full"
            and link.review_status in APPROVED_LINK_STATUSES
        ):
            owner_by_claim[link.claim_id].add(link.viewpoint_id)

    ranked_by_claim: dict[str, list[tuple[int, str, RecallNeighbor]]] = defaultdict(list)
    all_pairs = sorted(set(pair_terms) | set(pair_scriptures) | reviewed_duplicate_pairs)
    for left, right in all_pairs:
        shared_terms = sorted(pair_terms.get((left, right), set()))
        shared_scriptures = sorted(pair_scriptures.get((left, right), set()))
        duplicate = (left, right) in reviewed_duplicate_pairs
        same_role = role_by_claim[left] == role_by_claim[right]
        cross_source = source_by_claim[left] != source_by_claim[right]
        if not duplicate:
            if cross_source:
                accepted = bool(shared_terms) or (bool(shared_scriptures) and same_role)
            else:
                accepted = len(shared_terms) >= 2 or (
                    bool(shared_terms) and bool(shared_scriptures) and same_role
                )
            if not accepted:
                continue
        signals = []
        if duplicate:
            signals.append("reviewed_duplicate")
        if shared_terms:
            signals.append("shared_topic_term")
        if shared_scriptures:
            signals.append("shared_scripture_chapter")
        if same_role:
            signals.append("compatible_claim_role")
        if cross_source:
            signals.append("cross_source")
        topic_term_score = min(
            sum(
                max(
                    1,
                    9 - block_claim_counts.get(f"term:{term}", max_block_claims).bit_length(),
                )
                for term in shared_terms
            ),
            24,
        )
        score = (
            (100 if duplicate else 0)
            + topic_term_score
            + min(len(shared_scriptures), 2) * 2
            + (1 if same_role else 0)
            + (1 if cross_source else 0)
        )
        for focal, neighbor in ((left, right), (right, left)):
            ranked_by_claim[focal].append(
                (
                    score,
                    neighbor,
                    RecallNeighbor(
                        claim_id=neighbor,
                        claim_revision_sha256=semantic_record_sha(
                            claim_index[neighbor]
                        ),
                        statement=claim_index[neighbor].statement,
                        score=score,
                        signals=sorted(signals),
                        shared_topic_terms=shared_terms,
                        shared_scripture_chapters=shared_scriptures,
                        candidate_viewpoint_ids=sorted(owner_by_claim.get(neighbor, set())),
                    ),
                )
            )

    neighborhoods: list[RecallNeighborhood] = []
    selected_pairs: set[tuple[str, str]] = set()
    for claim_id in eligible_ids:
        ranked = sorted(
            ranked_by_claim.get(claim_id, []), key=lambda item: (-item[0], item[1])
        )[:max_neighbors_per_claim]
        neighbors = sorted((item[2] for item in ranked), key=lambda item: item.claim_id)
        selected_pairs.update(
            tuple(sorted((claim_id, neighbor.claim_id))) for neighbor in neighbors
        )
        neighborhoods.append(
            RecallNeighborhood(
                focal_claim_id=claim_id,
                focal_claim_revision_sha256=semantic_record_sha(claim_index[claim_id]),
                focal_statement=claim_index[claim_id].statement,
                claim_role=role_by_claim[claim_id],
                normalized_topic_terms=terms_by_claim[claim_id],
                scripture_chapter_keys=scriptures_by_claim[claim_id],
                neighbors=neighbors,
            )
        )

    found_positive_count = len(reviewed_duplicate_pairs & selected_pairs)
    known_positive = KnownPositiveRecall(
        eligible_pair_count=len(reviewed_duplicate_pairs),
        found_pair_count=found_positive_count,
        recall=(
            found_positive_count / len(reviewed_duplicate_pairs)
            if reviewed_duplicate_pairs
            else None
        ),
    )
    uncovered = sorted(
        item.focal_claim_id for item in neighborhoods if not item.neighbors
    )
    statistics = {
        "input_claim_count": len(claim_index),
        "eligible_claim_count": len(neighborhoods),
        "source_ineligible_claim_count": len(ineligible),
        "covered_claim_count": len(neighborhoods) - len(uncovered),
        "uncovered_claim_count": len(uncovered),
        "directed_neighbor_count": sum(len(item.neighbors) for item in neighborhoods),
        "unique_candidate_pair_count": len(selected_pairs),
        "suppressed_block_count": len(suppressed),
    }
    payload = {
        "schema_version": RECALL_ARTIFACT_VERSION,
        "blocking_version": RECALL_BLOCKING_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "claim_manifest_sha256": manifest_sha,
        "max_neighbors_per_claim": max_neighbors_per_claim,
        "max_block_claims": max_block_claims,
        "neighborhoods": [item.model_dump(mode="json") for item in neighborhoods],
        "suppressed_blocks": [item.model_dump(mode="json") for item in suppressed],
        "uncovered_claim_ids": uncovered,
        "source_ineligible_claim_ids": ineligible,
        "unparsed_scripture_refs": sorted(unparsed),
        "known_positive_recall": known_positive.model_dump(mode="json"),
        "statistics": statistics,
    }
    return ViewpointRecallBlockingArtifact(
        **payload,
        artifact_sha256=sha256_json(payload),
    )
