"""Targeted adjudication of one-sided EvidenceStep -> Claim relations.

This runner does not re-extract a source.  It freezes the exact Claim,
EvidenceStep and source-fragment evidence for each ``evidence_only`` pair and
asks two independent model families whether that one relation is supported.
Its repair path preserves the existing Claim projection for protected or still
disputed pairs, then applies only the resulting reference-array changes through
one guarded PostgreSQL ChangeSet.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.postgres_store import (
    CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND,
    ChangeOperation,
    ChangeSetPlan,
    build_claim_evidence_active_snapshot,
    build_claim_evidence_reciprocity_guard,
    build_product_dependency_active_snapshot,
    operation_fingerprint_rows,
    record_content_sha,
    review_event_fingerprint_rows,
    sha256_json,
    stored_operation_payload,
)
from backend.pipeline.claim_evidence_reciprocity_repair import (
    AUDIT_INPUT_SCHEMA_VERSION,
    AUDIT_SCHEMA_VERSION,
    BACKUP_VERIFICATION_SCHEMA_VERSION,
    ClaimEvidenceReciprocityRepairError,
    _change_set_from_dict,
    _read_post_apply_ledger,
    seal_artifact,
    validate_sealed_artifact,
    verify_postgres_backup_dump,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.corpus_ai_review_runner import DEFAULT_TRANSCRIPT_DIRS
from backend.pipeline.knowledge_source import load_knowledge_source_document
from backend.pipeline.source_projection import project_script


PACKET_SCHEMA_VERSION = "wang_claim_evidence_pair_packets_v1"
REVIEW_SCHEMA_VERSION = "wang_claim_evidence_pair_review_v1"
CONSENSUS_SCHEMA_VERSION = "wang_claim_evidence_pair_consensus_v1"
FINAL_DECISIONS_SCHEMA_VERSION = "wang_claim_evidence_pair_final_decisions_v1"
PAIR_REPAIR_AUTHORIZATION_SCHEMA_VERSION = (
    "wang_claim_evidence_pair_repair_authorization_v1"
)
PAIR_REPAIR_PLAN_SCHEMA_VERSION = "wang_claim_evidence_pair_repair_plan_v1"

DECISION_INCLUDE = "include"
DECISION_EXCLUDE = "exclude"
DECISION_HUMAN = "needs_human"
DECISIONS = {DECISION_INCLUDE, DECISION_EXCLUDE, DECISION_HUMAN}

PROTECTED_CLAIM_STATUSES = {
    "approved",
    "human_approved",
    "human_review_required",
    "superseded",
}

REASON_CODES = {
    "direct_support",
    "partial_support",
    "context_only",
    "question_or_opposed_material",
    "unrelated",
    "insufficient_source_context",
    "ambiguous_relation",
}

REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "pair_id": {"type": "string"},
                    "decision": {
                        "type": "string",
                        "enum": sorted(DECISIONS),
                    },
                    "reason_code": {
                        "type": "string",
                        "enum": sorted(REASON_CODES),
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                    "explanation": {"type": "string"},
                },
                "required": [
                    "pair_id",
                    "decision",
                    "reason_code",
                    "confidence",
                    "explanation",
                ],
            },
        }
    },
    "required": ["decisions"],
}

SYSTEM_PROMPT = """你是王守仁教授知识库的关系审核员。你只判断一个 EvidenceStep 是否应当列入指定 Claim 的 evidence_step_ids，不重写 Claim，不判断教授是否正确。

