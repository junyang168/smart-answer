"""Build a reproducible, source-anchored detailed knowledge package for one sermon."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.corpus_survey_runner import PROJECT_ROOT, _load, _slug
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.base_contract_coverage import sentence_spans
from backend.pipeline.detailed_knowledge_extraction import (
    DETAILED_RESPONSE_SCHEMA,
    AuditedSentence,
    DetailedExtractionValidationError,
    detailed_response_schema,
    exclusions_from_audit,
    extraction_identity,
    validate_response,
    validate_sentence_audit,
)
from backend.pipeline.sentence_ledger import sentence_id as ledger_sentence_id
from backend.pipeline.extraction_sections import (
    DEFAULT_SECTION_LEVEL,
    FROM_GENERATOR,
    SectionBoundaryError,
    Section,
    SectionPlan,
    apply_section_limit,
    combine_sections,
    generated_plan_insertions,
    has_transport_splits,
    leading_untitled_body_end,
    load_cached_plan,
    plan_sections,
    save_plan,
    section_generation_payload,
    section_payload,
    sections_from_structure,
    structure_has_section_headings,
    validate_titled_section_plan,
)
from backend.pipeline.knowledge_source import load_source_manifest, markdown_source_document
from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    validate_merged_package,
)
from backend.pipeline.relation_id_namespace import generation_namespace
from backend.pipeline.llm_usage import usage_row, usage_summary
from backend.pipeline.run_ledger import RunCancelled, RunRecord, run_record
from backend.pipeline.sentence_ledger_runner import run as run_ledger
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient
from backend.pipeline.subtitle_generation import generate_subtitles
from backend.pipeline.sermon_subtitle_persistence import (
    SubtitlePersistenceError,
    apply_insertions,
    verify_saved_result,
)
from backend.pipeline.source_projection import (
    EditorialHeading,
    LOCATOR_SPACE,
    SourceProjection,
    VISUAL_RENDERER_VERSION,
    VisualSourceBlock,
    VisualSourceAttestationError,
    is_editorial_row,
    live_script,
    project_script,
    provably_nonspoken_inline_markup,
    validate_visual_source_attestations,
    visual_source_blocks,
)


#: What each supported model needs to be reached. `gpt-5.6-sol` is the default,
#: measured on the whole 太16:21–23 母本 under the production rules: against
#: Claude Opus 5 it covers 129 of 132 substantive-prose sentences to Opus's 128,
#: produces 29% more observations and 56% more claims with the same zero
#: load_bearing orphans, keeps Traditional characters at least as reliably, and
#: costs about a quarter as much. It also restores the review stage's premise --
#: `corpus_ai_review` is a Claude model reading another family's output, which
#: is the point of it.
#:
#: An earlier reading of this comparison favoured Opus. It was taken with a
#: cut-down prompt that omitted the load_bearing rule, the relation-table
#: boundaries and the script requirement, where a stronger model supplies what
#: the instructions leave out. Once the rules were written down the ordering
#: reversed. Compare models on the prompt you will actually ship.
#: Whether a backend is sent `reasoning_effort` is declared here, not guessed
#: from the model id. `sends_reasoning_effort` absent means "undeclared", and
#: `Stage1OpenAIClient` then falls back to the old `gpt-5.6` prefix test, so an
#: entry that says nothing behaves exactly as it did.
#:
#: `gpt` deliberately declares nothing: the family spans models that take the
#: parameter (`gpt-5.6*`) and models that do not, so the answer belongs to a
#: model rather than to the family, and inventing a family-wide answer here
#: would be the same guess wearing a different hat.
MODEL_BACKENDS = {
    "claude": {"kind": "anthropic"},
    "gpt": {"kind": "openai"},
    # DeepSeek does not accept the parameter at all -- stated, where it used to
    # be inferred from not being named `gpt-5.6`.
    "deepseek": {"kind": "openai", "base_url": "https://api.deepseek.com",
                 "api_key_env": "DEEPSEEK_API_KEY", "sends_reasoning_effort": False},
}
DEFAULT_MODEL = "gpt-5.6-sol"

DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")
DEFAULT_OUTPUT_DIR = wang_platform_paths().claim_layer_staging / "detailed-extractions"
PROMPT_PATH = Path("backend/pipeline/prompts/detailed_knowledge_extraction.md")
NOTES_PROMPT_PATH = Path("backend/pipeline/prompts/detailed_notes_knowledge_extraction.md")
VALIDATION_ATTEMPTS = 4
MODEL_INPUT_CONTRACT_VERSION = "detailed-extraction-spoken-text-v3"
VISUAL_MODEL_INPUT_CONTRACT_VERSION = "detailed-extraction-visual-source-v1"
PACKAGE_COMPILER_VERSION = "wang-shared-knowledge-compiler-v3"
SECTION_CACHE_VERSION = "wang-detailed-extraction-section-cache-v3"
VISUAL_SOURCE_HEADER = (
    "本章节含有王教授在讲道中展示或画出的图。图是第一等来源证据，不是编辑备注，"
    "但也不是口述原句。Sxxxx/Vnn 是图的定位码。请同时阅读附图、原始 SVG 与系统生成的"
    "逐元素 literal facts，保留文字、颜色、位置、连线、箭头、包含或重叠等视觉关系。"
    "不要把几何事实擅自升级为神学解释；结合相邻口述判断教授如何解释该图。"
    "视觉锚点必须使用 source_modality=visual、对应的 Sxxxx/Vnn、空 verbatim_excerpt，"
    "并列出实际使用的 visual_fact_ids。同一幅图的全部 literal fact ID 必须在该章节各视觉"
    "锚点的并集中恰当出现，包含文字、形状、连线、分组和坐标关系；结构元素可归入使用整图"
    "的观察，但不能静默遗漏。普通口述锚点必须使用 source_modality=spoken，且"
    " visual_fact_ids 为空。每个视觉来源单位都必须被视觉锚点覆盖，不能作为结构标记排除。\n\n"
)


def _extraction_run_record(source_id: str, *, enabled: bool):
    """Return a normal ledger context or an explicit no-write record.

    A canary is allowed to create staging artifacts without touching the
    operational database.  Making that a command-line contract is safer than
    relying on whoever launches it to unset every database environment name.
    """

    if enabled:
        return run_record(subject=source_id, stage="extraction")
    return nullcontext(
        RunRecord(
            run_id="UNRECORDED-CANARY",
            subject_id=source_id,
            stage="extraction",
            subject_kind="source",
            conn=None,
        )
    )


def _assert_inline_source_readable(
    source_id: str,
    projection: SourceProjection,
    visual_source_attestations: Mapping[str, str] | None = None,
) -> None:
    """Allow attested professor visuals; reject malformed/editorial markup."""

    findings: list[str] = []
    try:
        validate_visual_source_attestations(
            projection, visual_source_attestations
        )
    except VisualSourceAttestationError as exc:
        findings.append(str(exc))
    visuals_by_segment: dict[str, list[VisualSourceBlock]] = {}
    for visual in projection.visual_blocks:
        visuals_by_segment.setdefault(visual.segment_index, []).append(visual)
    for position, row in enumerate(projection.body_rows, start=1):
        locator = f"S{position:04d}"
        visual_ranges = [
            (visual.char_start, visual.char_end)
            for visual in visuals_by_segment.get(locator, [])
        ]
        for span in provably_nonspoken_inline_markup(str(row.get("text") or "")):
            if span.kind == "svg" and any(
                left <= span.start and span.end <= right
                for left, right in visual_ranges
            ):
                continue
            if span.kind == "html_comment" and any(
                left <= span.start and span.end <= right
                for left, right in visual_ranges
            ):
                continue
            findings.append(f"{locator} ({span.kind})")
    if findings:
        raise DetailedExtractionValidationError(
            f"{source_id}: unattested or unreadable inline source at "
            + ", ".join(findings)
            + "; attest visual source explicitly, or correct malformed/editorial "
            "content through the sermon editor and republish before extraction"
        )


def _atomic_artifact_write(path: Path, data: bytes) -> None:
    """Install one recovery/audit artifact without exposing partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            try:
                os.fsync(directory_fd)
            except OSError:
                # The file was already fsynced and atomically installed. Do
                # not report failure after commit and invite a duplicate retry.
                pass
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_json_artifact_write(path: Path, payload: Any) -> None:
    _atomic_artifact_write(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _package_artifact_sha256(package: dict[str, Any]) -> str:
    """Hash the complete current package, excluding only this self-hash."""

    candidate = json.loads(json.dumps(package, ensure_ascii=False))
    (candidate.get("extraction") or {}).pop("artifact_sha256", None)
    return hashlib.sha256(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    raw = path.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()[:16]
    archive = path.parent / "generations" / f"{path.stem}.{fingerprint}.json"
    if not archive.exists():
        _atomic_artifact_write(archive, raw)


def _validation_feedback(
    error: DetailedExtractionValidationError,
    transcript: dict[str, Any],
) -> str:
    message = str(error)
    segment_rows: list[str] = []
    # Sxxxx addresses professor-body coordinates, not physical JSON rows.
    # A source may co-locate type=subtitle editor rows in the same script.  A
    # retry that indexes the mixed list directly can therefore show the model
    # the preceding body row or even an editorial heading while claiming it is
    # the failed locator.
    body_rows = project_script(transcript.get("script") or []).body_rows
    for locator in dict.fromkeys(re.findall(r"\bS\d{4}\b", message)):
        ordinal = int(locator[1:]) - 1
        if 0 <= ordinal < len(body_rows):
            segment_rows.append(
                f"[{locator}]\n{str(body_rows[ordinal].get('text') or '')}"
            )
    detail = (
        "\n涉及段落的完整原文如下：\n" + "\n\n".join(segment_rows)
        if segment_rows else ""
    )
    markup_guidance = (
        "该 excerpt 虽逐字存在，但落在 Markdown/HTML 编辑结构内；"
        "请改用同一论点在普通来源正文中的逐字片段，不可锚定该结构。"
        if "provenance-ambiguous inline markup" in message
        else ""
    )
    return (
        f"上一版未通过机械验证：{message}。{detail}\n"
        "请保留上一版中其余有效对象，只修复所有同类机械错误，再重新输出完整 JSON。"
        f"{markup_guidance}"
        "每个 verbatim_excerpt 必须从对应段落连续逐字复制；不能改字、补标点或拼接。"
    )


def _print_usage(source_id: str, usage_rows: list[dict[str, Any]]) -> None:
    if not usage_rows:
        return
    print(json.dumps(usage_summary(source_id, usage_rows), ensure_ascii=False))


def _archive_rejected_candidate(
    *, output_dir: Path, transcript_id: str, attempt: int, candidate: dict[str, Any],
    error: DetailedExtractionValidationError,
) -> None:
    target = output_dir / "rejected-generations" / _slug(transcript_id) / f"attempt-{attempt:02d}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = target.with_name(f"attempt-{attempt:02d}-{timestamp}.json")
    _atomic_json_artifact_write(
        target,
        {"validation_error": str(error), "candidate": candidate},
    )


def _segment_texts(source: dict[str, Any]) -> list[str]:
    return [str(segment.get("text") or "") for segment in source.get("script") or []]


def segment_locator(position: int) -> str:
    """The anchor locator for a segment, by its position in the whole source.

    Deliberately global. A section is a slice of the document but must never
    renumber it: anchors have to stay resolvable against the full source, or the
    ledger cannot place them and every downstream reader breaks.
    """

    return f"S{position + 1:04d}"


def section_sentences(source: dict[str, Any], section: Section) -> list[AuditedSentence]:
    """Every sentence the section has to be answered for, with a stable id."""

    projection = project_script(source.get("script") or [])
    script = projection.body_rows
    visuals_by_segment: dict[str, list[VisualSourceBlock]] = {}
    for visual in projection.visual_blocks:
        visuals_by_segment.setdefault(visual.segment_index, []).append(visual)
    rows: list[AuditedSentence] = []
    parent_start = section.parent_start if section.parent_start is not None else section.start
    parent_end = section.parent_end if section.parent_end is not None else section.end
    for position in range(parent_start, parent_end):
        text = str(script[position].get("text") or "")
        locator = segment_locator(position)
        visuals = visuals_by_segment.get(locator, [])
        if not visuals:
            for start, end in sentence_spans(text):
                rows.append(AuditedSentence(
                    sentence_id=f"{locator}#{len(rows) + 1:03d}",
                    segment_index=locator,
                    text=text[start:end],
                    char_start=start,
                    char_end=end,
                ))
            continue
        cursor = 0
        for visual in visuals:
            spoken_chunk = text[cursor:visual.char_start]
            for start, end in sentence_spans(spoken_chunk):
                rows.append(AuditedSentence(
                    sentence_id=f"{locator}#{len(rows) + 1:03d}",
                    segment_index=locator,
                    text=spoken_chunk[start:end],
                    char_start=cursor + start,
                    char_end=cursor + end,
                ))
            rows.append(AuditedSentence(
                sentence_id=f"{visual.locator}#001",
                segment_index=visual.locator,
                text=(
                    f"视觉来源 {visual.locator}：{len(visual.facts)} 个逐元素 literal facts；"
                    "必须由 visual anchor 覆盖。"
                ),
                source_modality="visual",
                char_start=visual.char_start,
                char_end=visual.char_end,
            ))
            cursor = visual.char_end
        spoken_chunk = text[cursor:]
        for start, end in sentence_spans(spoken_chunk):
            rows.append(AuditedSentence(
                sentence_id=f"{locator}#{len(rows) + 1:03d}",
                segment_index=locator,
                text=spoken_chunk[start:end],
                char_start=cursor + start,
                char_end=cursor + end,
            ))
    if section.sentence_start is None and section.sentence_end is None:
        return rows
    if section.sentence_start is None or section.sentence_end is None:
        raise ValueError("section sentence range must provide both start and end")
    return rows[section.sentence_start:section.sentence_end]


def _source_unit_count(text: str) -> int:
    """Count speech sentences plus first-class visual blocks in one body row."""

    projection = project_script([{"index": 1, "text": str(text or "")}])
    if not projection.visual_blocks:
        return len(sentence_spans(str(text or "")))
    return len(
        section_sentences(
            {"script": list(projection.body_rows)},
            Section(index=1, start=0, end=1, title=""),
        )
    )


def _section_prompt_body(
    source: dict[str, Any],
    section: Section,
    sentences: Sequence[AuditedSentence],
) -> str:
    """Render one section: its text, then the sentences it must account for.

    The listing is the whole change. Given the text alone the model produces
    records and stops when it feels done; given the text and "here are your 42
    sentences, one verdict each" it enumerates. That is measured, not assumed --
    50% coverage against 100% on the same material.
    """

    script = source.get("script") or []
    projection = project_script(script)
    if projection.visual_blocks:
        target_visual_locators = {
            row.segment_index
            for row in sentences
            if row.source_modality == "visual"
        }
        visuals_by_segment: dict[str, list[VisualSourceBlock]] = {}
        for visual in projection.visual_blocks:
            visuals_by_segment.setdefault(visual.segment_index, []).append(visual)

        rendered_rows: list[str] = []
        visual_rows: list[str] = []
        parent_start = (
            section.parent_start
            if section.parent_start is not None
            else section.start
        )
        parent_end = (
            section.parent_end if section.parent_end is not None else section.end
        )
        target_by_segment: dict[str, list[AuditedSentence]] = {}
        for sentence in sentences:
            target_by_segment.setdefault(
                sentence.segment_index.split("/", 1)[0], []
            ).append(sentence)
        for position in range(parent_start, parent_end):
            locator = segment_locator(position)
            targets = target_by_segment.get(locator, [])
            if not targets:
                continue
            source_text = str(projection.body_rows[position].get("text") or "")
            row_visuals = visuals_by_segment.get(locator, [])
            if section.sentence_start is None:
                left, right = 0, len(source_text)
            else:
                if any(
                    row.char_start is None or row.char_end is None
                    for row in targets
                ):
                    raise DetailedExtractionValidationError(
                        f"{locator}: target source-unit span is missing"
                    )
                left = min(int(row.char_start) for row in targets)
                right = max(int(row.char_end) for row in targets)
            text = source_text[left:right]
            for visual in sorted(
                row_visuals, key=lambda row: row.char_start, reverse=True
            ):
                if visual.char_end <= left or visual.char_start >= right:
                    continue
                local_start = max(visual.char_start, left) - left
                local_end = min(visual.char_end, right) - left
                replacement = (
                    f"\n[visual source {visual.locator}]\n"
                    if visual.locator in target_visual_locators
                    else "\n"
                )
                text = text[:local_start] + replacement + text[local_end:]
            label = (
                f"[segment {locator}; internal source-unit-range target]"
                if section.sentence_start is not None
                else f"[segment {locator}]"
            )
            rendered_rows.append(f"{label}\n{text}")
            for visual in row_visuals:
                if visual.locator not in target_visual_locators:
                    continue
                visual_rows.append(
                    f"[visual source {visual.locator}]\n"
                    f"raw_sha256={visual.raw_sha256}\n"
                    f"canonical_sha256={visual.canonical_sha256}\n"
                    "literal_facts="
                    + json.dumps(list(visual.facts), ensure_ascii=False, sort_keys=True)
                    + "\nraw_svg=\n"
                    + visual.raw_svg
                )
        body = "\n\n".join(rendered_rows)
        if visual_rows:
            body += "\n\n===== 视觉来源（原始 SVG 与逐元素事实）=====\n\n" + "\n\n".join(visual_rows)
        listing = "\n".join(f"[{row.sentence_id}] {row.text}" for row in sentences)
        split_context = (
            "本输入是系统为 transport 上限生成的内部连续分片，不是新的来源章节。"
            "只抽取并逐句审核下方列出的目标句和视觉来源；相邻分片关系由后续阶段恢复。\n\n"
            if section.sentence_start is not None
            else ""
        )
        return (
            f"范围：{segment_locator(section.start)}–{segment_locator(section.end - 1)}"
            f"（{section.length} 段）\n\n"
            f"{split_context}"
            f"{body}\n\n"
            f"===== 本章节全部来源单位（{len(sentences)} 个），每一个都必须在 sentence_audit 中出现一次 =====\n\n"
            f"{listing}"
        )
    if section.sentence_start is None:
        body = "\n\n".join(
            "[segment {locator}]\n{text}".format(
                locator=segment_locator(position),
                text=script[position].get("text", ""),
            )
            for position in range(section.start, section.end)
        )
    else:
        parent_start = section.parent_start if section.parent_start is not None else section.start
        parent_end = section.parent_end if section.parent_end is not None else section.end
        cursor = 0
        rendered: list[str] = []
        for position in range(parent_start, parent_end):
            source_text = str(script[position].get("text") or "")
            spans = sentence_spans(source_text)
            row_start = max(0, section.sentence_start - cursor)
            row_end = min(len(spans), section.sentence_end - cursor)
            cursor += len(spans)
            if row_start >= row_end:
                continue
            # Preserve every original character between the first and last
            # selected sentence. Joining normalized sentence strings would
            # manufacture adjacency and make a cross-sentence verbatim anchor
            # fail against the real source row.
            target_text = (
                source_text
                if row_start == 0 and row_end == len(spans)
                else source_text[spans[row_start][0]:spans[row_end - 1][1]]
            )
            rendered.append(
                "[segment {locator}; internal sentence-range target]\n{text}".format(
                    locator=segment_locator(position),
                    text=target_text,
                )
            )
        body = "\n\n".join(rendered)
    listing = "\n".join(f"[{row.sentence_id}] {row.text}" for row in sentences)
    split_context = (
        "本输入是系统为 transport 上限生成的内部连续分片，不是新的来源章节。"
        "只抽取并逐句审核下方列出的目标句；相邻分片关系由后续阶段恢复。\n\n"
        if section.sentence_start is not None
        else ""
    )
    return (
        f"范围：{segment_locator(section.start)}–{segment_locator(section.end - 1)}"
        f"（{section.length} 段）\n\n"
        f"{split_context}"
        f"{body}\n\n"
        f"===== 本章节全部句子（{len(sentences)} 句），每一句都必须在 sentence_audit 中出现一次 =====\n\n"
        f"{listing}"
    )


def _section_cache_path(output_dir: Path, source_id: str, fingerprint: str, section: Section) -> Path:
    sentence_suffix = (
        f"-s{section.sentence_start:04d}-{section.sentence_end:04d}"
        if section.sentence_start is not None and section.sentence_end is not None
        else ""
    )
    return (
        output_dir / "section-cache" / _slug(source_id) / fingerprint[:16]
        / f"p{section.start:04d}-{section.end:04d}{sentence_suffix}.json"
    )


def _section_cache_payload(section: Section) -> dict[str, Any]:
    """Coordinates that make one section response reusable across plan renumbering."""

    payload = section_generation_payload(section)
    payload.pop("index", None)
    return payload


def _section_generation_fingerprint(
    model_contract_fingerprint: str, model_input_sha256: str
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "model_contract_fingerprint_sha256": model_contract_fingerprint,
                "model_input_sha256": model_input_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _section_cache_artifact_sha256(artifact: dict[str, Any]) -> str:
    candidate = json.loads(json.dumps(artifact, ensure_ascii=False))
    candidate.pop("artifact_sha256", None)
    return hashlib.sha256(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _section_cache_artifact(
    section: Section,
    response: dict[str, Any],
    fingerprint: str,
    *,
    model_input_sha256: str,
) -> dict[str, Any]:
    artifact = {
        "schema_version": SECTION_CACHE_VERSION,
        "generation_fingerprint_sha256": fingerprint,
        "model_input_sha256": model_input_sha256,
        "section": _section_cache_payload(section),
        "response": response,
    }
    artifact["artifact_sha256"] = _section_cache_artifact_sha256(artifact)
    return artifact


def _load_valid_section_cache(
    path: Path,
    *,
    section: Section,
    fingerprint: str,
    source: dict[str, Any],
    sentences: Sequence[Any],
    model_input_sha256: str,
) -> dict[str, Any] | None:
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(artifact, dict):
            return None
        if artifact.get("schema_version") != SECTION_CACHE_VERSION:
            return None
        if artifact.get("generation_fingerprint_sha256") != fingerprint:
            return None
        if artifact.get("model_input_sha256") != model_input_sha256:
            return None
        if artifact.get("section") != _section_cache_payload(section):
            return None
        if artifact.get("artifact_sha256") != _section_cache_artifact_sha256(artifact):
            return None
        response = artifact.get("response")
        if not isinstance(response, dict):
            return None
        validate_response(
            response,
            source,
            visible_locators={
                row.segment_index.split("/", 1)[0] for row in sentences
            },
            visible_visual_locators={
                row.segment_index
                for row in sentences
                if row.source_modality == "visual"
            },
        )
        validate_sentence_audit(response, source, sentences)
    except (
        OSError,
        json.JSONDecodeError,
        DetailedExtractionValidationError,
        KeyError,
        TypeError,
        ValueError,
    ):
        return None
    return response


def _section_model_input_sha256s(
    source: dict[str, Any],
    header: str,
    plan: SectionPlan,
) -> list[dict[str, Any]]:
    """Hash the exact first-call user input for every planned section."""

    return [
        {
            "section_index": section.index,
            "sha256": _section_model_input_sha256(
                source,
                header,
                section,
                section_sentences(source, section),
            ),
        }
        for section in plan.sections
    ]


def _section_model_input_sha256(
    source: dict[str, Any],
    header: str,
    section: Section,
    sentences: Sequence[Any],
) -> str:
    value = header + _section_prompt_body(source, section, sentences)
    visual_blocks = _section_visual_blocks(source, sentences)
    if visual_blocks:
        # Cache identity follows the canonical source and the versioned render
        # contract, not host-specific PNG encoder bytes. The actual attached
        # PNG SHA is recorded separately in per-section provenance.
        image_rows = [
            {
                "locator": block.locator,
                "raw_sha256": block.raw_sha256,
                "canonical_sha256": block.canonical_sha256,
                "renderer_version": VISUAL_RENDERER_VERSION,
            }
            for block in visual_blocks
        ]
        value += "\n\n===== ATTACHED VISUAL IMAGE IDENTITY =====\n" + json.dumps(
            image_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _section_visual_blocks(
    source: dict[str, Any], sentences: Sequence[AuditedSentence]
) -> list[VisualSourceBlock]:
    wanted = {
        row.segment_index
        for row in sentences
        if row.source_modality == "visual"
    }
    return [
        block
        for block in project_script(source.get("script") or []).visual_blocks
        if block.locator in wanted
    ]


def _render_visual_png(block: VisualSourceBlock) -> bytes:
    if not block.readable:
        raise DetailedExtractionValidationError(
            f"{block.locator}: cannot render unreadable visual source: {block.parse_error}"
        )
    try:
        import cairosvg

        return bytes(
            cairosvg.svg2png(
                bytestring=_visual_svg_for_render(block).encode("utf-8"),
                background_color="#ffffff",
            )
        )
    except Exception as exc:
        raise DetailedExtractionValidationError(
            f"{block.locator}: SVG render failed: {type(exc).__name__}: {exc}"
        ) from exc


_VISUAL_RENDER_FONT_STACK = (
    "'Arial Unicode MS', 'Heiti SC', 'Noto Sans CJK SC', "
    "'Noto Sans CJK TC', sans-serif"
)
_SVG_ROOT_TAG = re.compile(r"<svg\b[^>]*>", re.I | re.S)


def _visual_svg_for_render(block: VisualSourceBlock) -> str:
    """Add a renderer-only CJK fallback without changing source evidence."""

    root = _SVG_ROOT_TAG.search(block.raw_svg)
    if root is None:
        raise DetailedExtractionValidationError(
            f"{block.locator}: SVG render input has no root tag"
        )
    style = (
        "<style>text,tspan{font-family:"
        + _VISUAL_RENDER_FONT_STACK
        + " !important;}</style>"
    )
    return block.raw_svg[: root.end()] + style + block.raw_svg[root.end() :]


def _visual_image_paths(
    *,
    output_dir: Path,
    source_id: str,
    blocks: Sequence[VisualSourceBlock],
) -> list[Path]:
    paths: list[Path] = []
    for block in blocks:
        raw = _render_visual_png(block)
        path = (
            output_dir
            / "visual-assets"
            / _slug(source_id)
            / f"{block.locator.rsplit('/', 1)[-1]}-{block.raw_sha256[:16]}.png"
        )
        _atomic_artifact_write(path, raw)
        paths.append(path.resolve())
    return paths


def _visual_image_provenance(
    *,
    output_dir: Path,
    blocks: Sequence[VisualSourceBlock],
    paths: Sequence[Path],
) -> list[dict[str, Any]]:
    if len(blocks) != len(paths):
        raise DetailedExtractionValidationError(
            "visual render provenance does not match the attached image count"
        )
    return [
        {
            "locator": block.locator,
            "raw_sha256": block.raw_sha256,
            "canonical_sha256": block.canonical_sha256,
            "renderer_version": VISUAL_RENDERER_VERSION,
            "background_color": "#ffffff",
            "font_stack": _VISUAL_RENDER_FONT_STACK,
            "png_path": str(path.relative_to(output_dir.resolve())),
            "png_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for block, path in zip(blocks, paths, strict=True)
    ]


def _subtitle_provider(source_id: str, client: CodexSubscriptionClient | None = None):
    """The sermon editor's own subtitle generator, for sources with no headings.

    It raises on failure and this runner does not catch it, so a source whose
    boundaries could not be generated fails instead of quietly becoming one
    section -- which is whole-document extraction, the behaviour sectioning
    exists to replace.
    """

    def provider(paragraphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return generate_subtitles(
            paragraphs,
            subject=source_id,
            consumer="extraction_sections",
            client=client,
            require_leading_title=True,
        )

    return provider


def _persist_generated_subtitles(
    *,
    source_id: str,
    source: dict[str, Any],
    raw: bytes,
    source_path: Path,
    output_dir: Path,
    actor_id: str,
    client: CodexSubscriptionClient | None = None,
    writer: Callable[..., dict[str, Any]] | None = None,
    scope_end: int | None = None,
    cached_generated_plan: SectionPlan | None = None,
) -> dict[str, Any]:
    """Persist governed subtitles, reusing the frozen generated plan when safe."""

    if source_path.parent.name != "script_review":
        raise SubtitlePersistenceError(
            "generated subtitles can only be persisted to a script_review source"
        )
    source_sha256 = hashlib.sha256(raw).hexdigest()
    segments = list(project_script(source.get("script")).body_rows)
    if scope_end is not None:
        if scope_end <= 0 or scope_end > len(segments):
            raise SubtitlePersistenceError(
                f"invalid subtitle generation scope end {scope_end!r}"
            )
        segments = segments[:scope_end]
    paragraphs = [
        {"index": segment.get("index"), "text": segment.get("text")}
        for segment in segments
    ]
    indexes = [str(row.get("index")) for row in paragraphs]
    if len(indexes) != len(set(indexes)):
        raise SubtitlePersistenceError("sermon paragraph indexes are not unique")

    if cached_generated_plan is not None:
        try:
            insertions = generated_plan_insertions(cached_generated_plan, paragraphs)
        except SectionBoundaryError as exc:
            raise SubtitlePersistenceError(str(exc)) from exc
        insertion_origin = "cached_generated_section_plan"
        print(json.dumps({
            "phase": "subtitle_generation", "source": source_id,
            "paragraphs": len(paragraphs), "status": "reused_cached_section_plan",
            "model_called": False,
        }, ensure_ascii=False), flush=True)
    else:
        insertion_origin = "new_model_generation"
        print(json.dumps({
            "phase": "subtitle_generation", "source": source_id,
            "paragraphs": len(paragraphs), "status": "started",
        }, ensure_ascii=False), flush=True)
        insertions = generate_subtitles(
            paragraphs,
            subject=source_id,
            consumer="extraction_persisted_subtitles",
            client=client,
            require_leading_title=True,
        )
    if not insertions:
        raise SubtitlePersistenceError(
            f"{source_id}: subtitle generator returned no insertions; extraction not started"
        )
    if not any(
        int(row.get("level") or 0) == 1
        and str(row.get("after_index") or "").upper() == "START"
        for row in insertions
    ):
        raise SubtitlePersistenceError(
            f"{source_id}: subtitle plan did not title the leading section; extraction not started"
        )
    allowed_after_indexes = {"START", *indexes}
    seen_boundaries: set[tuple[str, int]] = set()
    final_scoped_index = str(paragraphs[-1].get("index"))
    for insertion in insertions:
        raw_after = insertion.get("after_index")
        after_index = "START" if str(raw_after).upper() == "START" else str(raw_after)
        if raw_after is None or after_index not in allowed_after_indexes:
            raise SubtitlePersistenceError(
                f"{source_id}: subtitle after_index {raw_after!r} is outside its generation scope"
            )
        try:
            insertion_level = int(insertion.get("level"))
        except (TypeError, ValueError) as exc:
            raise SubtitlePersistenceError(
                f"{source_id}: subtitle level is not an integer"
            ) from exc
        boundary = (after_index, insertion_level)
        if boundary in seen_boundaries:
            raise SubtitlePersistenceError(
                f"{source_id}: duplicate subtitle boundary {boundary!r}"
            )
        seen_boundaries.add(boundary)
        if after_index == final_scoped_index:
            raise SubtitlePersistenceError(
                f"{source_id}: subtitle plan opens an empty section after the generation scope"
            )

    audit_dir = (
        output_dir / "subtitle-applications" / _slug(source_id)
        / source_sha256[:16]
    )
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "application.json"
    _atomic_artifact_write(audit_dir / "before-source.json", raw)
    before_payload = json.loads(raw)
    if not isinstance(before_payload, list):
        raise SubtitlePersistenceError("script_review sermon must be a JSON array")
    expected_after = apply_insertions(
        before_payload,
        insertions,
        source_sha256=source_sha256,
        user_id=actor_id,
    )
    expected_after_raw = json.dumps(
        expected_after, ensure_ascii=False, indent=4
    ).encode("UTF-8")
    audit: dict[str, Any] = {
        "schema_version": "wang_sermon_subtitle_application_v1",
        "source_id": source_id,
        "source_path": str(source_path),
        "before_source_sha256": source_sha256,
        "actor_id": actor_id,
        "scope_end": scope_end,
        "scope_paragraphs": len(paragraphs),
        "insertion_origin": insertion_origin,
        "cached_section_plan": (
            cached_generated_plan.identity() if cached_generated_plan is not None else None
        ),
        "insertions": insertions,
        "expected_after_source_sha256": hashlib.sha256(expected_after_raw).hexdigest(),
        "expected_after_body_sha256": project_script(expected_after).body_sha256,
        "status": "applying",
    }
    _atomic_json_artifact_write(audit_path, audit)

    print(json.dumps({
        "phase": "subtitle_persistence", "source": source_id,
        "insertions": len(insertions), "status": "started",
    }, ensure_ascii=False), flush=True)
    try:
        if writer is None:
            # Import lazily so read-only extraction does not initialize the web
            # application's file watchers. This is the same governed service
            # used by the editor and it enforces the sermon's ACL.
            from backend.api.sc_api.sermon_manager import sermonManager

            save = sermonManager.persist_generated_subtitles
        else:
            save = writer
        report = save(
            actor_id,
            source_id,
            expected_source_sha256=source_sha256,
            insertions=insertions,
        )
        if Path(str(report.get("source_path") or "")).resolve() != source_path.resolve():
            raise SubtitlePersistenceError("sermon save service wrote a different source path")
        if report.get("insertions") != len(insertions):
            raise SubtitlePersistenceError(
                "sermon save service did not confirm every generated subtitle insertion"
            )
    except Exception as exc:
        audit.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        _atomic_json_artifact_write(audit_path, audit)
        raise
    audit.update({"status": "persisted", "save_report": report})
    _atomic_json_artifact_write(audit_path, audit)
    print(json.dumps({
        "phase": "subtitle_persistence", "source": source_id,
        "insertions": len(insertions), "status": "persisted",
        "after_source_sha256": report.get("after_source_sha256"),
    }, ensure_ascii=False), flush=True)
    return {
        **report,
        "audit_path": str(audit_path),
        "insertion_origin": insertion_origin,
    }


def _assert_subtitle_persistence_authorized(
    actor_id: str,
    *,
    writer: Callable[..., dict[str, Any]] | None,
    authorizer: Callable[[str], bool] | None,
) -> None:
    """Fail before subtitle generation when the eventual save is forbidden."""

    if writer is None:
        if authorizer is not None:
            raise SubtitlePersistenceError(
                "a custom subtitle authorizer requires a custom subtitle writer"
            )
        # Use the same manager instance that will perform the governed save.
        # Import lazily so extraction that does not need a write remains free
        # of the web application's file-watcher initialization.
        from backend.api.sc_api.sermon_manager import sermonManager

        allowed = sermonManager.can_persist_generated_subtitles(actor_id)
    else:
        if authorizer is None:
            raise SubtitlePersistenceError(
                "a custom subtitle writer requires an authorization preflight"
            )
        allowed = authorizer(actor_id)
    if not allowed:
        raise PermissionError("You don't have permission to update this item")


def reconcile_subtitle_application(
    *, source_id: str, source_path: Path, output_dir: Path, current_raw: bytes
) -> dict[str, Any] | None:
    """Finalize an interrupted subtitle audit when the committed bytes prove it.

    The transcript and its staging audit cannot share one filesystem
    transaction. A hard process death after the atomic transcript replace can
    therefore leave an ``applying`` audit. On resume we reconstruct the exact
    proposed result from the saved before-image and accept it only when every
    row matches the current file.
    """

    root = output_dir / "subtitle-applications" / _slug(source_id)
    if not root.is_dir():
        return None
    try:
        current_rows = json.loads(current_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(current_rows, list):
        return None
    candidates = sorted(
        root.glob("*/application.json"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for audit_path in candidates:
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if audit.get("status") not in {"applying", "generated", "failed"}:
            continue
        before_path = audit_path.with_name("before-source.json")
        if not before_path.is_file():
            continue
        before_raw = before_path.read_bytes()
        if hashlib.sha256(before_raw).hexdigest() != audit.get("before_source_sha256"):
            continue
        try:
            before_rows = json.loads(before_raw)
            expected_rows = apply_insertions(
                before_rows,
                audit.get("insertions") or [],
                source_sha256=str(audit.get("before_source_sha256") or ""),
                user_id=str(audit.get("actor_id") or ""),
            )
        except (json.JSONDecodeError, SubtitlePersistenceError):
            continue
        if expected_rows != current_rows:
            continue
        current_sha256 = hashlib.sha256(current_raw).hexdigest()
        expected_sha256 = str(audit.get("expected_after_source_sha256") or "")
        report = {
            "source_path": str(source_path),
            "before_source_sha256": audit["before_source_sha256"],
            "after_source_sha256": current_sha256,
            "before_body_sha256": project_script(before_rows).body_sha256,
            "after_body_sha256": project_script(current_rows).body_sha256,
            "insertions": len(audit.get("insertions") or []),
            "actor_id": audit.get("actor_id"),
            "recovered_after_interrupted_audit": True,
            "expected_serialization_sha256": expected_sha256 or None,
            "expected_serialization_matched": (
                not expected_sha256 or expected_sha256 == current_sha256
            ),
        }
        audit.update({
            "status": "persisted",
            "save_report": report,
            "recovery": "current source exactly matched the predeclared subtitle application",
        })
        _atomic_json_artifact_write(audit_path, audit)
        return report
    return None


def resolve_section_plan(
    *, source: dict[str, Any], source_id: str, source_sha256: str, output_dir: Path,
    level: int = DEFAULT_SECTION_LEVEL, allow_generated: bool = True,
    max_section_sentences: int | None = None,
    client: CodexSubscriptionClient | None = None,
    source_file_sha256: str | None = None,
    preferred_plan: SectionPlan | None = None,
) -> SectionPlan:
    """The plan for this source, generated at most once and then reused.

    Generating boundaries is a model call, so an uncached rerun could resegment
    the source and quietly make two extractions incomparable. The cache is keyed
    on the source hash, so editing the source correctly invalidates it.
    """

    path = output_dir / "section-plans" / f"{_slug(source_id)}.json"
    projection = project_script(source.get("script"))
    texts = [str(row.get("text") or "") for row in projection.body_rows]
    indexes = [row.get("index") for row in projection.body_rows]
    counts = [_source_unit_count(text) for text in texts]
    # The cached artifact is the uncapped editorial/base plan. Sentence caps are
    # transport policy, derived deterministically in memory below. Keying this
    # cache by a newly introduced cap would regenerate every headingless source
    # and silently replace its frozen editorial boundaries.
    cached = preferred_plan
    cached_used_legacy_physical_identity = False
    cached_needs_identity_upgrade = False
    if path.is_file():
        try:
            cached_needs_identity_upgrade = (
                json.loads(path.read_text(encoding="utf-8")).get(
                    "editorial_topology_sha256"
                )
                is None
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            cached_needs_identity_upgrade = False
    if cached is None:
        cached = load_cached_plan(
            path, source_sha256, level=level,
            max_section_sentences=None,
            editorial_structure_sha256=projection.editorial_structure_sha256,
            editorial_topology_sha256=projection.editorial_topology_sha256,
            accept_any_max=True,
        )
    if (
        cached is None
        and source_file_sha256 is not None
        and source_file_sha256 != source_sha256
        and not any(
            is_editorial_row(row)
            for row in live_script(source.get("script"))
        )
    ):
        # Before spoken_body_v1 existed, generated plans were bound to the
        # physical JSON SHA. Reuse is coordinate-safe only when that exact file
        # still contains body rows and nothing else: one physical row then
        # equals one projected source row. Mixed files (subtitle/comment rows)
        # must never enter this compatibility path because the old boundaries
        # would point at a different locator space.
        cached = load_cached_plan(
            path,
            source_file_sha256,
            level=level,
            max_section_sentences=None,
            accept_any_max=True,
        )
        cached_used_legacy_physical_identity = cached is not None
    if cached is not None and has_transport_splits(cached):
        raise SectionBoundaryError(
            "legacy transport-split section cache requires an explicit migration"
        )
    if cached is not None:
        try:
            validate_titled_section_plan(cached, len(projection.body_rows))
        except SectionBoundaryError:
            # Untitled caches are not extraction preflight successes and must
            # be rebuilt. Transport caches take the explicit error above: do
            # not guess at their parent editorial boundaries here.
            cached = None
            cached_used_legacy_physical_identity = False
    if cached is not None:
        if structure_has_section_headings(
            projection.headings, level=level, body_length=len(texts)
        ):
            structural_plan = plan_sections(
                texts,
                headings=projection.headings,
                segment_indexes=indexes,
                level=level,
                sentence_counts=counts,
                max_section_sentences=None,
            )
            cached_partition = [
                (row.start, row.end, row.title) for row in cached.sections
            ]
            structural_partition = [
                (row.start, row.end, row.title) for row in structural_plan.sections
            ]
            if cached_partition != structural_partition:
                # The source's current editorial structure supersedes this
                # cache. Rebuild it deterministically below; transport caches
                # were already rejected before this comparison.
                cached = None
        if cached is not None:
            validate_titled_section_plan(cached, len(projection.body_rows))
            if (
                preferred_plan is not None
                or cached_used_legacy_physical_identity
                or cached_needs_identity_upgrade
            ):
                # A successful legacy read is upgraded once with explicit
                # body/editorial/topology bindings. Current cache hits do not
                # rewrite merely because a comment changed the physical file.
                save_plan(
                    path,
                    cached,
                    source_sha256,
                    source_file_sha256=source_file_sha256,
                    editorial_structure_sha256=projection.editorial_structure_sha256,
                    editorial_topology_sha256=projection.editorial_topology_sha256,
                )
            base_plan = cached
        else:
            base_plan = None
    else:
        base_plan = None
    if base_plan is None:
        provider = _subtitle_provider(source_id, client) if allow_generated else None
        base_plan = plan_sections(
            texts,
            headings=projection.headings,
            segment_indexes=indexes,
            level=level,
            provider=provider,
            sentence_counts=counts,
            max_section_sentences=None,
        )
        validate_titled_section_plan(base_plan, len(projection.body_rows))
        save_plan(
            path,
            base_plan,
            source_sha256,
            source_file_sha256=source_file_sha256,
            editorial_structure_sha256=projection.editorial_structure_sha256,
            editorial_topology_sha256=projection.editorial_topology_sha256,
        )
    if max_section_sentences is None:
        return base_plan
    return apply_section_limit(
        base_plan,
        counts,
        headings=projection.headings,
        max_section_sentences=max_section_sentences,
    )


def reusable_generated_plan(
    *, source: dict[str, Any], source_id: str, source_sha256: str,
    output_dir: Path, level: int, max_section_sentences: int | None,
) -> SectionPlan | None:
    """Return the frozen generated plan only when it can title this exact source."""

    path = output_dir / "section-plans" / f"{_slug(source_id)}.json"
    projection = project_script(source.get("script"))
    plan = load_cached_plan(
        path, source_sha256, level=level,
        max_section_sentences=None,
        editorial_structure_sha256=projection.editorial_structure_sha256,
        editorial_topology_sha256=projection.editorial_topology_sha256,
        accept_any_max=True,
    )
    if plan is None or plan.origin != FROM_GENERATOR:
        return None
    if has_transport_splits(plan):
        raise SubtitlePersistenceError(
            "legacy transport-split cache cannot be written back as editorial "
            "subtitles; an explicit cache migration is required"
        )
    try:
        generated_plan_insertions(plan, list(projection.body_rows))
    except SectionBoundaryError:
        return None
    return plan


def published_source_id(source_id: str, source_descriptor: dict[str, Any] | None) -> str:
    """The id the package publishes for this source, and the only one to cite.

    A manifest source names itself, so its descriptor id is the id everywhere.
    A transcript names nothing, so the package coins `SRC-<slug>` for it -- and
    an exclusion written against the bare transcript id then addressed a source
    the package does not contain, which the ledger reads as "nobody answered
    this sentence" for every sentence the audit did answer. Both sides call
    this.
    """

    return str((source_descriptor or {}).get("source_id") or f"SRC-{_slug(source_id)}")


def _extract_sections(
    *,
    source_id: str,
    exclusion_source_id: str,
    source: dict[str, Any],
    headings: Sequence[EditorialHeading],
    header: str,
    plan: SectionPlan,
    output_dir: Path,
    client: Stage1OpenAIClient | Stage1AnthropicClient | CodexSubscriptionClient,
    prompt: str,
    fingerprint: str,
    cache_contract_fingerprint: str | None = None,
    force: bool,
    only: tuple[int, ...] | None = None,
    record: RunRecord | None = None,
    response_schema: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run every section, then concatenate.

    Target sentence ranges do not overlap, so there is no response merge rule
    and nothing to deduplicate -- the combining is `combine_sections` and that
    is all of it. Two sentence-range chunks may name the same storage row, but
    `section_sentences` gives them disjoint targets.
    """

    answered: list[tuple[Section, dict[str, Any]]] = []
    usage_rows: list[dict[str, Any]] = []
    section_rows: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for section in plan.sections:
        if record is not None and record.cancel_requested():
            raise RunCancelled(f"cancel requested before section {section.index}")
        if only is not None and section.index not in only:
            continue
        sentences = section_sentences(source, section)
        visual_blocks = _section_visual_blocks(source, sentences)
        model_input_sha256 = _section_model_input_sha256(
            source, header, section, sentences
        )
        section_fingerprint = _section_generation_fingerprint(
            cache_contract_fingerprint or fingerprint,
            model_input_sha256,
        )
        cache_path = _section_cache_path(
            output_dir, source_id, section_fingerprint, section
        )
        if cache_path.is_file() and not force:
            cached = _load_valid_section_cache(
                cache_path,
                section=section,
                fingerprint=section_fingerprint,
                source=source,
                sentences=sentences,
                model_input_sha256=model_input_sha256,
            )
            if cached is not None:
                visual_image_paths = (
                    _visual_image_paths(
                        output_dir=output_dir,
                        source_id=source_id,
                        blocks=visual_blocks,
                    )
                    if visual_blocks
                    else []
                )
                print(json.dumps({
                    "phase": "extraction", "source": source_id,
                    "section": section.index, "sections": len(plan.sections),
                    "status": "cached",
                }, ensure_ascii=False), flush=True)
                answered.append((section, cached))
                exclusions.extend(exclusions_from_audit(
                    cached, sentences, source_id=exclusion_source_id,
                    ledger_sentence_id=ledger_sentence_id))
                row = {**section_payload(section), "attempts": 0, "cached": True}
                if visual_blocks:
                    row["visual_images"] = _visual_image_provenance(
                        output_dir=output_dir,
                        blocks=visual_blocks,
                        paths=visual_image_paths,
                    )
                section_rows.append(row)
                continue
        print(json.dumps({
            "phase": "extraction", "source": source_id,
            "section": section.index, "sections": len(plan.sections),
            "title": section.title, "sentences": len(sentences), "status": "started",
        }, ensure_ascii=False), flush=True)
        user_input = header + _section_prompt_body(source, section, sentences)
        visual_image_paths = (
            _visual_image_paths(
                output_dir=output_dir,
                source_id=source_id,
                blocks=visual_blocks,
            )
            if visual_blocks
            else []
        )
        last_error: DetailedExtractionValidationError | None = None
        last_candidate: dict[str, Any] | None = None
        response, attempts = None, 0
        for attempt in range(1, VALIDATION_ATTEMPTS + 1):
            if record is not None and record.cancel_requested():
                raise RunCancelled(
                    f"cancel requested before section {section.index} attempt {attempt}"
                )
            attempts = attempt
            print(json.dumps({
                "phase": "extraction", "source": source_id,
                "section": section.index, "sections": len(plan.sections),
                "attempt": attempt, "status": "model_call",
            }, ensure_ascii=False), flush=True)
            feedback = ""
            if last_error and last_candidate:
                feedback = (
                    "\n\n===== 上一版 JSON（必须以此为基础修复）=====\n"
                    + json.dumps(last_candidate, ensure_ascii=False)
                    + "\n\n===== 机械验证反馈 =====\n"
                    + _validation_feedback(last_error, source)
                )
            if record is not None:
                record.model_call_started()
            if visual_image_paths:
                if not isinstance(client, CodexSubscriptionClient):
                    raise DetailedExtractionValidationError(
                        f"{source_id}: visual source requires a multimodal extraction client"
                    )
                candidate = client.generate_json(
                    prompt,
                    feedback,
                    response_schema or DETAILED_RESPONSE_SCHEMA,
                    cache_prefix=user_input,
                    image_paths=visual_image_paths,
                )
            else:
                candidate = client.generate_json(
                    prompt,
                    feedback,
                    response_schema or DETAILED_RESPONSE_SCHEMA,
                    cache_prefix=user_input,
                )
            call_usage = {**usage_row(client.last_usage, attempt), "section_index": section.index}
            usage_rows.append(call_usage)
            # Reported per call rather than handed over at the end: a run that
            # dies in section three spent three sections' worth of money, and a
            # ledger that only learns the total on success prices that failure
            # at nothing.
            if record is not None:
                record.usage([call_usage])
                record.model_call_completed()
            try:
                # A section is a composition unit, so the full contract is
                # answerable inside it: measured, 0 of 264 relations cross a
                # `##`, and the step a load_bearing observation reasons to is
                # in the same section as the observation.
                validate_response(
                    candidate,
                    source,
                    visible_locators={
                        row.segment_index.split("/", 1)[0]
                        for row in sentences
                    },
                    visible_visual_locators={
                        row.segment_index
                        for row in sentences
                        if row.source_modality == "visual"
                    },
                )
                validate_sentence_audit(candidate, source, sentences)
                response = candidate
                break
            except DetailedExtractionValidationError as exc:
                last_error, last_candidate = exc, candidate
                _archive_rejected_candidate(
                    output_dir=output_dir, transcript_id=f"{source_id}#p{section.index:03d}",
                    attempt=attempt, candidate=candidate, error=exc,
                )
        if response is None:
            raise last_error or DetailedExtractionValidationError(
                f"section {section.index} validation failed"
            )
        _archive(cache_path)
        _atomic_json_artifact_write(
            cache_path,
            _section_cache_artifact(
                section,
                response,
                section_fingerprint,
                model_input_sha256=model_input_sha256,
            ),
        )
        answered.append((section, response))
        exclusions.extend(exclusions_from_audit(
            response, sentences, source_id=exclusion_source_id,
            ledger_sentence_id=ledger_sentence_id))
        row = {
            **section_payload(section),
            "attempts": attempts,
            "cached": False,
        }
        if visual_blocks:
            row["visual_images"] = _visual_image_provenance(
                output_dir=output_dir,
                blocks=visual_blocks,
                paths=visual_image_paths,
            )
        section_rows.append(row)
    return combine_sections(answered), usage_rows, section_rows, exclusions


def _anchored_fragment(
    *,
    fragment_id: str,
    source_id: str,
    anchor: dict[str, Any],
    source_rows: Sequence[dict[str, Any]],
    source_sha256: str,
    extraction_section_index: int | None = None,
) -> dict[str, Any]:
    paragraph = _anchor_source_row(source_rows, anchor)
    paragraph_text = str(paragraph.get("text") or "")
    locator = str(anchor.get("segment_index") or "")
    modality = str(anchor.get("source_modality") or "spoken")
    visual = None
    if modality == "visual":
        visual = next(
            (
                row
                for row in visual_source_blocks(
                    paragraph_text,
                    segment_index=locator.split("/", 1)[0],
                    source_segment_index=paragraph.get("index"),
                )
                if row.locator == locator
            ),
            None,
        )
        if visual is None or not visual.readable:
            raise DetailedExtractionValidationError(
                f"visual source locator {locator!r} cannot be bound"
            )
        excerpt = visual.raw_svg
    else:
        excerpt = anchor["verbatim_excerpt"]
    fragment = {
        "fragment_id": fragment_id,
        "source_id": source_id,
        "verbatim_excerpt": excerpt,
        "paragraph_key": anchor["segment_index"],
        "source_segment_index": paragraph.get("index"),
        "media_time": paragraph.get("start_time"),
        "media_end_time": paragraph.get("end_time"),
        "source_sha256": source_sha256,
        "paragraph_text_sha256": hashlib.sha256(paragraph_text.encode("utf-8")).hexdigest(),
        "verbatim_excerpt_sha256": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "anchor_state": "source_version_bound",
        "review_status": "candidate",
    }
    if visual is not None:
        requested = [str(value) for value in anchor.get("visual_fact_ids") or []]
        facts = {
            str(row["fact_id"]): row
            for row in visual.facts
        }
        fragment.update(
            {
                "source_modality": "visual",
                "visual_locator": visual.locator,
                "visual_block_sha256": visual.raw_sha256,
                "visual_canonical_sha256": visual.canonical_sha256,
                "visual_renderer_version": VISUAL_RENDERER_VERSION,
                "visual_facts": [facts[fact_id] for fact_id in requested],
            }
        )
    if extraction_section_index is not None:
        fragment["extraction_section_index"] = extraction_section_index
    return fragment


_SOURCE_LOCATOR = re.compile(r"^S([0-9]{4,})(?:/V[0-9]{2,})?$")


def _anchor_source_row(
    source_rows: Sequence[dict[str, Any]], anchor: Mapping[str, Any]
) -> dict[str, Any]:
    """Resolve a spoken or row-qualified visual locator to its body row."""

    locator = str(anchor.get("segment_index") or "")
    match = _SOURCE_LOCATOR.fullmatch(locator)
    if match is None:
        raise DetailedExtractionValidationError(
            f"invalid spoken-source locator {locator!r}"
        )
    ordinal = int(match.group(1)) - 1
    if ordinal < 0 or ordinal >= len(source_rows):
        raise DetailedExtractionValidationError(
            f"spoken-source locator {locator!r} is outside 1..{len(source_rows)}"
        )
    return source_rows[ordinal]


def compile_package(
    *, transcript_id: str, transcript_path: Path, transcript: dict[str, Any], raw: bytes,
    response: dict[str, Any], extraction: dict[str, Any],
    source_body_sha256: str | None = None,
    source_file_sha256: str | None = None,
    editorial_structure_sha256: str | None = None,
    editorial_topology_sha256: str | None = None,
    source_descriptor: dict[str, Any] | None = None,
    usage_rows: list[dict[str, Any]] | None = None,
    section_rows: list[dict[str, Any]] | None = None,
    exclusions: list[dict[str, Any]] | None = None,
    complete: bool = True,
    visual_source_attestations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    projection = project_script(transcript.get("script"))
    try:
        validate_visual_source_attestations(
            projection, visual_source_attestations
        )
    except VisualSourceAttestationError as exc:
        raise DetailedExtractionValidationError(str(exc)) from exc
    source_rows = projection.body_rows
    declared_body_bindings = [
        value
        for value in (
            extraction.get("source_sha256"),
            extraction.get("source_body_sha256"),
            source_body_sha256,
        )
        if value is not None
    ]
    if (
        extraction.get("source_sha256") is None
        or any(str(value) != projection.body_sha256 for value in declared_body_bindings)
    ):
        raise DetailedExtractionValidationError(
            "extraction anchor binding does not match the current spoken-body projection"
        )
    expected_body_sha256 = projection.body_sha256
    expected_text_sha256 = extraction.get("source_text_sha256")
    if (
        not isinstance(expected_text_sha256, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_text_sha256)
        or expected_text_sha256 != projection.spoken_text_sha256
    ):
        raise DetailedExtractionValidationError(
            "extraction spoken-text identity is missing or does not match the current source"
        )
    expected_visual_sha256 = extraction.get("source_visual_sha256")
    if projection.visual_blocks:
        if expected_visual_sha256 != projection.visual_content_sha256:
            raise DetailedExtractionValidationError(
                "extraction visual-source identity is missing or does not match the current source"
            )
    elif expected_visual_sha256 is not None:
        raise DetailedExtractionValidationError(
            "extraction declares visual-source identity for a source with no visuals"
        )
    current_file_sha256 = hashlib.sha256(raw).hexdigest()
    optional_bindings = (
        (
            "source file",
            current_file_sha256,
            (extraction.get("source_file_sha256"), source_file_sha256),
        ),
        (
            "editorial structure",
            projection.editorial_structure_sha256,
            (
                extraction.get("editorial_structure_sha256"),
                editorial_structure_sha256,
            ),
        ),
        (
            "editorial topology",
            projection.editorial_topology_sha256,
            (
                extraction.get("editorial_topology_sha256"),
                editorial_topology_sha256,
            ),
        ),
    )
    for label, current, declared in optional_bindings:
        if any(str(value) != current for value in declared if value is not None):
            raise DetailedExtractionValidationError(
                f"extraction {label} binding does not match the current source"
            )

    # Model-facing IDs are intentionally short so the JSON remains tractable.
    # Namespace them here before a package can ever be merged with another
    # sermon.  A 200-sermon corpus cannot safely contain 200 different CL001s.
    source_key = str((source_descriptor or {}).get("source_id") or transcript_id)
    source_type = str(
        (source_descriptor or {}).get("source_type") or "sermon_transcript"
    )
    model_output_sha256 = hashlib.sha256(
        json.dumps(
            response, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    namespace = generation_namespace(
        f"{source_type}:{source_key}",
        str(
            extraction.get("generation_fingerprint_sha256")
            or extraction.get("fingerprint_sha256")
            or ""
        ),
        model_output_sha256,
    )
    response = json.loads(json.dumps(response, ensure_ascii=False))
    id_maps = {
        "question": {row["question_id"]: f"{namespace}-{row['question_id']}" for row in response["questions"]},
        "position": {row["position_id"]: f"{namespace}-{row['position_id']}" for row in response["positions"]},
        "observation": {row["observation_id"]: f"{namespace}-{row['observation_id']}" for row in response["observations"]},
        "evidence": {row["evidence_step_id"]: f"{namespace}-{row['evidence_step_id']}" for row in response["evidence_steps"]},
        "claim": {row["claim_id"]: f"{namespace}-{row['claim_id']}" for row in response["claims"]},
        "evidence_relation": {row["relation_id"]: f"{namespace}-{row['relation_id']}" for row in response["evidence_relations"]},
        "claim_relation": {row["claim_relation_id"]: f"{namespace}-{row['claim_relation_id']}" for row in response["claim_relations"]},
    }
    for row in response["questions"]:
        row["question_id"] = id_maps["question"][row["question_id"]]
        row["answer_claim_ids"] = [id_maps["claim"][value] for value in row["answer_claim_ids"]]
    for row in response["positions"]:
        row["position_id"] = id_maps["position"][row["position_id"]]
    for row in response["observations"]:
        row["observation_id"] = id_maps["observation"][row["observation_id"]]
    for row in response["evidence_steps"]:
        row["evidence_step_id"] = id_maps["evidence"][row["evidence_step_id"]]
        row["produced_claim_ids"] = [id_maps["claim"][value] for value in row["produced_claim_ids"]]
    for row in response["claims"]:
        row["claim_id"] = id_maps["claim"][row["claim_id"]]
        row["evidence_step_ids"] = [id_maps["evidence"][value] for value in row["evidence_step_ids"]]
        row["opposed_position_ids"] = [id_maps["position"][value] for value in row["opposed_position_ids"]]
    # A relation's source is an evidence step or an observation -- the latter
    # is how "the professor reasoned from this observation" is recorded.  The
    # two id spaces do not collide (E001 vs OBS001), so one lookup covers both.
    relation_sources = {**id_maps["evidence"], **id_maps["observation"]}
    for row in response["evidence_relations"]:
        row["relation_id"] = id_maps["evidence_relation"][row["relation_id"]]
        row["from_id"] = relation_sources[row["from_id"]]
        row["to_id"] = id_maps["evidence"][row["to_id"]]
    for row in response["claim_relations"]:
        row["claim_relation_id"] = id_maps["claim_relation"][row["claim_relation_id"]]
        row["from_id"] = id_maps["claim"][row["from_id"]]
        row["to_id"] = id_maps["claim"][row["to_id"]]

    source_id = published_source_id(transcript_id, source_descriptor)
    source_sha256 = expected_body_sha256
    physical_sha256 = current_file_sha256
    structure_sha256 = projection.editorial_structure_sha256
    topology_sha256 = projection.editorial_topology_sha256
    spoken_text_sha256 = expected_text_sha256
    fragments: list[dict[str, Any]] = []
    fragment_by_anchor: dict[tuple[str, str, str, tuple[str, ...], int | None], str] = {}
    split_lineage = (
        ((extraction.get("section_plan") or {}).get("section_policy") or {}).get(
            "split_lineage"
        )
        or []
    )

    def fragment_for(owner_id: str, anchor: dict[str, Any], position: int) -> str:
        section_match = re.search(r"(?:^|-)P(\d+)-", owner_id)
        extraction_section_index = (
            int(section_match.group(1)) if section_match and split_lineage else None
        )
        key = (
            anchor["segment_index"],
            anchor["verbatim_excerpt"],
            str(anchor.get("source_modality") or "spoken"),
            tuple(str(value) for value in anchor.get("visual_fact_ids") or []),
            extraction_section_index,
        )
        existing = fragment_by_anchor.get(key)
        if existing:
            return existing
        fragment_id = f"FR-{_slug(source_key)}-{owner_id}-{position + 1:02d}"
        fragment_by_anchor[key] = fragment_id
        fragments.append(_anchored_fragment(
            fragment_id=fragment_id, source_id=source_id, anchor=anchor,
            source_rows=source_rows, source_sha256=source_sha256,
            extraction_section_index=extraction_section_index,
        ))
        return fragment_id

    questions = []
    for row in response["questions"]:
        item = dict(row)
        item["source_fragment_ids"] = [fragment_for(row["question_id"], anchor, i) for i, anchor in enumerate(item.pop("anchors"))]
        item["review_status"] = "candidate"
        questions.append(item)
    positions = []
    for row in response["positions"]:
        item = dict(row)
        item["source_fragment_ids"] = [fragment_for(row["position_id"], anchor, i) for i, anchor in enumerate(item.pop("anchors"))]
        item["review_status"] = "candidate"
        positions.append(item)
    observations = []
    for row in response["observations"]:
        item = dict(row)
        item["source_fragment_ids"] = [fragment_for(row["observation_id"], anchor, i) for i, anchor in enumerate(item.pop("anchors"))]
        item["review_status"] = "candidate"
        observations.append(item)
    evidence_steps = []
    evidence_anchor_snapshots: dict[str, list[dict[str, Any]]] = {}
    for row in response["evidence_steps"]:
        item = dict(row)
        anchors = item.pop("anchors")
        evidence_anchor_snapshots[row["evidence_step_id"]] = anchors
        item["source_fragment_ids"] = [fragment_for(row["evidence_step_id"], anchor, i) for i, anchor in enumerate(anchors)]
        item["review_status"] = "candidate"
        evidence_steps.append(item)
    claims = []
    for row in response["claims"]:
        item = dict(row)
        item["title"] = item.pop("statement")
        item["claim_type"] = item.pop("claim_kind")
        item["extraction_fingerprints"] = [
            extraction.get("generation_fingerprint_sha256")
            or extraction["fingerprint_sha256"]
        ]
        anchors = []
        for evidence_id in item["evidence_step_ids"]:
            evidence = next(step for step in evidence_steps if step["evidence_step_id"] == evidence_id)
            for anchor in evidence_anchor_snapshots[evidence_id]:
                paragraph = _anchor_source_row(source_rows, anchor)
                modality = str(anchor.get("source_modality") or "spoken")
                highlight = str(anchor.get("verbatim_excerpt") or "")
                visual_fields: dict[str, Any] = {}
                if modality == "visual":
                    visual = next(
                        row
                        for row in visual_source_blocks(
                            str(paragraph.get("text") or ""),
                            segment_index=str(anchor["segment_index"]).split("/", 1)[0],
                            source_segment_index=paragraph.get("index"),
                        )
                        if row.locator == anchor["segment_index"]
                    )
                    highlight = visual.raw_svg
                    visual_fields = {
                        "source_modality": "visual",
                        "visual_locator": visual.locator,
                        "visual_block_sha256": visual.raw_sha256,
                        "visual_fact_ids": list(anchor.get("visual_fact_ids") or []),
                    }
                anchors.append({
                    "paragraph_key": anchor["segment_index"],
                    "media_time": paragraph.get("start_time"),
                    "evidence_id": evidence_id,
                    "evidence_type": evidence["step_type"],
                    "speaker": evidence["speaker"],
                    "stance": evidence["stance"],
                    "discourse_role": evidence["discourse_role"],
                    "assertive": evidence["speaker"] == "professor" and evidence["stance"] == "asserted",
                    "proposed_highlight": {"text": highlight, "status": "proposed"},
                    **visual_fields,
                })
        item["occurrences"] = [{
            "source_id": source_key,
            "transcript_id": source_key,
            "lecture": source_key,
            "anchors": anchors,
        }]
        item["maturity"] = "candidate"
        claims.append(item)

    source_document = {
        "source_id": source_id,
        "source_type": "sermon_transcript",
        "transcript_id": transcript_id,
        "title": transcript.get("metadata", {}).get("title", transcript_id),
        "source_sha256": source_sha256,
        "source_body_sha256": source_sha256,
        "source_text_sha256": spoken_text_sha256,
        "anchor_binding_sha256": source_sha256,
        "source_file_sha256": physical_sha256,
        "editorial_structure_sha256": structure_sha256,
        "editorial_topology_sha256": topology_sha256,
        "locator_space": LOCATOR_SPACE,
        "extraction_record_namespace": namespace,
        "source_path": str(transcript_path),
        "review_status": "candidate",
    }
    if projection.visual_blocks:
        source_document["source_visual_sha256"] = projection.visual_content_sha256
        source_document["visual_sources"] = [
            block.descriptor() for block in projection.visual_blocks
        ]
        source_document["visual_source_attestations"] = [
            {
                "locator": locator,
                "raw_sha256": raw_sha256,
                "attestation": "professor_displayed_or_drawn_visual_source",
            }
            for locator, raw_sha256 in sorted(
                (visual_source_attestations or {}).items()
            )
        ]
    if source_descriptor:
        source_document.update(json.loads(json.dumps(source_descriptor, ensure_ascii=False)))
        source_document.update({
            "source_id": source_id,
            "source_sha256": source_sha256,
            "source_body_sha256": source_sha256,
            "source_text_sha256": spoken_text_sha256,
            "anchor_binding_sha256": source_sha256,
            "source_file_sha256": physical_sha256,
            "editorial_structure_sha256": structure_sha256,
            "editorial_topology_sha256": topology_sha256,
            "locator_space": LOCATOR_SPACE,
            "extraction_record_namespace": namespace,
            "source_path": str(transcript_path),
            "review_status": "candidate",
        })
        if projection.visual_blocks:
            source_document["source_visual_sha256"] = projection.visual_content_sha256
            source_document["visual_sources"] = [
                block.descriptor() for block in projection.visual_blocks
            ]
            source_document["visual_source_attestations"] = [
                {
                    "locator": locator,
                    "raw_sha256": raw_sha256,
                    "attestation": "professor_displayed_or_drawn_visual_source",
                }
                for locator, raw_sha256 in sorted(
                    (visual_source_attestations or {}).items()
                )
            ]
    package = {
        "schema_version": "wang_shared_knowledge_v1.2",
        "package_id": f"DETAILED-{_slug(source_key)}",
        "source_documents": [source_document],
        "source_fragments": fragments,
        "questions": questions,
        "position_nodes": positions,
        "observations": observations,
        "evidence_steps": evidence_steps,
        "claims": claims,
        "knowledge_relations": response["evidence_relations"],
        "claim_relations": response["claim_relations"],
        "extraction": {
            **extraction,
            "locator_space": LOCATOR_SPACE,
            "record_namespace": namespace,
            # Artifact metadata does not participate in the pre-generation
            # fingerprint. This lets the unchanged API identity keep matching
            # older caches while every newly written artifact names its
            # transport and binds the exact raw structured response.
            "backend": extraction.get("backend", "api"),
            "model_output_sha256": model_output_sha256,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
        "summary": {
            "source_fragment_count": len(fragments),
            "question_count": len(questions),
            "position_count": len(positions),
            "observation_count": len(observations),
            "evidence_step_count": len(evidence_steps),
            "claim_count": len(claims),
            "evidence_relation_count": len(response["evidence_relations"]),
            "claim_relation_count": len(response["claim_relations"]),
        },
        # Per-attempt token usage, including the rejected attempts: a package
        # that needed three tries cost three calls, and the summary counts alone
        # never showed that.
        "usage": list(usage_rows or []),
        # Which section of the source each call answered for.  Coverage is a
        # property of the section plan, so a package that cannot say how it was
        # cut cannot be compared with the ledger run taken against it.
        "sections": list(section_rows or []),
        # Every `not_extracted` verdict, as a candidate exclusion. None of them
        # is approved: the model that made the call is not a person, so the
        # ledger keeps them out of the terminal column until one looks. They
        # exist so "answered, awaiting review" stops being indistinguishable
        # from "nobody answered".
        "sentence_exclusions": list(exclusions or []),
        # False when only some sections were run. A partial package is a probe,
        # not a result: its coverage is measured against the whole source and
        # will read low for the sections nobody asked about.
        "complete": complete,
    }
    return package


@dataclass(frozen=True)
class SectionSettings:
    """How the source is cut into the units it was composed in."""

    level: int = DEFAULT_SECTION_LEVEL
    #: Optional guard for exceptional sources.  It remains opt-in so completed
    #: sources keep their legacy fingerprints; a configured source starts at
    #: `##` and only an oversized unit consults its `###` boundaries.
    max_sentences: int | None = None
    #: Subscription safety cap used only after an unchanged uncapped package
    #: has had a chance to prove itself current. This prevents a transport
    #: policy from invalidating successful corpus history while still bounding
    #: every new, stale or damaged extraction.
    fallback_max_sentences: int | None = None
    #: Whether a source with no headings may have boundaries generated for it.
    #: Off makes the run offline and deterministic; on covers the 90 published
    #: transcripts that carry no headings at all.
    allow_generated: bool = True
    #: Section numbers to run, or None for all of them. Checking a prompt or
    #: schema change costs one call this way instead of one per section, which
    #: is the difference between trying an idea and deciding not to.
    only: tuple[int, ...] | None = None

    def __post_init__(self) -> None:
        if self.max_sentences is not None and self.max_sentences <= 0:
            raise ValueError("max_sentences must be positive")
        if (
            self.fallback_max_sentences is not None
            and self.fallback_max_sentences <= 0
        ):
            raise ValueError("fallback_max_sentences must be positive")
        if (
            self.max_sentences is not None
            and self.fallback_max_sentences is not None
        ):
            raise ValueError(
                "max_sentences and fallback_max_sentences are mutually exclusive"
            )


def _run(
    *,
    source_id: str,
    source: dict[str, Any],
    raw: bytes,
    source_path: Path,
    header: str,
    output_dir: Path,
    client: Stage1OpenAIClient | Stage1AnthropicClient,
    prompt: str,
    reasoning_effort: str,
    sections: SectionSettings,
    force: bool,
    source_descriptor: dict[str, Any] | None = None,
    preferred_plan: SectionPlan | None = None,
    visual_source_attestations: Mapping[str, str] | None = None,
    record_run_ledger: bool = True,
) -> tuple[str, Path]:
    """Extract one source, whatever kind of source it is.

    Transcripts and notes manuscripts differ only in how they are loaded and
    how the prompt introduces them; they were two near-identical bodies before
    sectioning, and keeping them so would have meant maintaining the section
    loop twice.
    """

    source_file_sha256 = hashlib.sha256(raw).hexdigest()
    projection = project_script(source.get("script"))
    _assert_inline_source_readable(
        source_id, projection, visual_source_attestations
    )
    has_visual_source = bool(projection.visual_blocks)
    if has_visual_source and not isinstance(client, CodexSubscriptionClient):
        raise DetailedExtractionValidationError(
            f"{source_id}: visual source requires the codex-subscription multimodal backend"
        )
    response_schema = detailed_response_schema(
        has_visual_source=has_visual_source
    )
    effective_header = header + (VISUAL_SOURCE_HEADER if has_visual_source else "")
    source_sha256 = projection.body_sha256
    source_type = str(
        (source_descriptor or {}).get("source_type") or "sermon_transcript"
    )
    source_body = {
        **source,
        "metadata": {
            **dict(source.get("metadata") or {}),
            # The runner invocation owns provenance. A sermon JSON cannot
            # self-declare as reviewed notes to unlock blockquote anchors.
            "source_type": source_type,
        },
        "script": [dict(row) for row in projection.body_rows],
    }
    output_path = output_dir / f"{_slug(source_id)}.detailed-knowledge.json"

    def identity_for(plan: SectionPlan) -> dict[str, Any]:
        identity = extraction_identity(
            source_sha256=source_sha256, prompt=prompt,
            model_id=client.model, reasoning_effort=reasoning_effort,
            max_output_tokens=client.max_output_tokens,
            section_plan=plan.generation_identity(),
            source_text_sha256=projection.spoken_text_sha256,
            editorial_structure_sha256=projection.editorial_structure_sha256,
            model_context_sha256=hashlib.sha256(effective_header.encode("utf-8")).hexdigest(),
            model_input_contract_version=(
                VISUAL_MODEL_INPUT_CONTRACT_VERSION
                if has_visual_source
                else MODEL_INPUT_CONTRACT_VERSION
            ),
            section_model_input_sha256s=_section_model_input_sha256s(
                source_body, effective_header, plan
            ),
            source_file_sha256=source_file_sha256,
            package_compiler_version=PACKAGE_COMPILER_VERSION,
            section_scope=list(sections.only) if sections.only is not None else None,
            backend=(
                client.backend if isinstance(client, CodexSubscriptionClient) else None
            ),
            response_schema=response_schema,
        )
        identity["source_body_sha256"] = source_sha256
        if projection.visual_content_sha256 is not None:
            identity["source_visual_sha256"] = projection.visual_content_sha256
        identity["source_file_sha256"] = source_file_sha256
        identity["editorial_topology_sha256"] = (
            projection.editorial_topology_sha256
        )
        return identity

    def existing_result(identity: dict[str, Any]) -> tuple[str, Path] | None:
        if not output_path.is_file() or force:
            return None
        try:
            existing = json.loads(output_path.read_text(encoding="utf-8"))
            validate_merged_package(existing)
        except (OSError, json.JSONDecodeError, KnowledgePackageMergeError):
            # The current file is not a cache hit merely because its name is
            # right. `_run_extraction` archives it before installing a rebuilt
            # artifact, and validated section caches avoid a needless model
            # call when their own generation fingerprint still matches.
            existing = None
        if (
            existing is not None
            and (existing.get("extraction") or {}).get("fingerprint_sha256")
            == identity["fingerprint_sha256"]
            and existing.get("complete") is (sections.only is None)
            and (existing.get("extraction") or {}).get("artifact_sha256")
            == _package_artifact_sha256(existing)
        ):
            if isinstance(existing.get("coverage"), dict):
                return "skipped", output_path
            # Older code exposed the package at its current path before
            # calculating coverage. Finish that deterministic commit instead
            # of paying for identical model output again.
            with _extraction_run_record(
                source_id, enabled=record_run_ledger
            ) as record:
                record.inputs({"fingerprint_sha256": identity["fingerprint_sha256"]})
                existing["coverage"] = _coverage(source_path, output_path)
                _archive(output_path)
                existing.setdefault("extraction", {})["artifact_sha256"] = (
                    _package_artifact_sha256(existing)
                )
                _atomic_json_artifact_write(output_path, existing)
                record.quality({
                    **_coverage_quality(existing["coverage"]),
                    "recovered_interrupted_artifact_commit": True,
                })
                record.outputs(output_path)
            return "created", output_path
        return None

    if sections.fallback_max_sentences is not None:
        base_plan = resolve_section_plan(
            source=source, source_id=source_id, source_sha256=source_sha256,
            output_dir=output_dir, level=sections.level,
            max_section_sentences=None,
            allow_generated=sections.allow_generated,
            client=client if isinstance(client, CodexSubscriptionClient) else None,
            source_file_sha256=source_file_sha256,
            preferred_plan=preferred_plan,
        )
        base_identity = identity_for(base_plan)
        unchanged = existing_result(base_identity)
        if unchanged is not None:
            return unchanged
        plan = apply_section_limit(
            base_plan,
            [
                _source_unit_count(str(row.get("text") or ""))
                for row in projection.body_rows
            ],
            headings=projection.headings,
            max_section_sentences=sections.fallback_max_sentences,
        )
        identity = (
            base_identity if plan.identity() == base_plan.identity() else identity_for(plan)
        )
    else:
        plan = resolve_section_plan(
            source=source, source_id=source_id, source_sha256=source_sha256,
            output_dir=output_dir, level=sections.level,
            max_section_sentences=sections.max_sentences,
            allow_generated=sections.allow_generated,
            client=client if isinstance(client, CodexSubscriptionClient) else None,
            source_file_sha256=source_file_sha256,
            preferred_plan=preferred_plan,
        )
        identity = identity_for(plan)

    current = existing_result(identity)
    if current is not None:
        return current
    # Opened after the skip check so a no-op re-run does not file a row. At 240
    # sources a nightly "nothing changed" pass would otherwise bury the runs
    # that did something.
    with _extraction_run_record(
        source_id, enabled=record_run_ledger
    ) as record:
        record.model(client.model)
        if isinstance(client, CodexSubscriptionClient):
            record.metadata({"backend": client.backend})
        # `fingerprint_sha256` is the staleness key, not one input among
        # several: it already composes the source, the prompt, the model, the
        # schema and the section plan, and it is what the skip check above
        # compares. The other two are recorded so a reader can see *which* input
        # moved when the fingerprint stops matching.
        record.inputs({
            "fingerprint_sha256": identity.get("fingerprint_sha256"),
            "source_sha256": source_sha256,
            "prompt_sha256": identity.get("prompt_sha256"),
        })
        return _run_extraction(
            record=record, source_id=source_id, source=source_body,
            authoritative_source=source,
            headings=projection.headings, raw=raw,
            source_path=source_path, header=effective_header, plan=plan, identity=identity,
            output_path=output_path, output_dir=output_dir, client=client,
            prompt=prompt, sections=sections, force=force,
            source_descriptor=source_descriptor,
            visual_source_attestations=visual_source_attestations,
        )


def _run_extraction(
    *, record: RunRecord, source_id: str, source: dict[str, Any], raw: bytes,
    authoritative_source: dict[str, Any] | None = None,
    headings: Sequence[EditorialHeading],
    source_path: Path, header: str, plan: SectionPlan, identity: dict[str, Any],
    output_path: Path, output_dir: Path,
    client: Stage1OpenAIClient | Stage1AnthropicClient | CodexSubscriptionClient,
    prompt: str,
    sections: "SectionSettings", force: bool, source_descriptor: dict[str, Any] | None,
    visual_source_attestations: Mapping[str, str] | None = None,
) -> tuple[str, Path]:
    """The part of an extraction that is worth recording, once a row exists."""

    response_schema = detailed_response_schema(
        has_visual_source=bool(project_script(source.get("script")).visual_blocks)
    )
    response, usage_rows, section_rows, exclusions = _extract_sections(
        source_id=source_id,
        exclusion_source_id=published_source_id(source_id, source_descriptor),
        source=source, headings=headings, header=header, plan=plan,
        output_dir=output_dir, client=client, prompt=prompt,
        fingerprint=identity["generation_fingerprint_sha256"], force=force,
        cache_contract_fingerprint=identity["model_contract_fingerprint_sha256"],
        only=sections.only, record=record,
        response_schema=response_schema,
    )
    package = compile_package(
        transcript_id=source_id, transcript_path=source_path,
        transcript=authoritative_source or source,
        raw=raw, response=response, extraction=identity,
        source_body_sha256=str(identity["source_body_sha256"]),
        source_file_sha256=str(identity["source_file_sha256"]),
        editorial_structure_sha256=str(identity["editorial_structure_sha256"]),
        editorial_topology_sha256=str(identity["editorial_topology_sha256"]),
        source_descriptor=source_descriptor, usage_rows=usage_rows, section_rows=section_rows,
        exclusions=exclusions, complete=sections.only is None,
        visual_source_attestations=visual_source_attestations,
    )
    try:
        validate_merged_package(package)
    except KnowledgePackageMergeError as exc:
        raise DetailedExtractionValidationError(
            f"compiled package violates graph integrity: {exc}"
        ) from exc
    output_dir.mkdir(parents=True, exist_ok=True)
    # The ledger is arithmetic over the package that was just written -- no
    # model call, nothing to approve -- so every extraction can carry its own
    # scoreboard instead of it having to be recomputed by hand later. It reports
    # and does not gate: a red light onto a queue nobody can drain gets switched
    # off within a month, and who may switch this one on is not this runner's
    # decision to make. Compute it from a private complete package first. The
    # current path must never briefly contain a matching extraction fingerprint
    # without its coverage: a crash in that window would make the next run skip
    # the model generation and preserve the incomplete artifact forever.
    descriptor, coverage_name = tempfile.mkstemp(
        dir=output_dir, prefix=f".{output_path.name}.coverage.", suffix=".json"
    )
    coverage_path = Path(coverage_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(
                (json.dumps(package, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            )
            handle.flush()
            os.fsync(handle.fileno())
        package["coverage"] = _coverage(source_path, coverage_path)
    finally:
        try:
            coverage_path.unlink()
        except FileNotFoundError:
            pass
    _archive(output_path)
    package["extraction"]["artifact_sha256"] = _package_artifact_sha256(package)
    _atomic_json_artifact_write(output_path, package)
    record.quality(_coverage_quality(package["coverage"]))
    record.outputs(output_path)
    _print_usage(source_id, usage_rows)
    _print_coverage(source_id, package["coverage"])
    return "created", output_path


def _coverage_quality(coverage: dict[str, Any]) -> dict[str, Any]:
    """The overview's number for this run, taken from the ledger's own count.

    The denominator is the source's sentences, never the package's output --
    extraction grading what extraction produced scores full marks every time,
    including on the material it never looked at.
    """

    if not coverage.get("available"):
        return {"available": False, "reason": coverage.get("reason")}
    categories = coverage.get("by_category") or {}
    prose = categories.get("prose") or {}
    return {
        "available": True,
        "sentences": coverage.get("sentences"),
        "represented": coverage.get("represented"),
        "excluded": coverage.get("excluded"),
        "unprocessed": coverage.get("unprocessed"),
        "prose_represented": prose.get("represented"),
        "prose_total": prose.get("total"),
        "prose_pct": prose.get("represented_pct"),
        # The prose figure and the whole-source count are different
        # populations: 51 of one manuscript's 64 unaccounted sentences were
        # headings. Shown side by side without this breakdown they read as a
        # contradiction -- 97.7% covered, 64 missing.
        "prose_unprocessed": prose.get("unprocessed"),
        "unprocessed_by_category": {
            name: values.get("unprocessed")
            for name, values in categories.items()
            if values.get("unprocessed")
        },
        # Every unaccounted sentence here has a model-written reason that no
        # person has approved. "Nobody looked" and "answered, awaiting review"
        # are different states and the ledger keeps them apart.
        "exclusions_recorded": coverage.get("exclusions_recorded"),
        "exclusions_terminal": coverage.get("exclusions_terminal"),
    }


def _coverage(source_path: Path, package_path: Path) -> dict[str, Any]:
    """The ledger's verdict on the package just written, or why it could not run.

    A failure here must not fail the extraction: the package is already valid
    and on disk, and a scoreboard that can take the run down with it is worse
    than one that says it is missing.
    """

    try:
        report = run_ledger(source_path, package_path)
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {"available": True, **report}


def _print_coverage(source_id: str, coverage: dict[str, Any]) -> None:
    if not coverage.get("available"):
        print(json.dumps({"coverage": source_id, **coverage}, ensure_ascii=False))
        return
    prose = (coverage.get("by_category") or {}).get("prose") or {}
    print(json.dumps({
        "coverage": source_id,
        "prose_represented": prose.get("represented"),
        "prose_total": prose.get("total"),
        "prose_pct": prose.get("represented_pct"),
        "sentences": coverage.get("sentences"),
        "unprocessed": coverage.get("unprocessed"),
        "fragments_unplaced": coverage.get("fragments_unplaced"),
    }, ensure_ascii=False))


def run_source(
    source_descriptor: dict[str, Any], *, output_dir: Path,
    client: Stage1OpenAIClient | Stage1AnthropicClient | CodexSubscriptionClient,
    prompt: str, reasoning_effort: str, force: bool,
    sections: SectionSettings | None = None,
    record_run_ledger: bool = True,
) -> tuple[str, Path]:
    source, raw, source_path = markdown_source_document(source_descriptor)
    source_id = str(source_descriptor["source_id"])
    header = (
        f"来源类型：{source_descriptor.get('source_type', 'notes_manuscript')}\n\n"
        "以下是该 Markdown 讲稿的一个完整章节。S 编号是全文唯一定位码，不因章节而改变。"
        "请只输出符合 schema 的完整 JSON。\n\n"
    )
    return _run(
        source_id=source_id, source=source, raw=raw, source_path=source_path, header=header,
        output_dir=output_dir, client=client, prompt=prompt, reasoning_effort=reasoning_effort,
        sections=sections or SectionSettings(), force=force, source_descriptor=source_descriptor,
        visual_source_attestations=(
            source_descriptor.get("visual_source_attestations") or {}
        ),
        record_run_ledger=record_run_ledger,
    )


def run_one(
    transcript_path: Path, *, output_dir: Path,
    client: Stage1OpenAIClient | Stage1AnthropicClient | CodexSubscriptionClient,
    prompt: str, reasoning_effort: str, force: bool,
    sections: SectionSettings | None = None,
    write_back_subtitles: bool = False,
    subtitle_actor_id: str | None = None,
    subtitle_writer: Callable[..., dict[str, Any]] | None = None,
    subtitle_authorizer: Callable[[str], bool] | None = None,
    visual_source_attestations: Mapping[str, str] | None = None,
    record_run_ledger: bool = True,
) -> tuple[str, Path]:
    transcript, raw = _load(transcript_path)
    transcript_id = transcript_path.stem
    if transcript_path.parent.name == "script_review":
        reconcile_subtitle_application(
            source_id=transcript_id,
            source_path=transcript_path,
            output_dir=output_dir,
            current_raw=raw,
        )
    section_settings = sections or SectionSettings()
    projection = project_script(transcript.get("script"))
    _assert_inline_source_readable(
        transcript_id, projection, visual_source_attestations
    )
    leading_untitled_end = leading_untitled_body_end(
        projection.headings,
        len(projection.body_rows),
        level=section_settings.level,
    )
    if write_back_subtitles and not section_settings.allow_generated:
        raise SubtitlePersistenceError(
            "subtitle persistence cannot be combined with generated sections disabled"
        )
    if write_back_subtitles and not str(subtitle_actor_id or "").strip():
        raise SubtitlePersistenceError(
            "subtitle persistence requires an authenticated --subtitle-user-id"
        )
    if (
        transcript_path.parent.name == "script_review"
        and leading_untitled_end is not None
        and not write_back_subtitles
    ):
        raise SubtitlePersistenceError(
            "script_review sermon with an untitled leading section requires "
            "--write-back-generated-subtitles and --subtitle-user-id before extraction"
        )
    if write_back_subtitles and not projection.body_rows:
        raise SubtitlePersistenceError(
            "empty script_review sermon cannot receive generated subtitles"
        )
    preferred_plan: SectionPlan | None = None
    if (
        write_back_subtitles
        and section_settings.allow_generated
        and leading_untitled_end is not None
    ):
        _assert_subtitle_persistence_authorized(
            str(subtitle_actor_id),
            writer=subtitle_writer,
            authorizer=subtitle_authorizer,
        )
        before_source_sha256 = hashlib.sha256(raw).hexdigest()
        cached_plan = (
            reusable_generated_plan(
                source=transcript,
                source_id=transcript_id,
                source_sha256=projection.body_sha256,
                output_dir=output_dir,
                level=section_settings.level,
                max_section_sentences=section_settings.max_sentences,
            )
            if leading_untitled_end == len(projection.body_rows)
            else None
        )
        # Plans written before the body/editorial identity split were bound to
        # the physical file SHA. Accept one only through the same full-plan
        # validation as a current cache; after persistence `_run` rewrites its
        # metadata with the body identity. This is a compatibility read, not a
        # second source identity.
        legacy_plan_is_coordinate_safe = not any(
            is_editorial_row(row) for row in live_script(transcript.get("script"))
        )
        if (
            cached_plan is None
            and leading_untitled_end == len(projection.body_rows)
            and legacy_plan_is_coordinate_safe
        ):
            cached_plan = reusable_generated_plan(
                source=transcript,
                source_id=transcript_id,
                source_sha256=before_source_sha256,
                output_dir=output_dir,
                level=section_settings.level,
                max_section_sentences=section_settings.max_sentences,
            )
        before_payload = json.loads(raw)
        report = _persist_generated_subtitles(
            source_id=transcript_id,
            source=transcript,
            raw=raw,
            source_path=transcript_path,
            output_dir=output_dir,
            actor_id=str(subtitle_actor_id),
            client=client if isinstance(client, CodexSubscriptionClient) else None,
            writer=subtitle_writer,
            scope_end=leading_untitled_end,
            cached_generated_plan=cached_plan,
        )
        transcript, raw = _load(transcript_path)
        after_payload = json.loads(raw)
        if not isinstance(before_payload, list) or not isinstance(after_payload, list):
            raise SubtitlePersistenceError(
                "persisted script_review sermon must remain a JSON array"
            )
        verify_saved_result(
            before_payload,
            after_payload,
            expected_insertions=int(report["insertions"]),
        )
        reloaded_sha256 = hashlib.sha256(raw).hexdigest()
        if reloaded_sha256 != report.get("after_source_sha256"):
            raise SubtitlePersistenceError(
                "reloaded sermon SHA does not match the authorized save result"
            )
        reloaded_projection = project_script(transcript.get("script"))
        if leading_untitled_body_end(
            reloaded_projection.headings,
            len(reloaded_projection.body_rows),
            level=section_settings.level,
        ) is not None:
            raise SubtitlePersistenceError(
                "saved sermon still has an untitled leading section; extraction not started"
            )
        if cached_plan is not None:
            preferred_plan = cached_plan
        # The generator has completed its job. From here onward headings are
        # persisted editorial structure. They may guide section grouping, but
        # the source projection keeps them out of sentences and evidence.
        section_settings = SectionSettings(
            level=section_settings.level,
            max_sentences=section_settings.max_sentences,
            fallback_max_sentences=section_settings.fallback_max_sentences,
            allow_generated=False,
            only=section_settings.only,
        )
    header = (
        "来源类型：sermon_transcript\n\n"
        "以下是该逐字稿的一个完整章节。S 编号是全文唯一定位码，不因章节而改变。"
        "请只输出符合 schema 的完整 JSON。\n\n"
    )
    return _run(
        source_id=transcript_id, source=transcript, raw=raw, source_path=transcript_path,
        header=header, output_dir=output_dir, client=client, prompt=prompt,
        reasoning_effort=reasoning_effort, sections=section_settings, force=force,
        preferred_plan=preferred_plan,
        visual_source_attestations=visual_source_attestations,
        record_run_ledger=record_run_ledger,
    )


def parse_visual_source_attestations(values: Sequence[str]) -> dict[str, str]:
    """Parse repeated ``LOCATOR=SHA256`` command-line attestations."""

    result: dict[str, str] = {}
    for value in values:
        locator, separator, raw_sha256 = str(value).partition("=")
        locator = locator.strip()
        raw_sha256 = raw_sha256.strip()
        if (
            not separator
            or not re.fullmatch(r"S[0-9]{4,}/V[0-9]{2,}", locator)
            or not re.fullmatch(r"[0-9a-f]{64}", raw_sha256)
        ):
            raise ValueError(
                "visual source attestation must be LOCATOR=64-lowercase-hex-SHA256"
            )
        if locator in result:
            raise ValueError(f"duplicate visual source attestation: {locator}")
        result[locator] = raw_sha256
    return result


def build_client(
    model: str, *, reasoning_effort: str, max_output_tokens: int, backend: str = "api"
) -> Stage1OpenAIClient | Stage1AnthropicClient | CodexSubscriptionClient:
    """The client for a model id, chosen by its family prefix."""

    if backend == "codex-subscription":
        return CodexSubscriptionClient(
            model=model, reasoning_effort=reasoning_effort, timeout_seconds=900,
            max_output_tokens=max_output_tokens,
        )
    if backend != "api":
        raise ValueError(f"unknown backend {backend!r}")

    family = model.split("-", 1)[0]
    backend = MODEL_BACKENDS.get(family)
    if backend is None:
        raise ValueError(
            f"unknown model family {family!r}; expected one of {sorted(MODEL_BACKENDS)}"
        )
    if backend["kind"] == "anthropic":
        return Stage1AnthropicClient(
            model=model, timeout_seconds=900, max_retries=3,
            max_output_tokens=max_output_tokens,
        )
    return Stage1OpenAIClient(
        model=model, reasoning_effort=reasoning_effort, timeout_seconds=900,
        max_retries=3, max_output_tokens=max_output_tokens,
        base_url=backend.get("base_url"), api_key_env=backend.get("api_key_env", "OPENAI_API_KEY"),
        sends_reasoning_effort=backend.get("sends_reasoning_effort"),
        sends_temperature=backend.get("sends_temperature"),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcript-dir", type=Path, default=DEFAULT_TRANSCRIPT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ids", nargs="+")
    group.add_argument("--source-manifest", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help="claude-* (default), gpt-*, or deepseek-*")
    parser.add_argument(
        "--backend", choices=["api", "codex-subscription"], default="api",
        help="model transport; api is the unchanged default, codex-subscription is opt-in",
    )
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    # 32,000 was sized for a model that did not think from the same budget.
    # Claude Opus 5 spends adaptive thinking out of `max_tokens`, and a single
    # 1,391-character section spent 21,967 output tokens; the first run at the
    # old default failed mid-answer on the smallest section of the 母本.
    parser.add_argument("--max-output-tokens", type=int, default=64000)
    parser.add_argument("--section-level", type=int, default=DEFAULT_SECTION_LEVEL,
                        help="headings at or above this level start a section")
    parser.add_argument(
        "--max-section-sentences", type=int,
        help="split only an oversized section at the next heading level, using the "
             "fewest balanced chunks that satisfy this limit",
    )
    parser.add_argument(
        "--fallback-max-section-sentences", type=int,
        help="preserve an unchanged successful package; otherwise apply this "
             "sentence limit before any model call",
    )
    parser.add_argument("--only-sections", type=int, nargs="+", metavar="N",
                        help="run only these section numbers (1-based); the package "
                             "is then marked incomplete")
    parser.add_argument("--no-generated-sections", action="store_true",
                        help="never ask the subtitle generator for boundaries; "
                             "a source with no headings is then one section")
    parser.add_argument(
        "--write-back-generated-subtitles",
        action="store_true",
        help="for headingless script_review sermons, write generated subtitles to the "
             "review transcript, verify body preservation, and reload before extraction",
    )
    parser.add_argument(
        "--subtitle-user-id",
        help="authenticated sermon editor identity used for ACL-checked subtitle write-back",
    )
    parser.add_argument(
        "--visual-source-attestation",
        action="append",
        default=[],
        metavar="LOCATOR=SHA256",
        help="attest one inline SVG as professor-displayed/drawn source evidence; "
             "repeat for every visual block",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--no-run-ledger",
        action="store_true",
        help="create/inspect staging artifacts without writing pipeline_runs",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.write_back_generated_subtitles and args.no_generated_sections:
        parser.error("--write-back-generated-subtitles cannot be combined with --no-generated-sections")
    if args.write_back_generated_subtitles and not args.subtitle_user_id:
        parser.error("--write-back-generated-subtitles requires --subtitle-user-id")
    if args.max_section_sentences is not None and args.max_section_sentences <= 0:
        parser.error("--max-section-sentences must be positive")
    if (
        args.fallback_max_section_sentences is not None
        and args.fallback_max_section_sentences <= 0
    ):
        parser.error("--fallback-max-section-sentences must be positive")
    if (
        args.max_section_sentences is not None
        and args.fallback_max_section_sentences is not None
    ):
        parser.error(
            "--max-section-sentences and --fallback-max-section-sentences "
            "are mutually exclusive"
        )
    try:
        visual_source_attestations = parse_visual_source_attestations(
            args.visual_source_attestation
        )
    except ValueError as exc:
        parser.error(str(exc))
    sections = SectionSettings(
        level=args.section_level, max_sentences=args.max_section_sentences,
        fallback_max_sentences=args.fallback_max_section_sentences,
        allow_generated=not args.no_generated_sections,
        only=tuple(args.only_sections) if args.only_sections else None,
    )
    source_rows = load_source_manifest(args.source_manifest) if args.source_manifest else []
    paths = [args.transcript_dir / f"{transcript_id}.json" for transcript_id in (args.ids or [])]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        parser.error("missing transcripts: " + ", ".join(missing))
    if args.dry_run:
        def section_plan_summary(
            source_id: str, source: dict[str, Any]
        ) -> list[dict[str, Any]]:
            # Dry run never calls the generator; a source with no headings
            # reports one section, which is what an offline run would do.
            projection = project_script(source.get("script"))
            _assert_inline_source_readable(
                source_id, projection, visual_source_attestations
            )
            texts = [str(row.get("text") or "") for row in projection.body_rows]
            counts = [_source_unit_count(text) for text in texts]
            plan = plan_sections(
                texts,
                headings=projection.headings,
                segment_indexes=[row.get("index") for row in projection.body_rows],
                level=sections.level,
                sentence_counts=counts,
                max_section_sentences=(
                    sections.max_sentences or sections.fallback_max_sentences
                ),
            )
            source_body = {
                **source,
                "script": [dict(row) for row in projection.body_rows],
            }
            return [
                {
                    **section_payload(section),
                    "sentences": len(section_sentences(source_body, section)),
                }
                for section in plan.sections
            ]

        try:
            plan_rows = {
                path.stem: section_plan_summary(path.stem, _load(path)[0])
                for path in paths
            }
            plan_rows.update({
                str(row["source_id"]): section_plan_summary(
                    str(row["source_id"]), markdown_source_document(row)[0]
                )
                for row in source_rows
            })
            visual_rows = {
                path.stem: [
                    block.descriptor()
                    for block in project_script(_load(path)[0].get("script")).visual_blocks
                ]
                for path in paths
            }
            visual_rows.update(
                {
                    str(row["source_id"]): [
                        block.descriptor()
                        for block in project_script(
                            markdown_source_document(row)[0].get("script")
                        ).visual_blocks
                    ]
                    for row in source_rows
                }
            )
        except DetailedExtractionValidationError as exc:
            parser.error(str(exc))
        print(json.dumps({
            "transcripts": args.ids or [],
            "sources": [row["source_id"] for row in source_rows], "model": args.model,
            "backend": args.backend,
            "reasoning_effort": args.reasoning_effort,
            "max_output_tokens": args.max_output_tokens,
            "section_level": sections.level,
            "max_section_sentences": sections.max_sentences,
            "allow_generated_sections": sections.allow_generated,
            "write_back_generated_subtitles": args.write_back_generated_subtitles,
            "subtitle_user_id": args.subtitle_user_id,
            "sections_per_source": {
                key: len(value) for key, value in plan_rows.items()
            },
            "section_plans": plan_rows,
            "visual_sources": visual_rows,
            # Retained for scripts that read the old dry-run shape. Dry runs
            # never call either backend.
            "would_call_openai": False,
            "would_call_model": False,
        }, ensure_ascii=False))
        return 0
    load_dotenv(PROJECT_ROOT / ".env")
    prompt_path = NOTES_PROMPT_PATH if source_rows else PROMPT_PATH
    prompt = prompt_path.read_text(encoding="utf-8")
    client = build_client(
        args.model, reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens, backend=args.backend,
    )
    counts = {"created": 0, "skipped": 0, "failed": 0}
    for path in paths:
        try:
            status, output = run_one(
                path, output_dir=args.output_dir, client=client, prompt=prompt,
                reasoning_effort=args.reasoning_effort, force=args.force, sections=sections,
                write_back_subtitles=args.write_back_generated_subtitles,
                subtitle_actor_id=args.subtitle_user_id,
                visual_source_attestations=visual_source_attestations,
                record_run_ledger=not args.no_run_ledger,
            )
            counts[status] += 1
            print(f"{status}: {path.name} -> {output}")
        except (
            DetailedExtractionValidationError, RuntimeError, ValueError,
            json.JSONDecodeError, OSError,
        ) as exc:
            counts["failed"] += 1
            print(f"FAILED: {path.name}: {exc}")
    for source_row in source_rows:
        try:
            status, output = run_source(
                source_row, output_dir=args.output_dir, client=client, prompt=prompt,
                reasoning_effort=args.reasoning_effort, force=args.force, sections=sections,
                record_run_ledger=not args.no_run_ledger,
            )
            counts[status] += 1
            print(f"{status}: {source_row['source_id']} -> {output}")
        except (
            DetailedExtractionValidationError, RuntimeError, ValueError,
            json.JSONDecodeError, OSError,
        ) as exc:
            counts["failed"] += 1
            print(f"FAILED: {source_row['source_id']}: {exc}")
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
