from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel

from backend.api.config import OPENAI_GENERATION_MODEL
from backend.api.sermon_converter_service import NOTES_TO_SERMON_DIR, get_sermon_final_path
from backend.api.series_manuscript_service import (
    _load_json,
    _save_json,
    _sha256_text,
    _utcnow,
    _unwrap_artifact,
    get_latest_proposal,
    get_series_manuscript_dir,
)
from backend.pipeline.stage1 import SourceDocument, Stage1OpenAIClient


MERGER_PROMPT_PATH = (
    Path(__file__).resolve().parents[1]
    / "pipeline"
    / "prompts"
    / "series_unit_merger.md"
)


UNIT_MERGE_SCHEMA: Dict[str, Any] = {
    "name": "series_manuscript_unit_merge_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "unit_title": {"type": "string"},
            "manuscript_sections": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "exegesis": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "theological_significance": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "application": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "appendix": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["exegesis", "theological_significance", "application", "appendix"],
            },
            "covered_new_evidence_ids": {"type": "array", "items": {"type": "string"}},
            "change_summary": {"type": "string"},
        },
        "required": ["unit_title", "manuscript_sections", "covered_new_evidence_ids", "change_summary"],
    },
}


class SeriesBuildStatus(BaseModel):
    series_id: str
    project_id: str
    status: str = "idle"
    message: str = "No Series Draft build has been run."
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    proposal_id: Optional[str] = None
    draft_path: Optional[str] = None
    changed_unit_count: int = 0
    new_unit_count: int = 0
    evidence_count: int = 0


_build_statuses: Dict[Tuple[str, str], SeriesBuildStatus] = {}
_build_lock = threading.Lock()


TOP_LEVEL_RE = re.compile(r"^##\s+(.+?)\s*$")


def _split_canonical_units(project_id: str, markdown: str) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    current_title: Optional[str] = None
    current_lines: List[str] = []
    preamble: List[str] = []

    def emit() -> None:
        if not current_title:
            return
        ordinal = len(units)
        body = "\n".join(current_lines).strip()
        full_markdown = f"## {current_title}"
        if body:
            full_markdown += f"\n\n{body}"
        seed = f"{project_id}|{ordinal}|{current_title}"
        units.append(
            {
                "canonical_unit_id": f"CU-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}",
                "title": current_title,
                "source_project_ids": [project_id],
                "base_project_id": project_id,
                "ordinal_in_project": ordinal,
                "markdown": full_markdown,
                "content_sha256": _sha256_text(full_markdown),
                "status": "baseline",
            }
        )

    for raw_line in markdown.splitlines():
        match = TOP_LEVEL_RE.match(raw_line.rstrip())
        if match:
            emit()
            current_title = match.group(1).strip()
            current_lines = []
            if preamble:
                current_lines.extend(preamble)
                preamble = []
        elif current_title is None:
            preamble.append(raw_line)
        else:
            current_lines.append(raw_line)
    emit()

    if not units and markdown.strip():
        title = project_id
        content = markdown.strip()
        units.append(
            {
                "canonical_unit_id": f"CU-{hashlib.sha1(project_id.encode('utf-8')).hexdigest()[:12]}",
                "title": title,
                "source_project_ids": [project_id],
                "base_project_id": project_id,
                "ordinal_in_project": 0,
                "markdown": f"## {title}\n\n{content}",
                "content_sha256": _sha256_text(content),
                "status": "baseline",
            }
        )
    return units


def _build_baseline(proposal: Dict[str, Any]) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    for project in proposal.get("source_snapshot", {}).get("prior_projects", []):
        project_id = str(project.get("project_id") or "")
        final_path = get_sermon_final_path(project_id)
        if not project_id or not final_path.is_file():
            raise ValueError(f"Earlier manuscript is unavailable: {project_id}")
        markdown = final_path.read_text(encoding="utf-8")
        if _sha256_text(markdown) != project.get("content_sha256"):
            raise ValueError(f"Earlier manuscript changed after proposal review: {project_id}")
        units.extend(_split_canonical_units(project_id, markdown))
    if not units:
        raise ValueError("Series Draft requires at least one earlier manuscript unit")
    return units


