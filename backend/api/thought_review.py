from __future__ import annotations

import json
import os
import tempfile
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.api.canonical_repository.postgres_store import (
    ActiveSnapshotBlocked,
    ChangeSetConflict,
    PostgresKnowledgeStore,
    PostgresKnowledgeStoreError,
)
from backend.pipeline.editorial_draft_audit import (
    EditorialDraftAuditError,
    audit_editorial_draft,
)


router = APIRouter(prefix="/admin/thought-review", tags=["thought-review-admin"])

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CLAIMS_PATH = PROJECT_ROOT / "output" / "claim-layer" / "claims.json"
GRAPH_PATH = PROJECT_ROOT / "output" / "claim-layer" / "argument_graph.json"
COMPOSITION_PATH = PROJECT_ROOT / "output" / "claim-layer" / "composition_plan_matthew_17.json"
SHARED_KNOWLEDGE_PATH = PROJECT_ROOT / "output" / "claim-layer" / "shared_knowledge_pilot_v1.json"
REVIEW_STATE_PATH = PROJECT_ROOT / "output" / "claim-layer" / "review_state.json"
AI_REVIEW_PATH = PROJECT_ROOT / "output" / "claim-layer" / "independent_ai_review_v1.json"
AI_ADJUDICATION_PATH = PROJECT_ROOT / "output" / "claim-layer" / "ai_adjudication_v1.json"
DETAILED_EXTRACTION_DIR = PROJECT_ROOT / "output" / "claim-layer" / "detailed-extractions"
COMPOSITION_REVIEW_DIR = PROJECT_ROOT / "output" / "claim-layer" / "composition-reviews"
QA_VALIDATION_PATH = PROJECT_ROOT / "output" / "claim-layer" / "qa_validation_cases_v1.json"
QA_DIAGNOSTICS_PATH = PROJECT_ROOT / "output" / "claim-layer" / "qa_answer_diagnostics_v1.json"
ACTIVE_SNAPSHOT_ROOT = PROJECT_ROOT / "output" / "claim-layer" / "compiled"
TOPIC_STRUCTURE_ROOT = PROJECT_ROOT / "output" / "claim-layer" / "research-batches"
EDITORIAL_DRAFT_ROOT = PROJECT_ROOT / "output" / "claim-layer"

ReviewStatus = Literal["candidate", "approved", "changes_requested", "rejected"]


class ReviewUpdate(BaseModel):
    status: ReviewStatus
    note: str = ""
    reviewer: str = "同工"
    expected_revision: int | None = None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=503, detail=f"審核資料尚未準備：{path.name}") from exc


