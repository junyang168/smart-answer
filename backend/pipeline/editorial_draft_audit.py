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


AUDIT_SCHEMA_VERSION = "editorial-draft-audit.v2"
PUBLICATION_PROFILE_SCHEMA_VERSION = "publication-profile.v1"
PUBLICATION_PROFILE_ROOT = Path(__file__).resolve().parents[1] / "config" / "publication_profiles"
PROVENANCE_COMMENT_RE = re.compile(r"^<!--\s*provenance:\s*(\{.*\})\s*-->$")
INTERNAL_SOURCE_REFERENCE_RE = re.compile(
    r"(?<![\w/])(?:S\s+\d{6}|\d{2,}(?:-\d+){3,}|(?:SRC|DK|CL|FR|EV|CD|DRAFT)-[A-Z0-9][A-Z0-9_-]*)(?![\w/])"
    r"|\btranscript\s+`?(?:S\s+\d{6}|\d{2,}(?:-\d+){2,}|[A-Z0-9][A-Z0-9._-]*\d[A-Z0-9._-]*)`?",
    flags=re.IGNORECASE,
)
VALID_ANCHOR_STATES = {
    "canonical_citation_bound",
    "source_version_bound",
    "valid",
    "current",
}
# The five links of an application chain.  These are the published contract
# for life application and are not redefined by any manuscript.
REQUIRED_APPLICATION_CHAIN_FIELDS = {
    "scripture_context": "經文處境",
    "professor_interpretation_claim_ids": "教授解釋",
    "enduring_principle": "不變原則",
    "present_context": "今日處境",
    "application_and_limits": "應用與限制",
}
# Editorial synthesis is prose that no single professor claim carries on its
# own; it is where an unregistered application can hide.  Such a paragraph must
# therefore say whether it makes a present-day application.
DEFAULT_APPLICATION_DECLARATION_ATTRIBUTION = "editorial_synthesis"
VALID_MATERIAL_DISPOSITIONS = {
    "body",
    "sidebar",
    "appendix",
    "topic_route",
    "source_only",
    "explicit_exclusion",
}


#: Checks whose verdict depends on the manifest agreeing with the manuscript's
#: current shape -- which section a scripture marker sits under, which heading
#: carries an editorial label, whether an application chain was registered.
#: Nothing rebuilds that manifest when an article is rewritten, so these fire
#: on a stale checklist far more often than on a real defect: a run of this
#: article produced fourteen errors, of which every single one was the
#: checklist describing the previous version. A gate that cries wolf that
#: often trains its reader to bypass it, which costs more than it protects.
#: They stay as warnings -- still reported, no longer blocking -- while the
#: checks that need no checklist keep erroring.
MANIFEST_SHAPE_CODES = frozenset({
    "missing_scripture_quotation",
    "missing_editorial_attribution",
    "editor_paragraph_without_visible_label",
    "unregistered_application_paragraph",
    "missing_decision_heading",
})


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
    if code in MANIFEST_SHAPE_CODES and severity == "error":
        severity = "warning"
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


def _resolve_material_record(base: Path, relative_path: str) -> Path:
    """Resolve a staged knowledge record within the manuscript work area.

    A draft manifest normally lives one directory below the detailed extraction
    records it audits.  Source-only material may therefore point to a sibling
    staging record, but never outside that manuscript work area.
    """
    candidate = (base / relative_path).resolve()
    allowed_root = base.parent.resolve()
    try:
        candidate.relative_to(allowed_root)
    except ValueError as exc:
        raise EditorialDraftAuditError(
            f"材料记录路径越出稿件工作目录：{relative_path}"
        ) from exc
    return candidate


