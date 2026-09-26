"""Deterministic, zero-model ownership plan between corpus freeze and grouping.

Routing is a capacity and scheduling hint. It never asserts viewpoint identity.
"""

from __future__ import annotations

import json
import math
import re
import hashlib
from dataclasses import asdict
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from backend.api.canonical_repository.viewpoint_batch_resolution import ClaimGroupingResponse
from backend.api.canonical_repository.viewpoint_foundation import (
    CLAIM_MANIFEST_VERSION,
    canonical_json,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_resolution import structured_json_request
from backend.api.canonical_repository.viewpoint_production_safety import CVP_FREEZE_VERSION
from backend.pipeline.passage_scope_attestation import validate_passage_scope_attestation
from backend.pipeline.passage_knowledge_slice import Passage


PARTITION_MANIFEST_VERSION = "wang_cvp_partition_manifest_v1"
PARTITION_POLICY_VERSION = "wang_cvp_partition_policy_v1"
DEFAULT_PARTITION_POLICY = (
    Path(__file__).resolve().parent
    / "policies"
    / "wang_cvp_partition_policy_v1.json"
)
GROUPING_PROMPT = (
    Path(__file__).resolve().parent
    / "prompts"
    / "canonical_viewpoint_claim_grouping.md"
)
GROUPING_SCHEMA_NAME = "wang_canonical_viewpoint_claim_grouping_v1"
_HAN = re.compile(r"[\u4e00-\u9fff]{3,}")
_LATIN = re.compile(r"[A-Za-z]{4,}")


def grouping_claim(raw: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "claim_id": str(raw["claim_id"]),
        "statement": str(raw["statement"]),
        "source_id": str(raw["source_id"]),
        "scripture_refs": list(raw.get("scripture_refs") or []),
    }


def grouping_payload(scope_label: str, claims: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "scope_label": scope_label,
        "claims": [grouping_claim(item) for item in claims],
    }


def grouping_request_bytes(scope_label: str, claims: Sequence[Mapping[str, Any]]) -> int:
    """Count the serialized arguments passed to the grouping client.

    This includes the prompt, pretty-printed user payload, and strict JSON
    response schema. The subscription CLI may add transport-specific framing.
    """

    prompt = GROUPING_PROMPT.read_text(encoding="utf-8")
    request = structured_json_request(
        grouping_payload(scope_label, claims),
        prompt=prompt,
        response_model=ClaimGroupingResponse,
        schema_name=GROUPING_SCHEMA_NAME,
    )
    return len(canonical_json(request).encode("utf-8"))


def load_partition_policy(path: Path = DEFAULT_PARTITION_POLICY) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if set(policy) != {
        "schema_version", "target_request_bytes", "max_request_bytes",
        "max_claims_per_partition",
    }:
        raise ValueError("partition policy fields differ")
    if policy["schema_version"] != PARTITION_POLICY_VERSION:
        raise ValueError("unsupported partition policy")
    target = int(policy["target_request_bytes"])
    ceiling = int(policy["max_request_bytes"])
    if not 0 < target < ceiling:
        raise ValueError("partition target must be below the hard ceiling")
    if int(policy["max_claims_per_partition"]) < 1:
        raise ValueError("partition Claim ceiling must be positive")
    return policy


def _content_terms(statement: str) -> set[str]:
    result = {word.casefold() for word in _LATIN.findall(statement)}
    for sequence in _HAN.findall(statement):
        for width in (3, 4):
            result.update(
                sequence[index : index + width]
                for index in range(len(sequence) - width + 1)
            )
    return result


def _topic_candidates(claims: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    by_source: dict[str, set[str]] = defaultdict(set)
    by_claim: dict[str, set[str]] = {}
    frequency: Counter[str] = Counter()
    for row in claims:
        claim_id = str(row["claim_id"])
        terms = _content_terms(str(row["statement"]))
        by_claim[claim_id] = terms
        frequency.update(terms)
        for term in terms:
            by_source[term].add(str(row["source_id"]))
    upper = max(3, math.ceil(len(claims) * 0.25))
    result: dict[str, list[str]] = {}
    for claim_id, terms in by_claim.items():
        eligible = [
            term for term in terms
            if len(by_source[term]) >= 2 and frequency[term] <= upper
        ]
        eligible.sort(
            key=lambda term: (
                -len(by_source[term]),
                frequency[term],
                -len(term),
                term,
            )
        )
        result[claim_id] = [f"topic:{term}" for term in eligible[:3]]
    return result


def _route_candidates(
    row: Mapping[str, Any],
    *,
    exegesis_units: Mapping[str, Sequence[str]],
    topics: Mapping[str, Sequence[str]],
) -> list[str]:
    claim_id = str(row["claim_id"])
    passage = [f"passage:{unit}" for unit in sorted(set(exegesis_units.get(claim_id, ())))]
    topic = list(topics.get(claim_id, ()))
    return list(dict.fromkeys(passage + topic + [f"source:{row['source_id']}"]))


def _claim_row(raw: Mapping[str, Any], route_candidates: list[str]) -> dict[str, Any]:
    return {
        **grouping_claim(raw),
        "pinned_claim_revision": int(raw["pinned_claim_revision"]),
        "claim_revision_sha256": str(raw["claim_revision_sha256"]),
        "primary_route": route_candidates[0],
        "route_candidates": route_candidates,
    }


def build_partition_manifest(
    *,
    claim_manifest: Mapping[str, Any],
    claims: Sequence[Mapping[str, Any]],
    dispositions: Sequence[Mapping[str, Any]],
    freeze: Mapping[str, Any],
    cvp_policy_sha256: str,
    partition_policy: Mapping[str, Any],
    runner_commit: str,
    exegesis_units: Mapping[str, Sequence[str]] | None = None,
    passage_role_attestation: Mapping[str, Any] | None = None,
    passage_units: Mapping[str, Sequence[Passage]] | None = None,
    source_exclusions: Sequence[str] = (),
    mode: str = "preview",
) -> dict[str, Any]:
    """Build a stable partition plan from a validated corpus snapshot.

    `claims` are current ReviewClaim projections; `dispositions` account for
    manifest Claims intentionally absent from resolution. A preview may name
    unresolved Claims, while a final plan accepts only explicit exclusions.
    """

    from backend.pipeline.viewpoint_partition_validation import validate_partition_manifest

    if mode not in {"preview", "final"}:
        raise ValueError("partition mode must be preview or final")
    if claim_manifest.get("schema_version") != CLAIM_MANIFEST_VERSION:
        raise ValueError("unsupported Claim manifest")
    manifest_body = {k: v for k, v in claim_manifest.items() if k != "manifest_sha256"}
    claim_manifest_sha = sha256_json(manifest_body)
    if claim_manifest.get("manifest_sha256") != claim_manifest_sha:
        raise ValueError("Claim manifest SHA mismatch")
    freeze_body = {k: v for k, v in freeze.items() if k != "artifact_sha256"}
    if freeze.get("artifact_sha256") != sha256_json(freeze_body):
        raise ValueError("freeze SHA mismatch")
    if freeze.get("claim_manifest_sha256") != claim_manifest_sha:
        raise ValueError("freeze Claim manifest mismatch")
    if freeze.get("cvp_policy_sha256") != cvp_policy_sha256:
        raise ValueError("freeze CVP policy mismatch")
    if mode == "final" and (
        freeze.get("schema_version") != CVP_FREEZE_VERSION
        or freeze.get("status") != "frozen"
    ):
        raise ValueError("final partition plan requires a production corpus freeze")
    if bool(passage_role_attestation) != bool(passage_units):
        raise ValueError("passage role attestation and passage units must be supplied together")
    if mode == "final" and not passage_role_attestation:
        raise ValueError("final partition plan requires reviewed Matthew passage roles")
    if partition_policy.get("schema_version") != PARTITION_POLICY_VERSION:
        raise ValueError("unsupported partition policy")
    target = int(partition_policy["target_request_bytes"])
    ceiling = int(partition_policy["max_request_bytes"])
    if not 0 < target < ceiling:
        raise ValueError("invalid partition request limits")
    max_claims = int(partition_policy["max_claims_per_partition"])
    if max_claims < 1:
        raise ValueError("invalid partition Claim limit")

    manifest_index: dict[str, dict[str, Any]] = {}
    for raw in claim_manifest.get("claims") or []:
        row = dict(raw)
        claim_id = str(row["claim_id"])
        if claim_id in manifest_index:
            raise ValueError(f"duplicate Claim in manifest: {claim_id}")
        manifest_index[claim_id] = row

    claim_index: dict[str, dict[str, Any]] = {}
    for raw in claims:
        row = dict(raw)
        claim_id = str(row["claim_id"])
        if claim_id in claim_index:
            raise ValueError(f"duplicate Claim projection: {claim_id}")
        claim_index[claim_id] = row
    disposition_index: dict[str, dict[str, Any]] = {}
    for raw in dispositions:
        row = dict(raw)
        claim_id = str(row["claim_id"])
        if claim_id in disposition_index:
            raise ValueError(f"duplicate disposition: {claim_id}")
        if not str(row.get("reason_code") or "").strip():
            raise ValueError(f"{claim_id}: disposition requires a reason code")
        disposition_index[claim_id] = row
    extra = (set(claim_index) | set(disposition_index)) - set(manifest_index)
    overlap = set(claim_index) & set(disposition_index)
    missing = set(manifest_index) - set(claim_index) - set(disposition_index)
    if extra or overlap or (missing and mode == "final"):
        raise ValueError(
            f"Claim denominator mismatch: foreign={sorted(extra)}, "
            f"overlap={sorted(overlap)}, missing={sorted(missing)}"
        )
    if mode == "preview":
        for claim_id in missing:
            disposition_index[claim_id] = {
                "claim_id": claim_id,
                "disposition": "residual",
                "reason_code": "not_yet_compiled_for_preview",
            }
    if mode == "final" and any(
        row.get("disposition") != "excluded" for row in disposition_index.values()
    ):
        raise ValueError("final partition manifest requires resolved exclusions")
    for claim_id, disposition in disposition_index.items():
        pin = manifest_index[claim_id]
        for field in ("pinned_claim_revision", "claim_revision_sha256", "source_id"):
            if field in disposition and disposition[field] != pin[field]:
                raise ValueError(f"{claim_id}: disposition differs from Claim manifest pin")
            disposition[field] = pin[field]

    for claim_id, raw in claim_index.items():
        pin = manifest_index[claim_id]
        if any(
            raw.get(field) != pin.get(field)
            for field in (
                "pinned_claim_revision", "claim_revision_sha256", "source_id"
            )
        ):
            raise ValueError(f"{claim_id}: Claim projection differs from manifest pin")
        if not str(raw.get("statement") or "").strip():
            raise ValueError(f"{claim_id}: empty statement")

    sorted_claims = [claim_index[key] for key in sorted(claim_index)]
    if passage_role_attestation and passage_units:
        admissions = validate_passage_scope_attestation(
            passage_role_attestation,
            claims=sorted_claims,
            claim_manifest_sha256=claim_manifest_sha,
            passage_units=passage_units,
        )
        attested_units = {
            claim_id: sorted({unit for row in admitted for unit in row["passage_unit_ids"]})
            for claim_id, admitted in admissions.items()
        }
        if exegesis_units is not None and {
            key: sorted(set(value)) for key, value in exegesis_units.items() if value
        } != attested_units:
            raise ValueError("passage routes differ from reviewed role attestation")
        exegesis_units = attested_units
    topics = _topic_candidates(sorted_claims)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in sorted_claims:
        routes = _route_candidates(
            raw, exegesis_units=exegesis_units or {}, topics=topics
        )
        row = _claim_row(raw, routes)
        buckets[row["primary_route"]].append(row)

    def route_order(key: str) -> tuple[int, str]:
        return (0 if key.startswith("passage:") else 1 if key.startswith("topic:") else 2, key)

    partitions: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    current_routes: list[str] = []
    current_split: list[str] = []

    def flush() -> None:
        nonlocal current, current_routes, current_split
        if not current:
            return
        partition_id = f"p{len(partitions) + 1:05d}"
        partitions.append(
            {
                "partition_id": partition_id,
                "order": len(partitions) + 1,
                "routing_reasons": sorted(set(current_routes)),
                "split_parent_routes": sorted(set(current_split)),
                "claims": current,
                "context_refs": [],
                "grouping_request_bytes": grouping_request_bytes(partition_id, current),
            }
        )
        current, current_routes, current_split = [], [], []

    for route in sorted(buckets, key=route_order):
        remaining = sorted(buckets[route], key=lambda row: row["claim_id"])
        while remaining:
            partition_id = f"p{len(partitions) + 1:05d}"
            # Prefer keeping an entire routing bucket together. Split only
            # when it cannot fit within the target; the parent route is kept.
            low, high, best = 1, min(len(remaining), max_claims - len(current)), 0
            while low <= high:
                middle = (low + high) // 2
                size = grouping_request_bytes(partition_id, current + remaining[:middle])
                if size <= target:
                    best, low = middle, middle + 1
                else:
                    high = middle - 1
            if best == 0:
                if current:
                    flush()
                    continue
                raise ValueError(f"{remaining[0]['claim_id']}: one Claim exceeds target bytes")
            if best < len(remaining) and current:
                flush()
                continue
            current.extend(remaining[:best])
            current_routes.append(route)
            if best < len(remaining):
                current_split.append(route)
            remaining = remaining[best:]
            if remaining:
                flush()
    flush()

    route_owners: dict[str, set[str]] = defaultdict(set)
    for partition in partitions:
        for row in partition["claims"]:
            route_owners[row["primary_route"]].add(partition["partition_id"])
    context_by_partition: dict[str, dict[str, dict[str, str]]] = defaultdict(dict)
    for partition in partitions:
        owner = partition["partition_id"]
        for row in partition["claims"]:
            for route in row["route_candidates"][1:]:
                if route.startswith("source:"):
                    continue
                for target_partition in sorted(route_owners.get(route, ())):
                    if target_partition != owner:
                        context_by_partition[target_partition][row["claim_id"]] = {
                            "claim_id": row["claim_id"],
                            "claim_revision_sha256": row["claim_revision_sha256"],
                            "primary_owner": owner,
                            "reason_code": "secondary_route_context",
                        }
    for partition in partitions:
        partition["context_refs"] = [
            context_by_partition[partition["partition_id"]][key]
            for key in sorted(context_by_partition[partition["partition_id"]])
        ]

    prerequisites = freeze.get("prerequisites") or {}
    body = {
        "schema_version": PARTITION_MANIFEST_VERSION,
        "mode": mode,
        "global_freeze_sha256": freeze["artifact_sha256"],
        "claim_manifest_sha256": claim_manifest_sha,
        "source_reconciliation_sha256": (prerequisites.get("source_state_reconciliation") or {}).get("sha256"),
        "independent_audit_sha256": (prerequisites.get("independent_audit") or {}).get("sha256"),
        "corpus_fingerprint_sha256": freeze["corpus_fingerprint_sha256"],
        "cvp_policy_sha256": cvp_policy_sha256,
        "partition_policy": dict(partition_policy),
        "partition_policy_sha256": sha256_json(partition_policy),
        "grouping_prompt_sha256": sha256_json(GROUPING_PROMPT.read_text(encoding="utf-8")),
        "runner_commit": runner_commit,
        "planner_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "passage_role_attestation": dict(passage_role_attestation) if passage_role_attestation else None,
        "passage_units": {
            key: [asdict(item) for item in value]
            for key, value in sorted((passage_units or {}).items())
        },
        "denominator": [manifest_index[key] for key in sorted(manifest_index)],
        "source_exclusions": [
            {"source_id": source_id, "reason_code": "source_repair_deferred_by_owner"}
            for source_id in sorted(set(source_exclusions))
        ],
        "dispositions": [disposition_index[key] for key in sorted(disposition_index)],
        "partitions": partitions,
        "execution_contract": {
            "registry_apply": "global_serial",
            "registry_context": "reload_current_before_each_partition",
            "completion": "requires_cross_partition_duplicate_review",
        },
    }
    result = body | {"artifact_sha256": sha256_json(body)}
    validate_partition_manifest(
        result,
        claim_manifest=claim_manifest,
        freeze=freeze,
        cvp_policy_sha256=cvp_policy_sha256,
    )
    return result