规则：
1. 只根据给出的 Claim、候选 EvidenceStep、逐字来源片段与现有证据判断。
2. EvidenceStep 若对 Claim 的任何实质部分提供直接或部分论证，选 include；不要仅因与现有证据重复而排除。
3. 若只是背景、问题、听众发言、被教授反对的说法，或与 Claim 无关，选 exclude。
4. 来源片段不足以判断时选 needs_human，绝不猜。
5. 每个 pair_id 恰好输出一次，保持输入顺序。解释要短，并指出 EvidenceStep 与 Claim 之间的具体关系。
"""


def _json_copy(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False))


def _atomic_json_write(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _payload(endpoint: Mapping[str, Any], *, label: str) -> dict[str, Any]:
    value = ((endpoint.get("object_version") or {}).get("payload"))
    if not isinstance(value, Mapping):
        raise ClaimEvidenceReciprocityRepairError(f"{label} lacks frozen payload")
    return _json_copy(value)


def _source_identity_index(audit: Mapping[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    snapshot = audit.get("source_lineage_identity_snapshot") or {}
    rows = snapshot.get("records")
    if not isinstance(rows, list):
        raise ClaimEvidenceReciprocityRepairError(
            "audit lacks source-lineage identity records"
        )
    return {
        (str(row.get("collection") or ""), str(row.get("object_id") or "")): dict(row)
        for row in rows
        if isinstance(row, Mapping)
    }


def read_packet_source_records(
    store: PostgresKnowledgeStore,
    *,
    required: Sequence[tuple[str, str]],
) -> list[dict[str, Any]]:
    """Read exact source rows in one read-only snapshot."""

    fragment_ids = sorted({object_id for collection, object_id in required if collection == "source_fragments"})
    document_ids = sorted({object_id for collection, object_id in required if collection == "source_documents"})
    rows: list[dict[str, Any]] = []
    with store.connect() as conn, conn.cursor() as cursor:
        cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        for collection, object_ids in (
            ("source_fragments", fragment_ids),
            ("source_documents", document_ids),
        ):
            if not object_ids:
                continue
            cursor.execute(
                """SELECT object_id, revision, content_sha256, retired_at, payload
                   FROM wang_knowledge.objects
                   WHERE collection=%s AND object_id = ANY(%s)
                   ORDER BY object_id""",
                (collection, object_ids),
            )
            rows.extend(
                {
                    "collection": collection,
                    "object_id": str(row[0]),
                    "revision": int(row[1]),
                    "content_sha256": str(row[2]),
                    "retired": row[3] is not None,
                    "payload": dict(row[4]),
                }
                for row in cursor.fetchall()
            )
        conn.rollback()
    return rows


def packet_source_keys(audit: Mapping[str, Any], *, offset: int = 0, limit: int | None = None) -> list[tuple[str, str]]:
    pairs = sorted(
        (row for row in audit.get("pairs") or [] if row.get("mismatch_type") == "evidence_only"),
        key=lambda row: str(row.get("pair_id") or ""),
    )
    selected = pairs[offset : None if limit is None else offset + limit]
    result: set[tuple[str, str]] = set()
    for pair in selected:
        endpoint = pair.get("evidence_endpoint") or {}
        evidence = _payload(endpoint, label=str(pair.get("pair_id") or "pair"))
        fragment_ids = list(evidence.get("source_fragment_ids") or [])
        if evidence.get("source_fragment_id"):
            fragment_ids.append(evidence["source_fragment_id"])
        result.update(("source_fragments", str(value)) for value in fragment_ids if value)
        lineage = endpoint.get("source_lineage") or {}
        result.update(
            ("source_documents", str(row.get("object_id") or ""))
            for row in lineage.get("source_documents") or []
            if row.get("object_id")
        )
    return sorted(result)


def build_relation_packets(
    *,
    audit_artifact: Mapping[str, Any],
    frozen_input: Mapping[str, Any],
    source_records: Sequence[Mapping[str, Any]],
    offset: int = 0,
    limit: int | None = None,
) -> dict[str, Any]:
    audit = validate_sealed_artifact(
        audit_artifact, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    frozen = validate_sealed_artifact(
        frozen_input, expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION
    )
    frozen_snapshot = str((frozen.get("active_snapshot") or {}).get("snapshot_sha256") or "")
    if not frozen_snapshot or audit.get("active_snapshot_sha256") != frozen_snapshot:
        raise ClaimEvidenceReciprocityRepairError(
            "audit is not bound to the supplied frozen active snapshot"
        )

    active = {
        (str(row.get("collection") or ""), str(row.get("object_id") or "")): row
        for row in frozen.get("active_records") or []
    }
    source_identity = _source_identity_index(audit)
    source_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in source_records:
        row = dict(raw)
        key = (str(row.get("collection") or ""), str(row.get("object_id") or ""))
        expected = source_identity.get(key)
        if expected is None:
            raise ClaimEvidenceReciprocityRepairError(
                f"source row is outside frozen lineage: {key[0]}/{key[1]}"
            )
        actual_identity = {
            field: row.get(field)
            for field in ("collection", "object_id", "revision", "content_sha256", "retired")
        }
        if actual_identity != {
            field: expected.get(field)
            for field in ("collection", "object_id", "revision", "content_sha256", "retired")
        }:
            raise ClaimEvidenceReciprocityRepairError(
                f"source row drifted after freeze: {key[0]}/{key[1]}"
            )
        source_by_key[key] = row

    pairs = sorted(
        (row for row in audit.get("pairs") or [] if row.get("mismatch_type") == "evidence_only"),
        key=lambda row: str(row.get("pair_id") or ""),
    )
    selected = pairs[offset : None if limit is None else offset + limit]
    packets: list[dict[str, Any]] = []
    for pair in selected:
        pair_id = str(pair.get("pair_id") or "")
        claim_endpoint = pair.get("claim_endpoint") or {}
        evidence_endpoint = pair.get("evidence_endpoint") or {}
        claim = _payload(claim_endpoint, label=f"{pair_id}.claim")
        evidence = _payload(evidence_endpoint, label=f"{pair_id}.evidence")
        claim_id = str(pair.get("claim_id") or "")
        evidence_id = str(pair.get("evidence_step_id") or "")
        if evidence_id in {str(value) for value in claim.get("evidence_step_ids") or []}:
            raise ClaimEvidenceReciprocityRepairError(
                f"{pair_id} is no longer evidence-only"
            )

        existing_evidence = []
        for existing_id in claim.get("evidence_step_ids") or []:
            row = active.get(("evidence_steps", str(existing_id)))
            if row is None:
                raise ClaimEvidenceReciprocityRepairError(
                    f"{pair_id} references missing frozen EvidenceStep {existing_id}"
                )
            payload = dict(row.get("payload") or {})
            existing_evidence.append(
                {
                    "evidence_step_id": str(existing_id),
                    "statement": str(payload.get("statement") or ""),
                    "step_type": payload.get("step_type"),
                    "support_eligibility": payload.get("support_eligibility"),
                }
            )

        fragment_ids = list(evidence.get("source_fragment_ids") or [])
        if evidence.get("source_fragment_id"):
            fragment_ids.append(evidence["source_fragment_id"])
        fragments = []
        for fragment_id in dict.fromkeys(map(str, fragment_ids)):
            row = source_by_key.get(("source_fragments", fragment_id))
            if row is None:
                raise ClaimEvidenceReciprocityRepairError(
                    f"{pair_id} lacks frozen source fragment {fragment_id}"
                )
            payload = row.get("payload") or {}
            fragments.append(
                {
                    "fragment_id": fragment_id,
                    "source_id": payload.get("source_id"),
                    "paragraph_key": payload.get("paragraph_key"),
                    "media_time": payload.get("media_time"),
                    "verbatim_excerpt": payload.get("verbatim_excerpt"),
                    "revision": row["revision"],
                    "content_sha256": row["content_sha256"],
                }
            )
        document_ids = [
            str(row.get("object_id") or "")
            for row in (evidence_endpoint.get("source_lineage") or {}).get("source_documents") or []
            if row.get("object_id")
        ]
        documents = []
        for document_id in document_ids:
            row = source_by_key.get(("source_documents", document_id))
            if row is None:
                raise ClaimEvidenceReciprocityRepairError(
                    f"{pair_id} lacks frozen source document {document_id}"
                )
            payload = row.get("payload") or {}
            documents.append(
                {
                    "source_id": document_id,
                    "source_type": payload.get("source_type"),
                    "transcript_id": payload.get("transcript_id"),
                    "title": payload.get("title"),
                    "revision": row["revision"],
                    "content_sha256": row["content_sha256"],
                }
            )

        packets.append(
            {
                "pair_id": pair_id,
                "claim": {
                    "claim_id": claim_id,
                    "statement": claim.get("statement") or claim.get("title"),
                    "claim_type": claim.get("claim_type"),
                    "attribution": claim.get("attribution"),
                    "review_status": claim.get("review_status") or claim.get("maturity"),
                    "scripture_refs": claim.get("scripture_refs") or [],
                    "revision": claim_endpoint.get("revision"),
                    "content_sha256": claim_endpoint.get("content_sha256"),
                },
                "candidate_evidence": {
                    "evidence_step_id": evidence_id,
                    "statement": evidence.get("statement"),
                    "step_type": evidence.get("step_type"),
                    "support_eligibility": evidence.get("support_eligibility"),
                    "scripture_refs": evidence.get("scripture_refs") or [],
                    "revision": evidence_endpoint.get("revision"),
                    "content_sha256": evidence_endpoint.get("content_sha256"),
                },
                "source_documents": documents,
                "source_fragments": fragments,
                "claim_existing_evidence": existing_evidence,
            }
        )

    artifact = {
        "schema_version": PACKET_SCHEMA_VERSION,
        "audit_artifact_sha256": audit["artifact_sha256"],
        "frozen_input_artifact_sha256": frozen["artifact_sha256"],
        "active_snapshot_sha256": frozen_snapshot,
        "offset": offset,
        "pair_count": len(packets),
        "pair_ids": [row["pair_id"] for row in packets],
        "packets": packets,
    }
    return seal_artifact(artifact)


def _validate_decisions(
    response: Mapping[str, Any], *, expected_pair_ids: Sequence[str]
) -> list[dict[str, Any]]:
    rows = response.get("decisions")
    if not isinstance(rows, list):
        raise ClaimEvidenceReciprocityRepairError("review decisions must be a list")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"review decisions[{index}] must be an object"
            )
        row = {key: raw.get(key) for key in (
            "pair_id", "decision", "reason_code", "confidence", "explanation"
        )}
        if row["decision"] not in DECISIONS:
            raise ClaimEvidenceReciprocityRepairError(
                f"invalid decision for {row['pair_id']}"
            )
        if row["reason_code"] not in REASON_CODES:
            raise ClaimEvidenceReciprocityRepairError(
                f"invalid reason_code for {row['pair_id']}"
            )
        if row["confidence"] not in {"high", "medium", "low"}:
            raise ClaimEvidenceReciprocityRepairError(
                f"invalid confidence for {row['pair_id']}"
            )
        if not str(row["explanation"] or "").strip():
            raise ClaimEvidenceReciprocityRepairError(
                f"missing explanation for {row['pair_id']}"
            )
        result.append(_json_copy(row))
    actual_ids = [str(row.get("pair_id") or "") for row in result]
    if actual_ids != list(expected_pair_ids):
        raise ClaimEvidenceReciprocityRepairError(
            "review must cover every packet exactly once in input order"
        )
    return result


def review_relation_packets(
    *,
    packet_artifact: Mapping[str, Any],
    client: Any,
    reviewer_role: str,
) -> dict[str, Any]:
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    prompt = json.dumps({"relation_packets": packets["packets"]}, ensure_ascii=False)
    decisions: list[dict[str, Any]] | None = None
    last_error: ClaimEvidenceReciprocityRepairError | None = None
    previous: Mapping[str, Any] | None = None
    for _attempt in range(3):
        feedback = ""
        if previous is not None and last_error is not None:
            feedback = (
                "\n\n上一版输出未通过机械校验："
                + str(last_error)
                + "。请重新输出完整 decisions；必须逐一覆盖以下 pair_id，"
                "不得遗漏、增加或改变顺序：\n"
                + json.dumps(packets["pair_ids"], ensure_ascii=False)
            )
        response = client.generate_json(
            SYSTEM_PROMPT,
            prompt + feedback,
            REVIEW_RESPONSE_SCHEMA,
        )
        try:
            decisions = _validate_decisions(
                response, expected_pair_ids=packets["pair_ids"]
            )
            break
        except ClaimEvidenceReciprocityRepairError as exc:
            previous = response
            last_error = exc
    if decisions is None:
        raise last_error or ClaimEvidenceReciprocityRepairError(
            "review did not produce decisions"
        )
    return seal_artifact(
        {
            "schema_version": REVIEW_SCHEMA_VERSION,
            "packet_artifact_sha256": packets["artifact_sha256"],
            "reviewer_role": reviewer_role,
            "model_id": str(client.model),
            "backend": str(getattr(client, "backend", "unknown")),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "decisions": decisions,
        }
    )


def split_relation_packets(
    packet_artifact: Mapping[str, Any], *, batch_size: int
) -> list[dict[str, Any]]:
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    if batch_size <= 0:
        raise ClaimEvidenceReciprocityRepairError("batch_size must be positive")
    result = []
    for offset in range(0, len(packets["packets"]), batch_size):
        rows = deepcopy(packets["packets"][offset : offset + batch_size])
        result.append(
            seal_artifact(
                {
                    "schema_version": PACKET_SCHEMA_VERSION,
                    "parent_packet_artifact_sha256": packets["artifact_sha256"],
                    "audit_artifact_sha256": packets["audit_artifact_sha256"],
                    "frozen_input_artifact_sha256": packets[
                        "frozen_input_artifact_sha256"
                    ],
                    "active_snapshot_sha256": packets["active_snapshot_sha256"],
                    "offset": offset,
                    "pair_count": len(rows),
                    "pair_ids": [row["pair_id"] for row in rows],
                    "packets": rows,
                }
            )
        )
    return result


def build_dispute_packets(
    *,
    packet_artifact: Mapping[str, Any],
    consensus_artifact: Mapping[str, Any],
    source_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    consensus = validate_sealed_artifact(
        consensus_artifact, expected_schema_version=CONSENSUS_SCHEMA_VERSION
    )
    if consensus.get("packet_artifact_sha256") != packets["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "consensus is bound to another packet artifact"
        )
    disputed = {
        str(row["pair_id"]): row
        for row in consensus["decisions"]
        if row.get("decision") == DECISION_HUMAN
    }
    selected = [row for row in packets["packets"] if row["pair_id"] in disputed]

    expected: dict[tuple[str, str], tuple[int, str]] = {}
    for packet in selected:
        for row in packet["source_fragments"]:
            expected[("source_fragments", row["fragment_id"])] = (
                int(row["revision"]), str(row["content_sha256"])
            )
        for row in packet["source_documents"]:
            expected[("source_documents", row["source_id"])] = (
                int(row["revision"]), str(row["content_sha256"])
            )
    source_by_key = {
        (str(row.get("collection") or ""), str(row.get("object_id") or "")): dict(row)
        for row in source_records
    }
    if set(source_by_key) != set(expected):
        raise ClaimEvidenceReciprocityRepairError(
            "dispute source rows do not exactly cover the frozen packet sources"
        )
    for key, identity in expected.items():
        row = source_by_key[key]
        if row.get("retired") is True or (
            int(row.get("revision") or 0), str(row.get("content_sha256") or "")
        ) != identity:
            raise ClaimEvidenceReciprocityRepairError(
                f"dispute source row drifted: {key[0]}/{key[1]}"
            )

    contexts: dict[str, list[dict[str, Any]]] = {}
    fragments_by_source: dict[str, list[dict[str, Any]]] = {}
    for (collection, _object_id), row in source_by_key.items():
        if collection == "source_fragments":
            source_id = str((row.get("payload") or {}).get("source_id") or "")
            fragments_by_source.setdefault(source_id, []).append(row)
    for source_id, fragments in fragments_by_source.items():
        document = source_by_key.get(("source_documents", source_id))
        if document is None:
            raise ClaimEvidenceReciprocityRepairError(
                f"missing dispute source document {source_id}"
            )
        source_payload = dict(document.get("payload") or {})
        try:
            source, _raw, _path = load_knowledge_source_document(
                source_payload, list(DEFAULT_TRANSCRIPT_DIRS)
            )
        except ValueError as exc:
            # Old transcript descriptors predate ``locator_space``.  They
            # cannot be re-extracted, but this read-only adjudication may show
            # their exact byte-bound spoken projection.  Never use a changed
            # file or bypass any other source-validation failure.
            if not any(
                marker in str(exc)
                for marker in (
                    "legacy source has editorial rows but no locator_space",
                    "visual source attestation mismatch",
                )
            ):
                raise
            path = Path(str(source_payload.get("source_path") or ""))
            raw = path.read_bytes()
            expected_sha = str(source_payload.get("source_sha256") or "")
            if not expected_sha or hashlib.sha256(raw).hexdigest() != expected_sha:
                raise ClaimEvidenceReciprocityRepairError(
                    f"legacy source file drifted: {path}"
                ) from exc
            parsed = json.loads(raw)
            source = parsed if isinstance(parsed, dict) else {"script": parsed}
        rows = list(project_script(source.get("script") or []).spoken_rows)
        for fragment in fragments:
            payload = fragment.get("payload") or {}
            key = str(payload.get("paragraph_key") or "")
            source_segment_index = payload.get("source_segment_index")
            center = next(
                (
                    index
                    for index, row in enumerate(rows)
                    if str(row.get("index")) == str(source_segment_index)
                ),
                -1,
            )
            if center < 0:
                try:
                    center = int(key.removeprefix("S")) - 1
                except ValueError as exc:
                    raise ClaimEvidenceReciprocityRepairError(
                        f"invalid paragraph key for {fragment['object_id']}: {key}"
                    ) from exc
            if center < 0 or center >= len(rows):
                raise ClaimEvidenceReciprocityRepairError(
                    f"paragraph key outside source for {fragment['object_id']}: {key}"
                )
            contexts[str(fragment["object_id"])] = [
                {
                    "paragraph_key": f"S{index + 1:04d}",
                    "text": str(rows[index].get("text") or ""),
                }
                for index in range(max(0, center - 1), min(len(rows), center + 2))
            ]

    enriched = []
    for raw in selected:
        packet = deepcopy(raw)
        packet["prior_independent_reviews"] = {
            "first": deepcopy(disputed[packet["pair_id"]]["first_review"]),
            "second": deepcopy(disputed[packet["pair_id"]]["second_review"]),
        }
        for fragment in packet["source_fragments"]:
            fragment["surrounding_source"] = contexts[fragment["fragment_id"]]
        enriched.append(packet)
    return seal_artifact(
        {
            "schema_version": PACKET_SCHEMA_VERSION,
            "parent_packet_artifact_sha256": packets["artifact_sha256"],
            "prior_consensus_artifact_sha256": consensus["artifact_sha256"],
            "purpose": "model_disagreement_reconsideration_with_surrounding_source",
            "audit_artifact_sha256": packets["audit_artifact_sha256"],
            "frozen_input_artifact_sha256": packets[
                "frozen_input_artifact_sha256"
            ],
            "active_snapshot_sha256": packets["active_snapshot_sha256"],
            "offset": 0,
            "pair_count": len(enriched),
            "pair_ids": [row["pair_id"] for row in enriched],
            "packets": enriched,
        }
    )


def compile_relation_consensus(
    *,
    packet_artifact: Mapping[str, Any],
    first_review: Mapping[str, Any],
    second_review: Mapping[str, Any],
) -> dict[str, Any]:
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    first = validate_sealed_artifact(
        first_review, expected_schema_version=REVIEW_SCHEMA_VERSION
    )
    second = validate_sealed_artifact(
        second_review, expected_schema_version=REVIEW_SCHEMA_VERSION
    )
    for label, review in (("first", first), ("second", second)):
        if review.get("packet_artifact_sha256") != packets["artifact_sha256"]:
            raise ClaimEvidenceReciprocityRepairError(
                f"{label} review is bound to another packet artifact"
            )
    first_rows = {row["pair_id"]: row for row in first["decisions"]}
    second_rows = {row["pair_id"]: row for row in second["decisions"]}
    if set(first_rows) != set(packets["pair_ids"]) or set(second_rows) != set(packets["pair_ids"]):
        raise ClaimEvidenceReciprocityRepairError(
            "review pair coverage does not match packet artifact"
        )
    decisions = []
    counts = {DECISION_INCLUDE: 0, DECISION_EXCLUDE: 0, DECISION_HUMAN: 0}
    for pair_id in packets["pair_ids"]:
        left = first_rows[pair_id]
        right = second_rows[pair_id]
        if left["decision"] == right["decision"] and left["decision"] != DECISION_HUMAN:
            outcome = left["decision"]
            reason = "independent_model_consensus"
        else:
            outcome = DECISION_HUMAN
            reason = "model_disagreement_or_uncertainty"
        counts[outcome] += 1
        decisions.append(
            {
                "pair_id": pair_id,
                "decision": outcome,
                "reason_code": reason,
                "first_review": deepcopy(left),
                "second_review": deepcopy(right),
            }
        )
    return seal_artifact(
        {
            "schema_version": CONSENSUS_SCHEMA_VERSION,
            "packet_artifact_sha256": packets["artifact_sha256"],
            "first_review_artifact_sha256": first["artifact_sha256"],
            "second_review_artifact_sha256": second["artifact_sha256"],
            "counts": counts,
            "decisions": decisions,
        }
    )


def aggregate_relation_consensus(
    *,
    packet_artifact: Mapping[str, Any],
    packet_batches: Sequence[Mapping[str, Any]],
    consensus_batches: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    if len(packet_batches) != len(consensus_batches):
        raise ClaimEvidenceReciprocityRepairError(
            "packet and consensus batch counts differ"
        )
    decisions: list[dict[str, Any]] = []
    batch_roots: list[dict[str, str]] = []
    pair_ids: list[str] = []
    for index, (raw_packets, raw_consensus) in enumerate(
        zip(packet_batches, consensus_batches), start=1
    ):
        batch = validate_sealed_artifact(
            raw_packets, expected_schema_version=PACKET_SCHEMA_VERSION
        )
        consensus = validate_sealed_artifact(
            raw_consensus, expected_schema_version=CONSENSUS_SCHEMA_VERSION
        )
        if batch.get("parent_packet_artifact_sha256") != packets["artifact_sha256"]:
            raise ClaimEvidenceReciprocityRepairError(
                f"packet batch {index} is not derived from the full packet artifact"
            )
        if consensus.get("packet_artifact_sha256") != batch["artifact_sha256"]:
            raise ClaimEvidenceReciprocityRepairError(
                f"consensus batch {index} is bound to another packet batch"
            )
        batch_ids = list(batch.get("pair_ids") or [])
        decision_ids = [str(row.get("pair_id") or "") for row in consensus["decisions"]]
        if decision_ids != batch_ids:
            raise ClaimEvidenceReciprocityRepairError(
                f"consensus batch {index} pair order differs"
            )
        pair_ids.extend(batch_ids)
        decisions.extend(deepcopy(consensus["decisions"]))
        batch_roots.append(
            {
                "packet_artifact_sha256": batch["artifact_sha256"],
                "consensus_artifact_sha256": consensus["artifact_sha256"],
            }
        )
    if pair_ids != list(packets["pair_ids"]):
        raise ClaimEvidenceReciprocityRepairError(
            "consensus batches do not cover the full packet artifact exactly"
        )
    counts = {DECISION_INCLUDE: 0, DECISION_EXCLUDE: 0, DECISION_HUMAN: 0}
    for row in decisions:
        counts[str(row["decision"])] += 1
    return seal_artifact(
        {
            "schema_version": CONSENSUS_SCHEMA_VERSION,
            "packet_artifact_sha256": packets["artifact_sha256"],
            "batch_roots": batch_roots,
            "counts": counts,
            "decisions": decisions,
        }
    )


def merge_reconsidered_consensus(
    *,
    packet_artifact: Mapping[str, Any],
    initial_consensus: Mapping[str, Any],
    dispute_packets: Mapping[str, Any],
    dispute_consensus: Mapping[str, Any],
) -> dict[str, Any]:
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    initial = validate_sealed_artifact(
        initial_consensus, expected_schema_version=CONSENSUS_SCHEMA_VERSION
    )
    disputes = validate_sealed_artifact(
        dispute_packets, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    reconsidered = validate_sealed_artifact(
        dispute_consensus, expected_schema_version=CONSENSUS_SCHEMA_VERSION
    )
    if initial.get("packet_artifact_sha256") != packets["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "initial consensus is bound to another full packet artifact"
        )
    if disputes.get("parent_packet_artifact_sha256") != packets["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "dispute packets are bound to another full packet artifact"
        )
    if disputes.get("prior_consensus_artifact_sha256") != initial["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "dispute packets are bound to another initial consensus"
        )
    if reconsidered.get("packet_artifact_sha256") != disputes["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "reconsidered consensus is bound to another dispute packet artifact"
        )
    reconsidered_by_id = {
        str(row["pair_id"]): row for row in reconsidered["decisions"]
    }
    expected_disputes = [
        str(row["pair_id"])
        for row in initial["decisions"]
        if row.get("decision") == DECISION_HUMAN
    ]
    if list(disputes["pair_ids"]) != expected_disputes or set(reconsidered_by_id) != set(expected_disputes):
        raise ClaimEvidenceReciprocityRepairError(
            "reconsideration does not exactly cover initial disagreements"
        )
    decisions = []
    counts = {DECISION_INCLUDE: 0, DECISION_EXCLUDE: 0, DECISION_HUMAN: 0}
    for initial_row in initial["decisions"]:
        pair_id = str(initial_row["pair_id"])
        if initial_row.get("decision") == DECISION_HUMAN:
            row = deepcopy(reconsidered_by_id[pair_id])
            row["initial_disagreement"] = deepcopy(initial_row)
        else:
            row = deepcopy(initial_row)
        counts[str(row["decision"])] += 1
        decisions.append(row)
    return seal_artifact(
        {
            "schema_version": FINAL_DECISIONS_SCHEMA_VERSION,
            "packet_artifact_sha256": packets["artifact_sha256"],
            "initial_consensus_artifact_sha256": initial["artifact_sha256"],
            "dispute_packet_artifact_sha256": disputes["artifact_sha256"],
            "dispute_consensus_artifact_sha256": reconsidered["artifact_sha256"],
            "counts": counts,
            "decisions": decisions,
        }
    )


def _plan_identity(plan: ChangeSetPlan) -> dict[str, Any]:
    return {
        "change_set_id": plan.change_set_id,
        "fingerprint_sha256": plan.fingerprint_sha256,
        "package_id": plan.package_id,
        "source_kind": plan.source_kind,
        "source_sha256": plan.source_sha256,
        "operations_sha256": sha256_json(operation_fingerprint_rows(plan.operations)),
        "review_events_sha256": sha256_json(
            review_event_fingerprint_rows(plan.review_events)
        ),
    }


def _active_row_index(
    active_records: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in active_records:
        collection = str(raw.get("collection") or "")
        object_id = str(raw.get("object_id") or "")
        if not collection or not object_id:
            raise ClaimEvidenceReciprocityRepairError(
                "active repair record lacks collection or object_id"
            )
        key = (collection, object_id)
        if key in result:
            raise ClaimEvidenceReciprocityRepairError(
                f"active repair records repeat {collection}/{object_id}"
            )
        payload = raw.get("payload")
        if not isinstance(payload, Mapping):
            raise ClaimEvidenceReciprocityRepairError(
                f"active repair record lacks payload: {collection}/{object_id}"
            )
        result[key] = {
            "collection": collection,
            "object_id": object_id,
            "revision": int(raw["revision"]),
            "content_sha256": str(raw["content_sha256"]),
            "payload": _json_copy(payload),
        }
    return result


def compile_pair_repair_authorization(
    *,
    audit_artifact: Mapping[str, Any],
    frozen_input: Mapping[str, Any],
    packet_artifact: Mapping[str, Any],
    final_decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Turn pair reviews into one conservative, downstream-preserving policy."""

    audit = validate_sealed_artifact(
        audit_artifact, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    frozen = validate_sealed_artifact(
        frozen_input, expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION
    )
    packets = validate_sealed_artifact(
        packet_artifact, expected_schema_version=PACKET_SCHEMA_VERSION
    )
    decisions = validate_sealed_artifact(
        final_decisions, expected_schema_version=FINAL_DECISIONS_SCHEMA_VERSION
    )
    if (
        audit.get("active_snapshot_sha256")
        != frozen.get("active_snapshot", {}).get("snapshot_sha256")
        or audit.get("store_snapshot") != frozen.get("active_snapshot")
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "fresh audit and frozen active snapshot disagree"
        )
    if packets.get("active_snapshot_sha256") != audit["active_snapshot_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "pair packets were reviewed against another active pair snapshot"
        )
    if decisions.get("packet_artifact_sha256") != packets["artifact_sha256"]:
        raise ClaimEvidenceReciprocityRepairError(
            "final pair decisions are bound to another packet artifact"
        )

    packet_by_id = {str(row["pair_id"]): row for row in packets["packets"]}
    decision_by_id = {str(row["pair_id"]): row for row in decisions["decisions"]}
    evidence_pairs = {
        str(row["pair_id"]): row
        for row in audit.get("pairs") or []
        if row.get("mismatch_type") == "evidence_only"
    }
    if (
        len(packet_by_id) != len(packets["packets"])
        or len(decision_by_id) != len(decisions["decisions"])
        or set(packet_by_id) != set(evidence_pairs)
        or set(decision_by_id) != set(evidence_pairs)
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "pair reviews do not cover every current evidence-only relation exactly"
        )

    actions: list[dict[str, Any]] = []
    for pair_id, pair in sorted(evidence_pairs.items()):
        packet = packet_by_id[pair_id]
        decision = decision_by_id[pair_id]
        claim = packet.get("claim") or {}
        evidence = packet.get("candidate_evidence") or {}
        claim_endpoint = pair.get("claim_endpoint") or {}
        evidence_endpoint = pair.get("evidence_endpoint") or {}
        endpoint_claim_payload = (
            claim_endpoint.get("object_version") or {}
        ).get("payload") or {}
        endpoint_review_status = str(
            endpoint_claim_payload.get("review_status") or "candidate"
        )
        if (
            claim.get("claim_id") != pair.get("claim_id")
            or evidence.get("evidence_step_id") != pair.get("evidence_step_id")
            or int(claim.get("revision") or 0) != int(claim_endpoint.get("revision") or 0)
            or claim.get("content_sha256") != claim_endpoint.get("content_sha256")
            or int(evidence.get("revision") or 0)
            != int(evidence_endpoint.get("revision") or 0)
            or evidence.get("content_sha256")
            != evidence_endpoint.get("content_sha256")
            or str(claim.get("review_status") or "candidate")
            != endpoint_review_status
        ):
            raise ClaimEvidenceReciprocityRepairError(
                f"reviewed packet endpoint drifted: {pair_id}"
            )
        review_status = endpoint_review_status
        reviewed_decision = str(decision.get("decision") or "")
        if review_status in PROTECTED_CLAIM_STATUSES:
            effective = DECISION_EXCLUDE
            basis = f"preserve_{review_status}_claim_projection"
        elif reviewed_decision == DECISION_HUMAN:
            effective = DECISION_EXCLUDE
            basis = "preserve_claim_projection_on_persistent_model_disagreement"
        elif reviewed_decision in {DECISION_INCLUDE, DECISION_EXCLUDE}:
            effective = reviewed_decision
            basis = f"independent_model_consensus_{reviewed_decision}"
        else:
            raise ClaimEvidenceReciprocityRepairError(
                f"unsupported final decision for {pair_id}: {reviewed_decision}"
            )
        actions.append(
            {
                "pair_id": pair_id,
                "claim_id": str(pair["claim_id"]),
                "evidence_step_id": str(pair["evidence_step_id"]),
                "mismatch_type": "evidence_only",
                "claim_revision": int(claim_endpoint["revision"]),
                "claim_content_sha256": str(claim_endpoint["content_sha256"]),
                "evidence_revision": int(evidence_endpoint["revision"]),
                "evidence_content_sha256": str(
                    evidence_endpoint["content_sha256"]
                ),
                "claim_review_status": review_status,
                "reviewed_decision": reviewed_decision,
                "effective_decision": effective,
                "decision_basis": basis,
            }
        )

    for pair in sorted(
        (
            row
            for row in audit.get("pairs") or []
            if row.get("mismatch_type") == "claim_only"
        ),
        key=lambda row: str(row["pair_id"]),
    ):
        claim_endpoint = pair.get("claim_endpoint") or {}
        evidence_endpoint = pair.get("evidence_endpoint") or {}
        actions.append(
            {
                "pair_id": str(pair["pair_id"]),
                "claim_id": str(pair["claim_id"]),
                "evidence_step_id": str(pair["evidence_step_id"]),
                "mismatch_type": "claim_only",
                "claim_revision": int(claim_endpoint["revision"]),
                "claim_content_sha256": str(claim_endpoint["content_sha256"]),
                "evidence_revision": int(evidence_endpoint["revision"]),
                "evidence_content_sha256": str(
                    evidence_endpoint["content_sha256"]
                ),
                "effective_decision": DECISION_INCLUDE,
                "decision_basis": "project_existing_claim_binding_to_reverse_index",
            }
        )

    action_counts = {
        "add_claim_forward": sum(
            row["mismatch_type"] == "evidence_only"
            and row["effective_decision"] == DECISION_INCLUDE
            for row in actions
        ),
        "add_evidence_reverse": sum(
            row["mismatch_type"] == "claim_only" for row in actions
        ),
        "remove_evidence_reverse": sum(
            row["mismatch_type"] == "evidence_only"
            and row["effective_decision"] == DECISION_EXCLUDE
            for row in actions
        ),
    }
    return seal_artifact(
        {
            "schema_version": PAIR_REPAIR_AUTHORIZATION_SCHEMA_VERSION,
            "policy": "downstream_claim_projection_with_independent_pair_review_v1",
            "audit_artifact_sha256": audit["artifact_sha256"],
            "frozen_input_artifact_sha256": frozen["artifact_sha256"],
            "active_snapshot_sha256": audit["active_snapshot_sha256"],
            "packet_artifact_sha256": packets["artifact_sha256"],
            "final_decisions_artifact_sha256": decisions["artifact_sha256"],
            "reviewed_decision_counts": decisions["counts"],
            "action_counts": action_counts,
            "actions": sorted(actions, key=lambda row: str(row["pair_id"])),
        }
    )


def _expected_pair_operations(
    actions: Sequence[Mapping[str, Any]],
    active_rows: Mapping[
        tuple[str, str], tuple[str, str, int, str, Mapping[str, Any]]
    ],
) -> tuple[ChangeOperation, ...]:
    claim_additions: dict[str, set[str]] = {}
    evidence_additions: dict[str, set[str]] = {}
    evidence_removals: dict[str, set[str]] = {}
    for action in actions:
        claim_id = str(action["claim_id"])
        evidence_id = str(action["evidence_step_id"])
        if action["mismatch_type"] == "claim_only":
            evidence_additions.setdefault(evidence_id, set()).add(claim_id)
        elif action["effective_decision"] == DECISION_INCLUDE:
            claim_additions.setdefault(claim_id, set()).add(evidence_id)
        else:
            evidence_removals.setdefault(evidence_id, set()).add(claim_id)

    operations: list[ChangeOperation] = []
    targets = {
        *(('claims', object_id) for object_id in claim_additions),
        *(('evidence_steps', object_id) for object_id in evidence_additions),
        *(('evidence_steps', object_id) for object_id in evidence_removals),
    }
    for collection, object_id in sorted(targets):
        current = active_rows.get((collection, object_id))
        if current is None:
            raise ClaimEvidenceReciprocityRepairError(
                f"repair target is not active: {collection}/{object_id}"
            )
        payload = _json_copy(current[4])
        field = "evidence_step_ids" if collection == "claims" else "produced_claim_ids"
        before = payload.get(field) or []
        if not isinstance(before, list):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.{field} must be an array"
            )
        before_ids = [str(value) for value in before]
        if len(before_ids) != len(set(before_ids)) or any(not value for value in before_ids):
            raise ClaimEvidenceReciprocityRepairError(
                f"{collection}/{object_id}.{field} contains invalid references"
            )
        if collection == "claims":
            after = before_ids + sorted(claim_additions[object_id] - set(before_ids))
        else:
            after = [
                value
                for value in before_ids
                if value not in evidence_removals.get(object_id, set())
            ]
            after.extend(sorted(evidence_additions.get(object_id, set()) - set(after)))
        if after == before_ids:
            raise ClaimEvidenceReciprocityRepairError(
                f"repair action has no effect: {collection}/{object_id}"
            )
        payload[field] = after
        after_revision = current[2] + 1
        after_payload = dict(payload)
        after_payload["revision"] = after_revision
        operations.append(
            ChangeOperation(
                operation="update",
                collection=collection,
                object_id=object_id,
                before_sha256=current[3],
                after_sha256=record_content_sha(after_payload),
                before_revision=current[2],
                after_revision=after_revision,
                payload=payload,
            )
        )
    return tuple(operations)


