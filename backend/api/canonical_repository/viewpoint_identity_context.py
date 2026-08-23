"""Compile one bounded, source-local context expansion for identity review."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .viewpoint_foundation import sha256_json
from .viewpoint_resolution import ViewpointIdentityReviewPacket


class StrictContextModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceContextSegment(StrictContextModel):
    locator_kind: Literal["source_segment_index", "character_offset"]
    source_segment_start: int = Field(ge=0)
    source_segment_end: int = Field(ge=0)
    text: str = Field(min_length=1)
    text_sha256: str

    @model_validator(mode="after")
    def validate_segment(self) -> "SourceContextSegment":
        if self.source_segment_end < self.source_segment_start:
            raise ValueError("context segment range is reversed")
        if self.text_sha256 != hashlib.sha256(self.text.encode("utf-8")).hexdigest():
            raise ValueError("context segment text SHA mismatch")
        return self


class SourceContextWindow(StrictContextModel):
    source_id: str
    source_sha256: str
    anchor_paragraph_keys: list[str] = Field(min_length=1)
    window_before_items: int = Field(ge=0)
    window_after_items: int = Field(ge=0)
    segments: list[SourceContextSegment] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_window(self) -> "SourceContextWindow":
        if self.anchor_paragraph_keys != sorted(set(self.anchor_paragraph_keys)):
            raise ValueError("context anchors must be canonical")
        keys = [
            (item.source_segment_start, item.source_segment_end) for item in self.segments
        ]
        if keys != sorted(set(keys)):
            raise ValueError("context segments must be canonical and unique")
        return self


class ViewpointIdentityContextPacket(StrictContextModel):
    schema_version: Literal["wang_viewpoint_identity_context_packet_v1"] = (
        "wang_viewpoint_identity_context_packet_v1"
    )
    hypothesis_id: str
    parent_packet_sha256: str
    participant_claim_ids: list[str] = Field(min_length=2)
    expansion_ordinal: Literal[1] = 1
    expansion_reason: Literal[
        "unknown", "boundary_disagreement", "scope_context_insufficient"
    ]
    parent_evidence_packet: ViewpointIdentityReviewPacket
    source_context_windows: list[SourceContextWindow] = Field(min_length=1)
    context_character_count: int = Field(ge=1)
    master_data_mutations: Literal[0] = 0
    apply_allowed: Literal[False] = False
    packet_sha256: str

    @model_validator(mode="after")
    def validate_packet(self) -> "ViewpointIdentityContextPacket":
        if self.parent_packet_sha256 != self.parent_evidence_packet.packet_sha256:
            raise ValueError("context parent packet SHA mismatch")
        if self.participant_claim_ids != sorted(set(self.participant_claim_ids)):
            raise ValueError("context participants must be canonical")
        if self.participant_claim_ids != self.parent_evidence_packet.candidate.candidate_claim_ids:
            raise ValueError("context participants must match parent packet")
        source_ids = [item.source_id for item in self.source_context_windows]
        if source_ids != sorted(set(source_ids)):
            raise ValueError("context windows must be source-sorted and unique")
        claim_source_ids = {item.source_id for item in self.parent_evidence_packet.claims}
        if set(source_ids) != claim_source_ids:
            raise ValueError("context windows must cover every participant source")
        expected_characters = sum(
            len(segment.text)
            for window in self.source_context_windows
            for segment in window.segments
        )
        if self.context_character_count != expected_characters:
            raise ValueError("context character count mismatch")
        payload = self.model_dump(mode="json", exclude={"packet_sha256"})
        if self.packet_sha256 != sha256_json(payload):
            raise ValueError("context packet SHA mismatch")
        return self


def _source_rows(path: Path) -> tuple[str, list[dict[str, Any]]]:
    source_text = path.read_text(encoding="utf-8")
    if path.suffix.lower() not in {".json"}:
        rows = [
            {"start": match.start(), "end": match.end(), "text": match.group(0)}
            for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", source_text, re.S)
        ]
        if not rows:
            raise ValueError(f"source document has no paragraph content: {path}")
        return "character_offset", rows
    raw = json.loads(source_text)
    values = raw.get("script") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        raise ValueError(f"unsupported source document shape: {path}")
    rows = []
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get("index"), int):
            continue
        text = value.get("text")
        if not isinstance(text, str) or not text.strip():
            continue
        start = int(value["index"])
        end = int(value.get("end_index", start))
        rows.append({"start": start, "end": end, "text": text})
    rows.sort(key=lambda item: (item["start"], item["end"]))
    if not rows:
        raise ValueError(f"source document has no indexed content: {path}")
    return "source_segment_index", rows


def build_identity_context_packet(
    *,
    hypothesis_id: str,
    parent_packet: ViewpointIdentityReviewPacket,
    source_documents: Mapping[str, Mapping[str, Any]],
    source_fragment_indexes: Mapping[str, int],
    expansion_reason: Literal[
        "unknown", "boundary_disagreement", "scope_context_insufficient"
    ],
    window_before_items: int = 1,
    window_after_items: int = 1,
    max_context_characters: int = 120_000,
) -> ViewpointIdentityContextPacket:
    """Read exact source bytes and expand only around existing evidence anchors."""

    windows: list[SourceContextWindow] = []
    for source_id in sorted({item.source_id for item in parent_packet.claims}):
        descriptor = source_documents.get(source_id)
        if descriptor is None:
            raise ValueError(f"missing source descriptor for {source_id}")
        source_path = Path(str(descriptor.get("source_path") or ""))
        expected_sha = str(descriptor.get("source_sha256") or "")
        if not source_path.is_file():
            raise ValueError(f"source path is unavailable for {source_id}")
        actual_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        if not expected_sha or actual_sha != expected_sha:
            raise ValueError(f"source document SHA mismatch for {source_id}")
        parent_source_shas = {
            evidence.source_sha256
            for claim in parent_packet.claims
            if claim.source_id == source_id
            for evidence in claim.evidence
        }
        if parent_source_shas != {expected_sha}:
            raise ValueError(f"parent packet source binding mismatch for {source_id}")
        anchors = sorted(
            {
                str(evidence.paragraph_key)
                for claim in parent_packet.claims
                if claim.source_id == source_id
                for evidence in claim.evidence
            }
        )
        locator_kind, rows = _source_rows(source_path)
        selected_indexes: set[int] = set()
        if locator_kind == "source_segment_index":
            centers: list[int] = []
            fragment_ids = sorted(
                {
                    evidence.source_fragment_id
                    for claim in parent_packet.claims
                    if claim.source_id == source_id
                    for evidence in claim.evidence
                }
            )
            for fragment_id in fragment_ids:
                if fragment_id not in source_fragment_indexes:
                    raise ValueError(
                        f"{source_id}: missing source_segment_index for {fragment_id}"
                    )
                anchor = source_fragment_indexes[fragment_id]
                matches = [
                    index
                    for index, row in enumerate(rows)
                    if row["start"] <= anchor <= row["end"]
                ]
                if len(matches) != 1:
                    raise ValueError(
                        f"{source_id}: fragment {fragment_id} segment {anchor} resolves to "
                        f"{len(matches)} source items"
                    )
                centers.extend(matches)
        else:
            excerpts = sorted(
                {
                    evidence.verbatim_excerpt
                    for claim in parent_packet.claims
                    if claim.source_id == source_id
                    for evidence in claim.evidence
                }
            )
            centers = []
            for excerpt in excerpts:
                matches = [
                    index for index, row in enumerate(rows) if excerpt in row["text"]
                ]
                if not matches:
                    raise ValueError(
                        f"{source_id}: evidence excerpt is absent from source document"
                    )
                centers.extend(matches)
        for center in centers:
            selected_indexes.update(
                range(
                    max(0, center - window_before_items),
                    min(len(rows), center + window_after_items + 1),
                )
            )
        segments = [
            SourceContextSegment(
                locator_kind=locator_kind,
                source_segment_start=rows[index]["start"],
                source_segment_end=rows[index]["end"],
                text=rows[index]["text"],
                text_sha256=hashlib.sha256(
                    rows[index]["text"].encode("utf-8")
                ).hexdigest(),
            )
            for index in sorted(selected_indexes)
        ]
        windows.append(
            SourceContextWindow(
                source_id=source_id,
                source_sha256=expected_sha,
                anchor_paragraph_keys=anchors,
                window_before_items=window_before_items,
                window_after_items=window_after_items,
                segments=segments,
            )
        )
    context_characters = sum(
        len(segment.text) for window in windows for segment in window.segments
    )
    if context_characters > max_context_characters:
        raise ValueError(
            f"context expansion exceeds character cap: {context_characters} > "
            f"{max_context_characters}"
        )
    payload = {
        "schema_version": "wang_viewpoint_identity_context_packet_v1",
        "hypothesis_id": hypothesis_id,
        "parent_packet_sha256": parent_packet.packet_sha256,
        "participant_claim_ids": parent_packet.candidate.candidate_claim_ids,
        "expansion_ordinal": 1,
        "expansion_reason": expansion_reason,
        "parent_evidence_packet": parent_packet.model_dump(mode="json"),
        "source_context_windows": [item.model_dump(mode="json") for item in windows],
        "context_character_count": context_characters,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    payload["packet_sha256"] = sha256_json(payload)
    return ViewpointIdentityContextPacket.model_validate(payload)