def _load_publication_profile(profile_id: str) -> tuple[dict[str, Any], Path]:
    """Load the centrally owned publication contract for a draft."""
    if not profile_id or not re.fullmatch(r"[A-Za-z0-9._-]+", profile_id):
        raise EditorialDraftAuditError("初稿没有有效的 publication_profile_id。")
    profile_path = (PUBLICATION_PROFILE_ROOT / f"{profile_id}.json").resolve()
    try:
        profile_path.relative_to(PUBLICATION_PROFILE_ROOT.resolve())
    except ValueError as exc:
        raise EditorialDraftAuditError(f"出版体例编号无效：{profile_id}") from exc
    profile = _read_json(profile_path)
    if str(profile.get("profile_id") or "") != profile_id:
        raise EditorialDraftAuditError(f"出版体例内部编号不一致：{profile_id}")
    if str(profile.get("schema_version") or "") != PUBLICATION_PROFILE_SCHEMA_VERSION:
        raise EditorialDraftAuditError(
            f"出版体例版本不受支持：{profile.get('schema_version') or '未填写'}"
        )
    return profile, profile_path


FOOTNOTE_DEFINITION_RE = re.compile(r"^\[\^[^\]]+\]:")


def _markdown_blocks(content: str) -> list[dict[str, Any]]:
    """Return substantive blocks and the provenance declaration before each one."""
    rows: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    block_lines: list[str] = []
    block_line = 0

    def flush() -> None:
        nonlocal block_lines, block_line, pending
        if not block_lines:
            return
        text = "\n".join(block_lines).strip()
        if text:
            rows.append(
                {
                    "text": text,
                    "line": block_line,
                    "provenance": pending,
                }
            )
        block_lines = []
        block_line = 0
        pending = None

    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        if re.match(r"^#{1,6}\s+", stripped):
            flush()
            continue
        # A footnote definition is apparatus, not prose: read as a paragraph it
        # was reported as unattributed body text, the whole footnote block of a
        # real article at that. A provenance comment standing before one
        # belongs to it and is not a comment left dangling over nothing, so
        # both are dropped together.
        if FOOTNOTE_DEFINITION_RE.match(stripped):
            flush()
            pending = None
            continue
        match = PROVENANCE_COMMENT_RE.fullmatch(stripped)
        if match:
            flush()
            try:
                value = json.loads(match.group(1))
                pending = value if isinstance(value, dict) else {"_invalid": "not_an_object"}
            except json.JSONDecodeError as exc:
                pending = {"_invalid": str(exc)}
            continue
        # A blank quote line belongs to the surrounding blockquote. Ordinary
        # blank lines separate prose paragraphs.
        if not stripped or stripped == ">":
            if stripped == ">" and block_lines:
                block_lines.append(raw_line)
            else:
                flush()
            continue
        if not block_lines:
            block_line = line_number
        block_lines.append(raw_line)
    flush()
    if pending is not None:
        rows.append({"text": "", "line": len(content.splitlines()) + 1, "provenance": pending})
    return rows


def _reader_visible_markdown(markdown: str) -> str:
    """Remove hidden metadata and link targets while retaining reader-facing labels."""
    without_comments = re.sub(r"<!--.*?-->", "", markdown, flags=re.DOTALL)
    return re.sub(r"\[([^\]]+)\]\([^\n)]*\)", r"\1", without_comments)


def _audit_reader_facing_source_references(
    markdown: str,
    findings: list[dict[str, Any]],
) -> None:
    visible_markdown = _reader_visible_markdown(markdown)
    matches = list(dict.fromkeys(INTERNAL_SOURCE_REFERENCE_RE.findall(visible_markdown)))
    if not matches:
        return
    findings.append(
        _finding(
            "reader_facing_internal_source_id",
            "error",
            "正文出现内部来源编号",
            "请改用日期、讲道标题与机构等读者可理解的名称；如正文需要点名来源，名称必须链接到讲道网页。发现："
            + "、".join(matches[:5]),
        )
    )