def validate_pair_repair_authorization(
    authorization: Mapping[str, Any],
    *,
    plan: ChangeSetPlan,
    active_rows: Mapping[
        tuple[str, str], tuple[str, str, int, str, Mapping[str, Any]]
    ],
) -> dict[str, Any]:
    """Re-derive every permitted reference-array mutation at the DB boundary."""

    value = validate_sealed_artifact(
        authorization,
        expected_schema_version=PAIR_REPAIR_AUTHORIZATION_SCHEMA_VERSION,
    )
    if plan.source_kind != CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND:
        raise ClaimEvidenceReciprocityRepairError(
            "pair authorization is bound to the wrong source kind"
        )
    actions = value.get("actions")
    if not isinstance(actions, list) or len(actions) != len(
        {str(row.get("pair_id") or "") for row in actions if isinstance(row, Mapping)}
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "pair authorization has malformed or duplicate actions"
        )

    claim_pairs: set[tuple[str, str]] = set()
    evidence_pairs: set[tuple[str, str]] = set()
    for (collection, object_id), row in active_rows.items():
        payload = row[4]
        if collection == "claims":
            claim_pairs.update(
                (object_id, str(evidence_id))
                for evidence_id in payload.get("evidence_step_ids") or []
            )
        elif collection == "evidence_steps":
            evidence_pairs.update(
                (str(claim_id), object_id)
                for claim_id in payload.get("produced_claim_ids") or []
            )
    current_mismatches = claim_pairs ^ evidence_pairs
    authorized_pairs: set[tuple[str, str]] = set()
    for action in actions:
        if not isinstance(action, Mapping):
            raise ClaimEvidenceReciprocityRepairError("pair action must be an object")
        claim_id = str(action.get("claim_id") or "")
        evidence_id = str(action.get("evidence_step_id") or "")
        pair = (claim_id, evidence_id)
        claim = active_rows.get(("claims", claim_id))
        evidence = active_rows.get(("evidence_steps", evidence_id))
        if claim is None or evidence is None or pair in authorized_pairs:
            raise ClaimEvidenceReciprocityRepairError(
                f"pair action has missing or duplicate endpoints: {claim_id}/{evidence_id}"
            )
        authorized_pairs.add(pair)
        mismatch_type = (
            "claim_only" if pair in claim_pairs else "evidence_only"
        )
        if pair not in current_mismatches or action.get("mismatch_type") != mismatch_type:
            raise ClaimEvidenceReciprocityRepairError(
                f"pair action no longer matches the active graph: {claim_id}/{evidence_id}"
            )
        if (
            int(action.get("claim_revision") or 0) != claim[2]
            or action.get("claim_content_sha256") != claim[3]
            or int(action.get("evidence_revision") or 0) != evidence[2]
            or action.get("evidence_content_sha256") != evidence[3]
        ):
            raise ClaimEvidenceReciprocityRepairError(
                f"pair action endpoint identity drifted: {claim_id}/{evidence_id}"
            )
        if mismatch_type == "claim_only":
            if (
                action.get("effective_decision") != DECISION_INCLUDE
                or action.get("decision_basis")
                != "project_existing_claim_binding_to_reverse_index"
            ):
                raise ClaimEvidenceReciprocityRepairError(
                    f"claim-only action may only fill the reverse index: {action['pair_id']}"
                )
        else:
            status = str(claim[4].get("review_status") or "candidate")
            if action.get("claim_review_status") != status:
                raise ClaimEvidenceReciprocityRepairError(
                    f"pair action review status drifted: {action['pair_id']}"
                )
            if status in PROTECTED_CLAIM_STATUSES:
                expected = (DECISION_EXCLUDE, f"preserve_{status}_claim_projection")
            elif action.get("reviewed_decision") == DECISION_HUMAN:
                expected = (
                    DECISION_EXCLUDE,
                    "preserve_claim_projection_on_persistent_model_disagreement",
                )
            elif action.get("reviewed_decision") in {
                DECISION_INCLUDE,
                DECISION_EXCLUDE,
            }:
                expected = (
                    action["reviewed_decision"],
                    f"independent_model_consensus_{action['reviewed_decision']}",
                )
            else:
                raise ClaimEvidenceReciprocityRepairError(
                    f"pair action has an unsupported reviewed decision: {action['pair_id']}"
                )
            if (
                action.get("effective_decision"),
                action.get("decision_basis"),
            ) != expected:
                raise ClaimEvidenceReciprocityRepairError(
                    f"pair action violates the conservative policy: {action['pair_id']}"
                )
    if authorized_pairs != current_mismatches:
        raise ClaimEvidenceReciprocityRepairError(
            "pair authorization does not cover the full active mismatch denominator"
        )

    expected_operations = _expected_pair_operations(actions, active_rows)
    if operation_fingerprint_rows(expected_operations) != operation_fingerprint_rows(
        plan.operations
    ) or [stored_operation_payload(row) for row in expected_operations] != [
        stored_operation_payload(row) for row in plan.operations
    ]:
        raise ClaimEvidenceReciprocityRepairError(
            "pair repair ChangeSet differs from its authorized reference mutations"
        )
    if plan.review_events:
        raise ClaimEvidenceReciprocityRepairError(
            "pair repair must not manufacture review events"
        )
    return value