def _read_optional_json(path: Path) -> dict:
    """AI review artifacts are pipeline output; a workspace without them still works."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}


def _resolved_presentation_source(source_document: dict) -> dict:
    """Resolve sermon media at read time; knowledge records keep stable source IDs."""
    if source_document.get("source_type") != "sermon_transcript":
        return source_document
    from backend.api.canonical_repository.service import CanonicalRepositoryService

    transcript_id = str(source_document.get("transcript_id") or "").strip()
    metadata: dict = {}
    source_path = Path(str(source_document.get("source_path") or ""))
    if source_path.is_file():
        try:
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            # Published transcripts have appeared both as an object with a
            # metadata field and as a top-level segment array.  The latter is
            # still a valid source; it simply has no embedded metadata.
            metadata = payload.get("metadata") or {} if isinstance(payload, dict) else {}
        except (json.JSONDecodeError, OSError):
            pass
    catalog = CanonicalRepositoryService._sermon_catalog_record(transcript_id)
    media = CanonicalRepositoryService._sermon_media(transcript_id, metadata, catalog)
    return {
        **source_document,
        "public_url": f"/resources/sermons/{quote(transcript_id, safe='')}",
        "media": media.model_dump(mode="json"),
    }


def _editorial_drafts() -> list[dict]:
    """Load editor-authored manuscript drafts without treating them as claims.

    Draft manifests live beside generated manuscript artifacts.  A manifest
    binds a draft to a composition decision; the candidate workspace can then
    expose the draft without inventing a second, private product-plan ID.
    """
    drafts: list[dict] = []
    for manifest_path in sorted(EDITORIAL_DRAFT_ROOT.glob("**/editorial-draft-manifest.json")):
        manifest = _read_optional_json(manifest_path)
        for item in manifest.get("drafts", []):
            draft_id = str(item.get("draft_id") or "").strip()
            candidate_id = str(item.get("candidate_id") or "").strip()
            decision_id = str(item.get("decision_id") or "").strip()
            relative_path = str(item.get("relative_path") or "").strip()
            if not draft_id or (not candidate_id and not decision_id) or not relative_path:
                continue
            draft_path = (manifest_path.parent / relative_path).resolve()
            try:
                draft_path.relative_to(EDITORIAL_DRAFT_ROOT.resolve())
            except ValueError:
                continue
            if not draft_path.is_file():
                continue
            drafts.append(
                {
                    "draft_id": draft_id,
                    "candidate_id": candidate_id,
                    "decision_id": decision_id,
                    "title": item.get("title") or draft_path.stem,
                    "passage": str(item.get("passage") or "").strip(),
                    "status": item.get("status") or "editorial_draft",
                    "status_label": item.get("status_label") or "編輯初稿可審閱",
                    "draft_path": draft_path,
                    "manifest_path": manifest_path.resolve(),
                    "presentation_package_path": item.get("presentation_package_path"),
                    "audit_config": item.get("audit_config") or {},
                }
            )
    return drafts


def _resolved_source_presentations(decision: dict, source_documents: dict[str, dict]) -> list[dict]:
    """Attach current media URLs to stable, composition-owned clip ranges."""
    presentations = []
    for presentation in decision.get("source_presentations", []) or []:
        source_document = source_documents.get(presentation.get("source_id")) or {}
        presentations.append(
            {
                **presentation,
                "source": _resolved_presentation_source(source_document) if source_document else None,
            }
        )
    return presentations


def _draft_presentation_payload(draft: dict, shared: dict) -> dict:
    """Load the exact knowledge package used to compose a draft when declared.

    Active snapshots intentionally omit some presentation metadata.  A draft
    may therefore bind to its authoring package explicitly instead of silently
    deriving a second listening structure from the current database state.
    """
    relative_path = str(draft.get("presentation_package_path") or "").strip()
    if not relative_path:
        return shared
    package_path = (draft["manifest_path"].parent / relative_path).resolve()
    try:
        package_path.relative_to(EDITORIAL_DRAFT_ROOT.resolve())
    except ValueError:
        return shared
    return _read_optional_json(package_path) or shared


def editorial_draft_data(draft_id: str) -> dict:
    """Return one Markdown editorial draft and its composition destination."""
    draft = next((item for item in _editorial_drafts() if item["draft_id"] == draft_id), None)
    if not draft:
        raise HTTPException(status_code=404, detail="找不到這份編輯初稿。")

    shared = _shared_payload()
    candidate_id = draft.get("candidate_id") or None
    passage = str(draft.get("passage") or "").strip()
    decision_title = ""
    for plan in shared.get("product_plans", []):
        if candidate_id and str(plan.get("plan_id") or "") == candidate_id:
            break
        for decision in plan.get("decisions", []) or []:
            if str(decision.get("decision_id") or "") != draft["decision_id"]:
                continue
            candidate_id = plan.get("plan_id")
            passage = str(decision.get("passage") or "").strip()
            decision_title = str(
                decision.get("section_title")
                or decision.get("decision")
                or decision.get("passage")
                or ""
            ).strip()
            break
        if candidate_id:
            break

    presentation_payload = _draft_presentation_payload(draft, shared)
    presentation_plan = next(
        (
            plan
            for plan in presentation_payload.get("product_plans", []) or []
            if str(plan.get("plan_id") or "") == str(candidate_id or "")
        ),
        None,
    )
    presentation_decisions = {
        str(item.get("decision_id") or ""): item
        for item in (presentation_plan or {}).get("decisions", []) or []
    }
    presentation_sources = {
        str(item.get("source_id") or ""): item
        for item in presentation_payload.get("source_documents", []) or []
    }
    decision_media_sections = []
    for section in draft.get("audit_config", {}).get("decision_sections", []) or []:
        section_decision = presentation_decisions.get(str(section.get("decision_id") or "")) or {}
        presentations = _resolved_source_presentations(section_decision, presentation_sources)
        if not presentations:
            continue
        decision_media_sections.append(
            {
                "decision_id": section.get("decision_id"),
                "markdown_heading": section.get("markdown_heading"),
                "passage": section_decision.get("passage"),
                "section_title": section_decision.get("section_title"),
                "source_presentations": presentations,
                "source_presentation_summary": section_decision.get("source_presentation_summary"),
            }
        )

    audit = None
    audit_error = ""
    if draft.get("audit_config"):
        try:
            # This audit is deterministic and inexpensive.  Running it at
            # read time prevents the UI from displaying a stale result after
            # an editor changes either the Markdown or the knowledge snapshot.
            audit = audit_editorial_draft(draft["manifest_path"], draft_id)
        except EditorialDraftAuditError as exc:
            audit_error = str(exc)

    return {
        "draft_id": draft["draft_id"],
        "decision_id": draft["decision_id"],
        "candidate_id": candidate_id,
        "title": draft["title"],
        "status": draft["status"],
        "status_label": draft["status_label"],
        "passage": passage,
        "decision_title": decision_title,
        "markdown": draft["draft_path"].read_text(encoding="utf-8"),
        "decision_media_sections": decision_media_sections,
        "audit": audit,
        "audit_error": audit_error,
    }


def _postgres_store() -> PostgresKnowledgeStore | None:
    """Return the authoring authority only when explicitly configured."""
    database_url = os.getenv("KNOWLEDGE_DATABASE_URL")
    if not database_url:
        return None
    try:
        return PostgresKnowledgeStore(database_url)
    except PostgresKnowledgeStoreError:
        return None


def _merge_rows(base_rows: list[dict], database_rows: list[dict], id_key: str) -> list[dict]:
    """Keep legacy display metadata while PostgreSQL fields remain authoritative."""
    base = {str(item.get(id_key)): item for item in base_rows if item.get(id_key)}
    merged = []
    for row in database_rows:
        object_id = str(row.get(id_key) or "")
        value = {**base.get(object_id, {}), **row}
        if id_key == "claim_id":
            value.setdefault("title", value.get("statement", object_id))
            value.setdefault("scripture_refs", [])
            value.setdefault("lectures", [])
            value.setdefault("recurrence", len(value.get("occurrences") or []) or 1)
            value.setdefault("opposes", None)
        elif id_key == "question_id":
            value.setdefault("question", value.get("text", ""))
        merged.append(value)
    return merged


def _shared_payload() -> dict:
    """Read the PostgreSQL authoring store, with JSON only as a dev fallback.

    ``shared_knowledge_pilot_v1.json`` is a rebuildable candidate exchange
    artifact, not the active snapshot and not the production authoring
    authority.  When PostgreSQL is configured it may enrich display-only
    legacy fields, but its absence must never prevent the review workspace
    from loading.
    """
    store = _postgres_store()
    if not store:
        return _read_json(SHARED_KNOWLEDGE_PATH)

    base = _read_optional_json(SHARED_KNOWLEDGE_PATH)
    try:
        # The pilot package is only optional display enrichment.  Filtering by
        # its package_id would hide newer PostgreSQL records and make the
        # review workspace appear to contain only the original experiment.
        database = store.compile_package()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL 編輯主庫目前無法讀取，請檢查知識庫服務。",
        ) from exc
    collection_ids = {
        "source_documents": "source_id",
        "source_fragments": "fragment_id",
        "questions": "question_id",
        "observations": "observation_id",
        "claims": "claim_id",
        "topic_nodes": "topic_id",
        "evidence_steps": "evidence_step_id",
        "knowledge_relations": "relation_id",
        "claim_relations": "claim_relation_id",
        "claim_relation_constraints": "constraint_id",
        "position_nodes": "position_id",
        "knowledge_routes": "route_id",
        "product_dependencies": "dependency_id",
        "impact_events": "impact_event_id",
        "cross_source_syntheses": "synthesis_id",
        "editorial_checks": "check_id",
        "tensions": "tension_id",
    }
    result = dict(base)
    for key, id_key in collection_ids.items():
        result[key] = _merge_rows(base.get(key, []), database.get(key, []), id_key)
    base_plans = {str(item.get("plan_id")): item for item in base.get("product_plans", [])}
    result["product_plans"] = []
    for plan in database.get("product_plans", []):
        plan_id = str(plan.get("plan_id") or "")
        old = base_plans.get(plan_id, {})
        old_decisions = {
            str(item.get("decision_id")): item for item in old.get("decisions", [])
        }
        row = {**old, **plan}
        row["decisions"] = [
            {**old_decisions.get(str(item.get("decision_id")), {}), **item}
            for item in plan.get("decisions", [])
        ]
        result["product_plans"].append(row)
    database_summary = database.get("summary", {})
    database_counts = database_summary.get("counts", {})
    # The review workspace predates the PostgreSQL schema and its metric cards
    # intentionally use shorter, reader-facing names.  Preserve that API while
    # exposing the complete database counts as well.
    display_counts = {
        **database_counts,
        "sources": database_counts.get("source_documents", 0),
        "fragments": database_counts.get("source_fragments", 0),
        "relations": (
            database_counts.get("knowledge_relations", 0)
            + database_counts.get("claim_relations", 0)
        ),
        "cross_source_syntheses": database_counts.get("editorial_syntheses", 0),
    }
    result["summary"] = {**database_summary, "counts": display_counts}
    result["authoring_authority"] = "postgresql"
    return result


def _authoring_status() -> dict:
    store = _postgres_store()
    if not store:
        return {"backend": "json_fallback", "database_connected": False, "active_snapshot": None}
    try:
        status = store.status()
    except Exception as exc:
        return {
            "backend": "json_fallback",
            "database_connected": False,
            "error": str(exc),
            "active_snapshot": None,
        }
    active = _read_optional_json(ACTIVE_SNAPSHOT_ROOT / "active.json")
    return {
        "backend": "postgresql",
        "database_connected": True,
        "objects": status.get("objects", {}),
        "review_counts": status.get("review_counts", {}),
        "latest_change_set": status.get("latest_change_set"),
        "active_snapshot": active or None,
    }


def _sha256(path: Path) -> str | None:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def _qa_diagnostics_stale(artifact: dict) -> bool:
    """Diagnostics judged one specific answer against one specific knowledge package.

    Either input moving invalidates the verdict, so both hashes are checked.
    """
    source = artifact.get("source") or {}
    for recorded_key, path in (
        ("knowledge_sha256", SHARED_KNOWLEDGE_PATH),
        ("qa_sha256", QA_VALIDATION_PATH),
    ):
        recorded = source.get(recorded_key)
        current = _sha256(path)
        if recorded and current and recorded != current:
            return True
    return False


def _qa_human_required_case_ids() -> set[str]:
    artifact = _read_optional_json(QA_DIAGNOSTICS_PATH)
    if not artifact or _qa_diagnostics_stale(artifact):
        return set()
    return {
        item.get("case_id")
        for item in artifact.get("outcomes", [])
        if item.get("status") == "human_diagnostic_required"
    }


def _qa_diagnostics_for_case(case_id: str) -> dict:
    artifact = _read_optional_json(QA_DIAGNOSTICS_PATH)
    if not artifact:
        return {"available": False, "stale": False}
    review = next(
        (
            item
            for item in (artifact.get("claude_review") or {}).get("case_reviews", [])
            if item.get("case_id") == case_id
        ),
        None,
    )
    review_issues = {
        item.get("issue_id"): item
        for item in (review or {}).get("issues", [])
        if item.get("issue_id")
    }
    outcomes = [
        {
            **item,
            "answer_excerpt": (review_issues.get(item.get("issue_id")) or {}).get(
                "answer_excerpt"
            ),
            "recommended_action": (review_issues.get(item.get("issue_id")) or {}).get(
                "recommended_action"
            ),
        }
        for item in artifact.get("outcomes", [])
        if item.get("case_id") == case_id
    ]
    repairs = [item for item in artifact.get("repair_queue", []) if item.get("case_id") == case_id]
    return {
        "available": True,
        "stale": _qa_diagnostics_stale(artifact),
        "generated_at": artifact.get("generated_at"),
        "models": artifact.get("models", {}),
        "review": review,
        "outcomes": outcomes,
        "repairs": repairs,
        "human_required": any(
            item.get("status") == "human_diagnostic_required" for item in outcomes
        ),
    }


def _ai_review_index() -> dict[str, dict]:
    """Join the Claude review with the OpenAI adjudication, keyed by claim."""
    index: dict[str, dict] = {}

    artifact_pairs = [(AI_REVIEW_PATH, AI_ADJUDICATION_PATH)]
    artifact_pairs.extend(
        (
            review_path,
            review_path.with_name(
                review_path.name.replace(".independent-review.json", ".adjudication.json")
            ),
        )
        for review_path in sorted(DETAILED_EXTRACTION_DIR.glob("*.independent-review.json"))
    )
    for review_path, adjudication_path in artifact_pairs:
        review = _read_optional_json(review_path)
        if not review:
            continue
        adjudication = _read_optional_json(adjudication_path)
        outcomes = {item["claim_id"]: item for item in adjudication.get("results", [])}
        generated_at = (review.get("reviewer") or {}).get("generated_at")
        for item in review.get("claim_reviews", []):
            claim_id = item.get("claim_id")
            if not claim_id:
                continue
            outcome = outcomes.get(claim_id)
            adjudicated = None
            if outcome:
                openai = outcome.get("openai") or {}
                reconsideration = outcome.get("claude_reconsideration") or {}
                adjudicated = {
                    "status": outcome.get("status"),
                    "openai_decision": openai.get("decision"),
                    "openai_rationale": openai.get("rationale", ""),
                    "reconsideration_decision": reconsideration.get("decision"),
                    "reconsideration_rationale": reconsideration.get("rationale", ""),
                    "structural_notes": (openai.get("patch") or {}).get("structural_notes", []),
                    "approval_status": outcome.get("approval_status", "not_human_approved"),
                }
            # Later artifacts must not silently replace an earlier verdict for
            # the same claim; that would hide one of two disagreeing reviews.
            if claim_id in index:
                index[claim_id].setdefault("duplicate_review_sources", []).append(str(review_path))
                continue
            index[claim_id] = {
                "decision": item.get("decision"),
                "routing_status": item.get("routing_status"),
                "spot_check_selected": bool(item.get("spot_check_selected")),
                "issues": item.get("issues", []),
                "rationale": item.get("rationale", ""),
                "confidence": item.get("confidence", ""),
                "human_review_reason": item.get("human_review_reason", ""),
                "reviewed_at": generated_at,
                "adjudication": adjudicated,
            }
    return index


def _composition_ai_review_index() -> tuple[dict[str, dict], dict[str, dict]]:
    """Return decision-level reviews and plan-level argument-layer assessments."""
    decisions: dict[str, dict] = {}
    plans: dict[str, dict] = {}
    for review_path in sorted(COMPOSITION_REVIEW_DIR.glob("*.independent-review.json")):
        review = _read_optional_json(review_path)
        if not review:
            continue
        plan_id = review_path.name.removesuffix(".independent-review.json")
        adjudication = _read_optional_json(
            review_path.with_name(f"{plan_id}.adjudication.json")
        )
        outcomes = {
            item.get("decision_id"): item.get("status")
            for item in adjudication.get("outcomes", [])
        }
        openai = {
            item.get("decision_id"): item
            for item in (adjudication.get("openai_adjudication") or {}).get("adjudications", [])
        }
        reconsiderations = {
            item.get("decision_id"): item
            for item in (adjudication.get("claude_reconsideration") or {}).get("reconsiderations", [])
        }
        plans[plan_id] = {
            **(review.get("plan_assessment") or {}),
            "reviewed_at": (review.get("reviewer") or {}).get("generated_at"),
            "summary_counts": adjudication.get("summary", {}),
        }
        for item in review.get("decision_reviews", []):
            decision_id = item.get("decision_id")
            if not decision_id:
                continue
            decisions[decision_id] = {
                **item,
                "outcome": outcomes.get(decision_id, "pass"),
                "openai": openai.get(decision_id),
                "claude_reconsideration": reconsiderations.get(decision_id),
                "reviewed_at": (review.get("reviewer") or {}).get("generated_at"),
            }
    return decisions, plans


def _attention_for(
    ai_review: dict | None,
    review: dict,
    eligible_evidence: int | None = None,
    candidate_evidence: int = 0,
) -> tuple[str, str]:
    """Decide whether this claim still needs a person, and say why.

    Anything the two models settled between themselves stays out of the human
    queue.  Unrun automation is reported as pending automation, not as work a
    person must do.  Only irreducible ambiguity, model disagreement and sampled
    spot checks enter the human queue.
    """
    if not ai_review:
        attention, reason = (
            "pending_ai_review",
            "尚未執行獨立 AI 複審；這是待執行的自動流程，不是人工審核任務。",
        )
    elif ai_review["decision"] == "pass":
        if ai_review["spot_check_selected"]:
            attention, reason = (
                "human_spot_check",
                "隨機抽查：AI 未發現問題，抽樣核對 AI 複審本身是否可靠。",
            )
        else:
            attention, reason = (
                "ai_cleared",
                "Claude 依完整逐字稿複核，未發現來源忠實度問題。",
            )
    elif not ai_review["adjudication"]:
        attention, reason = "pending_ai", "Claude 提出意見，尚待第二模型仲裁。"
    else:
        status = ai_review["adjudication"]["status"]
        if status == "auto_applied":
            attention, reason = "ai_cleared", "兩個模型一致，修正已寫入候選層。"
        elif status == "withdrawn":
            attention, reason = (
                "ai_cleared",
                "第二模型反駁後 Claude 撤回意見，候選維持原樣。",
            )
        elif status == "human_confirmation_required":
            attention, reason = (
                "human_required",
                ai_review["human_review_reason"] or "Claude 判定來源本身無法裁定，需人工確認。",
            )
        elif status == "human_disagreement_required":
            attention, reason = "human_required", "兩個模型持續分歧，需人工裁決這一項。"
        else:
            attention, reason = "human_required", "AI 複審狀態不明，請人工確認。"
    if attention == "ai_cleared" and eligible_evidence == 0:
        attention, reason = (
            "pending_evidence_review",
            (
                f"AI 已完成主張複審；目前有 {candidate_evidence} 項可定位來源等待證據資格審核。"
                if candidate_evidence
                else "AI 已完成主張複審，但尚無合格來源；应先执行来源修复流程。"
            ),
        )
    if attention in {"human_required", "human_spot_check"} and review["status"] != "candidate":
        return "resolved", reason
    return attention, reason


ELIGIBLE_SUPPORT = {"eligible", "eligible_with_label"}


def _eligible_evidence_count(
    claim: dict,
    package_claim: dict | None,
    shared_payload: dict,
) -> int | None:
    """How many anchors may still support this claim, or None when unknowable here."""
    steps = shared_payload.get("evidence_steps")
    excluded = set(
        (package_claim or {}).get("ai_adjudication", {}).get("excluded_evidence_step_ids", [])
    )
    # Recompute from the claim's own evidence, the way the publication gate in
    # build_active_snapshot does.  The cached eligible list is only a snapshot of
    # an earlier build and does not follow later eligibility changes; claims
    # imported after the pilot never carried it at all.
    step_ids = (package_claim or claim).get("evidence_step_ids")
    if step_ids is not None and steps is not None:
        by_id = {item["evidence_step_id"]: item for item in steps}
        return sum(
            1
            for step_id in step_ids
            if step_id not in excluded
            and by_id.get(step_id, {}).get("support_eligibility", "eligible") in ELIGIBLE_SUPPORT
        )
    if package_claim and "eligible_evidence_step_ids" in package_claim:
        return len(package_claim["eligible_evidence_step_ids"])
    group_id = claim.get("group_id")
    if not group_id or steps is None:
        return None
    return sum(
        1
        for step in steps
        if group_id in step.get("claim_group_ids", [step.get("claim_group_id")])
        and step["evidence_step_id"] not in excluded
        and step.get("support_eligibility", "eligible") in ELIGIBLE_SUPPORT
    )


def _candidate_evidence_count(claim: dict, shared_payload: dict) -> int:
    evidence_ids = set(claim.get("evidence_step_ids") or [])
    return sum(
        1
        for step in shared_payload.get("evidence_steps", [])
        if step.get("evidence_step_id") in evidence_ids
        and step.get("support_eligibility") in {"eligible_candidate", "candidate"}
    )


def _attention_counts(items: list[dict]) -> dict[str, int]:
    result = {
        key: 0
        for key in (
            "human_required",
            "human_spot_check",
            "pending_ai_review",
            "pending_evidence_review",
            "pending_ai",
            "ai_cleared",
            "resolved",
        )
    }
    for item in items:
        result[item["attention"]] = result.get(item["attention"], 0) + 1
    return result


def _empty_state() -> dict:
    return {
        "schema_version": "wang_thought_review_state_v1",
        "claims": {},
        "syntheses": {},
        "composition_decisions": {},
    }


def _read_state() -> dict:
    if not REVIEW_STATE_PATH.exists():
        return _empty_state()
    state = _read_json(REVIEW_STATE_PATH)
    state.setdefault("claims", {})
    state.setdefault("syntheses", {})
    state.setdefault("composition_decisions", {})
    return state


def _write_state(state: dict) -> None:
    REVIEW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix="review-state-", suffix=".json", dir=REVIEW_STATE_PATH.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, REVIEW_STATE_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _review_for(item: dict, saved: dict | None) -> dict:
    if item.get("review_status", "candidate") != "candidate" or item.get("reviewed_at"):
        return {
            "status": item.get("review_status", "candidate"),
            "note": item.get("review_note", ""),
            "reviewer": item.get("reviewed_by", ""),
            "reviewed_at": item.get("reviewed_at"),
            "revision": item.get("revision"),
        }
    return saved or {
        "status": item.get("review_status", "candidate"),
        "note": "",
        "reviewer": "",
        "reviewed_at": None,
        "revision": item.get("revision"),
    }


def _status_counts(items: list[dict]) -> dict[str, int]:
    result = {key: 0 for key in ("candidate", "approved", "changes_requested", "rejected")}
    for item in items:
        result[item["review"]["status"]] = result.get(item["review"]["status"], 0) + 1
    return result


def workspace_data() -> dict:
    shared_payload = _shared_payload()
    state = _read_state()
    ai_reviews = _ai_review_index()
    composition_ai_reviews, composition_plan_assessments = _composition_ai_review_index()

    claims = []
    # The package holds the AI-corrected candidate; claims.json keeps the
    # untouched extraction.  Reviewers must judge what the products will use.
    for claim in shared_payload.get("claims", []):
        claim_id = claim["claim_id"]
        review = _review_for(claim, state["claims"].get(claim_id))
        ai_review = ai_reviews.get(claim_id)
        attention, attention_reason = _attention_for(
            ai_review,
            review,
            _eligible_evidence_count(claim, claim, shared_payload),
            _candidate_evidence_count(claim, shared_payload),
        )
        claims.append(
            {
                "claim_id": claim_id,
                "title": claim["title"],
                "claim_type": claim.get("claim_type", "主张"),
                "scripture_refs": claim.get("scripture_refs", []),
                "lectures": claim.get("lectures", []),
                "recurrence": claim.get("recurrence", 0),
                "cross_lecture": claim.get("cross_lecture"),
                "review": review,
                "ai_review": ai_review,
                "attention": attention,
                "attention_reason": attention_reason,
            }
        )

    product_plans = shared_payload.get("product_plans") or [_read_json(COMPOSITION_PATH)]
    plans = []
    decisions = []
    for plan in product_plans:
        plan_decisions = []
        for decision in plan.get("decisions", []):
            reviewed = {
                **decision,
                "plan_id": plan.get("plan_id"),
                "plan_title": plan.get("title"),
                "axis": plan.get("axis", "scripture"),
                "review": _review_for(
                    decision, state["composition_decisions"].get(decision["decision_id"])
                ),
                "ai_review": composition_ai_reviews.get(decision["decision_id"]),
            }
            plan_decisions.append(reviewed)
            decisions.append(reviewed)
        plans.append(
            {
                "plan_id": plan.get("plan_id"),
                "title": plan.get("title"),
                "description": plan.get("description"),
                "axis": plan.get("axis", "scripture"),
                "product_type": plan.get("product_type", "scripture_exposition"),
                "decision_ids": [item["decision_id"] for item in plan_decisions],
                "counts": _status_counts(plan_decisions),
                "ai_assessment": composition_plan_assessments.get(plan.get("plan_id")),
            }
        )

    syntheses = []
    claim_titles = {claim["claim_id"]: claim["title"] for claim in claims}
    for synthesis in shared_payload.get("cross_source_syntheses", []):
        syntheses.append(
            {
                **synthesis,
                "claim_titles": [claim_titles[item] for item in synthesis.get("claim_ids", []) if item in claim_titles],
                "review": _review_for(synthesis, state["syntheses"].get(synthesis["synthesis_id"])),
            }
        )

    open_questions = [
        question
        for question in shared_payload.get("questions", [])
        if question.get("answer_state") in {"partially_answered", "unanswered"}
    ]
    summary_counts = shared_payload.get("summary", {}).get("counts", {})
    qa_plan = _read_optional_json(QA_VALIDATION_PATH)
    qa_cases = qa_plan.get("cases", [])
    qa_counts = {
        state: sum(1 for item in qa_cases if item.get("answer_state") == state)
        for state in ("answered", "partially_answered", "unanswered")
    }
    qa_diagnostics = _read_optional_json(QA_DIAGNOSTICS_PATH)
    qa_human_required = _qa_human_required_case_ids()

    return {
        "title": "共享知識模型多用途驗證",
        "subtitle": "從同一份可溯源知識，驗證釋經、專題、問答、智能問答和方法研究。",
        "authoring_store": _authoring_status(),
        "pilot": {
            "package_id": shared_payload.get("package_id"),
            "title": shared_payload.get("title"),
            "corpus_scope": shared_payload.get("corpus_scope", {}),
            "counts": summary_counts,
        },
        "claims": claims,
        "claim_counts": _status_counts(claims),
        "ai_review": {
            "available": bool(ai_reviews),
            "reviewed_at": next(
                (item["reviewed_at"] for item in ai_reviews.values() if item["reviewed_at"]),
                None,
            ),
            "spot_check_percent": _read_optional_json(AI_REVIEW_PATH).get("spot_check_percent"),
            "counts": _attention_counts(claims),
        },
        "synthesis": {
            "items": syntheses,
            "counts": _status_counts(syntheses),
            "open_questions": open_questions,
        },
        "validation": {
            "experiments": shared_payload.get("validation_experiments", []),
            "tensions": shared_payload.get("tensions", []),
            "editorial_checks": shared_payload.get("editorial_checks", []),
        },
        "composition": {
            "plans": plans,
            "decisions": decisions,
            "counts": _status_counts(decisions),
        },
        "qa": {
            "plan_id": qa_plan.get("plan_id"),
            "title": qa_plan.get("title", "獨立問答用例驗證"),
            "description": qa_plan.get("description", ""),
            "corpus_scope": qa_plan.get("corpus_scope", ""),
            "counts": qa_counts,
            "diagnostics": {
                "available": bool(qa_diagnostics),
                "stale": bool(qa_diagnostics) and _qa_diagnostics_stale(qa_diagnostics),
                "summary": qa_diagnostics.get("summary", {}),
                "models": qa_diagnostics.get("models", {}),
            },
            "cases": [
                {
                    "case_id": item.get("case_id"),
                    "case_type": item.get("case_type"),
                    "question": item.get("question"),
                    "answer_state": item.get("answer_state"),
                    "answer_claim_count": len(item.get("answer_claim_ids", [])),
                    "source_question_count": len(item.get("source_question_ids", [])),
                    # Same principle as the claim queue: the list has to show
                    # where a person is needed without opening every case.
                    "human_required": item.get("case_id") in qa_human_required,
                }
                for item in qa_cases
            ],
        },
    }


def candidates_data() -> dict:
    """Project scripture and topic routes into a human-readable candidate queue."""
    shared = _shared_payload()
    state = _read_state()
    claims_by_id = {item["claim_id"]: item for item in shared.get("claims", [])}
    plans_by_id = {item.get("plan_id"): item for item in shared.get("product_plans", [])}
    topics_by_id = {item.get("topic_id"): item for item in shared.get("topic_nodes", [])}
    drafts = _editorial_drafts()
    # A new editorial package can be reviewed before it is promoted into the
    # global shared snapshot.  Use the package bound by its manifest to make
    # that candidate discoverable in the UI, while keeping the global snapshot
    # authoritative whenever it already contains the plan.
    for draft in drafts:
        candidate_id = str(draft.get("candidate_id") or "")
        if not candidate_id or candidate_id in plans_by_id:
            continue
        package = _draft_presentation_payload(draft, shared)
        package_plan = next(
            (
                plan
                for plan in package.get("product_plans", []) or []
                if str(plan.get("plan_id") or "") == candidate_id
            ),
            None,
        )
        if package_plan:
            plans_by_id[candidate_id] = package_plan

    drafts_by_decision_id: dict[str, list[dict]] = {}
    drafts_by_candidate_id: dict[str, list[dict]] = {}
    for draft in drafts:
        if draft.get("candidate_id"):
            drafts_by_candidate_id.setdefault(draft["candidate_id"], []).append(draft)
        if draft.get("decision_id"):
            drafts_by_decision_id.setdefault(draft["decision_id"], []).append(draft)

    grouped: dict[tuple[str, str], list[dict]] = {}
    for route in shared.get("knowledge_routes", []):
        route_type = route.get("route_type")
        if route_type not in {"scripture_exposition", "topic_research"}:
            continue
        axis = "scripture" if route_type == "scripture_exposition" else "topic"
        target_id = str(route.get("target_id") or "")
        if target_id:
            grouped.setdefault((axis, target_id), []).append(route)

    # A reviewed composition plan remains a candidate even before any route is
    # attached to it. This keeps the queue complete and makes plans discoverable.
    for plan_id, plan in plans_by_id.items():
        if not plan_id:
            continue
        axis = "topic" if plan.get("axis") == "topic" or plan.get("product_type") == "topic_research" else "scripture"
        grouped.setdefault((axis, str(plan_id)), [])

    items = []
    for (axis, target_id), routes in grouped.items():
        plan = plans_by_id.get(target_id)
        canonical_topic_ids = list(
            dict.fromkeys(
                topic_id
                for route in routes
                for topic_id in route.get("canonical_topic_ids", [])
                if topic_id
            )
        )
        topic_labels = [
            topics_by_id[topic_id].get("label", topic_id)
            for topic_id in canonical_topic_ids
            if topic_id in topics_by_id
        ]
        claim_ids = list(
            dict.fromkeys(route.get("claim_id") for route in routes if route.get("claim_id"))
        )
        decisions = list((plan or {}).get("decisions", []))
        reviewed_decisions = [
            {
                "decision_id": decision.get("decision_id"),
                # Keep the structured Scripture range separate from the
                # editorial heading.  The candidate workspace uses this field
                # to show readers where each decision belongs in the passage;
                # it must not guess the range from the title.
                "passage": str(decision.get("passage") or "").strip(),
                # Legacy composition plans stored the reader-facing heading in
                # ``section_title``/``passage``.  PostgreSQL-native plans use
                # ``decision`` for the same purpose.  Never expose an internal
                # decision ID merely because the record came from the new
                # authoring store.
                "title": (
                    decision.get("section_title")
                    or decision.get("passage")
                    or decision.get("decision")
                    or "未命名編排段落"
                ),
                "review": _review_for(
                    decision,
                    state["composition_decisions"].get(str(decision.get("decision_id"))),
                ),
            }
            for decision in decisions
        ]
        editorial_drafts = list(drafts_by_candidate_id.get(target_id, [])) + [
            {
                "draft_id": draft["draft_id"],
                "decision_id": draft["decision_id"],
                "title": draft["title"],
                "status": draft["status"],
                "status_label": draft["status_label"],
            }
            for decision in decisions
            for draft in drafts_by_decision_id.get(str(decision.get("decision_id") or ""), [])
        ]
        editorial_drafts = [
            {
                "draft_id": draft["draft_id"],
                "candidate_id": draft.get("candidate_id") or target_id,
                "decision_id": draft.get("decision_id") or "",
                "title": draft["title"],
                "status": draft["status"],
                "status_label": draft["status_label"],
            }
            for draft in {
                draft["draft_id"]: draft for draft in editorial_drafts
            }.values()
        ]
        navigation_claim_ids = list(claim_ids)
        for decision in decisions:
            for claim_ref in decision.get("claim_ids", []) or []:
                claim_id = claim_ref.get("claim_id") if isinstance(claim_ref, dict) else claim_ref
                if claim_id and claim_id not in navigation_claim_ids:
                    navigation_claim_ids.append(claim_id)
        title = (plan or {}).get("title") or "、".join(topic_labels) or target_id
        items.append(
            {
                "candidate_id": target_id,
                "axis": axis,
                "title": title,
                "description": (plan or {}).get("description") or (
                    "已有共享主張路由至此，尚待建立專題編排計劃。"
                    if axis == "topic"
                    else "已有共享主張路由至此，尚待建立釋經編排計劃。"
                ),
                "candidate_state": "composition_plan_ready" if plan else "research_leads",
                "candidate_state_label": "已有編排計劃" if plan else "已有材料，待編排",
                "canonical_topics": [
                    {"topic_id": topic_id, "label": topics_by_id[topic_id].get("label", topic_id)}
                    for topic_id in canonical_topic_ids
                    if topic_id in topics_by_id
                ],
                "claims": [
                    {"claim_id": claim_id, "title": claims_by_id[claim_id].get("title", claim_id)}
                    for claim_id in claim_ids
                    if claim_id in claims_by_id
                ],
                "claim_count": len(claim_ids),
                "decisions": reviewed_decisions,
                "decision_count": len(reviewed_decisions),
                "decision_counts": _status_counts(reviewed_decisions) if reviewed_decisions else {},
                "editorial_drafts": editorial_drafts,
                "draft_count": len(editorial_drafts),
                "scripture_navigation": (
                    _scripture_navigation(plan or {}, navigation_claim_ids, claims_by_id)
                    if axis == "scripture"
                    else None
                ),
            }
        )

    items.sort(key=lambda item: (0 if item["candidate_state"] == "composition_plan_ready" else 1, item["title"]))
    return {
        "title": "釋經與專題候選工作台",
        "description": "集中查看已形成編排計劃，以及已有材料、尚待編排的候選成果。",
        "scripture_candidates": [item for item in items if item["axis"] == "scripture"],
        "topic_candidates": [item for item in items if item["axis"] == "topic"],
        # Topic discovery is deliberately displayed as a separate stage.  A
        # discovered family is not yet an approved topic or product plan.
        "topic_structures": _topic_structure_candidates(claims_by_id),
    }


_NEW_TESTAMENT_BOOKS = {
    "Matt", "Mark", "Luke", "John", "Acts", "Rom", "1Cor", "2Cor", "Gal",
    "Eph", "Phil", "Col", "1Thess", "2Thess", "1Tim", "2Tim", "Titus",
    "Phlm", "Heb", "Jas", "1Pet", "2Pet", "1John", "2John", "3John", "Jude", "Rev",
}


def _scripture_navigation(plan: dict, claim_ids: list[str], claims_by_id: dict[str, dict]) -> dict:
    """Derive a candidate's primary book/chapter from structured scripture data.

    Composition passages are the strongest editorial signal. Older plans did
    not store them, so their claims' ``scripture_refs`` are the explicit
    fallback. Titles are deliberately excluded: they are display copy, not a
    stable scripture locator.
    """
    from backend.api.sermon_search.bible_refs import BOOKS, extract_refs

    decision_texts = [
        str(decision.get("passage") or "").strip()
        for decision in plan.get("decisions", []) or []
        if str(decision.get("passage") or "").strip()
    ]
    source = "composition_passage" if decision_texts else "claim_scripture_refs"
    source_texts = decision_texts
    if not source_texts:
        source_texts = [
            str(raw_ref).strip()
            for claim_id in claim_ids
            for raw_ref in (claims_by_id.get(claim_id, {}).get("scripture_refs", []) or [])
            if str(raw_ref).strip()
        ]

    refs = [ref for text in source_texts for ref in extract_refs(text)]
    if not refs:
        return {
            "located": False,
            "source": "unresolved",
            "book": None,
            "book_code": None,
            "chapter": None,
            "testament": None,
            "references": [],
        }

    # A passage such as Matt 16:27-17:1 contributes to both chapters. This
    # prevents a cross-chapter introduction from incorrectly owning an entire
    # chapter plan when the rest of the plan clearly concerns chapter 17.
    chapter_counts: dict[tuple[str, int], int] = {}
    book_counts: dict[str, int] = {}
    for ref in refs:
        end_chapter = ref.chapter_end or ref.chapter_start
        chapters = range(ref.chapter_start, min(end_chapter, ref.chapter_start + 20) + 1)
        book_counts[ref.book] = book_counts.get(ref.book, 0) + 1
        for chapter in chapters:
            key = (ref.book, chapter)
            chapter_counts[key] = chapter_counts.get(key, 0) + 1

    nt_first_order = [book for book, _label, _aliases in BOOKS if book in _NEW_TESTAMENT_BOOKS]
    nt_first_order.extend(book for book, _label, _aliases in BOOKS if book not in _NEW_TESTAMENT_BOOKS)
    order = {book: index for index, book in enumerate(nt_first_order)}
    primary_book = min(
        book_counts,
        key=lambda book: (-book_counts[book], order.get(book, 999), book),
    )
    primary_chapter = min(
        (chapter for book, chapter in chapter_counts if book == primary_book),
        key=lambda chapter: (-chapter_counts[(primary_book, chapter)], chapter),
    )
    labels = {book: label for book, label, _aliases in BOOKS}
    references = list(dict.fromkeys(ref.osis for ref in refs))
    return {
        "located": True,
        "source": source,
        "book": labels.get(primary_book, primary_book),
        "book_code": primary_book,
        "book_order": order.get(primary_book, 999),
        "chapter": primary_chapter,
        "testament": "new" if primary_book in _NEW_TESTAMENT_BOOKS else "old",
        "references": references,
    }


def _topic_structure_candidates(claims_by_id: dict[str, dict] | None = None) -> list[dict]:
    """Load reviewed mother-topic → subtopic → section candidates.

    These artifacts remain rebuildable pipeline output.  Keeping them separate
    from ``topic_candidates`` prevents an AI-discovered hierarchy from looking
    like an already accepted editorial plan.
    """
    batches: list[dict] = []
    claims_by_id = claims_by_id or {}
    paths = sorted(
        TOPIC_STRUCTURE_ROOT.glob("*/topic-structure/reviewed-topic-structure.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in paths:
        payload = _read_optional_json(path)
        final = payload.get("final") or {}
        families = final.get("topic_families") or []
        if not families:
            continue
        human_keys = {
            str(item.get("family_key") or "")
            for item in payload.get("human_review_items") or []
        }
        projected_families = []
        for family_index, family in enumerate(families, start=1):
            subtopics = []
            family_claim_ids: list[str] = []
            for subtopic_index, subtopic in enumerate(family.get("subtopics") or [], start=1):
                sections = []
                subtopic_claim_ids: list[str] = []
                for section_index, section in enumerate(subtopic.get("sections") or [], start=1):
                    claim_ids = list(dict.fromkeys(map(str, section.get("claim_ids") or [])))
                    subtopic_claim_ids.extend(claim_ids)
                    sections.append({
                        "section_id": f"{family_index}-{subtopic_index}-{section_index}",
                        "title": section.get("title") or "未命名篇章段落",
                        "role": section.get("role") or "",
                        "purpose": section.get("purpose") or "",
                        "claim_ids": claim_ids,
                        "claim_count": len(claim_ids),
                        "claims": [
                            {
                                "claim_id": claim_id,
                                "title": (
                                    claims_by_id.get(claim_id, {}).get("title")
                                    or claims_by_id.get(claim_id, {}).get("statement")
                                    or claim_id
                                ),
                            }
                            for claim_id in claim_ids
                        ],
                    })
                subtopic_claim_ids = list(dict.fromkeys(subtopic_claim_ids))
                family_claim_ids.extend(subtopic_claim_ids)
                subtopics.append({
                    "subtopic_id": f"{family_index}-{subtopic_index}",
                    "title": subtopic.get("title") or "未命名子專題",
                    "central_question": subtopic.get("central_question") or "",
                    "editorial_rationale": subtopic.get("editorial_rationale") or "",
                    "claim_count": len(subtopic_claim_ids),
                    "sections": sections,
                })
            family_claim_ids = list(dict.fromkeys(family_claim_ids))
            # Human items use stable family keys generated by the pipeline.  If
            # an older artifact lacks that key, the batch-level status remains
            # the conservative fallback.
            family_key = next(
                (
                    str(row.get("family_key"))
                    for row in payload.get("family_reviews") or []
                    if (row.get("openai_family") or {}).get("title") == family.get("title")
                ),
                "",
            )
            needs_human = family_key in human_keys or payload.get("status") == "human_review_required" and not family_key
            projected_families.append({
                "family_id": family_key or f"{path.parent.parent.name}-{family_index}",
                "title": family.get("title") or "未命名母題",
                "organizing_question": family.get("organizing_question") or "",
                "editorial_rationale": family.get("editorial_rationale") or "",
                "review_state": "human_review_required" if needs_human else "ai_consensus",
                "claim_count": len(family_claim_ids),
                "subtopic_count": len(subtopics),
                "subtopics": subtopics,
            })
        batches.append({
            "batch_id": path.parent.parent.name,
            "status": payload.get("status") or "candidate",
            "summary": final.get("summary") or "",
            "family_count": len(projected_families),
            "subtopic_count": sum(item["subtopic_count"] for item in projected_families),
            "claim_count": len({
                claim_id
                for family in projected_families
                for subtopic in family["subtopics"]
                for section in subtopic["sections"]
                for claim_id in section["claim_ids"]
            }),
            "unassigned_claim_count": len(final.get("unassigned_claim_ids") or []),
            "families": projected_families,
        })
    return batches


def qa_case_detail_data(case_id: str) -> dict:
    """Project one user question from shared knowledge without reading an article outline."""
    plan = _read_optional_json(QA_VALIDATION_PATH)
    case = next((item for item in plan.get("cases", []) if item.get("case_id") == case_id), None)
    if not case:
        raise HTTPException(status_code=404, detail="找不到這項問答驗證")

    shared = _shared_payload()
    claims_by_id = {item["claim_id"]: item for item in shared.get("claims", [])}
    questions_by_id = {item["question_id"]: item for item in shared.get("questions", [])}
    fragments_by_id = {item["fragment_id"]: item for item in shared.get("source_fragments", [])}
    positions_by_id = {item["position_id"]: item for item in shared.get("position_nodes", [])}
    plans_by_id = {item.get("plan_id"): item for item in shared.get("product_plans", [])}

    answer_claims = []
    missing_claim_ids = []
    for claim_id in case.get("answer_claim_ids", []):
        if claim_id not in claims_by_id:
            missing_claim_ids.append(claim_id)
            continue
        detail = claim_detail_data(claim_id)
        answer_claims.append(
            {
                "claim_id": claim_id,
                "title": detail["claim"].get("title"),
                "claim_type": detail["claim"].get("claim_type"),
                "scripture_refs": detail["claim"].get("scripture_refs", []),
                "evidence": detail["evidence"],
                "eligible_evidence_count": detail["review_gate"]["eligible_evidence_count"],
                "warnings": detail["review_gate"]["warnings"],
            }
        )

    context_claims = [
        {
            "claim_id": claim_id,
            "title": claims_by_id[claim_id].get("title"),
            "claim_type": claims_by_id[claim_id].get("claim_type"),
            "scripture_refs": claims_by_id[claim_id].get("scripture_refs", []),
        }
        for claim_id in case.get("context_claim_ids", [])
        if claim_id in claims_by_id
    ]

    source_questions = []
    for question_id in case.get("source_question_ids", []):
        question = questions_by_id.get(question_id)
        if not question:
            source_questions.append(
                {"question_id": question_id, "text": "尚未連結原始問題", "source_fragments": []}
            )
            continue
        fragment_ids = list(question.get("source_fragment_ids") or [])
        if not fragment_ids and question.get("source_fragment_id"):
            fragment_ids = [question["source_fragment_id"]]
        source_questions.append(
            {
                "question_id": question_id,
                "text": question.get("question") or question.get("text") or "",
                "questioner": question.get("questioner"),
                "answer_state": question.get("answer_state"),
                "answer_state_origin": question.get("answer_state_origin"),
                "argument_link_state": question.get("argument_link_state"),
                "answered_subquestions": question.get("answered_subquestions", []),
                "unanswered_subquestions": question.get("unanswered_subquestions", []),
                "answer_state_note": question.get("answer_state_note", ""),
                "source_fragments": [
                    fragments_by_id[fragment_id]
                    for fragment_id in fragment_ids
                    if fragment_id in fragments_by_id
                ],
            }
        )

    missing_context_ids = [
        claim_id for claim_id in case.get("context_claim_ids", []) if claim_id not in claims_by_id
    ]
    missing_position_ids = [
        position_id
        for position_id in case.get("opposed_position_ids", [])
        if position_id not in positions_by_id
    ]

    quality_warnings = []
    if missing_claim_ids:
        quality_warnings.append(f"缺少共享主張：{'、'.join(missing_claim_ids)}")
    if missing_context_ids:
        quality_warnings.append(f"缺少背景主張：{'、'.join(missing_context_ids)}")
    if missing_position_ids:
        # Without the opposed view the page cannot show what the professor was
        # rejecting, which is the whole point of an attribution case.
        quality_warnings.append(f"缺少反方立場：{'、'.join(missing_position_ids)}")
    weak_claims = [item["claim_id"] for item in answer_claims if not item["eligible_evidence_count"]]
    if weak_claims:
        quality_warnings.append(f"以下回答主張沒有合格來源：{'、'.join(weak_claims)}")
    if case.get("answer_state") == "unanswered" and answer_claims:
        quality_warnings.append("未回答問題不得把背景主張冒充直接答案。")

    full_answer_sections = case.get("full_answer_sections") or []
    answer_claim_ids = set(case.get("answer_claim_ids", []))
    context_claim_ids = set(case.get("context_claim_ids", []))
    if case.get("answer_state") in {"answered", "partially_answered"} and not full_answer_sections:
        quality_warnings.append("這項問答只有簡明結論，尚未編寫可供讀者閱讀的完整回答。")
    for section in full_answer_sections:
        section_type = section.get("section_type")
        cited_claim_ids = set(section.get("claim_ids") or [])
        allowed_claim_ids = (
            answer_claim_ids | context_claim_ids
            if section_type in {"background", "boundary"}
            else answer_claim_ids
        )
        unsupported_ids = cited_claim_ids - allowed_claim_ids
        if unsupported_ids:
            quality_warnings.append(
                f"完整回答「{section.get('heading', '未命名段落')}」引用了本題未授權的主張："
                f"{'、'.join(sorted(unsupported_ids))}"
            )
        if case.get("answer_state") == "unanswered" and section_type == "answer":
            quality_warnings.append("現有語料未回答的問題，不得把完整回答段落標成直接答案。")

    return {
        "plan": {
            "plan_id": plan.get("plan_id"),
            "title": plan.get("title"),
            "description": plan.get("description"),
            "corpus_scope": plan.get("corpus_scope"),
        },
        "case": case,
        "answer_claims": answer_claims,
        "context_claims": context_claims,
        "source_questions": source_questions,
        "opposed_positions": [
            positions_by_id[position_id]
            for position_id in case.get("opposed_position_ids", [])
            if position_id in positions_by_id
        ],
        "related_products": [
            {
                "plan_id": plan_id,
                "title": plans_by_id[plan_id].get("title"),
                "axis": plans_by_id[plan_id].get("axis"),
                "product_type": plans_by_id[plan_id].get("product_type"),
            }
            for plan_id in case.get("related_product_plan_ids", [])
            if plan_id in plans_by_id
        ],
        "quality_warnings": quality_warnings,
        "diagnostics": _qa_diagnostics_for_case(case_id),
    }


def synthesis_detail_data(synthesis_id: str) -> dict:
    shared = _shared_payload()
    state = _read_state()
    synthesis = next(
        (item for item in shared.get("cross_source_syntheses", []) if item["synthesis_id"] == synthesis_id),
        None,
    )
    if not synthesis:
        raise HTTPException(status_code=404, detail="找不到这项跨讲综合")
    claim_ids = set(synthesis.get("claim_ids", []))
    linked_claims = [
        {
            "claim_id": claim["claim_id"],
            "title": claim["title"],
            "claim_type": claim.get("claim_type"),
            "scripture_refs": claim.get("scripture_refs", []),
            "lectures": claim.get("lectures", []),
            "cross_lecture": claim.get("cross_lecture"),
        }
        for claim in shared.get("claims", [])
        if claim["claim_id"] in claim_ids
    ]
    return {
        "synthesis": synthesis,
        "review": _review_for(synthesis, state["syntheses"].get(synthesis_id)),
        "linked_claims": linked_claims,
        "corpus_scope": shared.get("corpus_scope", {}),
    }


def _node_sort_key(node: dict) -> tuple:
    """Graph lanes are numbered; package-only lanes are named, so keep them apart."""
    lane = node.get("lane")
    numbered = isinstance(lane, int)
    return (node.get("lec") or "", 0 if numbered else 1, lane if numbered else 99, str(node.get("id") or ""))


def _node_from_evidence_step(step: dict, fragment: dict, *, node_id: str | None = None) -> dict:
    """Render a package-only evidence step (e.g. a consensus-added anchor) as a graph node."""
    return {
        "id": node_id or step["evidence_step_id"],
        "lec": fragment.get("lecture") or fragment.get("source_title", ""),
        "lane": step.get("argument_lane"),
        "ty": step.get("function", ""),
        "full": step.get("statement", ""),
        "q": fragment.get("verbatim_excerpt", ""),
        "scr": step.get("scripture_refs", []),
        "qt": fragment.get("media_time"),
        "qp": fragment.get("paragraph_key"),
        "source_url": fragment.get("source_url"),
        "anchor_origin": fragment.get("anchor_origin"),
    }


def claim_detail_data(claim_id: str) -> dict:
    claims_payload = _read_json(CLAIMS_PATH)
    graph = _read_json(GRAPH_PATH)
    shared = _shared_payload()
    state = _read_state()
    corrected = {item["claim_id"]: item for item in shared.get("claims", [])}
    claim = corrected.get(claim_id) or next(
        (item for item in claims_payload.get("claims", []) if item["claim_id"] == claim_id), None
    )
    if not claim:
        raise HTTPException(status_code=404, detail="找不到这条主张")
    package_claim = corrected.get(claim_id)

    project_sources = {
        item["project_id"]: item.get("transcript_id") for item in claims_payload.get("source_projects", [])
    }
    group_id = claim.get("group_id")
    nodes = [
        item.copy()
        for item in graph.get("evidence_nodes", [])
        if group_id and group_id in item.get("claim_group_ids", [item.get("topic")])
    ]
    graph_node_ids = {item["id"] for item in nodes}
    # Consensus-added anchors only exist in the package, never in the exported
    # graph.  Without this the reviewer sees the excluded evidence but not the
    # verified source that replaced it.
    fragments_by_id = {item["fragment_id"]: item for item in shared.get("source_fragments", [])}
    sources_by_id = {item["source_id"]: item for item in shared.get("source_documents", [])}
    steps_by_id = {item["evidence_step_id"]: item for item in shared.get("evidence_steps", [])}
    for evidence_id in (package_claim or {}).get("evidence_step_ids", []):
        if evidence_id in graph_node_ids or evidence_id not in steps_by_id:
            continue
        step = steps_by_id[evidence_id]
        fragment_ids = list(step.get("source_fragment_ids") or [])
        if not fragment_ids and step.get("source_fragment_id"):
            fragment_ids = [step["source_fragment_id"]]
        for position, fragment_id in enumerate(fragment_ids):
            fragment = fragments_by_id.get(fragment_id, {})
            source = sources_by_id.get(fragment.get("source_id"), {})
            display_fragment = {
                **fragment,
                "source_title": source.get("title"),
                "source_url": fragment.get("source_url") or source.get("source_url"),
                "transcript_id": source.get("transcript_id"),
            }
            nodes.append(
                _node_from_evidence_step(
                    step,
                    display_fragment,
                    node_id=evidence_id if position == 0 else f"{evidence_id}::{fragment_id}",
                )
            )
    node_ids = {item["id"] for item in nodes}
    transcript_by_lecture = {
        occurrence.get("lecture"): occurrence.get("transcript_id")
        for occurrence in claim.get("occurrences", [])
    }
    for node in nodes:
        metadata = steps_by_id.get(str(node["id"]).split("::", 1)[0], {})
        fragment_ids = list(metadata.get("source_fragment_ids") or [])
        fragment = fragments_by_id.get(fragment_ids[0], {}) if fragment_ids else {}
        source = sources_by_id.get(fragment.get("source_id"), {})
        transcript_id = source.get("transcript_id") or transcript_by_lecture.get(node.get("lec"))
        if not transcript_id:
            for occurrence in claim.get("occurrences", []):
                transcript_id = project_sources.get(occurrence.get("project_id"))
                if transcript_id:
                    break
        node["transcript_id"] = transcript_id
        if transcript_id and node.get("qt") is not None:
            node["source_url"] = (
                f"/resources/sermons/{quote(transcript_id, safe='')}?t={int(node['qt'])}"
            )
        elif transcript_id and not node.get("source_url"):
            # A consensus-added anchor carries no media time yet; still link the sermon.
            node["source_url"] = f"/resources/sermons/{quote(transcript_id, safe='')}"
        else:
            node.setdefault("source_url", None)
    # Anchors the two models agreed to drop stay visible, but they must never
    # count as support again — the package-level eligibility flag is global and
    # does not know which claim excluded them.
    excluded_by_consensus = set(
        (package_claim or {}).get("ai_adjudication", {}).get("excluded_evidence_step_ids", [])
    )
    for node in nodes:
        metadata = steps_by_id.get(str(node["id"]).split("::", 1)[0], {})
        for field in (
            "speaker",
            "stance",
            "discourse_role",
            "anchor_quality",
            "support_eligibility",
            "review_note",
            "rejected_highlights",
        ):
            if field in metadata:
                node[field] = metadata[field]
        node.setdefault("support_eligibility", "eligible")
        base_node_id = str(node["id"]).split("::", 1)[0]
        if base_node_id in excluded_by_consensus:
            node["support_eligibility"] = "withheld_ai_consensus"
            node["review_note"] = "兩個模型一致認定這條錨點不足以支持本主張，已排除，不計入證據。"
        elif node.get("anchor_origin") == "ai_consensus_adjudication":
            node["review_note"] = "兩個模型一致補入的來源，已逐字比對原始逐字稿。"

    relations = [
        item
        for item in graph.get("relations", [])
        if item.get("source_evidence_id") in node_ids and item.get("target_evidence_id") in node_ids
    ]
    known_relation_ids = {item.get("relation_id") for item in relations}
    relations.extend(
        item
        for item in shared.get("knowledge_relations", [])
        if item.get("relation_id") not in known_relation_ids
        and item.get("source_id") in node_ids
        and item.get("target_id") in node_ids
    )
    position_titles = {item["position_id"]: item["title"] for item in shared.get("position_nodes", [])}
    claim_titles = {item["claim_id"]: item["title"] for item in shared.get("claims", [])}
    claim_relations = []
    for relation in shared.get("claim_relations", []):
        if claim_id not in {relation.get("source_id"), relation.get("target_id")}:
            continue
        claim_relations.append(
            {
                **relation,
                "source_title": claim_titles.get(relation.get("source_id")) or position_titles.get(relation.get("source_id")),
                "target_title": claim_titles.get(relation.get("target_id")) or position_titles.get(relation.get("target_id")),
            }
        )
    claim_relation_constraints = []
    for constraint in shared.get("claim_relation_constraints", []):
        if claim_id not in {constraint.get("source_id"), constraint.get("target_id")}:
            continue
        claim_relation_constraints.append(
            {
                **constraint,
                "source_title": claim_titles.get(constraint.get("source_id")),
                "target_title": claim_titles.get(constraint.get("target_id")),
            }
        )
    sorted_nodes = sorted(nodes, key=_node_sort_key)
    eligible_nodes = [item for item in sorted_nodes if item.get("support_eligibility") in {"eligible", "eligible_with_label"}]
    candidate_nodes = [
        item
        for item in sorted_nodes
        if item.get("support_eligibility") in {"eligible_candidate", "candidate"}
    ]
    gate_warnings = []
    if len(eligible_nodes) <= 1:
        gate_warnings.append(
            "目前只有一條合格證據；若用於主要釋經段落，應補強來源或降低篇章權重。"
            if eligible_nodes
            else "目前沒有合格證據，不能批准此主張。"
        )
    review = _review_for(claim, state["claims"].get(claim_id))
    ai_review = _ai_review_index().get(claim_id)
    attention, attention_reason = _attention_for(
        ai_review, review, len(eligible_nodes), len(candidate_nodes)
    )
    plans_by_id = {item.get("plan_id"): item for item in shared.get("product_plans", [])}
    topics_by_id = {item.get("topic_id"): item for item in shared.get("topic_nodes", [])}
    knowledge_routes = []
    for route in shared.get("knowledge_routes", []):
        if route.get("claim_id") != claim_id:
            continue
        axis = "scripture" if route.get("route_type") == "scripture_exposition" else "topic" if route.get("route_type") == "topic_research" else "other"
        plan = plans_by_id.get(route.get("target_id"))
        topic_labels = [
            topics_by_id[topic_id].get("label", topic_id)
            for topic_id in route.get("canonical_topic_ids", [])
            if topic_id in topics_by_id
        ]
        knowledge_routes.append(
            {
                **route,
                "axis": axis,
                "route_type_label": {
                    "scripture_exposition": "釋經候選",
                    "topic_research": "專題研究",
                    "method_research": "釋經方法研究",
                    "thought_development": "思想發展研究",
                }.get(route.get("route_type"), "後續整理"),
                "target_label": (plan or {}).get("title") or "、".join(topic_labels) or route.get("target_id"),
                "candidate_href": (
                    f"/admin/thought-review/candidates?axis={axis}&target={quote(str(route.get('target_id') or ''), safe='')}"
                    if axis in {"scripture", "topic"}
                    else None
                ),
            }
        )
    return {
        "claim": claim,
        "review": review,
        "ai_review": ai_review,
        "attention": attention,
        "attention_reason": attention_reason,
        "evidence": eligible_nodes,
        "candidate_evidence": candidate_nodes,
        "context_evidence": [item for item in sorted_nodes if item.get("support_eligibility") == "contextual_only"],
        "withheld_evidence": [item for item in sorted_nodes if str(item.get("support_eligibility", "")).startswith("withheld")],
        "relations": relations,
        "claim_relations": claim_relations,
        "claim_relation_constraints": claim_relation_constraints,
        "knowledge_routes": knowledge_routes,
        "review_gate": {
            "can_approve": bool(eligible_nodes),
            "eligible_evidence_count": len(eligible_nodes),
            "warnings": gate_warnings,
        },
        "argument_lanes": graph.get("argument_lanes", []),
    }


def composition_detail_data(decision_id: str) -> dict:
    shared = _shared_payload()
    compositions = shared.get("product_plans") or [_read_json(COMPOSITION_PATH)]
    state = _read_state()
    composition_ai_reviews, composition_plan_assessments = _composition_ai_review_index()
    composition = next(
        (
            plan
            for plan in compositions
            if any(item.get("decision_id") == decision_id for item in plan.get("decisions", []))
        ),
        None,
    )
    decision = next(
        (item for item in (composition or {}).get("decisions", []) if item["decision_id"] == decision_id),
        None,
    )
    if not decision:
        raise HTTPException(status_code=404, detail="找不到这项篇章决定")
    linked_ids = set(decision.get("claim_ids", []))
    linked_claims = [
        {
            "claim_id": claim["claim_id"],
            "title": claim["title"],
            "claim_type": claim.get("claim_type"),
            "scripture_refs": claim.get("scripture_refs", []),
        }
        for claim in shared.get("claims", [])
        if claim["claim_id"] in linked_ids
    ]
    source_documents = {item.get("source_id"): item for item in shared.get("source_documents", [])}
    source_presentations = _resolved_source_presentations(decision, source_documents)
    return {
        "plan": {
            "plan_id": composition.get("plan_id"),
            "title": composition.get("title"),
            "axis": composition.get("axis", "scripture"),
            "product_type": composition.get("product_type", "scripture_exposition"),
        },
        "decision": decision,
        "review": _review_for(decision, state["composition_decisions"].get(decision_id)),
        "ai_review": composition_ai_reviews.get(decision_id),
        "argument_layer_assessment": composition_plan_assessments.get(composition.get("plan_id")),
        "linked_claims": linked_claims,
        "claim_hierarchy": decision.get("claim_hierarchy"),
        "source_leads": [
            item
            for item in composition.get("source_leads", [])
            if item.get("source_lead_id") in set(decision.get("source_lead_ids", []))
        ],
        "source_presentations": source_presentations,
        "source_presentation_summary": decision.get("source_presentation_summary"),
    }


def update_review(collection: str, item_id: str, payload: ReviewUpdate) -> dict:
    store = _postgres_store()
    database_review = None
    if store:
        database_collection = {
            "claims": "claims",
            "syntheses": "editorial_syntheses",
            "composition_decisions": "composition_decisions",
        }[collection]
        try:
            database_review = store.record_review(
                database_collection,
                item_id,
                decision=payload.status,
                reason=payload.note,
                reviewer_id=payload.reviewer,
                reviewer_kind="human",
                expected_revision=payload.expected_revision,
            )
        except ChangeSetConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except PostgresKnowledgeStoreError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    state = _read_state()
    target = state[collection]
    target[item_id] = {
        "status": payload.status,
        "note": payload.note.strip(),
        "reviewer": payload.reviewer.strip() or "同工",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(state)
    return database_review or target[item_id]


@router.get("/workspace")
def get_workspace():
    return workspace_data()


@router.post("/active-snapshot/compile")
def compile_active_snapshot():
    """Build the approved-only read snapshot without changing the authoring store."""
    store = _postgres_store()
    if not store:
        raise HTTPException(
            status_code=503,
            detail="PostgreSQL 編輯主庫尚未連線，不能建立 Active Snapshot。",
        )
    try:
        return store.publish_active_snapshot(ACTIVE_SNAPSHOT_ROOT)
    except ActiveSnapshotBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "目前有資料完整性問題，舊的 Active Snapshot 已保留。",
                "findings": exc.findings,
            },
        ) from exc
    except PostgresKnowledgeStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/candidates")
def get_candidates():
    return candidates_data()


@router.get("/drafts/{draft_id}")
def get_editorial_draft(draft_id: str):
    return editorial_draft_data(draft_id)


@router.get("/qa/{case_id}")
def get_qa_case(case_id: str):
    return qa_case_detail_data(case_id)


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    return claim_detail_data(claim_id)


@router.patch("/claims/{claim_id}/review")
def review_claim(claim_id: str, payload: ReviewUpdate):
    detail = claim_detail_data(claim_id)
    if payload.status == "approved" and not detail["review_gate"]["can_approve"]:
        raise HTTPException(
            status_code=409,
            detail="此主張目前沒有可核查的合格證據，不能批准。請先補回來源錨點或改為需要修改。",
        )
    return {"review": update_review("claims", claim_id, payload)}


@router.get("/synthesis/{synthesis_id}")
def get_synthesis(synthesis_id: str):
    return synthesis_detail_data(synthesis_id)


@router.patch("/synthesis/{synthesis_id}/review")
def review_synthesis(synthesis_id: str, payload: ReviewUpdate):
    synthesis_detail_data(synthesis_id)
    return {"review": update_review("syntheses", synthesis_id, payload)}


@router.get("/composition/{decision_id}")
def get_composition_decision(decision_id: str):
    return composition_detail_data(decision_id)


@router.patch("/composition/{decision_id}/review")
def review_composition_decision(decision_id: str, payload: ReviewUpdate):
    composition_detail_data(decision_id)
    return {"review": update_review("composition_decisions", decision_id, payload)}