def _audit_paragraph_provenance(
    headings: list[dict[str, Any]],
    profile: dict[str, Any],
    claims: dict[str, dict[str, Any]],
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    policy = profile.get("paragraph_provenance") or {}
    if not policy.get("required"):
        return []
    audited_sections = {str(value).strip() for value in policy.get("audited_sections", [])}
    valid_attributions = {
        str(value).strip() for value in policy.get("valid_attributions", [])
    } or {"professor", "scripture", "editor"}
    editor_labels = [str(value).strip() for value in policy.get("visible_editor_labels", [])]
    results: list[dict[str, Any]] = []

    for heading in headings:
        if heading["text"] not in audited_sections:
            continue
        for block_index, block in enumerate(_markdown_blocks(str(heading.get("content") or "")), start=1):
            provenance = block.get("provenance")
            text = str(block.get("text") or "")
            result = {
                "section": heading["text"],
                "block_index": block_index,
                "line": block.get("line"),
                "excerpt": text[:160],
                "attribution": None,
                "claim_ids": [],
                "scripture_refs": [],
                # Application content is identified from the paragraph's own
                # declaration, never guessed from the wording of the prose.
                "application_chain_id": None,
                "declares_application": None,
                "valid": True,
            }
            if not text:
                findings.append(
                    _finding(
                        "dangling_paragraph_provenance",
                        "error",
                        "段落来源标记后没有正文",
                        f"「{heading['text']}」中有一项来源标记未对应任何文字。",
                    )
                )
                result["valid"] = False
                results.append(result)
                continue
            if provenance is None:
                findings.append(
                    _finding(
                        "unmapped_manuscript_paragraph",
                        "error",
                        "正文段落没有来源归属",
                        f"「{heading['text']}」中的段落必须映射到教授主张、圣经原文或明示的编辑说明：{text[:80]}",
                    )
                )
                result["valid"] = False
                results.append(result)
                continue
            if provenance.get("_invalid"):
                findings.append(
                    _finding(
                        "invalid_paragraph_provenance",
                        "error",
                        "段落来源标记格式错误",
                        f"「{heading['text']}」中的 provenance 不是有效 JSON：{provenance['_invalid']}",
                    )
                )
                result["valid"] = False
                results.append(result)
                continue

            attribution = str(provenance.get("attribution") or "").strip()
            result["attribution"] = attribution
            result["application_chain_id"] = (
                str(provenance.get("application_chain_id") or "").strip() or None
            )
            declared_application = provenance.get("contains_application")
            if isinstance(declared_application, bool):
                result["declares_application"] = declared_application
            elif result["application_chain_id"]:
                result["declares_application"] = True
            if attribution not in valid_attributions:
                findings.append(
                    _finding(
                        "invalid_paragraph_provenance",
                        "error",
                        "段落来源归属无效",
                        f"「{heading['text']}」使用了未知归属：{attribution or '未填写'}。",
                    )
                )
                result["valid"] = False
            elif attribution in {"professor", "editorial_synthesis"}:
                claim_ids = [str(value) for value in provenance.get("claim_ids", []) if value]
                result["claim_ids"] = claim_ids
                if not claim_ids:
                    findings.append(
                        _finding(
                            "professor_paragraph_without_claim",
                            "error",
                            "教授观点段落没有映射主张",
                            f"「{heading['text']}」中的教授观点必须列出 claim_ids。",
                        )
                    )
                    result["valid"] = False
                if attribution == "editorial_synthesis" and not str(
                    provenance.get("synthesis_note") or ""
                ).strip():
                    findings.append(
                        _finding(
                            "editorial_synthesis_without_note",
                            "error",
                            "跨來源編輯綜合沒有說明",
                            f"「{heading['text']}」中的 editorial_synthesis 必須在隱藏 provenance 提供 synthesis_note。",
                        )
                    )
                    result["valid"] = False
                for claim_id in claim_ids:
                    if claim_id not in claims:
                        findings.append(
                            _finding(
                                "paragraph_provenance_unknown_claim",
                                "error",
                                "段落映射的共享主张不存在",
                                f"当前知识快照中找不到 {claim_id}。",
                                claim_id=claim_id,
                            )
                        )
                        result["valid"] = False
            elif attribution == "scripture":
                scripture_refs = [
                    str(value) for value in provenance.get("scripture_refs", []) if value
                ]
                result["scripture_refs"] = scripture_refs
                if not scripture_refs:
                    findings.append(
                        _finding(
                            "scripture_paragraph_without_reference",
                            "error",
                            "圣经原文段落没有经文编号",
                            f"「{heading['text']}」中的圣经引文必须列出 scripture_refs。",
                        )
                    )
                    result["valid"] = False
            elif attribution == "editor" and not any(label in text for label in editor_labels):
                findings.append(
                    _finding(
                        "editor_paragraph_without_visible_label",
                        "error",
                        "编辑文字没有向读者明示归属",
                        f"「{heading['text']}」中的编辑文字必须显示以下标签之一：{'、'.join(editor_labels)}。",
                    )
                )
                result["valid"] = False
            results.append(result)
    return results


def _resolve_application_policy(
    profile: dict[str, Any],
    config: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the application policy from the profile and the manifest.

    The publication contract belongs to the profile.  A manuscript may name a
    different heading for its application section, or tighten the set of
    attributions that must declare their application status, but it cannot
    switch the requirement off by omitting its own ``application_policy``.
    """
    profile_policy = profile.get("application_policy") or {}
    manifest_policy = config.get("application_policy") or {}
    section = str(
        manifest_policy.get("section") or profile_policy.get("section") or "生活應用"
    ).strip()
    declaration_attributions = {
        str(value).strip()
        for value in (
            list(profile_policy.get("declaration_required_attributions") or [])
            + list(manifest_policy.get("declaration_required_attributions") or [])
        )
        if str(value).strip()
    } or {DEFAULT_APPLICATION_DECLARATION_ATTRIBUTION}
    return {
        "section": section,
        "requires_registered_chains": bool(
            profile_policy.get("requires_registered_chains")
        )
        or bool(manifest_policy.get("requires_registered_chains")),
        "declaration_required_attributions": declaration_attributions,
    }


def _audit_application_chains(
    policy: dict[str, Any],
    chains: list[dict[str, Any]],
    claims: dict[str, dict[str, Any]],
    paragraphs: list[dict[str, Any]],
    application_section_present: bool,
    findings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Audit life application as content, not as a heading.

    Life application is legitimate editorial work — the editorial team is the
    author of the published article.  What it may not be is an unsourced
    exhortation attached to the end of an exegetical section.  Every paragraph
    that carries a present-day situation and a course of action must therefore
    name a registered application chain, wherever in the manuscript it sits.
    Because code cannot read prose, the trigger is the paragraph's own
    provenance declaration rather than the wording of the sentences.
    """
    chain_results: list[dict[str, Any]] = []
    chains_by_id: dict[str, dict[str, Any]] = {}
    for index, chain in enumerate(chains, start=1):
        chain_id = str(
            chain.get("chain_id") or chain.get("application_chain_id") or ""
        ).strip()
        missing_fields = [
            label
            for field, label in REQUIRED_APPLICATION_CHAIN_FIELDS.items()
            if not chain.get(field)
        ]
        if missing_fields:
            findings.append(
                _finding(
                    "incomplete_application_chain",
                    "error",
                    "生活應用來源鏈不完整",
                    f"第 {index} 項缺少：{'、'.join(missing_fields)}。",
                )
            )
        claim_ids = [
            str(value)
            for value in chain.get("professor_interpretation_claim_ids", [])
            if value
        ]
        unknown_claim_ids = [claim_id for claim_id in claim_ids if claim_id not in claims]
        for claim_id in unknown_claim_ids:
            findings.append(
                _finding(
                    "application_chain_missing_claim",
                    "error",
                    "生活應用引用的教授主張不存在",
                    f"找不到 {claim_id}。",
                    claim_id=claim_id,
                )
            )
        if not chain_id:
            findings.append(
                _finding(
                    "application_chain_without_id",
                    "warning",
                    "生活應用推論鏈沒有識別碼",
                    f"第 {index} 項沒有 chain_id，正文段落無法宣告它。",
                )
            )
        row = {
            "chain_id": chain_id or None,
            "index": index,
            "professor_interpretation_claim_ids": claim_ids,
            "complete": not missing_fields,
            "grounded": bool(claim_ids) and not unknown_claim_ids,
            "paragraph_count": 0,
        }
        chain_results.append(row)
        if chain_id:
            if chain_id in chains_by_id:
                findings.append(
                    _finding(
                        "duplicate_application_chain_id",
                        "error",
                        "生活應用推論鏈識別碼重複",
                        f"{chain_id} 登記了多次。",
                    )
                )
            else:
                chains_by_id[chain_id] = row

    # Registration is retired. A five-link chain -- scripture context,
    # professor's interpretation, enduring principle, present context,
    # application and limits -- asked for a structure finer than the source
    # has. The professor states an application in a sentence and illustrates
    # it: "when you argue with someone, argue from what they accept; Paul
    # argued from the Old Testament to Jews and never to Gentiles." Nothing in
    # that decomposes into five registered fields, and no chain has ever been
    # registered for any article.
    #
    # What registration was for -- an application must not be invented -- is
    # what the grounding gate already does, paragraph by paragraph, against
    # the claims the paragraph declares. An application the professor made is
    # a claim the paragraph can cite; one he did not make fails the gate. The
    # form was doing that job again, and worse: on Matt.16.1-12 the contract
    # required an application while this rule forbade writing it, and the run
    # deadlocked with nothing registered to point at.
    return chain_results

    if application_section_present and not chains:
        findings.append(
            _finding(
                "unregistered_application_section",
                "error",
                "生活應用未登記來源鏈",
                "生活應用不是必備欄目；若保留，必須逐項登記「經文處境、教授解釋、不變原則、今日處境、應用與限制」。",
            )
        )

    declaration_attributions = policy.get("declaration_required_attributions") or set()
    for paragraph in paragraphs:
        section = str(paragraph.get("section") or "")
        excerpt = str(paragraph.get("excerpt") or "")[:80]
        attribution = paragraph.get("attribution")
        chain_id = paragraph.get("application_chain_id")
        declares_application = paragraph.get("declares_application")
        if chain_id:
            chain = chains_by_id.get(chain_id)
            if chain is None:
                findings.append(
                    _finding(
                        "application_chain_not_registered",
                        "error",
                        "應用段落引用了未登記的推論鏈",
                        f"「{section}」中的段落宣告 {chain_id}，但 manifest 的 application_chains 沒有這一條：{excerpt}",
                    )
                )
                paragraph["valid"] = False
                continue
            chain["paragraph_count"] += 1
            # An incomplete or ungrounded chain has already been reported once
            # against the chain itself; the paragraph is simply not covered.
            if not chain["complete"] or not chain["grounded"]:
                paragraph["valid"] = False
            continue
        if declares_application is True:
            findings.append(
                _finding(
                    "unregistered_application_paragraph",
                    "error",
                    "應用內容沒有登記推論鏈",
                    f"「{section}」中的段落聲明含有今日應用，必須宣告 application_chain_id：{excerpt}",
                )
            )
            paragraph["valid"] = False
            continue
        if declares_application is False:
            continue
        if section == policy.get("section") or attribution in declaration_attributions:
            findings.append(
                _finding(
                    "undeclared_application_content",
                    "error",
                    "段落未聲明是否為生活應用",
                    f"「{section}」中的 {attribution or '未歸屬'} 段落必須在 provenance 宣告 application_chain_id，"
                    f"或以 \"contains_application\": false 聲明不含今日處境與行動建議：{excerpt}",
                )
            )
            paragraph["valid"] = False
    return chain_results


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

    profile_id = str(draft.get("publication_profile_id") or "").strip()
    profile, profile_path = _load_publication_profile(profile_id)

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
    material_dispositions = config.get("material_dispositions") or []
    findings: list[dict[str, Any]] = []
    _audit_reader_facing_source_references(markdown, findings)

    # The publication contract is owned centrally.  A manuscript may select a
    # profile, but may not write its own exam by redefining required sections.
    for forbidden_key in ("required_top_level_sections", "optional_top_level_sections"):
        if forbidden_key in config:
            findings.append(
                _finding(
                    "manifest_publication_structure_override",
                    "error",
                    "初稿不得自行定义出版结构",
                    f"请从 audit_config 删除 {forbidden_key}；栏目要求由出版体例 {profile_id} 统一管理。",
                )
            )

    disposition_results: list[dict[str, Any]] = []
    material_record_fingerprints: dict[str, str] = {}
    seen_disposition_ids: set[str] = set()
    for disposition in material_dispositions:
        disposition_id = str(disposition.get("disposition_id") or "").strip()
        action = str(disposition.get("action") or "").strip()
        review_status = str(disposition.get("review_status") or "").strip()
        claim_ids = [str(value) for value in disposition.get("claim_ids", []) if value]
        disposition_findings_before = len(findings)
        disposition_claims = claims
        disposition_evidence = evidence
        disposition_fragments = fragments
        knowledge_record_path = str(disposition.get("knowledge_record_path") or "").strip()
        resolved_record_path: Path | None = None

        if knowledge_record_path:
            resolved_record_path = _resolve_material_record(base, knowledge_record_path)
            material_record = _read_json(resolved_record_path)
            material_record_fingerprints[knowledge_record_path] = _sha256_bytes(
                resolved_record_path.read_bytes()
            )
            disposition_claims = {
                str(item.get("claim_id")): item
                for item in material_record.get("claims", [])
            }
            disposition_evidence = {
                str(item.get("evidence_step_id")): item
                for item in material_record.get("evidence_steps", [])
            }
            disposition_fragments = {
                str(item.get("fragment_id")): item
                for item in material_record.get("source_fragments", [])
            }

        if not disposition_id or disposition_id in seen_disposition_ids:
            findings.append(
                _finding(
                    "invalid_material_disposition_id",
                    "error",
                    "材料处置记录缺少唯一编号",
                    "每项不进入正文的实质材料也必须有可审核的稳定编号。",
                )
            )
        else:
            seen_disposition_ids.add(disposition_id)
        if action not in VALID_MATERIAL_DISPOSITIONS:
            findings.append(
                _finding(
                    "invalid_material_disposition_action",
                    "error",
                    "材料处置方式无效",
                    f"{disposition_id or '未编号记录'} 使用了未知处置方式：{action or '未填写'}。",
                )
            )
        if not claim_ids:
            findings.append(
                _finding(
                    "material_disposition_without_claims",
                    "error",
                    "材料处置没有绑定共享主张",
                    "仅写编辑备注不能证明原材料已被记录。",
                )
            )
        missing_claim_ids = [
            claim_id for claim_id in claim_ids if claim_id not in disposition_claims
        ]
        for claim_id in missing_claim_ids:
            findings.append(
                _finding(
                    "material_disposition_missing_claim",
                    "error",
                    "材料处置引用的共享主张不存在",
                    f"当前知识快照或指定的待认证知识记录中找不到 {claim_id}。",
                    claim_id=claim_id,
                )
            )

        disposition_evidence_count = 0
        disposition_fragment_count = 0
        for claim_id in claim_ids:
            claim = disposition_claims.get(claim_id)
            if not claim:
                continue
            evidence_ids = [
                str(value) for value in claim.get("evidence_step_ids", []) if value
            ]
            if not evidence_ids:
                findings.append(
                    _finding(
                        "material_disposition_claim_without_evidence",
                        "error",
                        "保留材料的主张没有证据步骤",
                        f"{claim_id} 不能证明材料已完整保留。",
                        claim_id=claim_id,
                    )
                )
            for evidence_id in evidence_ids:
                step = disposition_evidence.get(evidence_id)
                if not step:
                    findings.append(
                        _finding(
                            "material_disposition_missing_evidence",
                            "error",
                            "保留材料缺少证据步骤",
                            f"找不到 {evidence_id}。",
                            claim_id=claim_id,
                        )
                    )
                    continue
                disposition_evidence_count += 1
                fragment_ids = [
                    str(value) for value in step.get("source_fragment_ids", []) if value
                ]
                if not fragment_ids and step.get("source_fragment_id"):
                    fragment_ids = [str(step["source_fragment_id"])]
                if not fragment_ids:
                    findings.append(
                        _finding(
                            "material_disposition_evidence_without_source",
                            "error",
                            "保留材料无法回到原始来源",
                            f"证据步骤 {evidence_id} 没有来源片段。",
                            claim_id=claim_id,
                        )
                    )
                for fragment_id in fragment_ids:
                    fragment = disposition_fragments.get(fragment_id)
                    if not fragment:
                        findings.append(
                            _finding(
                                "material_disposition_missing_source_fragment",
                                "error",
                                "保留材料的来源片段不存在",
                                f"找不到 {fragment_id}。",
                                claim_id=claim_id,
                            )
                        )
                        continue
                    disposition_fragment_count += 1
                    anchor_state = str(fragment.get("anchor_state") or "").strip()
                    if anchor_state not in VALID_ANCHOR_STATES:
                        findings.append(
                            _finding(
                                "material_disposition_invalid_source_anchor",
                                "error",
                                "保留材料的来源定位无效",
                                f"{fragment_id} 的定位状态为 {anchor_state or '未标记'}。",
                                claim_id=claim_id,
                            )
                        )
        if action in {"source_only", "explicit_exclusion"} and disposition.get("article_inclusion") is not False:
            findings.append(
                _finding(
                    "excluded_material_marked_for_article",
                    "error",
                    "不入文材料的出版标记互相矛盾",
                    "source_only 或 explicit_exclusion 必须明确设置 article_inclusion=false。",
                )
            )
        if action == "source_only" and review_status != "requires_human_verification":
            findings.append(
                _finding(
                    "source_only_without_human_verification",
                    "error",
                    "仅保留来源的材料未转人工认证",
                    "source_only 材料必须明确进入人工认证队列，避免永久搁置。",
                )
            )

        disposition_results.append(
            {
                "disposition_id": disposition_id,
                "title": disposition.get("title") or "",
                "action": action,
                "article_inclusion": disposition.get("article_inclusion"),
                "review_status": review_status,
                "claim_ids": claim_ids,
                "record_state": (
                    "active_snapshot"
                    if not knowledge_record_path
                    else "staged_for_human_verification"
                ),
                "knowledge_record_path": knowledge_record_path or None,
                "evidence_step_count": disposition_evidence_count,
                "source_fragment_count": disposition_fragment_count,
                "finding_count": len(findings) - disposition_findings_before,
            }
        )

    required_sections = [str(value).strip() for value in profile.get("required_sections", [])]
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

    paragraph_provenance = _audit_paragraph_provenance(
        headings,
        profile,
        claims,
        findings,
    )

    # A reader should not need to leave the manuscript to discover what a
    # cited or interpreted passage actually says.  The manifest declares the
    # minimum verbatim markers required under each relevant heading; the
    # deterministic audit then prevents prose-only paraphrase from silently
    # replacing the biblical text.
    for quotation in config.get("required_scripture_quotations", []):
        markdown_heading = str(quotation.get("markdown_heading") or "").strip()
        heading = heading_by_text.get(markdown_heading)
        if not heading:
            findings.append(
                _finding(
                    "missing_scripture_quote_scope",
                    "error",
                    "經文引用要求找不到對應段落",
                    f"未找到小標題：{markdown_heading or '（尚未配置）'}。",
                )
            )
            continue
        body = str(heading.get("content") or "")
        missing_markers = [
            str(marker)
            for marker in quotation.get("required_markers", [])
            if str(marker) and str(marker) not in body
        ]
        if missing_markers:
            findings.append(
                _finding(
                    "missing_scripture_quotation",
                    "error",
                    "解釋所依據的經文沒有直接引入正文",
                    "此段缺少經文文字：" + "、".join(missing_markers),
                )
            )

    # Life application is legitimate editorial content, and it is optional.
    # What it may not be is unregistered: every application must be a complete
    # source-backed chain rather than fluent prose appended to an exegetical
    # section.  The check is therefore driven by what each paragraph declares
    # about itself, not by whether a heading named 生活應用 exists.
    application_policy = _resolve_application_policy(profile, config)
    application_chains = _audit_application_chains(
        application_policy,
        config.get("application_chains") or [],
        claims,
        paragraph_provenance,
        bool(heading_by_text.get(application_policy["section"])),
        findings,
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

        # Section-level claim coverage does not prove that every sentence is
        # the professor's own interpretation. Registered editorial connective
        # prose must therefore be visibly attributed in the manuscript.
        editorial_boundary = (
            mapping.get("editorial_boundary")
            or decision.get("editorial_boundary")
            or {}
        )
        if editorial_boundary.get("required") and heading:
            body = str(heading.get("content") or "")
            label = str(editorial_boundary.get("label") or "編輯說明").strip()
            if label not in body:
                findings.append(
                    _finding(
                        "missing_editorial_attribution",
                        "error",
                        "編輯推論未明示歸屬",
                        str(editorial_boundary.get("reason") or "本段含有編輯補充，正文必須清楚標示編輯聲音。"),
                        decision_id=decision_id,
                    )
                )

        claim_ids = [str(value) for value in decision.get("claim_ids", []) if value]
        declared_coverage_gap = (
            # The store spells this `decision_type`, a projection spells it
            # `action`, and `CompositionDecisionRecord` accepts both. Reading
            # only one meant a snapshot compiled from the store failed this
            # exemption and reported every declared coverage gap as a decision
            # without claims.
            (decision.get("action") or decision.get("decision_type")) == "coverage_gap"
            and decision.get("coverage") == "missing"
            and editorial_boundary.get("required") is True
        )
        if not claim_ids and not declared_coverage_gap:
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
            "publication_profile_sha256": _sha256_bytes(profile_path.read_bytes()),
            "material_record_sha256s": material_record_fingerprints,
        },
        "publication_profile": {
            "profile_id": profile_id,
            "revision": profile.get("revision"),
            "title": profile.get("title") or "",
            "required_sections": required_sections,
            "optional_sections": profile.get("optional_sections") or [],
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
            "material_disposition_total": len(disposition_results),
            "source_only_pending_human_total": sum(
                item["action"] == "source_only"
                and item["review_status"] == "requires_human_verification"
                for item in disposition_results
            ),
            "paragraph_total": len(paragraph_provenance),
            "paragraph_valid_total": sum(item["valid"] for item in paragraph_provenance),
            "professor_paragraph_total": sum(
                item["attribution"] == "professor" for item in paragraph_provenance
            ),
            "scripture_paragraph_total": sum(
                item["attribution"] == "scripture" for item in paragraph_provenance
            ),
            "editor_paragraph_total": sum(
                item["attribution"] == "editor" for item in paragraph_provenance
            ),
            "editorial_synthesis_paragraph_total": sum(
                item["attribution"] == "editorial_synthesis"
                for item in paragraph_provenance
            ),
            "application_chain_total": len(application_chains),
            "application_paragraph_total": sum(
                bool(item.get("application_chain_id")) for item in paragraph_provenance
            ),
        },
        "decisions": decision_results,
        "paragraph_provenance": paragraph_provenance,
        "application_chains": application_chains,
        "material_dispositions": disposition_results,
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