def build_pair_repair_plan(
    *,
    audit_artifact: Mapping[str, Any],
    frozen_input: Mapping[str, Any],
    packet_artifact: Mapping[str, Any],
    final_decisions: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one atomic preview that only changes the two relation arrays."""

    audit = validate_sealed_artifact(
        audit_artifact, expected_schema_version=AUDIT_SCHEMA_VERSION
    )
    frozen = validate_sealed_artifact(
        frozen_input, expected_schema_version=AUDIT_INPUT_SCHEMA_VERSION
    )
    authorization = compile_pair_repair_authorization(
        audit_artifact=audit,
        frozen_input=frozen,
        packet_artifact=packet_artifact,
        final_decisions=final_decisions,
    )
    indexed = _active_row_index(frozen.get("active_records") or [])
    active_rows = {
        key: (
            row["collection"],
            row["object_id"],
            row["revision"],
            row["content_sha256"],
            row["payload"],
        )
        for key, row in indexed.items()
        if key[0] in {"claims", "evidence_steps"}
    }
    operations = _expected_pair_operations(authorization["actions"], active_rows)
    source_package = {
        "schema_version": PAIR_REPAIR_PLAN_SCHEMA_VERSION,
        "audit_artifact_sha256": audit["artifact_sha256"],
        "authorization_artifact_sha256": authorization["artifact_sha256"],
        "actions_sha256": sha256_json(authorization["actions"]),
    }
    source_sha = sha256_json(source_package)
    package_id = f"CLAIM-EVIDENCE-PAIR-REPAIR-{source_sha[:20]}"
    fingerprint = sha256_json(
        {
            "planner_schema": "wang_postgres_changeset_v2",
            "source_kind": CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND,
            "source_sha256": source_sha,
            "package_id": package_id,
            "operations": operation_fingerprint_rows(operations),
            "review_events": [],
        }
    )
    plan = ChangeSetPlan(
        change_set_id=f"KCS-{fingerprint[:20]}",
        fingerprint_sha256=fingerprint,
        package_id=package_id,
        source_kind=CLAIM_EVIDENCE_PAIR_ADJUDICATION_SOURCE_KIND,
        source_sha256=source_sha,
        operations=operations,
        unchanged=0,
        ignored_keys=(),
        review_events=(),
    )

    final_rows = dict(active_rows)
    for operation in operations:
        final_rows[(operation.collection, operation.object_id)] = (
            operation.collection,
            operation.object_id,
            operation.after_revision,
            operation.after_sha256,
            stored_operation_payload(operation),
        )
    final_snapshot = build_claim_evidence_active_snapshot(final_rows.values())
    if any(
        final_snapshot["counts"][field]
        for field in (
            "claim_only_pairs",
            "evidence_only_pairs",
            "dangling_endpoints",
            "duplicate_array_references",
        )
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "pair repair preview does not produce one clean reciprocal graph"
        )

    dependency_records = [
        (
            row["object_id"],
            row["revision"],
            row["content_sha256"],
            row["payload"],
        )
        for key, row in indexed.items()
        if key[0] == "product_dependencies"
    ]
    dependency_snapshot = build_product_dependency_active_snapshot(
        dependency_records
    )
    guard = build_claim_evidence_reciprocity_guard(
        plan,
        audit["store_snapshot"],
        dependency_snapshot,
        audit["review_event_ledger_snapshot"],
        audit.get("freeze_binding"),
        audit["source_lineage_identity_snapshot"],
        authorization,
    )
    validate_pair_repair_authorization(
        authorization, plan=plan, active_rows=active_rows
    )
    return seal_artifact(
        {
            "schema_version": PAIR_REPAIR_PLAN_SCHEMA_VERSION,
            "status": "planned",
            "apply_allowed": True,
            "audit_artifact_sha256": audit["artifact_sha256"],
            "frozen_input_artifact_sha256": frozen["artifact_sha256"],
            "packet_artifact_sha256": authorization["packet_artifact_sha256"],
            "final_decisions_artifact_sha256": authorization[
                "final_decisions_artifact_sha256"
            ],
            "authorization": authorization,
            "action_counts": authorization["action_counts"],
            "changed_object_counts": {
                "claims": sum(row.collection == "claims" for row in operations),
                "evidence_steps": sum(
                    row.collection == "evidence_steps" for row in operations
                ),
                "total": len(operations),
            },
            "change_set": plan.as_dict(),
            "change_set_fingerprint_sha256": plan.fingerprint_sha256,
            "operation_manifest_sha256": sha256_json(
                operation_fingerprint_rows(operations)
            ),
            "store_guard": guard,
            "expected_final_snapshot_sha256": final_snapshot["snapshot_sha256"],
            "expected_final_counts": final_snapshot["counts"],
            "product_dependency_snapshot": dependency_snapshot,
        }
    )


def apply_pair_repair_plan(
    artifact: Mapping[str, Any],
    *,
    audit_artifact: Mapping[str, Any],
    frozen_input: Mapping[str, Any],
    packet_artifact: Mapping[str, Any],
    final_decisions: Mapping[str, Any],
    prerequisites_manifest: Mapping[str, Any],
    backup_dump: Path,
    store: Any,
    committed_receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Apply the exact preview and prove the fresh graph is reciprocal."""

    value = validate_sealed_artifact(
        artifact, expected_schema_version=PAIR_REPAIR_PLAN_SCHEMA_VERSION
    )
    rebuilt = build_pair_repair_plan(
        audit_artifact=audit_artifact,
        frozen_input=frozen_input,
        packet_artifact=packet_artifact,
        final_decisions=final_decisions,
    )
    if rebuilt != value:
        raise ClaimEvidenceReciprocityRepairError(
            "pair repair preview differs from its frozen inputs or decisions"
        )
    change_set = _change_set_from_dict(value["change_set"])
    authorization = validate_sealed_artifact(
        value["authorization"],
        expected_schema_version=PAIR_REPAIR_AUTHORIZATION_SCHEMA_VERSION,
    )
    freeze_binding = (value.get("store_guard") or {}).get(
        "expected_freeze_binding"
    )
    backup = validate_sealed_artifact(
        verify_postgres_backup_dump(backup_dump, freeze_binding=freeze_binding),
        expected_schema_version=BACKUP_VERIFICATION_SCHEMA_VERSION,
    )
    repair_metadata = {
        "audit_artifact_sha256": value["audit_artifact_sha256"],
        "plan_artifact_sha256": value["artifact_sha256"],
        "action_manifest_sha256": sha256_json(authorization["actions"]),
        "operation_manifest_sha256": value["operation_manifest_sha256"],
        "pair_adjudication_authorization_sha256": authorization[
            "artifact_sha256"
        ],
        "store_guard": value["store_guard"],
        "backup": backup,
    }
    apply_result = store.apply_plan(
        change_set,
        metadata={"claim_evidence_reciprocity_repair": repair_metadata},
        expected_claim_evidence_guard=value["store_guard"],
    )
    committed_receipt = seal_artifact(
        {
            "schema_version": "wang_claim_evidence_pair_repair_committed_receipt_v1",
            "plan_artifact_sha256": value["artifact_sha256"],
            "change_set_id": change_set.change_set_id,
            "change_set_fingerprint_sha256": change_set.fingerprint_sha256,
            "apply_status": str(apply_result.get("status") or ""),
            "backup_artifact_sha256": backup["artifact_sha256"],
        }
    )
    if committed_receipt_path is not None:
        _atomic_json_write(committed_receipt_path, committed_receipt)
    if not isinstance(prerequisites_manifest, Mapping):
        raise ClaimEvidenceReciprocityRepairError(
            "pair repair result requires its prerequisites manifest"
        )
    return build_pair_repair_readback_result(
        value,
        committed_receipt=committed_receipt,
        store=store,
        apply_result=apply_result,
    )


def build_pair_repair_readback_result(
    artifact: Mapping[str, Any],
    *,
    committed_receipt: Mapping[str, Any],
    store: Any,
    apply_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Seal a compact post-commit readback without materializing all lineage."""

    value = validate_sealed_artifact(
        artifact, expected_schema_version=PAIR_REPAIR_PLAN_SCHEMA_VERSION
    )
    receipt = validate_sealed_artifact(
        committed_receipt,
        expected_schema_version="wang_claim_evidence_pair_repair_committed_receipt_v1",
    )
    change_set = _change_set_from_dict(value["change_set"])
    authorization = validate_sealed_artifact(
        value["authorization"],
        expected_schema_version=PAIR_REPAIR_AUTHORIZATION_SCHEMA_VERSION,
    )
    ledger = _read_post_apply_ledger(store, change_set)
    counts = ledger["active_snapshot"]["counts"]
    if (
        ledger["active_snapshot"]["snapshot_sha256"]
        != value["expected_final_snapshot_sha256"]
        or ledger["product_dependency_snapshot"]
        != value["product_dependency_snapshot"]
        or ledger["review_event_ledger_snapshot"]
        != value["store_guard"]["expected_review_event_ledger_snapshot"]
        or counts["claim_only_pairs"]
        or counts["evidence_only_pairs"]
        or counts["dangling_endpoints"]
        or counts["duplicate_array_references"]
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "committed pair repair failed its fresh reciprocal graph readback"
        )
    applied_change_set = ledger.get("change_set")
    persisted_metadata = (
        applied_change_set.get("metadata")
        if isinstance(applied_change_set, Mapping)
        else None
    )
    persisted_repair = (
        persisted_metadata.get("claim_evidence_reciprocity_repair")
        if isinstance(persisted_metadata, Mapping)
        else None
    )
    backup = (
        persisted_repair.get("backup")
        if isinstance(persisted_repair, Mapping)
        else None
    )
    if not isinstance(backup, Mapping):
        raise ClaimEvidenceReciprocityRepairError(
            "committed pair repair lacks its verified backup metadata"
        )
    backup = validate_sealed_artifact(
        backup, expected_schema_version=BACKUP_VERIFICATION_SCHEMA_VERSION
    )
    repair_metadata = {
        "audit_artifact_sha256": value["audit_artifact_sha256"],
        "plan_artifact_sha256": value["artifact_sha256"],
        "action_manifest_sha256": sha256_json(authorization["actions"]),
        "operation_manifest_sha256": value["operation_manifest_sha256"],
        "pair_adjudication_authorization_sha256": authorization[
            "artifact_sha256"
        ],
        "store_guard": value["store_guard"],
        "backup": backup,
    }
    if (
        receipt.get("plan_artifact_sha256") != value["artifact_sha256"]
        or receipt.get("change_set_id") != change_set.change_set_id
        or receipt.get("change_set_fingerprint_sha256")
        != change_set.fingerprint_sha256
        or receipt.get("backup_artifact_sha256") != backup["artifact_sha256"]
        or receipt.get("apply_status") not in {"applied", "already_applied"}
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "committed receipt differs from the pair repair ledger"
        )
    if not isinstance(applied_change_set, Mapping) or any(
        (
            applied_change_set.get(field) != expected
            for field, expected in {
                "change_set_id": change_set.change_set_id,
                "fingerprint_sha256": change_set.fingerprint_sha256,
                "package_id": change_set.package_id,
                "source_kind": change_set.source_kind,
                "source_sha256": change_set.source_sha256,
                "status": "applied",
                "metadata": {
                    "claim_evidence_reciprocity_repair": repair_metadata
                },
            }.items()
        )
    ):
        raise ClaimEvidenceReciprocityRepairError(
            "committed pair repair ChangeSet ledger differs from its preview"
        )
    if len(ledger["operations"]) != len(change_set.operations):
        raise ClaimEvidenceReciprocityRepairError(
            "committed pair repair operation count differs from its preview"
        )
    for index, (observed, expected) in enumerate(
        zip(ledger["operations"], change_set.operations)
    ):
        expected_core = {
            "operation_index": index,
            "operation": expected.operation,
            "collection": expected.collection,
            "object_id": expected.object_id,
            "before_sha256": expected.before_sha256,
            "after_sha256": expected.after_sha256,
            "before_revision": expected.before_revision,
            "after_revision": expected.after_revision,
        }
        if any(observed.get(key) != expected_value for key, expected_value in expected_core.items()):
            raise ClaimEvidenceReciprocityRepairError(
                f"committed pair repair operation {index} differs from preview"
            )
        object_version = observed.get("object_version")
        if not isinstance(object_version, Mapping) or object_version != {
            "revision": expected.after_revision,
            "content_sha256": expected.after_sha256,
            "payload": stored_operation_payload(expected),
            "change_set_id": change_set.change_set_id,
        }:
            raise ClaimEvidenceReciprocityRepairError(
                f"committed pair repair ObjectVersion {index} differs from preview"
            )
    return seal_artifact(
        {
            "schema_version": "wang_claim_evidence_pair_repair_result_v1",
            "status": "verified",
            "plan_artifact_sha256": value["artifact_sha256"],
            "authorization_artifact_sha256": authorization["artifact_sha256"],
            "committed_receipt": receipt,
            "backup": backup,
            "apply_result": _json_copy(
                apply_result
                or {
                    "status": receipt["apply_status"],
                    "change_set_id": change_set.change_set_id,
                }
            ),
            "post_apply_active_snapshot": ledger["active_snapshot"],
            "post_apply_product_dependency_snapshot": ledger[
                "product_dependency_snapshot"
            ],
            "post_apply_review_event_ledger_snapshot": ledger[
                "review_event_ledger_snapshot"
            ],
            "applied_change_set": _json_copy(applied_change_set),
            "applied_operations": _json_copy(ledger["operations"]),
            "fresh_counts": counts,
            "fresh_preview_operations": 0,
        }
    )


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ClaimEvidenceReciprocityRepairError(f"{path} must contain an object")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    packets = commands.add_parser("packets")
    packets.add_argument("--audit", required=True, type=Path)
    packets.add_argument("--freeze", required=True, type=Path)
    packets.add_argument("--output", required=True, type=Path)
    packets.add_argument("--offset", type=int, default=0)
    packets.add_argument("--limit", type=int)
    packets.add_argument("--database-url")

    review = commands.add_parser("review")
    review.add_argument("--packets", required=True, type=Path)
    review.add_argument("--output", required=True, type=Path)
    review.add_argument("--reviewer", choices=("openai", "fable"), required=True)
    review.add_argument("--model")

    split = commands.add_parser("split")
    split.add_argument("--packets", required=True, type=Path)
    split.add_argument("--output-dir", required=True, type=Path)
    split.add_argument("--batch-size", type=int, default=30)

    consensus = commands.add_parser("consensus")
    consensus.add_argument("--packets", required=True, type=Path)
    consensus.add_argument("--first-review", required=True, type=Path)
    consensus.add_argument("--second-review", required=True, type=Path)
    consensus.add_argument("--output", required=True, type=Path)

    aggregate = commands.add_parser("aggregate")
    aggregate.add_argument("--packets", required=True, type=Path)
    aggregate.add_argument("--batch-dir", required=True, type=Path)
    aggregate.add_argument("--output", required=True, type=Path)

    disputes = commands.add_parser("disputes")
    disputes.add_argument("--packets", required=True, type=Path)
    disputes.add_argument("--consensus", required=True, type=Path)
    disputes.add_argument("--output", required=True, type=Path)
    disputes.add_argument("--database-url")

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--packets", required=True, type=Path)
    finalize.add_argument("--initial-consensus", required=True, type=Path)
    finalize.add_argument("--dispute-packets", required=True, type=Path)
    finalize.add_argument("--dispute-consensus", required=True, type=Path)
    finalize.add_argument("--output", required=True, type=Path)

    repair_plan = commands.add_parser("repair-plan")
    repair_plan.add_argument("--audit", required=True, type=Path)
    repair_plan.add_argument("--freeze", required=True, type=Path)
    repair_plan.add_argument("--packets", required=True, type=Path)
    repair_plan.add_argument("--final-decisions", required=True, type=Path)
    repair_plan.add_argument("--output", required=True, type=Path)

    repair_apply = commands.add_parser("repair-apply")
    repair_apply.add_argument("--plan", required=True, type=Path)
    repair_apply.add_argument("--audit", required=True, type=Path)
    repair_apply.add_argument("--freeze", required=True, type=Path)
    repair_apply.add_argument("--packets", required=True, type=Path)
    repair_apply.add_argument("--final-decisions", required=True, type=Path)
    repair_apply.add_argument("--prerequisites", required=True, type=Path)
    repair_apply.add_argument("--backup-dump", required=True, type=Path)
    repair_apply.add_argument("--committed-receipt", required=True, type=Path)
    repair_apply.add_argument("--output", required=True, type=Path)
    repair_apply.add_argument("--database-url")

    repair_readback = commands.add_parser("repair-readback")
    repair_readback.add_argument("--plan", required=True, type=Path)
    repair_readback.add_argument("--committed-receipt", required=True, type=Path)
    repair_readback.add_argument("--output", required=True, type=Path)
    repair_readback.add_argument("--database-url")

    args = parser.parse_args(argv)
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if args.command == "packets":
        audit = _load(args.audit)
        frozen = _load(args.freeze)
        required = packet_source_keys(audit, offset=args.offset, limit=args.limit)
        records = read_packet_source_records(
            PostgresKnowledgeStore(args.database_url), required=required
        )
        artifact = build_relation_packets(
            audit_artifact=audit,
            frozen_input=frozen,
            source_records=records,
            offset=args.offset,
            limit=args.limit,
        )
    elif args.command == "review":
        packet_artifact = _load(args.packets)
        if args.reviewer == "fable":
            client = ClaudeSubscriptionClient(
                model=args.model or "claude-fable-5-1",
                reasoning_effort="high",
            )
        else:
            client = CodexSubscriptionClient(
                model=args.model or "gpt-5.6-sol",
                reasoning_effort="medium",
            )
        artifact = review_relation_packets(
            packet_artifact=packet_artifact,
            client=client,
            reviewer_role=args.reviewer,
        )
    elif args.command == "consensus":
        artifact = compile_relation_consensus(
            packet_artifact=_load(args.packets),
            first_review=_load(args.first_review),
            second_review=_load(args.second_review),
        )
    elif args.command == "split":
        batches = split_relation_packets(
            _load(args.packets), batch_size=args.batch_size
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for index, artifact in enumerate(batches, start=1):
            _atomic_json_write(
                args.output_dir / f"batch-{index:03d}.packets.json", artifact
            )
        print(
            json.dumps(
                {
                    "status": "created",
                    "output_dir": str(args.output_dir),
                    "batch_count": len(batches),
                },
                ensure_ascii=False,
            )
        )
        return 0
    elif args.command == "aggregate":
        packet_paths = sorted(args.batch_dir.glob("batch-*.packets.json"))
        consensus_paths = [
            path.with_name(path.name.replace(".packets.json", ".consensus.json"))
            for path in packet_paths
        ]
        missing = [str(path) for path in consensus_paths if not path.is_file()]
        if missing:
            raise ClaimEvidenceReciprocityRepairError(
                "missing consensus batches: " + ", ".join(missing)
            )
        artifact = aggregate_relation_consensus(
            packet_artifact=_load(args.packets),
            packet_batches=[_load(path) for path in packet_paths],
            consensus_batches=[_load(path) for path in consensus_paths],
        )
    elif args.command == "disputes":
        full_packets = _load(args.packets)
        prior_consensus = _load(args.consensus)
        disputed_ids = {
            str(row.get("pair_id") or "")
            for row in prior_consensus.get("decisions") or []
            if row.get("decision") == DECISION_HUMAN
        }
        selected = [
            row for row in full_packets.get("packets") or []
            if row.get("pair_id") in disputed_ids
        ]
        required = sorted(
            {
                *(('source_fragments', fragment['fragment_id']) for packet in selected for fragment in packet['source_fragments']),
                *(('source_documents', document['source_id']) for packet in selected for document in packet['source_documents']),
            }
        )
        records = read_packet_source_records(
            PostgresKnowledgeStore(args.database_url), required=required
        )
        artifact = build_dispute_packets(
            packet_artifact=full_packets,
            consensus_artifact=prior_consensus,
            source_records=records,
        )
    elif args.command == "finalize":
        artifact = merge_reconsidered_consensus(
            packet_artifact=_load(args.packets),
            initial_consensus=_load(args.initial_consensus),
            dispute_packets=_load(args.dispute_packets),
            dispute_consensus=_load(args.dispute_consensus),
        )
    elif args.command == "repair-plan":
        artifact = build_pair_repair_plan(
            audit_artifact=_load(args.audit),
            frozen_input=_load(args.freeze),
            packet_artifact=_load(args.packets),
            final_decisions=_load(args.final_decisions),
        )
    elif args.command == "repair-apply":
        artifact = apply_pair_repair_plan(
            _load(args.plan),
            audit_artifact=_load(args.audit),
            frozen_input=_load(args.freeze),
            packet_artifact=_load(args.packets),
            final_decisions=_load(args.final_decisions),
            prerequisites_manifest=_load(args.prerequisites),
            backup_dump=args.backup_dump,
            store=PostgresKnowledgeStore(args.database_url),
            committed_receipt_path=args.committed_receipt,
        )
    else:
        artifact = build_pair_repair_readback_result(
            _load(args.plan),
            committed_receipt=_load(args.committed_receipt),
            store=PostgresKnowledgeStore(args.database_url),
        )
    _atomic_json_write(args.output, artifact)
    print(json.dumps({"status": "created", "output": str(args.output), "counts": artifact.get("counts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
