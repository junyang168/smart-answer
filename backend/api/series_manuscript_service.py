from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from backend.api.config import DATA_BASE_PATH, OPENAI_GENERATION_MODEL
from backend.api.lecture_manager import get_series
from backend.api.sermon_converter_service import (
    NOTES_TO_SERMON_DIR,
    get_sermon_final_path,
    get_sermon_project_metadata,
)
from backend.pipeline.stage1 import Stage1OpenAIClient


SERIES_MANUSCRIPTS_DIR = DATA_BASE_PATH / "series_manuscripts"
PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "pipeline"
    / "prompts"
    / "series_continuity_analyzer.md"
)


RELATIONSHIPS = [
    "new",
    "duplicate",
    "extension",
    "correction",
    "related_qa",
    "tangential_qa",
    "non_substantive",
]

ACTIONS = [
    "create_new_unit",
    "merge_into_existing",
    "move_to_appendix",
    "omit_exact_duplicate",
    "omit_non_substantive",
    "needs_editor_decision",
]


CONTINUITY_SCHEMA: Dict[str, Any] = {
    "name": "series_continuity_proposal_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "summary": {"type": "string"},
            "decisions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "current_evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "relationship": {"type": "string", "enum": RELATIONSHIPS},
                        "matched_prior_section_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "new_contribution": {"type": "string"},
                        "recommended_action": {"type": "string", "enum": ACTIONS},
                        "reason": {"type": "string"},
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": [
                        "current_evidence_ids",
                        "relationship",
                        "matched_prior_section_ids",
                        "new_contribution",
                        "recommended_action",
                        "reason",
                        "confidence",
                    ],
                },
            },
            "unassigned_evidence_ids": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["summary", "decisions", "unassigned_evidence_ids"],
    },
}


class ContinuityStatus(BaseModel):
    series_id: str
    project_id: str
    status: str = "idle"
    message: str = "No continuity analysis has been run."
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    proposal_id: Optional[str] = None
    proposal: Optional[Dict[str, Any]] = None


_statuses: Dict[Tuple[str, str], ContinuityStatus] = {}
_status_lock = threading.Lock()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    return value if isinstance(value, dict) else {}


def _save_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _unwrap_artifact(path: Path) -> Dict[str, Any]:
    artifact = _load_json(path)
    payload = artifact.get("payload")
    return payload if isinstance(payload, dict) else artifact


def get_series_manuscript_dir(series_id: str) -> Path:
    return SERIES_MANUSCRIPTS_DIR / series_id


def _ordered_project_ids(series_id: str) -> List[str]:
    series = get_series(series_id)
    if not series:
        raise ValueError(f"Series not found: {series_id}")
    return [project_id for lecture in series.lectures for project_id in lecture.project_ids]


def _prior_project_ids(series_id: str, project_id: str) -> List[str]:
    ordered = _ordered_project_ids(series_id)
    if project_id not in ordered:
        raise ValueError(f"Project {project_id} is not assigned to series {series_id}")
    return ordered[: ordered.index(project_id)]


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def _split_markdown_sections(project_id: str, markdown: str) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    stack: List[Tuple[int, str]] = []
    buffer: List[str] = []
    ordinal = 0

    def flush() -> None:
        nonlocal ordinal
        text = "\n".join(buffer).strip()
        if not text:
            buffer.clear()
            return
        heading_path = [title for _, title in stack]
        seed = f"{project_id}|{ordinal}|{'/'.join(heading_path)}"
        sections.append(
            {
                "section_id": f"PS-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}",
                "project_id": project_id,
                "heading_path": heading_path,
                "text": text,
                "content_sha256": _sha256_text(text),
                "ordinal": ordinal,
            }
        )
        ordinal += 1
        buffer.clear()

    for raw_line in markdown.splitlines():
        match = HEADING_RE.match(raw_line.rstrip())
        if match:
            flush()
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
        else:
            buffer.append(raw_line)
    flush()
    return sections


