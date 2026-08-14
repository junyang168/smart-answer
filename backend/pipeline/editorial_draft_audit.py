"""Deterministic audit for an editorial manuscript draft.

The audit does not decide whether the editor's prose is theologically correct.
It verifies the parts that code can prove: a draft covers every explicit
composition decision, every routed claim exists, and every claim can be
traced through an evidence step to a version-bound source fragment.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


AUDIT_SCHEMA_VERSION = "editorial-draft-audit.v1"
VALID_ANCHOR_STATES = {
    "canonical_citation_bound",
    "source_version_bound",
    "valid",
    "current",
}


class EditorialDraftAuditError(ValueError):
    """Raised when audit configuration or required artifacts are invalid."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EditorialDraftAuditError(f"找不到审核资料：{path}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(encoded.encode("utf-8"))


def _headings(markdown: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r"^(#{1,6})\s+(.+?)\s*#*\s*$", markdown, flags=re.MULTILINE):
        rows.append(
            {
                "level": len(match.group(1)),
                "text": match.group(2).strip(),
                "start": match.start(),
                "content_start": match.end(),
            }
        )
    for index, row in enumerate(rows):
        end = len(markdown)
        for following in rows[index + 1 :]:
            if following["level"] <= row["level"]:
                end = following["start"]
                break
        row["content"] = markdown[row["content_start"] : end]
    return rows


def _finding(
    code: str,
    severity: str,
    title: str,
    detail: str,
    *,
    decision_id: str | None = None,
    claim_id: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "severity": severity,
        "title": title,
        "detail": detail,
        "decision_id": decision_id,
        "claim_id": claim_id,
    }


def _resolve_inside(base: Path, relative_path: str) -> Path:
    candidate = (base / relative_path).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError as exc:
        raise EditorialDraftAuditError(f"审核路径越出 manifest 目录：{relative_path}") from exc
    return candidate


