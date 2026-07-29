from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from backend.api.openai_client import DEFAULT_OPENAI_GENERATION_MODEL
from backend.api.sermon_search.slugify import slugify_heading
from backend.pipeline.stage1 import (
    SourceDocument,
    Stage1OpenAIClient,
    StructuredLogger,
    _sha256_text,
    get_stage1_prompt_bundle,
)


LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[str, int], None]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


SOURCE_RANGE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "start_line": {"type": "integer"},
        "end_line": {"type": "integer"},
    },
    "required": ["start_line", "end_line"],
}


SCRIPTURE_PRESENTATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reference": {"type": "string"},
        "mode": {
            "type": "string",
            "enum": ["direct_quote", "paraphrase", "reference_only"],
        },
        "quoted_text": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "role": {"type": "string"},
    },
    "required": ["reference", "mode", "quoted_text", "role"],
}


EVIDENCE_SCHEMA: Dict[str, Any] = {
    "name": "transcript_evidence_inventory_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "evidence_id": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": [
                                "question",
                                "answer",
                                "scripture_evidence",
                                "exegesis",
                                "reasoning",
                                "theology",
                                "application",
                                "appendix",
                            ],
                        },
                        "category": {
                            "type": "string",
                            "enum": ["釋經", "神學", "應用", "附錄"],
                        },
                        "content": {"type": "string"},
                        "scripture_refs": {"type": "array", "items": {"type": "string"}},
                        "scripture_presentations": {
                            "type": "array",
                            "items": SCRIPTURE_PRESENTATION_SCHEMA,
                        },
                        "source_ranges": {"type": "array", "items": SOURCE_RANGE_SCHEMA},
                        "supports": {"type": "array", "items": {"type": "string"}},
                        "answers_question": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                        "question_status": {
                            "anyOf": [
                                {"type": "string", "enum": ["answered", "unanswered"]},
                                {"type": "null"},
                            ]
                        },
                    },
                    "required": [
                        "evidence_id",
                        "type",
                        "category",
                        "content",
                        "scripture_refs",
                        "scripture_presentations",
                        "source_ranges",
                        "supports",
                        "answers_question",
                        "question_status",
                    ],
                },
            },
            "inventory_summary": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "total_evidence": {"type": "integer"},
                    "question_ids": {"type": "array", "items": {"type": "string"}},
                    "unanswered_question_ids": {"type": "array", "items": {"type": "string"}},
                    "scripture_evidence_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "total_evidence",
                    "question_ids",
                    "unanswered_question_ids",
                    "scripture_evidence_ids",
                ],
            },
        },
        "required": ["evidence", "inventory_summary"],
    },
}


PLAN_SCHEMA: Dict[str, Any] = {
    "name": "transcript_manuscript_plan_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "unit_id": {"type": "string"},
                        "title": {"type": "string"},
                        "unit_kind": {
                            "type": "string",
                            "enum": ["main", "appendix"],
                        },
                        "supports_unit_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "central_question": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                        "direct_answer": {
                            "anyOf": [{"type": "string"}, {"type": "null"}]
                        },
                        "scripture_range": {"type": "string"},
                        "objective": {"type": "string"},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "category_assignments": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "exegesis": {"type": "array", "items": {"type": "string"}},
                                "theological_significance": {"type": "array", "items": {"type": "string"}},
                                "application": {"type": "array", "items": {"type": "string"}},
                                "appendix": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": [
                                "exegesis",
                                "theological_significance",
                                "application",
                                "appendix",
                            ],
                        },
                        "source_ranges": {"type": "array", "items": SOURCE_RANGE_SCHEMA},
                        "plan_reason": {"type": "string"},
                    },
                    "required": [
                        "unit_id",
                        "title",
                        "unit_kind",
                        "supports_unit_ids",
                        "central_question",
                        "direct_answer",
                        "scripture_range",
                        "objective",
                        "evidence_ids",
                        "category_assignments",
                        "source_ranges",
                        "plan_reason",
                    ],
                },
            },
            "unassigned_evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["units", "unassigned_evidence_ids"],
    },
}


UNIT_GENERATION_SCHEMA: Dict[str, Any] = {
    "name": "transcript_manuscript_unit_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "manuscript_sections": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "exegesis": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "theological_significance": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "application": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                    "appendix": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": [
                    "exegesis",
                    "theological_significance",
                    "application",
                    "appendix",
                ],
            },
            "covered_evidence_ids": {"type": "array", "items": {"type": "string"}},
            "coverage_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["manuscript_sections", "covered_evidence_ids", "coverage_notes"],
    },
}