def _cjk_bigrams(value: str) -> set[str]:
    normalized = re.sub(r"[^\u3400-\u9fffA-Za-z0-9]+", "", value).lower()
    if len(normalized) < 2:
        return {normalized} if normalized else set()
    return {normalized[index : index + 2] for index in range(len(normalized) - 1)}


def _candidate_score(evidence: Dict[str, Any], section: Dict[str, Any]) -> float:
    evidence_text = " ".join(
        [str(evidence.get("content") or ""), *[str(item) for item in evidence.get("scripture_refs", [])]]
    )
    section_text = " ".join([*section.get("heading_path", []), section.get("text", "")])
    evidence_terms = _cjk_bigrams(evidence_text)
    section_terms = _cjk_bigrams(section_text)
    lexical = len(evidence_terms & section_terms) / max(len(evidence_terms), 1)
    scripture_bonus = sum(
        1.5 for ref in evidence.get("scripture_refs", []) if str(ref) and str(ref) in section_text
    )
    return lexical + scripture_bonus


def _select_prior_candidates(
    evidence: List[Dict[str, Any]], prior_sections: List[Dict[str, Any]], limit: int = 60
) -> List[Dict[str, Any]]:
    scores: Dict[str, float] = {}
    section_by_id = {item["section_id"]: item for item in prior_sections}
    for item in evidence:
        ranked = sorted(
            ((_candidate_score(item, section), section["section_id"]) for section in prior_sections),
            reverse=True,
        )[:3]
        for score, section_id in ranked:
            if score > 0:
                scores[section_id] = max(scores.get(section_id, 0.0), score)
    selected_ids = [item[0] for item in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:limit]]
    return [section_by_id[section_id] for section_id in selected_ids]


def build_continuity_context(series_id: str, project_id: str) -> Dict[str, Any]:
    project = get_sermon_project_metadata(project_id)
    if not project:
        raise ValueError(f"Project not found: {project_id}")
    if project.series_id != series_id:
        raise ValueError(f"Project {project_id} does not belong to series {series_id}")

    project_dir = NOTES_TO_SERMON_DIR / project_id
    evidence_payload = _unwrap_artifact(project_dir / "evidence_inventory.json")
    evidence = evidence_payload.get("evidence", [])
    if not evidence:
        raise ValueError("Analyze the transcript first; no evidence inventory is available.")

    prior_projects: List[Dict[str, Any]] = []
    prior_sections: List[Dict[str, Any]] = []
    for prior_project_id in _prior_project_ids(series_id, project_id):
        final_path = get_sermon_final_path(prior_project_id)
        if not final_path.is_file():
            continue
        prior_project = get_sermon_project_metadata(prior_project_id)
        markdown = final_path.read_text(encoding="utf-8")
        sections = _split_markdown_sections(prior_project_id, markdown)
        prior_sections.extend(sections)
        prior_projects.append(
            {
                "project_id": prior_project_id,
                "title": prior_project.title if prior_project else prior_project_id,
                "content_sha256": _sha256_text(markdown),
                "section_count": len(sections),
            }
        )

    candidates = _select_prior_candidates(evidence, prior_sections)
    if not candidates:
        raise ValueError("No earlier checked-in manuscript content is available for comparison.")
    return {
        "series_id": series_id,
        "project_id": project_id,
        "current_evidence": evidence,
        "prior_projects": prior_projects,
        "prior_candidates": candidates,
    }


def _validate_proposal(
    proposal: Dict[str, Any], evidence_ids: set[str], candidate_ids: set[str]
) -> None:
    assigned: List[str] = []
    for decision in proposal.get("decisions", []):
        assigned.extend(decision.get("current_evidence_ids", []))
        unknown_sections = set(decision.get("matched_prior_section_ids", [])) - candidate_ids
        if unknown_sections:
            raise ValueError(f"Continuity proposal references unknown prior sections: {sorted(unknown_sections)}")
    duplicates = sorted({item for item in assigned if assigned.count(item) > 1})
    missing = sorted(evidence_ids - set(assigned))
    unknown = sorted(set(assigned) - evidence_ids)
    unassigned = proposal.get("unassigned_evidence_ids", [])
    if duplicates or missing or unknown or unassigned:
        raise ValueError(
            "Invalid continuity proposal: "
            f"missing={missing}, unknown={unknown}, duplicates={duplicates}, unassigned={unassigned}"
        )