def audit_editorial_draft(manifest_path: Path, draft_id: str) -> dict[str, Any]:
    """Audit one draft declared in an editorial draft manifest."""
    manifest_path = manifest_path.resolve()
    manifest = _read_json(manifest_path)
    draft = next(
        (item for item in manifest.get("drafts", []) if str(item.get("draft_id")) == draft_id),
        None,
    )
    if not draft:
        raise EditorialDraftAuditError(f"manifest 中找不到初稿：{draft_id}")

    config = draft.get("audit_config") or {}
    plan_id = str(config.get("plan_id") or draft.get("candidate_id") or "").strip()
    if not plan_id:
        raise EditorialDraftAuditError("初稿没有 audit_config.plan_id。")
    snapshot_relative = str(config.get("knowledge_snapshot_path") or "").strip()
    if not snapshot_relative:
        raise EditorialDraftAuditError("初稿没有 audit_config.knowledge_snapshot_path。")

    base = manifest_path.parent
    draft_path = _resolve_inside(base, str(draft.get("relative_path") or ""))
    snapshot_path = _resolve_inside(base, snapshot_relative)
    markdown = draft_path.read_text(encoding="utf-8")
    snapshot = _read_json(snapshot_path)
    headings = _headings(markdown)
    heading_by_text = {row["text"]: row for row in headings}

    plans = {str(item.get("plan_id")): item for item in snapshot.get("product_plans", [])}
    plan = plans.get(plan_id)
    if not plan:
        raise EditorialDraftAuditError(f"共享知识快照中找不到编排计划：{plan_id}")

    claims = {str(item.get("claim_id")): item for item in snapshot.get("claims", [])}
    evidence = {
        str(item.get("evidence_step_id")): item for item in snapshot.get("evidence_steps", [])
    }
    fragments = {
        str(item.get("fragment_id")): item for item in snapshot.get("source_fragments", [])
    }
    decisions = {
        str(item.get("decision_id")): item for item in plan.get("decisions", [])
    }
    mappings = config.get("decision_sections") or []
    mapping_by_id = {str(item.get("decision_id")): item for item in mappings}
    findings: list[dict[str, Any]] = []

    required_sections = [str(value).strip() for value in config.get("required_top_level_sections", [])]
    present_headings = {row["text"] for row in headings}
    for section in required_sections:
        if section not in present_headings:
            findings.append(
                _finding(
                    "missing_required_section",
                    "error",
                    f"缺少「{section}」栏目",
                    "初稿必须保持既定的出版结构。",
                )
            )

    plan_ids = set(decisions)
    mapped_ids = set(mapping_by_id)
    for missing_id in sorted(plan_ids - mapped_ids):
        findings.append(
            _finding(
                "unmapped_decision",
                "error",
                "编排决定尚未对应到正文",
                "请在 manifest 中明确指定该决定对应的 Markdown 小标题。",
                decision_id=missing_id,
            )
        )
    for unknown_id in sorted(mapped_ids - plan_ids):
        findings.append(
            _finding(
                "unknown_decision",
                "error",
                "初稿引用了不存在的编排决定",
                "请修正 manifest 中的 decision_id。",
                decision_id=unknown_id,
            )
        )

    decision_results: list[dict[str, Any]] = []
    checked_claims: set[str] = set()
    checked_evidence: set[str] = set()
    checked_fragments: set[str] = set()
    valid_fragments: set[str] = set()

    for decision_id, decision in decisions.items():
        mapping = mapping_by_id.get(decision_id) or {}
        markdown_heading = str(mapping.get("markdown_heading") or "").strip()
        heading = heading_by_text.get(markdown_heading)
        decision_findings_before = len(findings)
        if not heading:
            findings.append(
                _finding(
                    "missing_decision_heading",
                    "error",
                    "正文缺少编排段落",
                    f"未找到小标题：{markdown_heading or '（尚未配置）'}",
                    decision_id=decision_id,
                )
            )

        claim_ids = [str(value) for value in decision.get("claim_ids", []) if value]
        if not claim_ids:
            findings.append(
                _finding(
                    "decision_without_claims",
                    "error",
                    "编排段落没有共享主张",
                    "没有论证依据的段落不能进入出版初稿。",
                    decision_id=decision_id,
                )
            )

        decision_claims = 0
        decision_evidence = 0
        decision_valid_fragments = 0
        for claim_id in claim_ids:
            checked_claims.add(claim_id)
            claim = claims.get(claim_id)
            if not claim:
                findings.append(
                    _finding(
                        "missing_claim",
                        "error",
                        "共享主张不存在",
                        "编排计划引用的主张不在当前知识快照中。",
                        decision_id=decision_id,
                        claim_id=claim_id,
                    )
                )
                continue
            decision_claims += 1
            evidence_ids = [str(value) for value in claim.get("evidence_step_ids", []) if value]
            if not evidence_ids:
                findings.append(
                    _finding(
                        "claim_without_evidence",
                        "error",
                        "主张没有证据步骤",
                        str(claim.get("statement") or claim.get("title") or claim_id),
                        decision_id=decision_id,
                        claim_id=claim_id,
                    )
                )
                continue
            eligible_evidence_count = 0
            for evidence_id in evidence_ids:
                checked_evidence.add(evidence_id)
                step = evidence.get(evidence_id)
                if not step:
                    findings.append(
                        _finding(
                            "missing_evidence_step",
                            "error",
                            "证据步骤不存在",
                            f"共享主张引用的证据步骤 {evidence_id} 不在快照中。",
                            decision_id=decision_id,
                            claim_id=claim_id,
                        )
                    )
                    continue
                decision_evidence += 1
                eligibility = str(step.get("support_eligibility") or "").strip()
                if eligibility.startswith("eligible"):
                    eligible_evidence_count += 1
                fragment_ids = [str(value) for value in step.get("source_fragment_ids", []) if value]
                if not fragment_ids and step.get("source_fragment_id"):
                    fragment_ids = [str(step["source_fragment_id"])]
                if not fragment_ids:
                    findings.append(
                        _finding(
                            "evidence_without_source_fragment",
                            "error",
                            "证据没有原始来源定位",
                            f"证据步骤 {evidence_id} 无法回到笔记或讲道。",
                            decision_id=decision_id,
                            claim_id=claim_id,
                        )
                    )
                for fragment_id in fragment_ids:
                    checked_fragments.add(fragment_id)
                    fragment = fragments.get(fragment_id)
                    if not fragment:
                        findings.append(
                            _finding(
                                "missing_source_fragment",
                                "error",
                                "来源片段不存在",
                                f"找不到来源片段 {fragment_id}。",
                                decision_id=decision_id,
                                claim_id=claim_id,
                            )
                        )
                        continue
                    anchor_state = str(fragment.get("anchor_state") or "").strip()
                    if anchor_state not in VALID_ANCHOR_STATES:
                        findings.append(
                            _finding(
                                "invalid_source_anchor",
                                "error",
                                "来源定位已失效或未经版本绑定",
                                f"来源片段的定位状态为 {anchor_state or '未标记'}。",
                                decision_id=decision_id,
                                claim_id=claim_id,
                            )
                        )
                    else:
                        valid_fragments.add(fragment_id)
                        decision_valid_fragments += 1

            # A claim may legitimately retain audience remarks or other
            # contextual material.  Such evidence cannot support the claim,
            # but its presence must not make an otherwise supported claim
            # fail.  The gate is therefore claim-level: at least one evidence
            # step must be explicitly eligible.
            if eligible_evidence_count == 0:
                findings.append(
                    _finding(
                        "claim_without_eligible_evidence",
                        "error",
                        "主张没有合格的支持证据",
                        "现有证据只可作为背景，不能独立支持这条主张。",
                        decision_id=decision_id,
                        claim_id=claim_id,
                    )
                )

        if decision.get("topic_route_ids"):
            body = str((heading or {}).get("content") or "")
            links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", body)
            if not links:
                findings.append(
                    _finding(
                        "missing_topic_link",
                        "warning",
                        "专题延伸尚未加入链接",
                        "编排决定要求把完整讨论导向专题。",
                        decision_id=decision_id,
                    )
                )
            elif all("待建立" in link for link in links):
                findings.append(
                    _finding(
                        "placeholder_topic_link",
                        "warning",
                        "专题链接仍是占位符",
                        "正文结构已完成，但正式发布前需要替换为实际专题页面。",
                        decision_id=decision_id,
                    )
                )

        decision_results.append(
            {
                "decision_id": decision_id,
                "passage": decision.get("passage") or "",
                "section_title": decision.get("section_title") or "",
                "markdown_heading": markdown_heading,
                "heading_found": bool(heading),
                "claim_count": decision_claims,
                "evidence_step_count": decision_evidence,
                "valid_source_fragment_count": decision_valid_fragments,
                "finding_count": len(findings) - decision_findings_before,
            }
        )

    errors = sum(item["severity"] == "error" for item in findings)
    warnings = sum(item["severity"] == "warning" for item in findings)
    status = "fail" if errors else "pass_with_warnings" if warnings else "pass"
    output = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "draft_id": draft_id,
        "plan_id": plan_id,
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "结构、论证可追溯性与来源锚点；不代替神学判断或逐句语义忠实度审核。",
        "fingerprint": {
            "draft_sha256": _sha256_bytes(markdown.encode("utf-8")),
            "knowledge_snapshot_sha256": _sha256_bytes(snapshot_path.read_bytes()),
            "audit_config_sha256": _sha256_json(config),
        },
        "summary": {
            "decision_total": len(decisions),
            "decision_headings_found": sum(item["heading_found"] for item in decision_results),
            "claim_total": len(checked_claims),
            "evidence_step_total": len(checked_evidence),
            "source_fragment_total": len(checked_fragments),
            "valid_source_fragment_total": len(valid_fragments),
            "error_total": errors,
            "warning_total": warnings,
        },
        "decisions": decision_results,
        "findings": findings,
    }
    return output


def write_editorial_draft_audit(manifest_path: Path, draft_id: str) -> Path:
    manifest = _read_json(manifest_path)
    draft = next(
        (item for item in manifest.get("drafts", []) if str(item.get("draft_id")) == draft_id),
        None,
    )
    if not draft:
        raise EditorialDraftAuditError(f"manifest 中找不到初稿：{draft_id}")
    output_relative = str((draft.get("audit_config") or {}).get("audit_output_path") or "editorial-draft-audit.json")
    output_path = _resolve_inside(manifest_path.resolve().parent, output_relative)
    output = audit_editorial_draft(manifest_path, draft_id)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path