def _current_evidence_is_fresh(proposal: Dict[str, Any]) -> None:
    project_id = str(proposal.get("project_id") or "")
    actual_payload = _unwrap_artifact(NOTES_TO_SERMON_DIR / project_id / "evidence_inventory.json")
    actual_evidence = actual_payload.get("evidence", [])
    evidence = proposal.get("current_evidence", [])
    if actual_evidence != evidence:
        raise ValueError("Current evidence inventory changed after proposal review")
    current_hash = _sha256_text(json.dumps(actual_evidence, ensure_ascii=False, sort_keys=True))
    expected = proposal.get("source_snapshot", {}).get("current_evidence_sha256")
    if current_hash != expected:
        raise ValueError("Continuity proposal evidence snapshot is stale")


def get_series_draft(series_id: str) -> str:
    path = get_series_manuscript_dir(series_id) / "draft.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def get_series_draft_review(series_id: str) -> Dict[str, Any]:
    root = get_series_manuscript_dir(series_id)
    manifest = _load_json(root / "manifest.json")
    proposal_id = manifest.get("approved_proposal_id")
    if not proposal_id:
        return {}
    build = _load_json(root / "merge_runs" / str(proposal_id) / "build.json")
    if build.get("status") != "draft_ready":
        return {}
    return {
        "proposal_id": proposal_id,
        "project_id": build.get("project_id"),
        "built_at": build.get("built_at"),
        "changed_unit_count": build.get("changed_unit_count", 0),
        "new_unit_count": build.get("new_unit_count", 0),
        "evidence_count": build.get("evidence_count", 0),
        "changes": build.get("integration_changes", []),
    }


def _find_target_unit(decision: Dict[str, Any], baseline: List[Dict[str, Any]]) -> Optional[str]:
    for matched in decision.get("matched_prior_units", []):
        project_id = matched.get("project_id")
        unit_title = matched.get("unit_title")
        for unit in baseline:
            if unit["base_project_id"] == project_id and unit["title"] == unit_title:
                return str(unit["canonical_unit_id"])
    return None


def _group_new_main_decisions(
    candidates: List[Tuple[int, Dict[str, Any]]],
    evidence_by_id: Dict[str, Dict[str, Any]],
) -> List[List[Tuple[int, Dict[str, Any]]]]:
    """Group new decisions that belong to one reader-facing logical unit.

    Shared Scripture references form connected components. A reference-free
    related Q&A is attached to the nearest Scripture-grounded new decision;
    other reference-free decisions remain independent.
    """
    if not candidates:
        return []
    references: List[set[str]] = []
    for _, decision in candidates:
        refs: set[str] = set()
        for evidence_id in decision.get("current_evidence_ids", []):
            refs.update(str(item).strip() for item in evidence_by_id.get(evidence_id, {}).get("scripture_refs", []) if str(item).strip())
        references.append(refs)

    parents = list(range(len(candidates)))

    def find(item: int) -> int:
        while parents[item] != item:
            parents[item] = parents[parents[item]]
            item = parents[item]
        return item

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parents[right_root] = left_root

    for left in range(len(candidates)):
        for right in range(left + 1, len(candidates)):
            if references[left] and references[left].intersection(references[right]):
                union(left, right)

    for index, (decision_index, decision) in enumerate(candidates):
        if references[index] or decision.get("relationship") != "related_qa":
            continue
        grounded = [
            other for other in range(len(candidates))
            if references[other]
        ]
        if grounded:
            nearest = min(grounded, key=lambda other: abs(candidates[other][0] - decision_index))
            union(index, nearest)

    grouped: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
    for index, candidate in enumerate(candidates):
        grouped.setdefault(find(index), []).append(candidate)
    return sorted(grouped.values(), key=lambda group: min(item[0] for item in group))