def _enrich_matched_prior_units(
    proposal: Dict[str, Any], prior_sections: List[Dict[str, Any]]
) -> None:
    section_by_id = {str(item["section_id"]): item for item in prior_sections}
    for decision in proposal.get("decisions", []):
        matched_units: List[Dict[str, str]] = []
        seen: set[Tuple[str, str]] = set()
        for section_id in decision.get("matched_prior_section_ids", []):
            section = section_by_id.get(str(section_id))
            if not section:
                continue
            heading_path = section.get("heading_path") or []
            unit_title = str(heading_path[0]) if heading_path else str(section.get("project_id") or "")
            project_id = str(section.get("project_id") or "")
            key = (project_id, unit_title)
            if key in seen:
                continue
            seen.add(key)
            matched_units.append(
                {
                    "project_id": project_id,
                    "unit_title": unit_title,
                    "section_id": str(section_id),
                }
            )
        decision["matched_prior_units"] = matched_units


def analyze_series_continuity(
    series_id: str,
    project_id: str,
    llm: Optional[Any] = None,
) -> Dict[str, Any]:
    context = build_continuity_context(series_id, project_id)
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = llm or Stage1OpenAIClient(
        model=OPENAI_GENERATION_MODEL,
        timeout_seconds=300,
        max_retries=3,
        max_output_tokens=40000,
        reasoning_effort="medium",
    )
    user_prompt = (
        "請分析目前講次相對於既有系列 manuscript 的重複、增量、修正與問答歸屬。\n\n"
        f"【目前 Evidence Inventory】\n{json.dumps(context['current_evidence'], ensure_ascii=False, indent=2)}\n\n"
        f"【較早 Project 摘要】\n{json.dumps(context['prior_projects'], ensure_ascii=False, indent=2)}\n\n"
        f"【按內容檢索出的既有候選段落】\n{json.dumps(context['prior_candidates'], ensure_ascii=False, indent=2)}"
    )
    evidence_ids = {str(item["evidence_id"]) for item in context["current_evidence"]}
    candidate_ids = {str(item["section_id"]) for item in context["prior_candidates"]}
    runtime_schema = json.loads(json.dumps(CONTINUITY_SCHEMA))
    decision_properties = runtime_schema["schema"]["properties"]["decisions"]["items"]["properties"]
    decision_properties["current_evidence_ids"]["items"]["enum"] = sorted(evidence_ids)
    decision_properties["matched_prior_section_ids"]["items"]["enum"] = sorted(candidate_ids)
    proposal = client.generate_json(
        prompt,
        user_prompt,
        runtime_schema,
        timeout_seconds=300,
    )
    try:
        _validate_proposal(proposal, evidence_ids, candidate_ids)
    except ValueError as exc:
        repair_prompt = (
            f"{user_prompt}\n\n"
            f"【上一版輸出】\n{json.dumps(proposal, ensure_ascii=False, indent=2)}\n\n"
            f"【確定性驗證錯誤】\n{exc}\n\n"
            "請保留已正確的語義判斷，只修復 ID 分配。每個目前 evidence ID 必須且只能出現一次，"
            "且只能使用 schema 允許的 prior section IDs。重新輸出完整 proposal。"
        )
        proposal = client.generate_json(
            prompt,
            repair_prompt,
            runtime_schema,
            timeout_seconds=300,
        )
        _validate_proposal(proposal, evidence_ids, candidate_ids)

    _enrich_matched_prior_units(proposal, context["prior_candidates"])

    proposal_id = str(uuid.uuid4())
    created_at = _utcnow()
    source_snapshot = {
        "current_evidence_sha256": _sha256_text(
            json.dumps(context["current_evidence"], ensure_ascii=False, sort_keys=True)
        ),
        "prior_projects": context["prior_projects"],
    }
    result = {
        "schema_version": 1,
        "proposal_id": proposal_id,
        "series_id": series_id,
        "project_id": project_id,
        "status": "proposed",
        "created_at": created_at,
        "model": OPENAI_GENERATION_MODEL,
        "source_snapshot": source_snapshot,
        "current_evidence": context["current_evidence"],
        "prior_sections": context["prior_candidates"],
        **proposal,
    }

    root = get_series_manuscript_dir(series_id)
    proposal_path = root / "merge_runs" / proposal_id / "proposal.json"
    _save_json(proposal_path, result)
    manifest = _load_json(root / "manifest.json")
    manifest.update(
        {
            "schema_version": 1,
            "series_id": series_id,
            "status": "proposal_ready",
            "latest_proposal_id": proposal_id,
            "latest_project_id": project_id,
            "updated_at": created_at,
        }
    )
    _save_json(root / "manifest.json", manifest)
    return result