AUDIT_SCHEMA: Dict[str, Any] = {
    "name": "transcript_coverage_audit_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "overall_status": {"type": "string", "enum": ["pass", "needs_revision"]},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "type": {
                            "type": "string",
                            "enum": [
                                "missing_evidence",
                                "unanswered_question",
                                "scripture_role_lost",
                                "logic_gap",
                                "misclassification",
                                "unsupported_addition",
                                "tone_or_format",
                            ],
                        },
                        "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                        "unit_id": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                        "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        "description": {"type": "string"},
                        "recommended_fix": {"type": "string"},
                    },
                    "required": [
                        "finding_id",
                        "type",
                        "severity",
                        "unit_id",
                        "evidence_ids",
                        "description",
                        "recommended_fix",
                    ],
                },
            },
            "missing_evidence_ids": {"type": "array", "items": {"type": "string"}},
            "unanswered_question_ids": {"type": "array", "items": {"type": "string"}},
            "misclassified_evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "overall_status",
            "findings",
            "missing_evidence_ids",
            "unanswered_question_ids",
            "misclassified_evidence_ids",
        ],
    },
}


@dataclass
class TranscriptRunSummary:
    evidence: List[Dict[str, Any]] = field(default_factory=list)
    units: List[Dict[str, Any]] = field(default_factory=list)
    generated_units: List[Dict[str, Any]] = field(default_factory=list)
    audit: Dict[str, Any] = field(default_factory=dict)
    combined_markdown: str = ""