def _build_operations(proposal: Dict[str, Any], baseline: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    merge_groups: Dict[str, Dict[str, Any]] = {}
    new_operations: List[Dict[str, Any]] = []
    new_main_candidates: List[Tuple[int, Dict[str, Any]]] = []
    dispositions: List[Dict[str, Any]] = []
    evidence_by_id = {item["evidence_id"]: item for item in proposal.get("current_evidence", [])}

    for index, decision in enumerate(proposal.get("decisions", []), start=1):
        evidence_ids = list(decision.get("current_evidence_ids", []))
        action = decision.get("recommended_action")
        target_id = _find_target_unit(decision, baseline)

        if action in {"omit_exact_duplicate", "omit_non_substantive"}:
            for evidence_id in evidence_ids:
                dispositions.append(
                    {
                        "evidence_id": evidence_id,
                        "disposition": (
                            "represented_by_existing_unit"
                            if action == "omit_exact_duplicate"
                            else "omitted_non_substantive"
                        ),
                        "canonical_unit_ids": [target_id] if target_id else [],
                        "reason": decision.get("reason", ""),
                    }
                )
            continue

        if action == "needs_editor_decision":
            raise ValueError(f"Proposal still requires an editor decision for evidence {evidence_ids}")

        if action == "merge_into_existing" and target_id:
            operation = merge_groups.setdefault(
                target_id,
                {
                    "operation_id": f"OP-MERGE-{len(merge_groups) + 1:03d}",
                    "kind": "merge_existing",
                    "target_canonical_unit_id": target_id,
                    "decisions": [],
                    "evidence_ids": [],
                },
            )
            operation["decisions"].append(decision)
            operation["evidence_ids"].extend(evidence_ids)
        elif action == "move_to_appendix":
            new_operations.append(
                {
                    "operation_id": f"OP-NEW-{index:03d}",
                    "kind": "create_appendix",
                    "target_canonical_unit_id": None,
                    "decisions": [decision],
                    "evidence_ids": evidence_ids,
                }
            )
        else:
            new_main_candidates.append((index, decision))

    for group in _group_new_main_decisions(new_main_candidates, evidence_by_id):
        decision_indexes = [item[0] for item in group]
        decisions = [item[1] for item in group]
        new_operations.append(
            {
                "operation_id": f"OP-NEW-{min(decision_indexes):03d}",
                "kind": "create_new",
                "target_canonical_unit_id": None,
                "decisions": decisions,
                "evidence_ids": [
                    evidence_id
                    for decision in decisions
                    for evidence_id in decision.get("current_evidence_ids", [])
                ],
            }
        )

    operations = [*merge_groups.values(), *new_operations]
    return operations, dispositions


def _source_excerpt(source: SourceDocument, evidence: List[Dict[str, Any]]) -> str:
    ranges: List[Tuple[int, int]] = []
    for item in evidence:
        for source_range in item.get("source_ranges", []):
            ranges.append((int(source_range["start_line"]), int(source_range["end_line"])))
    ranges.sort()
    merged: List[List[int]] = []
    for start, end in ranges:
        if merged and start <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return "\n\n".join(
        f"【原文第 {start}–{end} 行】\n{source.slice_by_lines(start, end)}"
        for start, end in merged
    )


def _strip_heading(value: Optional[str], label: str) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    aliases = {
        "釋經": "(?:釋經|释经)",
        "神學意義": "(?:神學意義|神学意义)",
        "生活應用": "(?:生活應用|生活应用)",
        "附錄": "(?:附錄|附录)",
    }
    normalized = value.strip()
    pattern = re.compile(rf"^\s*#{{1,6}}\s*{aliases[label]}\s*[：:]?\s*(?:\r?\n+|$)")
    while pattern.match(normalized):
        normalized = pattern.sub("", normalized, count=1).lstrip()
    return normalized or None


def _render_unit(title: str, sections: Dict[str, Optional[str]]) -> str:
    blocks = [f"## {title.strip()}"]
    labels = [
        ("exegesis", "釋經"),
        ("theological_significance", "神學意義"),
        ("application", "生活應用"),
        ("appendix", "附錄"),
    ]
    for key, label in labels:
        value = _strip_heading(sections.get(key), label)
        if value:
            blocks.append(f"### {label}\n\n{value}")
    return "\n\n".join(blocks).strip()


def _generate_operation(
    client: Any,
    operation: Dict[str, Any],
    baseline_by_id: Dict[str, Dict[str, Any]],
    evidence_by_id: Dict[str, Dict[str, Any]],
    source: SourceDocument,
) -> Dict[str, Any]:
    evidence = [evidence_by_id[item] for item in operation["evidence_ids"]]
    existing = baseline_by_id.get(operation.get("target_canonical_unit_id"))
    user_prompt = (
        f"【操作】\n{json.dumps(operation, ensure_ascii=False, indent=2)}\n\n"
        f"【既有 Canonical Unit】\n{json.dumps(existing, ensure_ascii=False, indent=2) if existing else 'null'}\n\n"
        f"【本次新增 Evidence】\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
        f"【對應 Transcript 原文】\n{_source_excerpt(source, evidence)}"
    )
    schema = json.loads(json.dumps(UNIT_MERGE_SCHEMA))
    schema["schema"]["properties"]["covered_new_evidence_ids"]["items"]["enum"] = sorted(operation["evidence_ids"])
    result = client.generate_json(
        MERGER_PROMPT_PATH.read_text(encoding="utf-8"),
        user_prompt,
        schema,
        timeout_seconds=300,
    )
    covered = list(result.get("covered_new_evidence_ids", []))
    missing = sorted(set(operation["evidence_ids"]) - set(covered))
    unknown = sorted(set(covered) - set(operation["evidence_ids"]))
    if missing or unknown or len(covered) != len(set(covered)):
        raise ValueError(
            f"Series unit operation {operation['operation_id']} failed evidence coverage: "
            f"missing={missing}, unknown={unknown}"
        )
    markdown = _render_unit(result["unit_title"], result["manuscript_sections"])
    return {
        **result,
        "generated_markdown": markdown,
        "base_canonical_unit_id": operation.get("target_canonical_unit_id"),
    }


def _operation_cache_key(
    operation: Dict[str, Any],
    baseline_by_id: Dict[str, Dict[str, Any]],
    evidence_by_id: Dict[str, Dict[str, Any]],
) -> str:
    existing = baseline_by_id.get(operation.get("target_canonical_unit_id"))
    payload = {
        "operation": operation,
        "existing_content_sha256": existing.get("content_sha256") if existing else None,
        "evidence": [evidence_by_id[item] for item in operation["evidence_ids"]],
        "prompt_sha256": _sha256_text(MERGER_PROMPT_PATH.read_text(encoding="utf-8")),
        "model": OPENAI_GENERATION_MODEL,
    }
    return _sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_series_draft(
    series_id: str,
    project_id: str,
    proposal_id: str,
    llm: Optional[Any] = None,
) -> Dict[str, Any]:
    proposal = get_latest_proposal(series_id, project_id)
    if not proposal or proposal.get("proposal_id") != proposal_id:
        raise ValueError("Reviewed continuity proposal was not found")
    _current_evidence_is_fresh(proposal)
    proposal["status"] = "approved"
    proposal["approved_at"] = proposal.get("approved_at") or _utcnow()
    root = get_series_manuscript_dir(series_id)
    proposal_path = root / "merge_runs" / proposal_id / "proposal.json"
    _save_json(proposal_path, proposal)

    baseline = _build_baseline(proposal)
    baseline_by_id = {item["canonical_unit_id"]: item for item in baseline}
    operations, dispositions = _build_operations(proposal, baseline)
    evidence_by_id = {item["evidence_id"]: item for item in proposal.get("current_evidence", [])}
    source = SourceDocument.from_path(NOTES_TO_SERMON_DIR / project_id / "unified_source.md")
    client = llm or Stage1OpenAIClient(
        model=OPENAI_GENERATION_MODEL,
        timeout_seconds=300,
        max_retries=3,
        max_output_tokens=50000,
        reasoning_effort="medium",
    )

    prior_build = _load_json(root / "merge_runs" / proposal_id / "build.json")
    cached_results: Dict[str, Dict[str, Any]] = {}
    for item in prior_build.get("generated", []):
        cached_operation = item.get("operation")
        cached_result = item.get("result")
        if not isinstance(cached_operation, dict) or not isinstance(cached_result, dict):
            continue
        try:
            cache_key = _operation_cache_key(cached_operation, baseline_by_id, evidence_by_id)
        except (KeyError, TypeError):
            continue
        cached_results[cache_key] = cached_result

    generated: List[Dict[str, Any]] = []
    for operation in operations:
        cache_key = _operation_cache_key(operation, baseline_by_id, evidence_by_id)
        result = cached_results.get(cache_key)
        if result is None:
            result = _generate_operation(client, operation, baseline_by_id, evidence_by_id, source)
        generated.append(
            {
                "operation": operation,
                "operation_cache_key": cache_key,
                "result": result,
            }
        )

    updated_by_id: Dict[str, Dict[str, Any]] = {}
    new_main: List[Dict[str, Any]] = []
    new_appendix: List[Dict[str, Any]] = []
    generated_dispositions: List[Dict[str, Any]] = []
    integration_changes: List[Dict[str, Any]] = []
    for index, item in enumerate(generated, start=1):
        operation = item["operation"]
        result = item["result"]
        target_id = operation.get("target_canonical_unit_id")
        if target_id:
            base = baseline_by_id[target_id]
            unit = {
                **base,
                "title": result["unit_title"],
                "markdown": result["generated_markdown"],
                "content_sha256": _sha256_text(result["generated_markdown"]),
                "status": "updated",
                "contributing_project_ids": [project_id],
                "change_summary": result["change_summary"],
            }
            updated_by_id[target_id] = unit
            canonical_id = target_id
            target_project_id = base["base_project_id"]
            previous_title = base["title"]
            change_type = "updated"
        else:
            canonical_id = f"CU-{hashlib.sha1(f'{proposal_id}|{index}'.encode('utf-8')).hexdigest()[:12]}"
            unit = {
                "canonical_unit_id": canonical_id,
                "title": result["unit_title"],
                "source_project_ids": [project_id],
                "base_project_id": project_id,
                "ordinal_in_project": index,
                "markdown": result["generated_markdown"],
                "content_sha256": _sha256_text(result["generated_markdown"]),
                "status": "new",
                "change_summary": result["change_summary"],
            }
            (new_appendix if operation["kind"] == "create_appendix" else new_main).append(unit)
            target_project_id = None
            previous_title = None
            change_type = "appendix" if operation["kind"] == "create_appendix" else "new"
        integration_changes.append(
            {
                "canonical_unit_id": canonical_id,
                "change_type": change_type,
                "target_project_id": target_project_id,
                "target_final_sha256": next(
                    (
                        prior.get("content_sha256")
                        for prior in proposal.get("source_snapshot", {}).get("prior_projects", [])
                        if prior.get("project_id") == target_project_id
                    ),
                    None,
                ),
                "previous_title": previous_title,
                "previous_unit_sha256": base.get("content_sha256") if target_id else None,
                "unit_title": result["unit_title"],
                "change_summary": result["change_summary"],
                "evidence_ids": operation["evidence_ids"],
                "markdown": result["generated_markdown"],
            }
        )
        for evidence_id in operation["evidence_ids"]:
            generated_dispositions.append(
                {
                    "evidence_id": evidence_id,
                    "disposition": (
                        "merged_as_extension" if target_id else "fully_represented"
                    ),
                    "canonical_unit_ids": [canonical_id],
                    "reason": operation["decisions"][0].get("reason", ""),
                }
            )

    canonical_units = [updated_by_id.get(item["canonical_unit_id"], item) for item in baseline]
    canonical_units.extend(new_main)
    canonical_units.extend(new_appendix)
    all_dispositions = [*dispositions, *generated_dispositions]
    expected_ids = set(evidence_by_id)
    disposition_ids = [item["evidence_id"] for item in all_dispositions]
    if set(disposition_ids) != expected_ids or len(disposition_ids) != len(set(disposition_ids)):
        missing = sorted(expected_ids - set(disposition_ids))
        duplicates = sorted({item for item in disposition_ids if disposition_ids.count(item) > 1})
        raise ValueError(f"Series evidence registry is invalid: missing={missing}, duplicates={duplicates}")

    draft = "\n\n".join(item["markdown"].strip() for item in canonical_units).strip() + "\n"
    built_at = _utcnow()
    canonical_plan = {
        "schema_version": 1,
        "series_id": series_id,
        "proposal_id": proposal_id,
        "source_project_id": project_id,
        "built_at": built_at,
        "units": [{key: value for key, value in item.items() if key != "markdown"} for item in canonical_units],
    }
    evidence_registry = {
        "schema_version": 1,
        "series_id": series_id,
        "proposal_id": proposal_id,
        "project_id": project_id,
        "evidence": all_dispositions,
    }
    build_result = {
        "schema_version": 1,
        "series_id": series_id,
        "project_id": project_id,
        "proposal_id": proposal_id,
        "status": "draft_ready",
        "model": OPENAI_GENERATION_MODEL,
        "merger_prompt_sha256": _sha256_text(MERGER_PROMPT_PATH.read_text(encoding="utf-8")),
        "built_at": built_at,
        "changed_unit_count": len(updated_by_id),
        "new_unit_count": len(new_main) + len(new_appendix),
        "evidence_count": len(all_dispositions),
        "integration_changes": integration_changes,
        "operations": operations,
        "generated": generated,
    }
    _save_json(root / "canonical_plan.json", canonical_plan)
    _save_json(root / "evidence_registry.json", evidence_registry)
    (root / "draft.md").write_text(draft, encoding="utf-8")
    _save_json(root / "merge_runs" / proposal_id / "build.json", build_result)
    manifest = _load_json(root / "manifest.json")
    manifest.update(
        {
            "status": "draft_ready",
            "approved_proposal_id": proposal_id,
            "draft_built_at": built_at,
            "draft_sha256": _sha256_text(draft),
            "updated_at": built_at,
        }
    )
    _save_json(root / "manifest.json", manifest)
    return build_result


def get_series_build_status(series_id: str, project_id: str) -> SeriesBuildStatus:
    key = (series_id, project_id)
    with _build_lock:
        active = _build_statuses.get(key)
        if active:
            return active.model_copy(deep=True)
    root = get_series_manuscript_dir(series_id)
    manifest = _load_json(root / "manifest.json")
    proposal_id = manifest.get("approved_proposal_id")
    if manifest.get("status") == "draft_ready" and proposal_id:
        build = _load_json(root / "merge_runs" / str(proposal_id) / "build.json")
        if build.get("project_id") == project_id:
            return SeriesBuildStatus(
                series_id=series_id,
                project_id=project_id,
                status="completed",
                message="Series Draft is ready for editorial review.",
                finished_at=build.get("built_at"),
                proposal_id=str(proposal_id),
                draft_path=str(root / "draft.md"),
                changed_unit_count=int(build.get("changed_unit_count", 0)),
                new_unit_count=int(build.get("new_unit_count", 0)),
                evidence_count=int(build.get("evidence_count", 0)),
            )
    return SeriesBuildStatus(series_id=series_id, project_id=project_id)


def queue_series_draft_build(series_id: str, project_id: str, proposal_id: str) -> Tuple[SeriesBuildStatus, bool]:
    key = (series_id, project_id)
    with _build_lock:
        existing = _build_statuses.get(key)
        if existing and existing.status in {"queued", "running"}:
            return existing.model_copy(deep=True), False
        status = SeriesBuildStatus(
            series_id=series_id,
            project_id=project_id,
            proposal_id=proposal_id,
            status="queued",
            message="Series Draft build is queued.",
            started_at=_utcnow(),
        )
        _build_statuses[key] = status
        return status.model_copy(deep=True), True


def run_series_draft_build(series_id: str, project_id: str, proposal_id: str) -> None:
    key = (series_id, project_id)
    with _build_lock:
        status = _build_statuses[key]
        status.status = "running"
        status.message = "Merging approved evidence into the Series Draft…"
    try:
        result = build_series_draft(series_id, project_id, proposal_id)
        with _build_lock:
            status = _build_statuses[key]
            status.status = "completed"
            status.message = "Series Draft is ready for editorial review."
            status.finished_at = result["built_at"]
            status.draft_path = str(get_series_manuscript_dir(series_id) / "draft.md")
            status.changed_unit_count = result["changed_unit_count"]
            status.new_unit_count = result["new_unit_count"]
            status.evidence_count = result["evidence_count"]
    except Exception as exc:
        with _build_lock:
            status = _build_statuses[key]
            status.status = "failed"
            status.message = str(exc)
            status.finished_at = _utcnow()