def get_latest_proposal(series_id: str, project_id: str) -> Optional[Dict[str, Any]]:
    root = get_series_manuscript_dir(series_id)
    manifest = _load_json(root / "manifest.json")
    proposal_id = manifest.get("latest_proposal_id")
    if not proposal_id or manifest.get("latest_project_id") != project_id:
        return None
    proposal_path = root / "merge_runs" / str(proposal_id) / "proposal.json"
    proposal = _load_json(proposal_path)
    if proposal and any("matched_prior_units" not in item for item in proposal.get("decisions", [])):
        _enrich_matched_prior_units(proposal, proposal.get("prior_sections", []))
        _save_json(proposal_path, proposal)
    return proposal or None


def get_continuity_status(series_id: str, project_id: str) -> ContinuityStatus:
    key = (series_id, project_id)
    with _status_lock:
        active = _statuses.get(key)
        if active:
            return active.model_copy(deep=True)
    proposal = get_latest_proposal(series_id, project_id)
    if proposal:
        return ContinuityStatus(
            series_id=series_id,
            project_id=project_id,
            status="completed",
            message="Continuity proposal is ready for review.",
            finished_at=proposal.get("created_at"),
            proposal_id=proposal.get("proposal_id"),
            proposal=proposal,
        )
    return ContinuityStatus(series_id=series_id, project_id=project_id)


def queue_continuity_analysis(series_id: str, project_id: str) -> Tuple[ContinuityStatus, bool]:
    key = (series_id, project_id)
    with _status_lock:
        existing = _statuses.get(key)
        if existing and existing.status in {"queued", "running"}:
            return existing.model_copy(deep=True), False
        status = ContinuityStatus(
            series_id=series_id,
            project_id=project_id,
            status="queued",
            message="Continuity analysis is queued.",
            started_at=_utcnow(),
        )
        _statuses[key] = status
        return status.model_copy(deep=True), True


def run_continuity_analysis(series_id: str, project_id: str) -> None:
    key = (series_id, project_id)
    with _status_lock:
        status = _statuses.setdefault(
            key, ContinuityStatus(series_id=series_id, project_id=project_id)
        )
        status.status = "running"
        status.message = "Comparing this transcript with earlier checked-in manuscripts…"
        status.started_at = status.started_at or _utcnow()
    try:
        proposal = analyze_series_continuity(series_id, project_id)
        with _status_lock:
            status = _statuses[key]
            status.status = "completed"
            status.message = "Continuity proposal is ready for review."
            status.finished_at = _utcnow()
            status.proposal_id = proposal["proposal_id"]
            status.proposal = proposal
    except Exception as exc:
        with _status_lock:
            status = _statuses[key]
            status.status = "failed"
            status.message = str(exc)
            status.finished_at = _utcnow()
