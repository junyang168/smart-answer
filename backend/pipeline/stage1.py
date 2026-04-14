from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from anthropic import Anthropic

LogCallback = Callable[[str, str], None]
ProgressCallback = Callable[[str, int], None]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_prompt(prompt_name: str) -> str:
    prompts_dir = Path(__file__).resolve().parent / "prompts"
    prompt_path = prompts_dir / prompt_name
    prompt_text = prompt_path.read_text(encoding="utf-8")
    shared_tokens = {
        "{{CATEGORY_DEFINITIONS}}": (prompts_dir / "shared" / "category_definitions.md").read_text(encoding="utf-8").strip(),
    }
    for token, value in shared_tokens.items():
        prompt_text = prompt_text.replace(token, value)
    return prompt_text


def _to_chinese_section_number(value: int) -> str:
    digits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"]
    if value <= 0:
        return str(value)
    if value < 10:
        return digits[value]
    if value < 20:
        return "十" if value == 10 else f"十{digits[value % 10]}"
    if value < 100:
        tens, ones = divmod(value, 10)
        return f"{digits[tens]}十" if ones == 0 else f"{digits[tens]}十{digits[ones]}"
    return str(value)


@dataclass
class UnitBoundary:
    unit_id: str
    chapter_title: str
    section_title: str
    unit_title: str
    scripture_range: str
    start_line: int
    end_line: int
    split_reason: str
    prev_unit_id: Optional[str] = None
    next_unit_id: Optional[str] = None


@dataclass
class GeneratedUnit:
    unit_id: str
    chapter_title: str
    section_title: str
    unit_title: str
    scripture_range: str
    start_line: int
    end_line: int
    prev_unit_id: Optional[str]
    next_unit_id: Optional[str]
    points: List[Dict[str, str]]
    manuscript_sections: Dict[str, Optional[str]]
    coverage_checks: List[Dict[str, str]]
    coverage_summary: Dict[str, Any]
    generated_markdown: str
    status: str = "completed"
    error: Optional[str] = None


@dataclass
class SourceDocument:
    IGNORE_BELOW_MARKER = "<!-- Ignore Below -->"

    path: Path
    content: str
    lines: List[str]
    sha256: str

    @classmethod
    def from_path(cls, path: Path) -> "SourceDocument":
        content = path.read_text(encoding="utf-8")
        return cls(
            path=path,
            content=content,
            lines=content.splitlines(),
            sha256=_sha256_text(content),
        )

    def split_cutoff_line(self) -> int:
        for index, line in enumerate(self.lines, start=1):
            if self.IGNORE_BELOW_MARKER in line:
                return index - 1
        return len(self.lines)

    def with_line_numbers(self, end_line: Optional[int] = None) -> str:
        visible_end_line = len(self.lines) if end_line is None else min(len(self.lines), end_line)
        numbered_lines = []
        for index, line in enumerate(self.lines[:visible_end_line], start=1):
            numbered_lines.append(f"{index:04d}: {line}")
        return "\n".join(numbered_lines)

    def slice_by_lines(self, start_line: int, end_line: int) -> str:
        start = max(1, start_line)
        end = min(len(self.lines), end_line)
        if start > end or not self.lines:
            return ""
        return "\n".join(self.lines[start - 1 : end])


@dataclass
class RunSummary:
    input_path: str
    output_dir: str
    source_sha256: str
    units: List[UnitBoundary] = field(default_factory=list)
    generated_units: List[GeneratedUnit] = field(default_factory=list)
    failed_units: List[Dict[str, str]] = field(default_factory=list)
    combined_markdown: str = ""


class StructuredLogger:
    def __init__(self, log_path: Path, callback: Optional[LogCallback] = None) -> None:
        self.log_path = log_path
        self.callback = callback
        self.logger = logging.getLogger(f"stage1.{log_path}")
        self.logger.setLevel(logging.INFO)

    def emit(self, role: str, message: str, **fields: Any) -> None:
        entry = {
            "timestamp": _utcnow(),
            "role": role,
            "message": message,
        }
        if fields:
            entry.update(fields)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.logger.info("%s: %s", role, message)
        if self.callback:
            self.callback(role, message)


class Stage1AnthropicClient:
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout_seconds: float = 90.0,
        max_retries: int = 3,
        max_output_tokens: int = 20000,
    ) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable is not set")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_output_tokens = max_output_tokens
        self.client = Anthropic(
            api_key=api_key,
            max_retries=0,
            timeout=timeout_seconds,
        )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any],
        temperature: float = 0.0,
        timeout_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        return self._with_retries(
            lambda: self._generate_json_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                json_schema=json_schema,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        )

    def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        return self._with_retries(
            lambda: self._generate_text_once(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        )

    def _with_retries(self, func: Callable[[], Any]) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return func()
            except Exception as exc:  # pragma: no cover - SDK exception surface varies by version
                last_error = exc
                if attempt >= self.max_retries:
                    break
                error_text = self._format_exception(exc).lower()
                if (
                    "429" in error_text
                    or "rate_limit" in error_text
                    or "overloaded" in error_text
                    or "too many requests" in error_text
                ):
                    delay_seconds = min(15 * attempt, 60)
                else:
                    delay_seconds = min(2 ** (attempt - 1), 8)
                time.sleep(delay_seconds)
        if last_error is None:
            raise RuntimeError("LLM call failed without an exception")
        raise last_error

    def _generate_json_once(
        self,
        system_prompt: str,
        user_prompt: str,
        json_schema: Dict[str, Any],
        temperature: float,
        timeout_seconds: Optional[float],
    ) -> Dict[str, Any]:
        schema_description = json.dumps(json_schema.get("schema", {}), ensure_ascii=False, indent=2)
        content = self._post_chat_completion(
            system_prompt=system_prompt,
            user_prompt=(
                f"{user_prompt}\n\n"
                "你必須只輸出合法 JSON，不可包含 Markdown 代碼塊、說明文字或前後綴。\n"
                "輸出 JSON 必須符合以下 schema：\n"
                f"{schema_description}"
            ),
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )
        return self._parse_json_response(content)

    def _generate_text_once(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout_seconds: Optional[float],
    ) -> str:
        return self._post_chat_completion(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )

    def _post_chat_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        timeout_seconds: Optional[float] = None,
    ) -> str:
        client = self.client
        effective_timeout = timeout_seconds or self.timeout_seconds
        if effective_timeout != self.timeout_seconds:
            client = Anthropic(
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
                max_retries=0,
                timeout=effective_timeout,
            )
        try:
            message = client.messages.create(
                model=self.model,
                max_tokens=self.max_output_tokens,
                temperature=temperature,
                system=system_prompt,
                thinking={"type": "disabled"},
                messages=[
                    {
                        "role": "user",
                        "content": [{"type": "text", "text": user_prompt}],
                    }
                ],
            )
        except Exception as exc:
            raise RuntimeError(self._format_exception(exc)) from exc

        text_blocks = [
            block.text.strip()
            for block in getattr(message, "content", [])
            if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
        ]
        if not text_blocks:
            raise RuntimeError(f"Anthropic response missing text content: {message}")
        return "\n".join(text_blocks).strip()

    def _format_exception(self, exc: Exception) -> str:
        status_code = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        body = None
        if response is not None:
            body = getattr(response, "text", None)
            if body is None:
                try:
                    body = response.json()
                except Exception:
                    body = None
        pieces = ["Anthropic API error"]
        if status_code is not None:
            pieces.append(str(status_code))
        message = str(exc).strip()
        if message:
            pieces.append(message)
        if body:
            body_text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            pieces.append(body_text[:2000])
        return ": ".join(pieces)

    def _parse_json_response(self, content: str) -> Dict[str, Any]:
        cleaned = content.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}\s*$", cleaned, re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise


class Stage1Pipeline:
    SPLIT_SCHEMA: Dict[str, Any] = {
        "name": "stage1_unit_split_v1",
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
                            "chapter_title": {"type": "string"},
                            "section_title": {"type": "string"},
                            "unit_title": {"type": "string"},
                            "scripture_range": {"type": "string"},
                            "start_line": {"type": "integer"},
                            "end_line": {"type": "integer"},
                            "split_reason": {"type": "string"},
                            "prev_unit_id": {
                                "anyOf": [{"type": "string"}, {"type": "null"}]
                            },
                            "next_unit_id": {
                                "anyOf": [{"type": "string"}, {"type": "null"}]
                            },
                        },
                        "required": [
                            "unit_id",
                            "chapter_title",
                            "section_title",
                            "unit_title",
                            "scripture_range",
                            "start_line",
                            "end_line",
                            "split_reason",
                            "prev_unit_id",
                            "next_unit_id",
                        ],
                    },
                }
            },
            "required": ["units"],
        },
    }
    POINT_EXTRACTION_SCHEMA: Dict[str, Any] = {
        "name": "stage1_unit_generation_v1",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "points": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "point_id": {"type": "string"},
                            "category": {
                                "type": "string",
                                "enum": ["釋經", "神學", "應用", "附錄"],
                            },
                            "content": {"type": "string"},
                        },
                        "required": ["point_id", "category", "content"],
                    },
                }
            },
            "required": ["points"],
        },
    }
    MANUSCRIPT_GENERATION_SCHEMA: Dict[str, Any] = {
        "name": "stage1_manuscript_generation_v1",
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
                "coverage_checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "point_id": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": ["covered", "needs_revision"],
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["point_id", "status", "note"],
                    },
                },
                "coverage_summary": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "covered_count": {"type": "integer"},
                        "total_points": {"type": "integer"},
                        "missing_point_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["covered_count", "total_points", "missing_point_ids"],
                },
            },
            "required": ["manuscript_sections", "coverage_checks", "coverage_summary"],
        },
    }

    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        timeout_seconds: float = 90.0,
        max_retries: int = 3,
        logger: Optional[StructuredLogger] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> None:
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.llm = Stage1AnthropicClient(
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        self.logger = logger
        self.progress_callback = progress_callback
        self.split_prompt = _read_prompt("unit_splitter.md")
        self.point_extractor_prompt = _read_prompt("point_extractor.md")
        self.generator_prompt = _read_prompt("unit_generator.md")

    def run(
        self,
        input_path: Path,
        output_dir: Path,
        force: bool = False,
        split_only: bool = False,
        selected_unit_ids: Optional[List[str]] = None,
    ) -> RunSummary:
        input_path = input_path.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        if force:
            if selected_unit_ids:
                self._clear_selected_unit_outputs(output_dir, selected_unit_ids)
            else:
                self._clear_previous_outputs(output_dir)

        source_doc = SourceDocument.from_path(input_path)
        manifest_path = output_dir / "stage1_manifest.json"
        units_path = output_dir / "stage1_units.json"
        generated_dir = output_dir / "generated_units"
        generated_dir.mkdir(exist_ok=True)

        summary = RunSummary(
            input_path=str(input_path),
            output_dir=str(output_dir),
            source_sha256=source_doc.sha256,
        )

        manifest = self._load_manifest(manifest_path)
        manifest = self._initialize_manifest(
            manifest=manifest,
            input_path=input_path,
            output_dir=output_dir,
            source_doc=source_doc,
            force=force,
        )
        self._save_manifest(manifest_path, manifest)

        units = self._load_or_create_units(
            source_doc=source_doc,
            units_path=units_path,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        summary.units = units
        self._refresh_manifest_unit_statuses(
            manifest=manifest,
            manifest_path=manifest_path,
            units=units,
            generated_dir=generated_dir,
            source_hash=source_doc.sha256,
        )

        if split_only:
            manifest["status"] = "split_only_completed"
            manifest["completed_at"] = _utcnow()
            available_units = self._load_available_generated_units(
                generated_dir=generated_dir,
                units=units,
                source_hash=source_doc.sha256,
            )
            failed_units = self._collect_failed_units_from_manifest(manifest)
            manifest["successful_unit_count"] = len(available_units)
            manifest["failed_unit_count"] = len(failed_units)
            manifest["failed_units"] = failed_units
            self._save_manifest(manifest_path, manifest)
            self._progress("單元切割完成", 100)
            self._log("system", f"Stage 1A 完成，共 {len(units)} 個單元。")
            return summary

        selected_ids = set(selected_unit_ids or [])
        units_to_process = [unit for unit in units if not selected_ids or unit.unit_id in selected_ids]
        if not units_to_process:
            raise ValueError("No matching units selected for generation")

        total_units = len(units_to_process)
        completed_units: List[GeneratedUnit] = []
        failed_units: List[Dict[str, str]] = []

        for index, unit in enumerate(units_to_process, start=1):
            progress = 10 if total_units == 0 else 10 + int((index - 1) / total_units * 85)
            self._progress("逐單元生成", progress)
            artifact_path = generated_dir / f"{unit.unit_id}.json"
            points_artifact_path = generated_dir / f"{unit.unit_id}.points.json"
            existing_unit = self._load_generated_unit(
                artifact_path=artifact_path,
                expected_source_hash=source_doc.sha256,
                expected_model=self.model,
            )
            if existing_unit:
                completed_units.append(existing_unit)
                self._log(
                    "expander",
                    f"跳過 {unit.unit_id}，沿用既有輸出。",
                    unit_id=unit.unit_id,
                )
                self._mark_unit_status(
                    manifest=manifest,
                    manifest_path=manifest_path,
                    unit_id=unit.unit_id,
                    status="completed",
                    artifact=str(artifact_path),
                )
                continue

            self._log(
                "expander",
                f"開始生成 {unit.unit_id}：{unit.unit_title}",
                unit_id=unit.unit_id,
                scripture_range=unit.scripture_range,
            )
            self._mark_unit_status(
                manifest=manifest,
                manifest_path=manifest_path,
                unit_id=unit.unit_id,
                status="running",
                artifact=str(artifact_path),
            )
            try:
                points = self._load_points_artifact(
                    artifact_path=points_artifact_path,
                    expected_source_hash=source_doc.sha256,
                    expected_model=self.model,
                )
                if points:
                    self._log(
                        "expander",
                        f"沿用既有要點提取 {unit.unit_id}。",
                        unit_id=unit.unit_id,
                    )
                else:
                    self._log(
                        "expander",
                        f"開始提取要點 {unit.unit_id}：{unit.unit_title}",
                        unit_id=unit.unit_id,
                    )
                    points = self._extract_points_for_unit(
                        source_doc=source_doc,
                        unit=unit,
                        units=units,
                    )
                    self._save_points_artifact(
                        artifact_path=points_artifact_path,
                        source_hash=source_doc.sha256,
                        unit=unit,
                        points=points,
                    )
                    self._log(
                        "expander",
                        f"要點提取完成 {unit.unit_id}，共 {len(points)} 條。",
                        unit_id=unit.unit_id,
                    )

                self._log(
                    "expander",
                    f"開始生成逐字稿 {unit.unit_id}：{unit.unit_title}",
                    unit_id=unit.unit_id,
                )
                generated_unit = self._generate_manuscript_for_unit(
                    source_doc=source_doc,
                    unit=unit,
                    units=units,
                    points=points,
                )
                completed_units.append(generated_unit)
                self._save_generated_unit(
                    artifact_path=artifact_path,
                    source_hash=source_doc.sha256,
                    generated_unit=generated_unit,
                )
                self._mark_unit_status(
                    manifest=manifest,
                    manifest_path=manifest_path,
                    unit_id=unit.unit_id,
                    status="completed",
                    artifact=str(artifact_path),
                )
                self._log(
                    "expander",
                    f"完成 {unit.unit_id}：{unit.unit_title}",
                    unit_id=unit.unit_id,
                )
            except Exception as exc:
                failed_units.append({"unit_id": unit.unit_id, "error": str(exc)})
                self._mark_unit_status(
                    manifest=manifest,
                    manifest_path=manifest_path,
                    unit_id=unit.unit_id,
                    status="failed",
                    artifact=str(artifact_path),
                    error=str(exc),
                )
                self._log(
                    "expander",
                    f"生成失敗 {unit.unit_id}：{exc}",
                    unit_id=unit.unit_id,
                )

        ordered_completed_units = self._load_available_generated_units(
            generated_dir=generated_dir,
            units=units,
            source_hash=source_doc.sha256,
        )
        combined_markdown = self._combine_units(ordered_completed_units)

        draft_path = output_dir / "draft_v1.md"
        draft_path.write_text(combined_markdown, encoding="utf-8")

        manifest_failed_units = self._collect_failed_units_from_manifest(manifest)
        has_all_units = len(ordered_completed_units) == len(units)
        if manifest_failed_units:
            manifest["status"] = "completed_with_failures" if has_all_units else "partial_completed_with_failures"
        else:
            manifest["status"] = "completed" if has_all_units else "partial_completed"
        manifest["completed_at"] = _utcnow()
        manifest["draft_path"] = str(draft_path)
        manifest["successful_unit_count"] = len(ordered_completed_units)
        manifest["failed_unit_count"] = len(manifest_failed_units)
        manifest["failed_units"] = manifest_failed_units
        self._save_manifest(manifest_path, manifest)

        self._progress("Stage 1 完成", 100)
        self._log(
            "system",
            f"Stage 1 完成：成功 {len(ordered_completed_units)} 個單元，失敗 {len(failed_units)} 個單元。",
        )

        summary.generated_units = ordered_completed_units
        summary.failed_units = manifest_failed_units
        summary.combined_markdown = combined_markdown
        return summary

    def _load_or_create_units(
        self,
        source_doc: SourceDocument,
        units_path: Path,
        manifest: Dict[str, Any],
        manifest_path: Path,
    ) -> List[UnitBoundary]:
        if units_path.exists() and manifest.get("source_sha256") == source_doc.sha256:
            units = self._load_units(units_path)
            if units:
                self._log("segmenter", f"沿用既有切割結果，共 {len(units)} 個單元。")
                return units

        self._progress("教學單元切割", 5)
        self._log("segmenter", "開始執行 Stage 1A 單元切割。")
        manifest["split_status"] = "running"
        self._save_manifest(manifest_path, manifest)
        try:
            units = self._split_units(source_doc)
            self._save_units(units_path, units)
            manifest["split_status"] = "completed"
            manifest["unit_count"] = len(units)
            manifest["units_path"] = str(units_path)
            self._save_manifest(manifest_path, manifest)
            self._log("segmenter", f"單元切割完成，共 {len(units)} 個單元。")
            return units
        except Exception as exc:
            manifest["status"] = "failed"
            manifest["split_status"] = "failed"
            manifest["error"] = str(exc)
            manifest["failed_at"] = _utcnow()
            self._save_manifest(manifest_path, manifest)
            self._log("segmenter", f"單元切割失敗：{exc}")
            raise

    def _split_units(self, source_doc: SourceDocument) -> List[UnitBoundary]:
        split_cutoff_line = source_doc.split_cutoff_line()
        if split_cutoff_line < 1:
            raise ValueError("No usable note lines remain above <!-- Ignore Below -->")
        if split_cutoff_line < len(source_doc.lines):
            self._log(
                "segmenter",
                f"偵測到 Ignore Below 標記，單元切割只使用第 1–{split_cutoff_line} 行。",
            )
        user_prompt = (
            "以下是已校正的釋經課筆記。每一行都帶有明確行號。\n"
            "請只回傳切割後的單元邊界與中繼資料，不可複製任何原始筆記內容。\n\n"
            "【來源筆記（含行號）】\n"
            f"{source_doc.with_line_numbers(end_line=split_cutoff_line)}"
        )
        response = self.llm.generate_json(
            system_prompt=self.split_prompt,
            user_prompt=user_prompt,
            json_schema=self.SPLIT_SCHEMA,
            temperature=0.0,
        )
        raw_units = response.get("units", [])
        try:
            return self._normalize_units(raw_units, line_count=split_cutoff_line)
        except Exception as exc:
            self._log("segmenter", f"初次切割結果驗證失敗，嘗試修正：{exc}")
            repair_user_prompt = (
                f"{user_prompt}\n\n"
                "【上一輪切割結果（有錯誤，請修正）】\n"
                f"{json.dumps({'units': raw_units}, ensure_ascii=False, indent=2)}\n\n"
                "【驗證錯誤】\n"
                f"{exc}\n\n"
                "請修正上一輪切割結果，並重新輸出完整 JSON。修正要求：\n"
                f"1. 所有單元必須落在 1 到 {split_cutoff_line} 行之內。\n"
                "2. 所有單元必須依 start_line 遞增排列。\n"
                "3. 單元之間不可重疊，後一單元的 start_line 必須大於前一單元的 end_line。\n"
                "4. 保留原本的邏輯切割意圖，只在必要處修正邊界。\n"
                "5. 仍然不可複製任何原始筆記內容到欄位中。\n"
            )
            repaired_response = self.llm.generate_json(
                system_prompt=self.split_prompt,
                user_prompt=repair_user_prompt,
                json_schema=self.SPLIT_SCHEMA,
                temperature=0.0,
            )
            repaired_units = repaired_response.get("units", [])
            normalized = self._normalize_units(repaired_units, line_count=split_cutoff_line)
            self._log("segmenter", "切割修正成功，已採用修正版邊界。")
            return normalized

    def _normalize_units(
        self,
        raw_units: List[Dict[str, Any]],
        line_count: int,
    ) -> List[UnitBoundary]:
        if not raw_units:
            raise ValueError("Splitter returned no units")

        normalized: List[UnitBoundary] = []
        seen_ids: Dict[str, int] = {}
        for index, raw_unit in enumerate(raw_units, start=1):
            unit_id = (raw_unit.get("unit_id") or f"u{index:03d}").strip() or f"u{index:03d}"
            if unit_id in seen_ids:
                unit_id = f"u{index:03d}"
            seen_ids[unit_id] = index

            start_line = int(raw_unit["start_line"])
            end_line = int(raw_unit["end_line"])
            if start_line < 1 or end_line < 1 or start_line > end_line or end_line > line_count:
                raise ValueError(
                    f"Invalid line range for {unit_id}: {start_line}-{end_line} with line_count={line_count}"
                )

            normalized.append(
                UnitBoundary(
                    unit_id=unit_id,
                    chapter_title=str(raw_unit.get("chapter_title", "")).strip(),
                    section_title=str(raw_unit.get("section_title", "")).strip(),
                    unit_title=str(raw_unit.get("unit_title", "")).strip(),
                    scripture_range=str(raw_unit.get("scripture_range", "")).strip(),
                    start_line=start_line,
                    end_line=end_line,
                    split_reason=str(raw_unit.get("split_reason", "")).strip(),
                )
            )

        normalized.sort(key=lambda item: (item.start_line, item.end_line, item.unit_id))
        previous_end = 0
        for unit in normalized:
            if unit.start_line <= previous_end:
                raise ValueError(
                    f"Overlapping unit boundaries detected at {unit.unit_id}: {unit.start_line}-{unit.end_line}"
                )
            previous_end = unit.end_line

        normalized_ids = [f"u{index + 1:03d}" for index in range(len(normalized))]
        for index, unit in enumerate(normalized):
            unit.unit_id = normalized_ids[index]
            unit.prev_unit_id = normalized_ids[index - 1] if index > 0 else None
            unit.next_unit_id = normalized_ids[index + 1] if index < len(normalized) - 1 else None

        return normalized

    def _generate_unit(
        self,
        source_doc: SourceDocument,
        unit: UnitBoundary,
        units: List[UnitBoundary],
    ) -> GeneratedUnit:
        raise NotImplementedError("Use _extract_points_for_unit and _generate_manuscript_for_unit")

    def _extract_points_for_unit(
        self,
        source_doc: SourceDocument,
        unit: UnitBoundary,
        units: List[UnitBoundary],
    ) -> List[Dict[str, str]]:
        current_slice = source_doc.slice_by_lines(unit.start_line, unit.end_line)
        previous_unit = self._find_unit(units, unit.prev_unit_id)
        next_unit = self._find_unit(units, unit.next_unit_id)
        previous_slice = (
            source_doc.slice_by_lines(previous_unit.start_line, previous_unit.end_line)
            if previous_unit
            else ""
        )
        next_slice = (
            source_doc.slice_by_lines(next_unit.start_line, next_unit.end_line)
            if next_unit
            else ""
        )

        user_prompt = (
            f"【章標題】\n{unit.chapter_title or '未標明'}\n\n"
            f"【節標題】\n{unit.section_title or '未標明'}\n\n"
            f"【單元標題】\n{unit.unit_title}\n\n"
            f"【經文範圍】\n{unit.scripture_range or '未標明'}\n\n"
            f"【當前單元筆記（第 {unit.start_line}–{unit.end_line} 行）】\n{current_slice}\n\n"
            f"【上一單元筆記】\n{previous_slice or '無'}\n\n"
            f"【下一單元筆記】\n{next_slice or '無'}\n"
        )
        generated_payload = self.llm.generate_json(
            system_prompt=self.point_extractor_prompt,
            user_prompt=user_prompt,
            json_schema=self.POINT_EXTRACTION_SCHEMA,
            temperature=0.0,
        )
        return self._normalize_points(generated_payload.get("points", []))

    def _generate_manuscript_for_unit(
        self,
        source_doc: SourceDocument,
        unit: UnitBoundary,
        units: List[UnitBoundary],
        points: List[Dict[str, str]],
    ) -> GeneratedUnit:
        current_slice = source_doc.slice_by_lines(unit.start_line, unit.end_line)
        previous_unit = self._find_unit(units, unit.prev_unit_id)
        next_unit = self._find_unit(units, unit.next_unit_id)
        heavy_unit = len(points) >= 20 or len(current_slice) >= 3000 or (unit.end_line - unit.start_line + 1) >= 90
        previous_slice = ""
        next_slice = ""
        if not heavy_unit:
            previous_slice = (
                source_doc.slice_by_lines(previous_unit.start_line, previous_unit.end_line)
                if previous_unit
                else ""
            )
            next_slice = (
                source_doc.slice_by_lines(next_unit.start_line, next_unit.end_line)
                if next_unit
                else ""
            )
        else:
            self._log(
                "expander",
                f"單元 {unit.unit_id} 較大（{len(points)} points, {len(current_slice)} chars），逐字稿生成省略相鄰單元上下文。",
                unit_id=unit.unit_id,
            )

        manuscript_timeout_seconds = max(self.timeout_seconds, 240.0 if heavy_unit else 180.0)

        points_json = json.dumps({"points": points}, ensure_ascii=False, indent=2)
        user_prompt = (
            f"【章標題】\n{unit.chapter_title or '未標明'}\n\n"
            f"【節標題】\n{unit.section_title or '未標明'}\n\n"
            f"【單元標題】\n{unit.unit_title}\n\n"
            f"【經文範圍】\n{unit.scripture_range or '未標明'}\n\n"
            f"【第一步已提取的結構化要點】\n{points_json}\n\n"
            f"【當前單元筆記（僅供校對與措辭對照）】\n{current_slice}\n\n"
            f"【上一單元筆記】\n{previous_slice or '無'}\n\n"
            f"【下一單元筆記】\n{next_slice or '無'}\n"
        )
        generated_payload = self.llm.generate_json(
            system_prompt=self.generator_prompt,
            user_prompt=user_prompt,
            json_schema=self.MANUSCRIPT_GENERATION_SCHEMA,
            temperature=0.2,
            timeout_seconds=manuscript_timeout_seconds,
        )
        manuscript_sections = self._normalize_manuscript_sections(generated_payload.get("manuscript_sections", {}))
        coverage_checks = self._normalize_coverage_checks(generated_payload.get("coverage_checks", []), points=points)
        coverage_summary = self._normalize_coverage_summary(
            generated_payload.get("coverage_summary", {}),
            points=points,
            coverage_checks=coverage_checks,
        )

        original_outline_score = self._outline_style_score(manuscript_sections)
        if self._manuscript_needs_prose_refinement(manuscript_sections):
            self._log(
                "expander",
                f"逐字稿偏向提綱式表達，嘗試 prose 化重寫 {unit.unit_id}。",
                unit_id=unit.unit_id,
            )
            retry_user_prompt = (
                user_prompt
                + "\n\n【重要修正要求】\n"
                + "上一版逐字稿仍偏向課堂提綱／筆記式表達。\n"
                + "請保留必要的小標題，但正文必須改寫為連續的分析性段落。\n"
                + "除非來源本身明確要求，禁止使用 `1. 2. 3.`、`第一/第二/第三`、`一、二、三、` 或 bullet points 作為正文主體展開方式。\n"
                + "每個小標題下方至少應有完整散文段落來承載論證，而不是用條列替代逐字稿。\n"
            )
            try:
                retry_payload = self.llm.generate_json(
                    system_prompt=self.generator_prompt,
                    user_prompt=retry_user_prompt,
                    json_schema=self.MANUSCRIPT_GENERATION_SCHEMA,
                    temperature=0.2,
                    timeout_seconds=manuscript_timeout_seconds,
                )
                retry_sections = self._normalize_manuscript_sections(retry_payload.get("manuscript_sections", {}))
                retry_coverage_checks = self._normalize_coverage_checks(
                    retry_payload.get("coverage_checks", []),
                    points=points,
                )
                retry_coverage_summary = self._normalize_coverage_summary(
                    retry_payload.get("coverage_summary", {}),
                    points=points,
                    coverage_checks=retry_coverage_checks,
                )
                retry_outline_score = self._outline_style_score(retry_sections)
                if retry_outline_score < original_outline_score:
                    manuscript_sections = retry_sections
                    coverage_checks = retry_coverage_checks
                    coverage_summary = retry_coverage_summary
                    self._log(
                        "expander",
                        f"採用 prose 化重寫版本 {unit.unit_id}（score {original_outline_score} -> {retry_outline_score}）。",
                        unit_id=unit.unit_id,
                    )
                else:
                    self._log(
                        "expander",
                        f"保留原版本 {unit.unit_id}（prose 化未改善 score {original_outline_score} -> {retry_outline_score}）。",
                        unit_id=unit.unit_id,
                    )
            except Exception as retry_exc:
                self._log(
                    "expander",
                    f"prose 化重寫失敗，保留原版本 {unit.unit_id}：{retry_exc}",
                    unit_id=unit.unit_id,
                )

        generated_markdown = self._render_generated_unit_markdown(
            points=points,
            manuscript_sections=manuscript_sections,
            coverage_checks=coverage_checks,
            coverage_summary=coverage_summary,
        )

        return GeneratedUnit(
            unit_id=unit.unit_id,
            chapter_title=unit.chapter_title,
            section_title=unit.section_title,
            unit_title=unit.unit_title,
            scripture_range=unit.scripture_range,
            start_line=unit.start_line,
            end_line=unit.end_line,
            prev_unit_id=unit.prev_unit_id,
            next_unit_id=unit.next_unit_id,
            points=points,
            manuscript_sections=manuscript_sections,
            coverage_checks=coverage_checks,
            coverage_summary=coverage_summary,
            generated_markdown=generated_markdown,
        )

    def _save_points_artifact(
        self,
        artifact_path: Path,
        source_hash: str,
        unit: UnitBoundary,
        points: List[Dict[str, str]],
    ) -> None:
        payload = {
            "unit_id": unit.unit_id,
            "chapter_title": unit.chapter_title,
            "section_title": unit.section_title,
            "unit_title": unit.unit_title,
            "scripture_range": unit.scripture_range,
            "start_line": unit.start_line,
            "end_line": unit.end_line,
            "prev_unit_id": unit.prev_unit_id,
            "next_unit_id": unit.next_unit_id,
            "points": points,
            "source_sha256": source_hash,
            "model": self.model,
            "generated_at": _utcnow(),
        }
        artifact_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_points_artifact(
        self,
        artifact_path: Path,
        expected_source_hash: str,
        expected_model: str,
    ) -> Optional[List[Dict[str, str]]]:
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        if payload.get("source_sha256") != expected_source_hash:
            return None
        if payload.get("model") != expected_model:
            return None
        raw_points = payload.get("points")
        if not isinstance(raw_points, list):
            return None
        return self._normalize_points(raw_points)

    def _normalize_points(self, raw_points: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        if not raw_points:
            raise ValueError("Generated output contains no extracted points")
        normalized: List[Dict[str, str]] = []
        seen_ids: set[str] = set()
        for index, raw_point in enumerate(raw_points, start=1):
            point_id = str(raw_point.get("point_id") or f"p{index:03d}").strip() or f"p{index:03d}"
            if point_id in seen_ids:
                point_id = f"p{index:03d}"
            seen_ids.add(point_id)
            category = str(raw_point.get("category") or "").strip()
            content = str(raw_point.get("content") or "").strip()
            if category not in {"釋經", "神學", "應用", "附錄"}:
                raise ValueError(f"Invalid point category: {category}")
            if not content:
                raise ValueError(f"Point {point_id} has empty content")
            normalized.append(
                {
                    "point_id": point_id,
                    "category": category,
                    "content": content,
                }
            )
        return normalized

    def _normalize_manuscript_sections(
        self,
        raw_sections: Dict[str, Any],
    ) -> Dict[str, Optional[str]]:
        normalized = {
            "exegesis": self._normalize_section_text(raw_sections.get("exegesis"), "釋經"),
            "theological_significance": self._normalize_section_text(raw_sections.get("theological_significance"), "神學意義"),
            "application": self._normalize_section_text(raw_sections.get("application"), "生活應用"),
            "appendix": self._normalize_section_text(raw_sections.get("appendix"), "附錄"),
        }
        if not any(normalized.values()):
            raise ValueError("Generated output contains no manuscript sections")
        return normalized

    def _normalize_coverage_checks(
        self,
        raw_checks: List[Dict[str, Any]],
        points: List[Dict[str, str]],
    ) -> List[Dict[str, str]]:
        point_ids = {point["point_id"] for point in points}
        normalized: List[Dict[str, str]] = []
        seen_ids: set[str] = set()
        for raw_check in raw_checks:
            point_id = str(raw_check.get("point_id") or "").strip()
            status = str(raw_check.get("status") or "").strip()
            note = str(raw_check.get("note") or "").strip()
            if point_id not in point_ids:
                raise ValueError(f"Coverage check references unknown point_id: {point_id}")
            if point_id in seen_ids:
                raise ValueError(f"Duplicate coverage check for point_id: {point_id}")
            if status not in {"covered", "needs_revision"}:
                raise ValueError(f"Invalid coverage status: {status}")
            if not note:
                raise ValueError(f"Coverage check for {point_id} has empty note")
            seen_ids.add(point_id)
            normalized.append(
                {
                    "point_id": point_id,
                    "status": status,
                    "note": note,
                }
            )
        if seen_ids != point_ids:
            missing = sorted(point_ids - seen_ids)
            raise ValueError(f"Coverage checks missing point ids: {', '.join(missing)}")
        return normalized

    def _normalize_coverage_summary(
        self,
        raw_summary: Dict[str, Any],
        points: List[Dict[str, str]],
        coverage_checks: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        total_points = len(points)
        actual_missing = [
            check["point_id"]
            for check in coverage_checks
            if check["status"] == "needs_revision"
        ]
        actual_covered = total_points - len(actual_missing)
        summary = {
            "covered_count": int(raw_summary.get("covered_count", actual_covered)),
            "total_points": int(raw_summary.get("total_points", total_points)),
            "missing_point_ids": [
                str(point_id).strip()
                for point_id in raw_summary.get("missing_point_ids", actual_missing)
                if str(point_id).strip()
            ],
        }
        summary["missing_point_ids"] = sorted(summary["missing_point_ids"])
        if summary["total_points"] != total_points:
            raise ValueError("Coverage summary total_points does not match extracted points")
        if summary["covered_count"] != actual_covered:
            raise ValueError("Coverage summary covered_count does not match coverage checks")
        if summary["missing_point_ids"] != sorted(actual_missing):
            raise ValueError("Coverage summary missing_point_ids does not match coverage checks")
        return summary

    def _render_generated_unit_markdown(
        self,
        points: List[Dict[str, str]],
        manuscript_sections: Dict[str, Optional[str]],
        coverage_checks: List[Dict[str, str]],
        coverage_summary: Dict[str, Any],
    ) -> str:
        point_lines = [
            f"{index}. [{point['category']}] {point['content']}"
            for index, point in enumerate(points, start=1)
        ]
        manuscript_lines: List[str] = []
        section_map = [
            ("exegesis", "釋經"),
            ("theological_significance", "神學意義"),
            ("application", "生活應用"),
            ("appendix", "附錄"),
        ]
        for key, label in section_map:
            section_text = manuscript_sections.get(key)
            if section_text:
                manuscript_lines.append(f"### {label}\n{section_text.strip()}")
        coverage_lines = []
        for index, check in enumerate(coverage_checks, start=1):
            status_icon = "✅" if check["status"] == "covered" else "🔄"
            coverage_lines.append(f"{index}. {status_icon} {check['point_id']} {check['note']}")
        missing_point_ids = coverage_summary["missing_point_ids"]
        missing_text = "無" if not missing_point_ids else "[" + ", ".join(missing_point_ids) + "]"
        return (
            "【要點清單】\n"
            + "\n".join(point_lines).strip()
            + "\n\n【逐字稿】\n"
            + "\n\n".join(manuscript_lines).strip()
            + "\n\n【核對確認】\n"
            + "\n".join(coverage_lines).strip()
            + f"\n已覆蓋：{coverage_summary['covered_count']}/{coverage_summary['total_points']}\n"
            + f"遺漏：{missing_text}"
        ).strip()

    def _clean_optional_text(self, value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _sanitize_section_headings(self, text: str, expected_label: str) -> str:
        lines = text.splitlines()
        while lines and lines[0].strip() == "":
            lines.pop(0)

        redundant_heading_re = re.compile(
            rf"^#{{2,6}}\s*{re.escape(expected_label)}(?:\s*[:：].*)?$"
        )
        while lines:
            first_line = lines[0].strip()
            if redundant_heading_re.fullmatch(first_line):
                lines.pop(0)
                while lines and lines[0].strip() == "":
                    lines.pop(0)
                continue
            break

        normalized_lines: List[str] = []
        heading_re = re.compile(r"^(#{1,6})(\s+.*)$")
        for line in lines:
            match = heading_re.match(line)
            if match:
                level = len(match.group(1))
                suffix = match.group(2)
                normalized_level = "#" * max(level, 4)
                normalized_lines.append(f"{normalized_level}{suffix}")
            else:
                normalized_lines.append(line)
        return "\n".join(normalized_lines).strip()

    def _normalize_section_text(self, value: Any, expected_label: str) -> Optional[str]:
        text = self._clean_optional_text(value)
        if not text:
            return None
        normalized_text = self._sanitize_section_headings(text, expected_label)
        return normalized_text or None

    def _outline_style_score(self, manuscript_sections: Dict[str, Optional[str]]) -> int:
        score = 0
        numbered_heading_re = re.compile(r"^#{3,6}\s*[一二三四五六七八九十]+\s*[、.．)]", re.MULTILINE)
        numbered_list_re = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
        chinese_sequence_re = re.compile(r"(第[一二三四五六七八九十]+|[一二三四五六七八九十]+、)")
        bullet_re = re.compile(r"^\s*[-*•]\s+", re.MULTILINE)
        for section_text in manuscript_sections.values():
            if not section_text:
                continue
            score += len(numbered_heading_re.findall(section_text)) * 3
            score += len(numbered_list_re.findall(section_text)) * 3
            score += len(bullet_re.findall(section_text)) * 2
            score += len(chinese_sequence_re.findall(section_text))
        return score

    def _manuscript_needs_prose_refinement(
        self,
        manuscript_sections: Dict[str, Optional[str]],
    ) -> bool:
        for section_text in manuscript_sections.values():
            if not section_text:
                continue
            numbered_heading_count = len(
                re.findall(r"^#{3,6}\s*[一二三四五六七八九十]+\s*[、.．)]", section_text, re.MULTILINE)
            )
            numbered_list_count = len(re.findall(r"^\s*\d+\.\s+", section_text, re.MULTILINE))
            bullet_count = len(re.findall(r"^\s*[-*•]\s+", section_text, re.MULTILINE))
            if numbered_heading_count >= 3 or numbered_list_count >= 2 or bullet_count >= 4:
                return True
        return False

    def _render_manuscript_only_markdown(
        self,
        manuscript_sections: Dict[str, Optional[str]],
    ) -> str:
        manuscript_lines: List[str] = []
        section_map = [
            ("exegesis", "釋經"),
            ("theological_significance", "神學意義"),
            ("application", "生活應用"),
            ("appendix", "附錄"),
        ]
        for key, label in section_map:
            section_text = manuscript_sections.get(key)
            if section_text:
                manuscript_lines.append(f"### {label}\n\n{section_text.strip()}")
        return "\n\n".join(manuscript_lines).strip()

    def _combine_units(self, generated_units: List[GeneratedUnit]) -> str:
        blocks: List[str] = []
        for index, unit in enumerate(generated_units, start=1):
            heading = f"## {_to_chinese_section_number(index)}、{unit.unit_title}"
            manuscript_markdown = self._render_manuscript_only_markdown(unit.manuscript_sections)
            blocks.append(f"{heading}\n\n{manuscript_markdown}")
        return "\n\n".join(blocks).strip()

    def _find_unit(self, units: List[UnitBoundary], unit_id: Optional[str]) -> Optional[UnitBoundary]:
        if not unit_id:
            return None
        for unit in units:
            if unit.unit_id == unit_id:
                return unit
        return None

    def _load_manifest(self, manifest_path: Path) -> Dict[str, Any]:
        if not manifest_path.exists():
            return {}
        return json.loads(manifest_path.read_text(encoding="utf-8"))

    def _initialize_manifest(
        self,
        manifest: Dict[str, Any],
        input_path: Path,
        output_dir: Path,
        source_doc: SourceDocument,
        force: bool,
    ) -> Dict[str, Any]:
        if not manifest or manifest.get("source_sha256") != source_doc.sha256 or force:
            manifest = {
                "status": "running",
                "started_at": _utcnow(),
                "input_path": str(input_path),
                "output_dir": str(output_dir),
                "source_sha256": source_doc.sha256,
                "model": self.model,
                "timeout_seconds": self.timeout_seconds,
                "max_retries": self.max_retries,
                "split_status": "pending",
                "units": {},
            }
        else:
            manifest["status"] = "running"
            manifest["resumed_at"] = _utcnow()
        return manifest

    def _save_manifest(self, manifest_path: Path, manifest: Dict[str, Any]) -> None:
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _save_units(self, units_path: Path, units: List[UnitBoundary]) -> None:
        payload = {
            "units": [asdict(unit) for unit in units],
        }
        units_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_units(self, units_path: Path) -> List[UnitBoundary]:
        data = json.loads(units_path.read_text(encoding="utf-8"))
        return [UnitBoundary(**item) for item in data.get("units", [])]

    def _save_generated_unit(
        self,
        artifact_path: Path,
        source_hash: str,
        generated_unit: GeneratedUnit,
    ) -> None:
        payload = asdict(generated_unit)
        payload["source_sha256"] = source_hash
        payload["model"] = self.model
        payload["generated_at"] = _utcnow()
        artifact_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_generated_unit(
        self,
        artifact_path: Path,
        expected_source_hash: str,
        expected_model: str,
    ) -> Optional[GeneratedUnit]:
        if not artifact_path.exists():
            return None
        payload = json.loads(artifact_path.read_text(encoding="utf-8"))
        if payload.get("source_sha256") != expected_source_hash:
            return None
        if payload.get("model") != expected_model:
            return None
        if not isinstance(payload.get("points"), list):
            return None
        if not isinstance(payload.get("manuscript_sections"), dict):
            return None
        if not isinstance(payload.get("coverage_checks"), list):
            return None
        if not isinstance(payload.get("coverage_summary"), dict):
            return None
        generated_markdown = str(payload.get("generated_markdown", "")).strip()
        if not generated_markdown:
            return None
        unit_payload = {
            key: payload.get(key)
            for key in [
                "unit_id",
                "chapter_title",
                "section_title",
                "unit_title",
                "scripture_range",
                "start_line",
                "end_line",
                "prev_unit_id",
                "next_unit_id",
                "points",
                "manuscript_sections",
                "coverage_checks",
                "coverage_summary",
                "generated_markdown",
                "status",
                "error",
            ]
        }
        return GeneratedUnit(**unit_payload)

    def _mark_unit_status(
        self,
        manifest: Dict[str, Any],
        manifest_path: Path,
        unit_id: str,
        status: str,
        artifact: str,
        error: Optional[str] = None,
    ) -> None:
        manifest.setdefault("units", {})
        manifest["units"][unit_id] = {
            "status": status,
            "artifact": artifact,
            "updated_at": _utcnow(),
        }
        if error:
            manifest["units"][unit_id]["error"] = error
        self._save_manifest(manifest_path, manifest)

    def _refresh_manifest_unit_statuses(
        self,
        manifest: Dict[str, Any],
        manifest_path: Path,
        units: List[UnitBoundary],
        generated_dir: Path,
        source_hash: str,
    ) -> None:
        manifest.setdefault("units", {})
        for unit in units:
            artifact_path = generated_dir / f"{unit.unit_id}.json"
            existing_unit = self._load_generated_unit(
                artifact_path=artifact_path,
                expected_source_hash=source_hash,
                expected_model=self.model,
            )
            current_entry = manifest["units"].get(unit.unit_id, {})
            if existing_unit:
                status = "completed"
                error = None
            elif current_entry.get("status") == "failed":
                status = "failed"
                error = current_entry.get("error")
            else:
                status = "pending"
                error = None
            manifest["units"][unit.unit_id] = {
                "status": status,
                "artifact": str(artifact_path),
                "updated_at": current_entry.get("updated_at") or _utcnow(),
            }
            if error:
                manifest["units"][unit.unit_id]["error"] = error
        self._save_manifest(manifest_path, manifest)

    def _load_available_generated_units(
        self,
        generated_dir: Path,
        units: List[UnitBoundary],
        source_hash: str,
    ) -> List[GeneratedUnit]:
        available_units: List[GeneratedUnit] = []
        for unit in units:
            artifact_path = generated_dir / f"{unit.unit_id}.json"
            existing_unit = self._load_generated_unit(
                artifact_path=artifact_path,
                expected_source_hash=source_hash,
                expected_model=self.model,
            )
            if existing_unit:
                available_units.append(existing_unit)
        return available_units

    def _collect_failed_units_from_manifest(self, manifest: Dict[str, Any]) -> List[Dict[str, str]]:
        failures: List[Dict[str, str]] = []
        for unit_id, payload in (manifest.get("units") or {}).items():
            if isinstance(payload, dict) and payload.get("status") == "failed":
                failures.append({"unit_id": unit_id, "error": str(payload.get("error") or "Unknown error")})
        return failures

    def _clear_selected_unit_outputs(self, output_dir: Path, selected_unit_ids: List[str]) -> None:
        generated_dir = output_dir / "generated_units"
        for unit_id in selected_unit_ids:
            for suffix in (".json", ".points.json"):
                artifact_path = generated_dir / f"{unit_id}{suffix}"
                if artifact_path.exists():
                    artifact_path.unlink()

    def _clear_previous_outputs(self, output_dir: Path) -> None:
        generated_dir = output_dir / "generated_units"
        if generated_dir.exists():
            shutil.rmtree(generated_dir)
        for filename in ["stage1_manifest.json", "stage1_units.json", "draft_v1.md", "stage1_draft.md", "stage1_logs.jsonl"]:
            file_path = output_dir / filename
            if file_path.exists():
                file_path.unlink()

    def _log(self, role: str, message: str, **fields: Any) -> None:
        if self.logger:
            self.logger.emit(role, message, **fields)

    def _progress(self, stage: str, progress: int) -> None:
        if self.progress_callback:
            self.progress_callback(stage, progress)


def run_stage1_pipeline(
    input_path: Path,
    output_dir: Path,
    model: str = "claude-sonnet-4-6",
    timeout_seconds: float = 90.0,
    max_retries: int = 3,
    force: bool = False,
    split_only: bool = False,
    selected_unit_ids: Optional[List[str]] = None,
    log_path: Optional[Path] = None,
    log_callback: Optional[LogCallback] = None,
    progress_callback: Optional[ProgressCallback] = None,
) -> RunSummary:
    logger = StructuredLogger(log_path, callback=log_callback) if log_path else None
    pipeline = Stage1Pipeline(
        model=model,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
        logger=logger,
        progress_callback=progress_callback,
    )
    return pipeline.run(
        input_path=input_path,
        output_dir=output_dir,
        force=force,
        split_only=split_only,
        selected_unit_ids=selected_unit_ids,
    )