class TranscriptPipeline:
    def __init__(
        self,
        model: str = DEFAULT_OPENAI_GENERATION_MODEL,
        timeout_seconds: float = 180.0,
        max_retries: int = 3,
        logger: Optional[StructuredLogger] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.logger = logger
        self.progress_callback = progress_callback
        self.prompts = get_stage1_prompt_bundle("transcript")
        self.pipeline_signature = _sha256_text(
            "\n---\n".join([model, *self.prompts.values()])
        )
        self.llm = Stage1OpenAIClient(
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            max_output_tokens=60000,
            reasoning_effort="medium",
        )

    def run(
        self,
        input_path: Path,
        output_dir: Path,
        mode: str = "generate_all",
        selected_unit_id: Optional[str] = None,
        force: bool = False,
    ) -> TranscriptRunSummary:
        if mode not in {"analyze", "generate_all", "generate_unit", "audit"}:
            raise ValueError(f"Unsupported transcript pipeline mode: {mode}")
        input_path = input_path.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        source = SourceDocument.from_path(input_path)
        if force:
            self._clear_outputs(output_dir, mode, selected_unit_id)

        manifest_path = output_dir / "transcript_manifest.json"
        manifest = self._load_json(manifest_path) or {}
        if (
            manifest.get("source_sha256") != source.sha256
            or manifest.get("pipeline_signature") != self.pipeline_signature
        ):
            manifest = {}
        manifest.update(
            {
                "status": "running",
                "mode": mode,
                "model": self.model,
                "source_sha256": source.sha256,
                "pipeline_signature": self.pipeline_signature,
                "updated_at": _utcnow(),
            }
        )
        self._save_json(manifest_path, manifest)

        evidence_payload = self._load_cached_payload(
            output_dir / "evidence_inventory.json",
            source.sha256,
            allow_pipeline_mismatch=mode == "audit",
        )
        if not evidence_payload:
            self._progress("全文證據提取", 5)
            self._log("evidence", "開始建立全文 evidence inventory。")
            evidence_payload = self._extract_evidence(source)
            self._save_cached_payload(output_dir / "evidence_inventory.json", source.sha256, evidence_payload)
            self._log("evidence", f"全文證據提取完成，共 {len(evidence_payload['evidence'])} 條。")

        plan_payload = self._load_cached_payload(
            output_dir / "manuscript_plan.json",
            source.sha256,
            allow_pipeline_mismatch=mode == "audit",
        )
        if not plan_payload:
            self._progress("全文邏輯規劃", 25)
            self._log("planner", "開始依全文證據建立 manuscript plan。")
            plan_payload = self._plan_manuscript(evidence_payload)
            self._save_cached_payload(output_dir / "manuscript_plan.json", source.sha256, plan_payload)
            self._log("planner", f"全文邏輯規劃完成，共 {len(plan_payload['units'])} 個單元。")

        summary = TranscriptRunSummary(
            evidence=evidence_payload["evidence"],
            units=plan_payload["units"],
        )
        manifest["evidence_count"] = len(summary.evidence)
        manifest["unit_count"] = len(summary.units)
        manifest["analysis_completed"] = True

        if mode == "analyze":
            manifest.update({"status": "analysis_completed", "completed_at": _utcnow()})
            self._save_json(manifest_path, manifest)
            self._progress("全文分析完成", 100)
            return summary

        generated_dir = output_dir / "transcript_generated_units"
        generated_dir.mkdir(exist_ok=True)
        units_to_generate = [] if mode == "audit" else summary.units
        if mode == "generate_unit":
            units_to_generate = [unit for unit in summary.units if unit["unit_id"] == selected_unit_id]
            if not units_to_generate:
                raise ValueError(f"Unknown transcript manuscript unit: {selected_unit_id}")

        evidence_by_id = {item["evidence_id"]: item for item in summary.evidence}
        for index, unit in enumerate(units_to_generate, start=1):
            unit_path = generated_dir / f"{unit['unit_id']}.json"
            existing = self._load_cached_payload(unit_path, source.sha256)
            if existing and not force:
                self._log("generator", f"沿用既有 manuscript 单元 {unit['unit_id']}。")
                continue
            progress = 35 + int((index - 1) / max(len(units_to_generate), 1) * 45)
            self._progress("按邏輯單元生成", progress)
            self._log("generator", f"开始生成 {unit['unit_id']}：{unit['title']}。")
            generated = self._generate_unit(source, unit, evidence_by_id)
            self._save_cached_payload(unit_path, source.sha256, generated)

        summary.generated_units = self._load_generated_units(
            generated_dir, source.sha256, summary.units
        )
        integration_context: Optional[Dict[str, Any]] = None
        integration_path = output_dir / "integration_application.json"
        if integration_path.is_file():
            candidate = json.loads(integration_path.read_text(encoding="utf-8"))
            if candidate.get("status") == "draft_generated_pending_patch_review":
                expected_ids = {item["evidence_id"] for item in summary.evidence}
                disposition_ids = [
                    item.get("evidence_id")
                    for item in candidate.get("evidence_dispositions", [])
                ]
                if set(disposition_ids) != expected_ids or len(disposition_ids) != len(set(disposition_ids)):
                    raise ValueError(
                        "Integration Application does not account for every evidence ID exactly once"
                    )
                integration_context = candidate
        draft_path = output_dir / "draft_v1.md"
        if mode == "audit":
            if not draft_path.exists() or not draft_path.read_text(encoding="utf-8").strip():
                raise ValueError("Coverage audit requires an existing manuscript draft")
            # A coverage audit is read-only. The human-edited draft is authoritative
            # and must never be rebuilt from older generated-unit artifacts here.
            summary.combined_markdown = draft_path.read_text(encoding="utf-8")
        else:
            summary.combined_markdown = self._combine_units(summary.generated_units)
            draft_path.write_text(summary.combined_markdown, encoding="utf-8")

        all_units_ready = (
            mode == "audit"
            or bool(integration_context)
            or len(summary.generated_units) == len(summary.units)
        )
        if all_units_ready:
            self._progress("全文覆蓋審核", 85)
            self._log("auditor", "开始执行全文 coverage audit。")
            audit = self._audit(
                source,
                evidence_payload,
                plan_payload,
                summary.combined_markdown,
                integration_context=integration_context,
            )
            self._save_cached_payload(output_dir / "coverage_audit.json", source.sha256, audit)

            repairable = self._group_repairable_findings(audit)
            if repairable and mode != "audit":
                self._log("auditor", f"审核发现 {sum(len(v) for v in repairable.values())} 项可定位问题，开始定点修复。")
                for unit_id, findings in repairable.items():
                    unit = next((item for item in summary.units if item["unit_id"] == unit_id), None)
                    existing = next((item for item in summary.generated_units if item["unit_id"] == unit_id), None)
                    if not unit or not existing:
                        continue
                    repaired = self._generate_unit(
                        source,
                        unit,
                        evidence_by_id,
                        existing=existing,
                        repair_findings=findings,
                    )
                    self._save_cached_payload(generated_dir / f"{unit_id}.json", source.sha256, repaired)
                summary.generated_units = self._load_generated_units(
                    generated_dir, source.sha256, summary.units
                )
                summary.combined_markdown = self._combine_units(summary.generated_units)
                (output_dir / "draft_v1.md").write_text(summary.combined_markdown, encoding="utf-8")
                audit = self._audit(
                    source,
                    evidence_payload,
                    plan_payload,
                    summary.combined_markdown,
                    integration_context=integration_context,
                )
                self._save_cached_payload(output_dir / "coverage_audit.json", source.sha256, audit)

            summary.audit = audit
            manifest["audit_status"] = audit.get("overall_status")
            manifest["audit_finding_count"] = len(audit.get("findings", []))
            manifest["status"] = "completed" if audit.get("overall_status") == "pass" else "completed_with_findings"
        else:
            manifest["status"] = "partial_generated"

        manifest["generated_unit_count"] = len(summary.generated_units)
        manifest["completed_at"] = _utcnow()
        self._save_json(manifest_path, manifest)
        self._progress("Transcript pipeline 完成", 100)
        return summary

    def _extract_evidence(self, source: SourceDocument) -> Dict[str, Any]:
        user_prompt = (
            "以下是完整 transcript，每一行都有固定行号。请建立全文 evidence inventory。\n\n"
            f"【完整 transcript】\n{source.with_line_numbers()}"
        )
        payload = self.llm.generate_json(
            self.prompts["evidence_inventory"], user_prompt, EVIDENCE_SCHEMA,
            timeout_seconds=max(self.timeout_seconds, 300.0),
        )
        evidence = payload.get("evidence", [])
        if not evidence:
            raise ValueError("Evidence inventory is empty")
        ids: set[str] = set()
        for index, item in enumerate(evidence, start=1):
            evidence_id = str(item.get("evidence_id") or f"E{index:03d}")
            if evidence_id in ids:
                raise ValueError(f"Duplicate evidence ID: {evidence_id}")
            ids.add(evidence_id)
            item["evidence_id"] = evidence_id
            if not str(item.get("content") or "").strip():
                raise ValueError(f"Evidence {evidence_id} has no content")
            self._validate_ranges(item.get("source_ranges", []), len(source.lines), evidence_id)
            self._validate_scripture_evidence(item, source)
        for item in evidence:
            for ref in [*item.get("supports", []), *([item["answers_question"]] if item.get("answers_question") else [])]:
                if ref not in ids:
                    raise ValueError(f"Evidence {item['evidence_id']} references unknown evidence {ref}")
        payload["inventory_summary"]["total_evidence"] = len(evidence)
        return payload

    def _plan_manuscript(self, evidence_payload: Dict[str, Any]) -> Dict[str, Any]:
        user_prompt = (
            "请把以下完整 evidence inventory 重组为 manuscript 逻辑单元。每个 evidence ID 必须且只能出现一次。\n\n"
            f"【Evidence Inventory】\n{json.dumps(evidence_payload, ensure_ascii=False, indent=2)}"
        )
        payload = self.llm.generate_json(
            self.prompts["manuscript_planner"], user_prompt, PLAN_SCHEMA,
            timeout_seconds=max(self.timeout_seconds, 240.0),
        )
        evidence_ids = {item["evidence_id"] for item in evidence_payload["evidence"]}
        assigned: List[str] = []
        unit_id_map: Dict[str, str] = {}
        for index, unit in enumerate(payload.get("units", []), start=1):
            original_unit_id = str(unit.get("unit_id") or f"U{index:03d}")
            normalized_unit_id = f"U{index:03d}"
            unit_id_map[original_unit_id] = normalized_unit_id
            unit["unit_id"] = normalized_unit_id
            assigned.extend(unit.get("evidence_ids", []))
            assigned_set = set(unit.get("evidence_ids", []))
            category_ids = []
            for values in unit.get("category_assignments", {}).values():
                category_ids.extend(values)
            if set(category_ids) != assigned_set or len(category_ids) != len(set(category_ids)):
                raise ValueError(f"Category assignments do not exactly match {unit['unit_id']} evidence IDs")
        unit_ids = {unit["unit_id"] for unit in payload.get("units", [])}
        main_counter = 0
        appendix_counter = 0
        for unit in payload.get("units", []):
            support_ids = [
                unit_id_map.get(str(target_id), str(target_id))
                for target_id in unit.get("supports_unit_ids", [])
            ]
            unit["supports_unit_ids"] = support_ids
            if any(target_id not in unit_ids or target_id == unit["unit_id"] for target_id in support_ids):
                raise ValueError(f"Invalid appendix support targets for {unit['unit_id']}: {support_ids}")
            title = self._strip_unit_numbering(str(unit.get("title") or ""))
            unit["title"] = title
            if unit.get("unit_kind") == "appendix":
                appendix_counter += 1
                if not support_ids:
                    raise ValueError(f"Appendix {unit['unit_id']} must support at least one manuscript unit")
                heading_title = f"附錄{self._chinese_number(appendix_counter)}：{title}"
            else:
                main_counter += 1
                if support_ids:
                    raise ValueError(f"Main unit {unit['unit_id']} cannot declare supports_unit_ids")
                heading_title = f"{self._chinese_number(main_counter)}、{title}"
            unit["heading_title"] = heading_title
            unit["heading_anchor"] = slugify_heading(heading_title)

        units_by_id = {unit["unit_id"]: unit for unit in payload.get("units", [])}
        for appendix in (unit for unit in payload.get("units", []) if unit.get("unit_kind") == "appendix"):
            link = {
                "unit_id": appendix["unit_id"],
                "title": appendix["heading_title"],
                "anchor": appendix["heading_anchor"],
            }
            for target_id in appendix["supports_unit_ids"]:
                units_by_id[target_id].setdefault("supporting_appendices", []).append(link)
        assigned_set = set(assigned)
        missing = sorted(evidence_ids - assigned_set)
        unknown = sorted(assigned_set - evidence_ids)
        duplicates = sorted({item for item in assigned if assigned.count(item) > 1})
        if missing or unknown or duplicates or payload.get("unassigned_evidence_ids"):
            raise ValueError(
                f"Invalid manuscript plan: missing={missing}, unknown={unknown}, duplicates={duplicates}, "
                f"unassigned={payload.get('unassigned_evidence_ids', [])}"
            )
        if not payload.get("units"):
            raise ValueError("Manuscript plan contains no units")
        return payload

    def _generate_unit(
        self,
        source: SourceDocument,
        unit: Dict[str, Any],
        evidence_by_id: Dict[str, Dict[str, Any]],
        existing: Optional[Dict[str, Any]] = None,
        repair_findings: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        assigned = unit["evidence_ids"]
        evidence = [evidence_by_id[item] for item in assigned]
        source_ranges = self._merge_ranges(
            [source_range for item in evidence for source_range in item.get("source_ranges", [])]
        )
        source_slices = []
        for source_range in source_ranges:
            start, end = source_range["start_line"], source_range["end_line"]
            source_slices.append(f"【来源第 {start}–{end} 行】\n{source.slice_by_lines(start, end)}")
        joined_source_slices = "\n\n".join(source_slices)
        user_prompt = (
            f"【Manuscript Unit Plan】\n{json.dumps(unit, ensure_ascii=False, indent=2)}\n\n"
            f"【Assigned Evidence】\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
            f"【对应 transcript 原文】\n{joined_source_slices}\n\n"
            "必须覆盖每一个 assigned evidence ID，并在 covered_evidence_ids 中逐一列出。"
        )
        if existing and repair_findings:
            user_prompt += (
                f"\n\n【现有单元】\n{json.dumps(existing, ensure_ascii=False, indent=2)}"
                f"\n\n【审核发现，只修复这些问题】\n{json.dumps(repair_findings, ensure_ascii=False, indent=2)}"
                "\n请保留现有单元已正确覆盖的全部内容，只做必要的定点修复。"
            )
        payload = self.llm.generate_json(
            self.prompts["unit_generator"], user_prompt, UNIT_GENERATION_SCHEMA,
            timeout_seconds=max(self.timeout_seconds, 240.0),
        )
        covered = set(payload.get("covered_evidence_ids", []))
        missing = [item for item in assigned if item not in covered]
        unknown = sorted(covered - set(assigned))
        normalized_sections = self._normalize_manuscript_sections(payload["manuscript_sections"])
        scripture_format_issues = self._scripture_format_issues(normalized_sections, evidence)
        appendix_link_issues = self._appendix_link_issues(normalized_sections, unit)
        if missing or unknown or scripture_format_issues or appendix_link_issues:
            retry_prompt = (
                f"{user_prompt}\n\n【确定性覆盖检查失败】\n"
                f"遗漏 evidence IDs：{missing}\n非本单元 evidence IDs：{unknown}\n"
                f"经文呈现问题：{scripture_format_issues}\n"
                f"附录链接问题：{appendix_link_issues}\n"
                "请重新输出完整单元；保留已正确内容，明确补足遗漏，并移除非本单元材料。"
            )
            payload = self.llm.generate_json(
                self.prompts["unit_generator"], retry_prompt, UNIT_GENERATION_SCHEMA,
                timeout_seconds=max(self.timeout_seconds, 240.0),
            )
            covered = set(payload.get("covered_evidence_ids", []))
            missing = [item for item in assigned if item not in covered]
            unknown = sorted(covered - set(assigned))
            normalized_sections = self._normalize_manuscript_sections(payload["manuscript_sections"])
            scripture_format_issues = self._scripture_format_issues(normalized_sections, evidence)
            appendix_link_issues = self._appendix_link_issues(normalized_sections, unit)
            if missing or unknown or scripture_format_issues or appendix_link_issues:
                raise ValueError(
                    f"Unit {unit['unit_id']} coverage failed: missing={missing}, unknown={unknown}, "
                    f"scripture_format={scripture_format_issues}, appendix_links={appendix_link_issues}"
                )
        return {
            "unit_id": unit["unit_id"],
            "unit_title": unit["title"],
            "heading_title": unit.get("heading_title", unit["title"]),
            "heading_anchor": unit.get("heading_anchor", slugify_heading(unit["title"])),
            "unit_kind": unit.get("unit_kind", "main"),
            "supports_unit_ids": unit.get("supports_unit_ids", []),
            "supporting_appendices": unit.get("supporting_appendices", []),
            "scripture_range": unit.get("scripture_range", ""),
            "source_ranges": source_ranges,
            "evidence_ids": assigned,
            "manuscript_sections": normalized_sections,
            "covered_evidence_ids": payload["covered_evidence_ids"],
            "coverage_notes": payload.get("coverage_notes", []),
            "generated_markdown": self._render_unit(
                unit.get("heading_title", unit["title"]), normalized_sections
            ),
        }

    def _audit(
        self,
        source: SourceDocument,
        evidence_payload: Dict[str, Any],
        plan_payload: Dict[str, Any],
        manuscript: str,
        integration_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        user_prompt = (
            f"【完整 transcript】\n{source.content}\n\n"
            f"【Evidence Inventory】\n{json.dumps(evidence_payload, ensure_ascii=False)}\n\n"
            f"【Manuscript Plan】\n{json.dumps(plan_payload, ensure_ascii=False)}\n\n"
            f"【Generated Manuscript】\n{manuscript}\n\n"
            f"【Integration Application】\n"
            f"{json.dumps(integration_context, ensure_ascii=False) if integration_context else 'null'}"
        )
        audit = self.llm.generate_json(
            self.prompts["coverage_auditor"], user_prompt, AUDIT_SCHEMA,
            timeout_seconds=max(self.timeout_seconds, 300.0),
        )
        if not integration_context:
            deterministic_findings = [
                *self._whole_manuscript_scripture_findings(
                    evidence_payload, plan_payload, manuscript
                ),
                *self._whole_manuscript_structure_findings(plan_payload, manuscript),
            ]
            if deterministic_findings:
                existing_ids = {finding.get("finding_id") for finding in audit.get("findings", [])}
                audit.setdefault("findings", []).extend(
                    finding for finding in deterministic_findings
                    if finding["finding_id"] not in existing_ids
                )
                audit["overall_status"] = "needs_revision"
        return audit

    def _group_repairable_findings(self, audit: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for finding in audit.get("findings", []):
            unit_id = finding.get("unit_id")
            if not unit_id or finding.get("severity") == "low":
                continue
            grouped.setdefault(unit_id, []).append(finding)
        return grouped

    def _load_generated_units(
        self,
        generated_dir: Path,
        source_hash: str,
        units: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        output = []
        for unit in units:
            payload = self._load_cached_payload(generated_dir / f"{unit['unit_id']}.json", source_hash)
            if payload:
                output.append(payload)
        return output

    def _render_unit(self, title: str, sections: Dict[str, Optional[str]]) -> str:
        blocks = [f"## {title}"]
        labels = [
            ("exegesis", "釋經"),
            ("theological_significance", "神學意義"),
            ("application", "生活應用"),
            ("appendix", "附錄"),
        ]
        for key, label in labels:
            value = self._strip_redundant_section_heading(sections.get(key), label)
            if isinstance(value, str) and value.strip():
                blocks.append(f"### {label}\n\n{value.strip()}")
        return "\n\n".join(blocks).strip()

    @staticmethod
    def _chinese_number(value: int) -> str:
        digits = "零一二三四五六七八九"
        if value < 10:
            return digits[value]
        if value == 10:
            return "十"
        if value < 20:
            return f"十{digits[value - 10]}"
        if value < 100:
            tens, ones = divmod(value, 10)
            return f"{digits[tens]}十{digits[ones] if ones else ''}"
        return str(value)

    @staticmethod
    def _strip_unit_numbering(title: str) -> str:
        return re.sub(
            r"^(?:[一二三四五六七八九十百]+、|附[錄录][一二三四五六七八九十百]+[：:、])\s*",
            "",
            title.strip(),
        )

    @staticmethod
    def _appendix_link_issues(
        sections: Dict[str, Optional[str]], unit: Dict[str, Any]
    ) -> List[str]:
        markdown = "\n\n".join(value for value in sections.values() if value)
        issues = []
        for appendix in unit.get("supporting_appendices", []):
            expected = f"](#{appendix['anchor']})"
            if expected not in markdown:
                issues.append(f"缺少指向{appendix['title']}的内部链接 {expected}")
        return issues

    def _normalize_manuscript_sections(
        self, sections: Dict[str, Optional[str]]
    ) -> Dict[str, Optional[str]]:
        labels = {
            "exegesis": "釋經",
            "theological_significance": "神學意義",
            "application": "生活應用",
            "appendix": "附錄",
        }
        return {
            key: self._strip_redundant_section_heading(sections.get(key), label)
            for key, label in labels.items()
        }

    def _strip_redundant_section_heading(
        self, value: Optional[str], label: str
    ) -> Optional[str]:
        if not isinstance(value, str) or not value.strip():
            return None
        aliases = {
            "釋經": ("釋經", "释经"),
            "神學意義": ("神學意義", "神学意义"),
            "生活應用": ("生活應用", "生活应用"),
            "附錄": ("附錄", "附录"),
        }[label]
        alias_pattern = "|".join(re.escape(alias) for alias in aliases)
        heading_pattern = re.compile(
            rf"^\s*#{{1,6}}\s*(?:{alias_pattern})\s*[：:]?\s*(?:\r?\n+|$)"
        )
        normalized = value.strip()
        while heading_pattern.match(normalized):
            normalized = heading_pattern.sub("", normalized, count=1).lstrip()
        return normalized or None

    @staticmethod
    def _compact_reference(value: str) -> str:
        return re.sub(r"[\s《》〈〉]", "", str(value or "")).lower()

    @staticmethod
    def _compact_quote(value: str) -> str:
        compact = re.sub(r"\s+", "", str(value or ""))
        return compact.strip("「」『』“”‘’\"'")

    @classmethod
    def _blockquote_text(cls, markdown: str) -> str:
        quote_lines = []
        for line in str(markdown or "").splitlines():
            match = re.match(r"^\s*>\s?(.*)$", line)
            if match:
                quote_lines.append(match.group(1))
        return cls._compact_quote("\n".join(quote_lines))

    def _validate_scripture_evidence(self, item: Dict[str, Any], source: SourceDocument) -> None:
        evidence_id = item["evidence_id"]
        scripture_refs = [str(ref).strip() for ref in item.get("scripture_refs", []) if str(ref).strip()]
        presentations = item.get("scripture_presentations", [])
        if scripture_refs and not presentations:
            raise ValueError(f"Evidence {evidence_id} has Scripture references but no presentation data")
        if not scripture_refs and presentations:
            raise ValueError(f"Evidence {evidence_id} has presentation data without Scripture references")

        source_text = "\n".join(
            source.slice_by_lines(source_range["start_line"], source_range["end_line"])
            for source_range in item.get("source_ranges", [])
        )
        compact_source = self._compact_quote(source_text)
        for presentation in presentations:
            reference = str(presentation.get("reference") or "").strip()
            mode = presentation.get("mode")
            quoted_text = presentation.get("quoted_text")
            role = str(presentation.get("role") or "").strip()
            if not reference or not role:
                raise ValueError(f"Evidence {evidence_id} has incomplete Scripture presentation data")
            if mode == "direct_quote":
                compact_quote = self._compact_quote(str(quoted_text or ""))
                if not compact_quote:
                    raise ValueError(f"Evidence {evidence_id} direct quote has no quoted_text")
                if compact_quote not in compact_source:
                    raise ValueError(
                        f"Evidence {evidence_id} quoted_text is not verbatim in its transcript source range"
                    )
            elif quoted_text is not None:
                raise ValueError(
                    f"Evidence {evidence_id} {mode} presentation must set quoted_text to null"
                )

    def _scripture_presentation_issues(
        self, markdown: str, evidence: List[Dict[str, Any]]
    ) -> List[Dict[str, str]]:
        compact_markdown_reference = self._compact_reference(markdown)
        compact_blockquotes = self._blockquote_text(markdown)
        issues: List[Dict[str, str]] = []
        for item in evidence:
            evidence_id = str(item.get("evidence_id") or "")
            for presentation in item.get("scripture_presentations", []):
                reference = str(presentation.get("reference") or "").strip()
                mode = presentation.get("mode")
                compact_reference = self._compact_reference(reference)
                if compact_reference and compact_reference not in compact_markdown_reference:
                    issues.append({
                        "evidence_id": evidence_id,
                        "reason": f"缺少经文出处 {reference}",
                    })
                if mode == "direct_quote":
                    quoted_text = str(presentation.get("quoted_text") or "")
                    compact_quote = self._compact_quote(quoted_text)
                    if compact_quote and compact_quote not in compact_blockquotes:
                        issues.append({
                            "evidence_id": evidence_id,
                            "reason": f"经文原句未使用 Markdown blockquote：{reference}",
                        })
        return issues

    def _scripture_format_issues(
        self,
        sections: Dict[str, Optional[str]],
        evidence: List[Dict[str, Any]],
    ) -> List[str]:
        markdown = "\n\n".join(value for value in sections.values() if value)
        return [
            f"{issue['evidence_id']}: {issue['reason']}"
            for issue in self._scripture_presentation_issues(markdown, evidence)
        ]

    def _whole_manuscript_scripture_findings(
        self,
        evidence_payload: Dict[str, Any],
        plan_payload: Dict[str, Any],
        manuscript: str,
    ) -> List[Dict[str, Any]]:
        evidence_by_id = {
            item["evidence_id"]: item
            for item in evidence_payload.get("evidence", [])
        }
        findings: List[Dict[str, Any]] = []
        finding_index = 1
        for unit in plan_payload.get("units", []):
            unit_evidence = [
                evidence_by_id[evidence_id]
                for evidence_id in unit.get("evidence_ids", [])
                if evidence_id in evidence_by_id
            ]
            for issue in self._scripture_presentation_issues(manuscript, unit_evidence):
                findings.append({
                    "finding_id": f"FMT{finding_index:03d}",
                    "type": "tone_or_format",
                    "severity": "medium",
                    "unit_id": unit.get("unit_id"),
                    "evidence_ids": [issue["evidence_id"]],
                    "description": issue["reason"],
                    "recommended_fix": (
                        "按 notes-to-manuscript 格式单独标示经文出处；若 transcript 提供经文原句，"
                        "将原句放入 Markdown blockquote，再另起段落说明这段经文在论证中的作用。"
                    ),
                })
                finding_index += 1
        return findings

    def _whole_manuscript_structure_findings(
        self,
        plan_payload: Dict[str, Any],
        manuscript: str,
    ) -> List[Dict[str, Any]]:
        """Verify headings and appendix navigation produced by the current plan schema.

        Legacy plans do not have ``heading_title`` and are intentionally skipped.
        """
        heading_matches = list(re.finditer(r"^##\s+(.+?)\s*$", manuscript, re.MULTILINE))
        sections: List[Dict[str, str]] = []
        for index, match in enumerate(heading_matches):
            end = heading_matches[index + 1].start() if index + 1 < len(heading_matches) else len(manuscript)
            sections.append({
                "title": match.group(1).strip(),
                "markdown": manuscript[match.start():end],
            })

        findings: List[Dict[str, Any]] = []
        finding_index = 1
        main_counter = 0
        appendix_counter = 0
        planned_units = [
            unit for unit in plan_payload.get("units", [])
            if str(unit.get("heading_title") or "").strip()
        ]
        section_by_unit_id = {
            str(unit.get("unit_id")): sections[index]
            for index, unit in enumerate(planned_units)
            if unit.get("unit_id") and index < len(sections)
        }
        for unit_index, unit in enumerate(planned_units):
            unit_id = unit.get("unit_id")
            if unit.get("unit_kind") == "appendix":
                appendix_counter += 1
                required_prefix = f"附錄{self._chinese_number(appendix_counter)}："
            else:
                main_counter += 1
                required_prefix = f"{self._chinese_number(main_counter)}、"
            unit_section = sections[unit_index] if unit_index < len(sections) else None
            if unit_section is None or not unit_section["title"].startswith(required_prefix):
                findings.append({
                    "finding_id": f"NAV{finding_index:03d}",
                    "type": "tone_or_format",
                    "severity": "medium",
                    "unit_id": unit_id,
                    "evidence_ids": unit.get("evidence_ids", []),
                    "description": f"单元标题必须使用连续编号前缀：## {required_prefix}...",
                    "recommended_fix": "恢复规划中的连续中文单元编号或附录编号。",
                })
                finding_index += 1
                if unit_section is None:
                    continue
            unit_markdown = unit_section["markdown"]
            for appendix in unit.get("supporting_appendices", []):
                appendix_section = section_by_unit_id.get(str(appendix.get("unit_id") or ""))
                target_anchor = (
                    slugify_heading(appendix_section["title"])
                    if appendix_section
                    else appendix["anchor"]
                )
                expected = f"](#{target_anchor})"
                if expected in unit_markdown:
                    continue
                findings.append({
                    "finding_id": f"NAV{finding_index:03d}",
                    "type": "tone_or_format",
                    "severity": "medium",
                    "unit_id": unit_id,
                    "evidence_ids": unit.get("evidence_ids", []),
                    "description": f"正文未链接其支持附录：{appendix['title']}",
                    "recommended_fix": (
                        "在最相关的正文句子或段落说明附录与论证的关系，并加入内部链接 "
                        f"[{appendix['title']}](#{target_anchor})"
                    ),
                })
                finding_index += 1
        return findings

    def _combine_units(self, units: List[Dict[str, Any]]) -> str:
        return "\n\n".join(unit["generated_markdown"].strip() for unit in units).strip()

    def _validate_ranges(self, ranges: List[Dict[str, Any]], line_count: int, label: str) -> None:
        if not ranges:
            raise ValueError(f"{label} has no source ranges")
        for source_range in ranges:
            start, end = int(source_range["start_line"]), int(source_range["end_line"])
            if start < 1 or end < start or end > line_count:
                raise ValueError(f"Invalid source range for {label}: {start}-{end}")

    def _merge_ranges(self, ranges: List[Dict[str, int]]) -> List[Dict[str, int]]:
        ordered = sorted(
            ({"start_line": int(item["start_line"]), "end_line": int(item["end_line"])} for item in ranges),
            key=lambda item: (item["start_line"], item["end_line"]),
        )
        merged: List[Dict[str, int]] = []
        for item in ordered:
            if merged and item["start_line"] <= merged[-1]["end_line"] + 1:
                merged[-1]["end_line"] = max(merged[-1]["end_line"], item["end_line"])
            else:
                merged.append(dict(item))
        return merged

    def _clear_outputs(self, output_dir: Path, mode: str, selected_unit_id: Optional[str]) -> None:
        generated_dir = output_dir / "transcript_generated_units"
        if mode == "generate_unit" and selected_unit_id:
            target = generated_dir / f"{selected_unit_id}.json"
            if target.exists():
                target.unlink()
            audit = output_dir / "coverage_audit.json"
            if audit.exists():
                audit.unlink()
            return
        if mode == "audit":
            audit = output_dir / "coverage_audit.json"
            if audit.exists():
                audit.unlink()
            return
        if mode == "analyze":
            for name in ["evidence_inventory.json", "manuscript_plan.json", "coverage_audit.json", "transcript_manifest.json"]:
                path = output_dir / name
                if path.exists():
                    path.unlink()
        if generated_dir.exists():
            shutil.rmtree(generated_dir)

    def _load_cached_payload(
        self,
        path: Path,
        source_hash: str,
        *,
        allow_pipeline_mismatch: bool = False,
    ) -> Optional[Dict[str, Any]]:
        wrapper = self._load_json(path)
        if not wrapper:
            return None
        if wrapper.get("source_sha256") != source_hash:
            return None
        if not allow_pipeline_mismatch and wrapper.get("pipeline_signature") != self.pipeline_signature:
            return None
        payload = wrapper.get("payload")
        return payload if isinstance(payload, dict) else None

    def _save_cached_payload(self, path: Path, source_hash: str, payload: Dict[str, Any]) -> None:
        self._save_json(
            path,
            {
                "source_sha256": source_hash,
                "pipeline_signature": self.pipeline_signature,
                "model": self.model,
                "updated_at": _utcnow(),
                "payload": payload,
            },
        )

    def _load_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _log(self, role: str, message: str, **fields: Any) -> None:
        if self.logger:
            self.logger.emit(role, message, **fields)

    def _progress(self, stage: str, progress: int) -> None:
        if self.progress_callback:
            self.progress_callback(stage, progress)


def run_transcript_pipeline(
    input_path: Path,
    output_dir: Path,
    mode: str = "generate_all",
    selected_unit_id: Optional[str] = None,
    model: str = DEFAULT_OPENAI_GENERATION_MODEL,
    timeout_seconds: float = 180.0,
    max_retries: int = 3,
    force: bool = False,
    log_path: Optional[Path] = None,
    log_callback: Optional[LogCallback] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> TranscriptRunSummary:
    logger = StructuredLogger(log_path, callback=log_callback) if log_path else None
    pipeline = TranscriptPipeline(
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        logger=logger,
        progress_callback=progress_callback,
    )
    return pipeline.run(
        input_path=input_path,
        output_dir=output_dir,
        mode=mode,
        selected_unit_id=selected_unit_id,
        force=force,
    )
