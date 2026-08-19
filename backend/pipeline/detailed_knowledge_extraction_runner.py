"""Build a reproducible, source-anchored detailed knowledge package for one sermon."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.corpus_survey_runner import PROJECT_ROOT, _load, _slug
from backend.pipeline.base_contract_coverage import sentence_spans
from backend.pipeline.detailed_knowledge_extraction import (
    DETAILED_RESPONSE_SCHEMA,
    AuditedSentence,
    DetailedExtractionValidationError,
    extraction_identity,
    validate_response,
    validate_sentence_audit,
)
from backend.pipeline.extraction_sections import (
    DEFAULT_SECTION_LEVEL,
    Section,
    SectionPlan,
    breadcrumb_for,
    combine_sections,
    load_cached_plan,
    plan_sections,
    save_plan,
)
from backend.pipeline.knowledge_source import load_source_manifest, markdown_source_document
from backend.pipeline.sentence_ledger_runner import run as run_ledger
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


#: What each supported model needs to be reached. Opus 5 is the default: on the
#: same section under the same production rules it and DeepSeek v4 pro both pass
#: validation, but Opus marks 13 load_bearing observations to DeepSeek's 3 and
#: keeps the source's Traditional characters, and that observation layer is what
#: #86 / #62 / the ledger are built on. DeepSeek stays wired up as the cheap
#: fallback (about a third of the cost) for runs where that layer matters less.
MODEL_BACKENDS = {
    "claude": {"kind": "anthropic"},
    "gpt": {"kind": "openai"},
    "deepseek": {"kind": "openai", "base_url": "https://api.deepseek.com",
                 "api_key_env": "DEEPSEEK_API_KEY"},
}
DEFAULT_MODEL = "claude-opus-5"

DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")
DEFAULT_OUTPUT_DIR = wang_platform_paths().claim_layer_staging / "detailed-extractions"
PROMPT_PATH = Path("backend/pipeline/prompts/detailed_knowledge_extraction.md")
NOTES_PROMPT_PATH = Path("backend/pipeline/prompts/detailed_notes_knowledge_extraction.md")
VALIDATION_ATTEMPTS = 4


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
        fingerprint = str((old.get("extraction") or {}).get("fingerprint_sha256") or "legacy")[:12]
    except (OSError, json.JSONDecodeError):
        fingerprint = "unreadable"
    archive = path.parent / "generations" / f"{path.stem}.{fingerprint}.json"
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        shutil.copy2(path, archive)


def _validation_feedback(
    error: DetailedExtractionValidationError,
    transcript: dict[str, Any],
) -> str:
    message = str(error)
    segment_rows: list[str] = []
    for locator in dict.fromkeys(re.findall(r"\bS\d{4}\b", message)):
        ordinal = int(locator[1:]) - 1
        script = transcript.get("script") or []
        if 0 <= ordinal < len(script):
            segment_rows.append(f"[{locator}]\n{str(script[ordinal].get('text') or '')}")
    detail = (
        "\n涉及段落的完整原文如下：\n" + "\n\n".join(segment_rows)
        if segment_rows else ""
    )
    return (
        f"上一版未通过机械验证：{message}。{detail}\n"
        "请保留上一版中其余有效对象，只修复所有同类机械错误，再重新输出完整 JSON。"
        "每个 verbatim_excerpt 必须从对应段落连续逐字复制；不能改字、补标点或拼接。"
    )


def _usage_row(usage: Any, attempt: int) -> dict[str, Any]:
    """Flatten one call's token usage, tolerating the SDK's optional fields.

    `cached_tokens` is the number worth watching: the source text is sent as a
    cached prefix precisely so a validation retry re-reads it instead of paying
    for it again, and this is the only evidence that it happens.
    """

    details = getattr(usage, "prompt_tokens_details", None)
    return {
        "attempt": attempt,
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "cached_tokens": getattr(details, "cached_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
        "total_tokens": getattr(usage, "total_tokens", None),
    }


def _print_usage(source_id: str, usage_rows: list[dict[str, Any]]) -> None:
    if not usage_rows:
        return
    total = sum(r["total_tokens"] or 0 for r in usage_rows)
    cached = sum(r["cached_tokens"] or 0 for r in usage_rows)
    prompt = sum(r["prompt_tokens"] or 0 for r in usage_rows)
    hit = f"{100 * cached / prompt:.0f}%" if prompt else "n/a"
    print(json.dumps({
        "usage": source_id, "calls": len(usage_rows), "total_tokens": total,
        "prompt_tokens": prompt, "cached_tokens": cached, "cache_hit": hit,
        "completion_tokens": sum(r["completion_tokens"] or 0 for r in usage_rows),
    }, ensure_ascii=False))


def _archive_rejected_candidate(
    *, output_dir: Path, transcript_id: str, attempt: int, candidate: dict[str, Any],
    error: DetailedExtractionValidationError,
) -> None:
    target = output_dir / "rejected-generations" / _slug(transcript_id) / f"attempt-{attempt:02d}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target = target.with_name(f"attempt-{attempt:02d}-{timestamp}.json")
    target.write_text(
        json.dumps(
            {"validation_error": str(error), "candidate": candidate},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
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

    script = source.get("script") or []
    rows: list[AuditedSentence] = []
    for position in range(section.start, section.end):
        text = str(script[position].get("text") or "")
        for start, end in sentence_spans(text):
            locator = segment_locator(position)
            rows.append(AuditedSentence(
                sentence_id=f"{locator}#{len(rows) + 1:03d}",
                segment_index=locator,
                text=text[start:end],
            ))
    return rows


def _section_prompt_body(
    source: dict[str, Any], section: Section, sentences: Sequence[AuditedSentence]
) -> str:
    """Render one section: its text, then the sentences it must account for.

    The listing is the whole change. Given the text alone the model produces
    records and stops when it feels done; given the text and "here are your 42
    sentences, one verdict each" it enumerates. That is measured, not assumed --
    50% coverage against 100% on the same material.
    """

    script = source.get("script") or []
    body = "\n\n".join(
        "[segment {locator}; source_index={index}; {begin}-{finish}]\n{text}".format(
            locator=segment_locator(position),
            index=script[position].get("index"),
            begin=script[position].get("start_time"),
            finish=script[position].get("end_time"),
            text=script[position].get("text", ""),
        )
        for position in range(section.start, section.end)
    )
    listing = "\n".join(f"[{row.sentence_id}] {row.text}" for row in sentences)
    header = f"本章节：{section.title}" if section.title else "本章节"
    breadcrumb = breadcrumb_for(_segment_texts(source), section.start)
    if breadcrumb and breadcrumb != section.title:
        header += f"\n所在标题层级：{breadcrumb}"
    return (
        f"{header}\n"
        f"范围：{segment_locator(section.start)}–{segment_locator(section.end - 1)}"
        f"（{section.length} 段）\n\n"
        f"{body}\n\n"
        f"===== 本章节全部句子（{len(sentences)} 句），每一句都必须在 sentence_audit 中出现一次 =====\n\n"
        f"{listing}"
    )


def _section_cache_path(output_dir: Path, source_id: str, fingerprint: str, section: Section) -> Path:
    return (
        output_dir / "section-cache" / _slug(source_id) / fingerprint[:16]
        / f"p{section.index:03d}-{section.start:04d}-{section.end:04d}.json"
    )


def _subtitle_provider():
    """The sermon editor's own subtitle generator, for sources with no headings.

    Imported lazily: `backend.api` pulls in the web app, and nothing in this
    module should need a running server to be importable.
    """

    from backend.api.gemini_client import GeminiClient

    return GeminiClient().generate_subtitles


def resolve_section_plan(
    *, source: dict[str, Any], source_id: str, source_sha256: str, output_dir: Path,
    level: int = DEFAULT_SECTION_LEVEL, allow_generated: bool = True,
) -> SectionPlan:
    """The plan for this source, generated at most once and then reused.

    Generating boundaries is a model call, so an uncached rerun could resegment
    the source and quietly make two extractions incomparable. The cache is keyed
    on the source hash, so editing the source correctly invalidates it.
    """

    path = output_dir / "section-plans" / f"{_slug(source_id)}.json"
    cached = load_cached_plan(path, source_sha256)
    if cached is not None:
        return cached
    provider = _subtitle_provider() if allow_generated else None
    plan = plan_sections(_segment_texts(source), level=level, provider=provider)
    save_plan(path, plan, source_sha256)
    return plan


def _extract_sections(
    *,
    source_id: str,
    source: dict[str, Any],
    header: str,
    plan: SectionPlan,
    output_dir: Path,
    client: Stage1OpenAIClient | Stage1AnthropicClient,
    prompt: str,
    fingerprint: str,
    force: bool,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Run every section, then concatenate.

    Sections do not overlap, so there is no merge step and nothing to
    deduplicate -- the combining is `combine_sections` and that is all of it.
    """

    answered: list[tuple[Section, dict[str, Any]]] = []
    usage_rows: list[dict[str, Any]] = []
    section_rows: list[dict[str, Any]] = []
    for section in plan.sections:
        cache_path = _section_cache_path(output_dir, source_id, fingerprint, section)
        if cache_path.is_file() and not force:
            answered.append((section, json.loads(cache_path.read_text(encoding="utf-8"))["response"]))
            section_rows.append({**vars(section), "attempts": 0, "cached": True})
            continue
        sentences = section_sentences(source, section)
        user_input = header + _section_prompt_body(source, section, sentences)
        last_error: DetailedExtractionValidationError | None = None
        last_candidate: dict[str, Any] | None = None
        response, attempts = None, 0
        for attempt in range(1, VALIDATION_ATTEMPTS + 1):
            attempts = attempt
            feedback = ""
            if last_error and last_candidate:
                feedback = (
                    "\n\n===== 上一版 JSON（必须以此为基础修复）=====\n"
                    + json.dumps(last_candidate, ensure_ascii=False)
                    + "\n\n===== 机械验证反馈 =====\n"
                    + _validation_feedback(last_error, source)
                )
            candidate = client.generate_json(
                prompt, feedback, DETAILED_RESPONSE_SCHEMA, cache_prefix=user_input
            )
            usage_rows.append({**_usage_row(client.last_usage, attempt), "section_index": section.index})
            try:
                # A section is a composition unit, so the full contract is
                # answerable inside it: measured, 0 of 264 relations cross a
                # `##`, and the step a load_bearing observation reasons to is
                # in the same section as the observation.
                validate_response(candidate, source)
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
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps({"section": vars(section), "response": response}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        answered.append((section, response))
        section_rows.append({**vars(section), "attempts": attempts, "cached": False})
    return combine_sections(answered), usage_rows, section_rows


def _anchored_fragment(
    *,
    fragment_id: str,
    source_id: str,
    anchor: dict[str, Any],
    transcript: dict[str, Any],
    source_sha256: str,
) -> dict[str, Any]:
    ordinal = int(anchor["segment_index"][1:]) - 1
    paragraph = transcript["script"][ordinal]
    paragraph_text = str(paragraph.get("text") or "")
    excerpt = anchor["verbatim_excerpt"]
    return {
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


def compile_package(
    *, transcript_id: str, transcript_path: Path, transcript: dict[str, Any], raw: bytes,
    response: dict[str, Any], extraction: dict[str, Any],
    source_descriptor: dict[str, Any] | None = None,
    usage_rows: list[dict[str, Any]] | None = None,
    section_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Model-facing IDs are intentionally short so the JSON remains tractable.
    # Namespace them here before a package can ever be merged with another
    # sermon.  A 200-sermon corpus cannot safely contain 200 different CL001s.
    source_key = str((source_descriptor or {}).get("source_id") or transcript_id)
    namespace = f"DK-{hashlib.sha256(source_key.encode('utf-8')).hexdigest()[:12]}"
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

    source_id = str((source_descriptor or {}).get("source_id") or f"SRC-{_slug(transcript_id)}")
    source_sha256 = hashlib.sha256(raw).hexdigest()
    fragments: list[dict[str, Any]] = []
    fragment_by_anchor: dict[tuple[str, str], str] = {}

    def fragment_for(owner_id: str, anchor: dict[str, Any], position: int) -> str:
        key = (anchor["segment_index"], anchor["verbatim_excerpt"])
        existing = fragment_by_anchor.get(key)
        if existing:
            return existing
        fragment_id = f"FR-{_slug(source_key)}-{owner_id}-{position + 1:02d}"
        fragment_by_anchor[key] = fragment_id
        fragments.append(_anchored_fragment(
            fragment_id=fragment_id, source_id=source_id, anchor=anchor,
            transcript=transcript, source_sha256=source_sha256,
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
        item["extraction_fingerprints"] = [extraction["fingerprint_sha256"]]
        anchors = []
        for evidence_id in item["evidence_step_ids"]:
            evidence = next(step for step in evidence_steps if step["evidence_step_id"] == evidence_id)
            for anchor in evidence_anchor_snapshots[evidence_id]:
                anchors.append({
                    "paragraph_key": anchor["segment_index"],
                    "media_time": anchor["start_time"],
                    "evidence_id": evidence_id,
                    "evidence_type": evidence["step_type"],
                    "speaker": evidence["speaker"],
                    "stance": evidence["stance"],
                    "discourse_role": evidence["discourse_role"],
                    "assertive": evidence["speaker"] == "professor" and evidence["stance"] == "asserted",
                    "proposed_highlight": {"text": anchor["verbatim_excerpt"], "status": "proposed"},
                })
        item["occurrences"] = [{
            "source_id": source_key,
            "transcript_id": source_key,
            "lecture": transcript.get("metadata", {}).get("title", transcript_id),
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
        "source_path": str(transcript_path),
        "review_status": "candidate",
    }
    if source_descriptor:
        source_document.update(json.loads(json.dumps(source_descriptor, ensure_ascii=False)))
        source_document.update({
            "source_id": source_id,
            "source_sha256": source_sha256,
            "source_path": str(transcript_path),
            "review_status": "candidate",
        })
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
        "extraction": {**extraction, "generated_at": datetime.now(timezone.utc).isoformat()},
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
    }
    return package


@dataclass(frozen=True)
class SectionSettings:
    """How the source is cut into the units it was composed in."""

    level: int = DEFAULT_SECTION_LEVEL
    #: Whether a source with no headings may have boundaries generated for it.
    #: Off makes the run offline and deterministic; on covers the 90 published
    #: transcripts that carry no headings at all.
    allow_generated: bool = True


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
) -> tuple[str, Path]:
    """Extract one source, whatever kind of source it is.

    Transcripts and notes manuscripts differ only in how they are loaded and
    how the prompt introduces them; they were two near-identical bodies before
    sectioning, and keeping them so would have meant maintaining the section
    loop twice.
    """

    source_sha256 = hashlib.sha256(raw).hexdigest()
    plan = resolve_section_plan(
        source=source, source_id=source_id, source_sha256=source_sha256,
        output_dir=output_dir, level=sections.level,
        allow_generated=sections.allow_generated,
    )
    identity = extraction_identity(
        source_sha256=source_sha256, prompt=prompt,
        model_id=client.model, reasoning_effort=reasoning_effort,
        max_output_tokens=client.max_output_tokens,
        section_plan=plan.identity(),
    )
    output_path = output_dir / f"{_slug(source_id)}.detailed-knowledge.json"
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        if (existing.get("extraction") or {}).get("fingerprint_sha256") == identity["fingerprint_sha256"]:
            return "skipped", output_path
    response, usage_rows, section_rows = _extract_sections(
        source_id=source_id, source=source, header=header, plan=plan,
        output_dir=output_dir, client=client, prompt=prompt,
        fingerprint=identity["generation_fingerprint_sha256"], force=force,
    )
    package = compile_package(
        transcript_id=source_id, transcript_path=source_path, transcript=source,
        raw=raw, response=response, extraction=identity,
        source_descriptor=source_descriptor, usage_rows=usage_rows, section_rows=section_rows,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    _archive(output_path)
    output_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # The ledger is arithmetic over the package that was just written -- no
    # model call, nothing to approve -- so every extraction can carry its own
    # scoreboard instead of it having to be recomputed by hand later. It reports
    # and does not gate: a red light onto a queue nobody can drain gets switched
    # off within a month, and who may switch this one on is not this runner's
    # decision to make.
    package["coverage"] = _coverage(source_path, output_path)
    output_path.write_text(json.dumps(package, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _print_usage(source_id, usage_rows)
    _print_coverage(source_id, package["coverage"])
    return "created", output_path


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
    source_descriptor: dict[str, Any], *, output_dir: Path, client: Stage1OpenAIClient,
    prompt: str, reasoning_effort: str, force: bool,
    sections: SectionSettings | None = None,
) -> tuple[str, Path]:
    source, raw, source_path = markdown_source_document(source_descriptor)
    source_id = str(source_descriptor["source_id"])
    header = (
        f"来源 ID：{source_id}\n标题：{source.get('metadata', {}).get('title', source_id)}\n"
        f"来源类型：{source_descriptor.get('source_type', 'notes_manuscript')}\n\n"
        "以下是该 Markdown 讲稿的一个完整章节。S 编号是全文唯一定位码，不因章节而改变。"
        "请只输出符合 schema 的完整 JSON。\n\n"
    )
    return _run(
        source_id=source_id, source=source, raw=raw, source_path=source_path, header=header,
        output_dir=output_dir, client=client, prompt=prompt, reasoning_effort=reasoning_effort,
        sections=sections or SectionSettings(), force=force, source_descriptor=source_descriptor,
    )


def run_one(
    transcript_path: Path, *, output_dir: Path, client: Stage1OpenAIClient,
    prompt: str, reasoning_effort: str, force: bool,
    sections: SectionSettings | None = None,
) -> tuple[str, Path]:
    transcript, raw = _load(transcript_path)
    transcript_id = transcript_path.stem
    header = (
        f"逐字稿 ID：{transcript_id}\n标题：{transcript.get('metadata', {}).get('title', transcript_id)}\n\n"
        "以下是该逐字稿的一个完整章节。S 编号是全文唯一定位码，不因章节而改变。"
        "请只输出符合 schema 的完整 JSON。\n\n"
    )
    return _run(
        source_id=transcript_id, source=transcript, raw=raw, source_path=transcript_path,
        header=header, output_dir=output_dir, client=client, prompt=prompt,
        reasoning_effort=reasoning_effort, sections=sections or SectionSettings(), force=force,
    )


def build_client(
    model: str, *, reasoning_effort: str, max_output_tokens: int
) -> Stage1OpenAIClient | Stage1AnthropicClient:
    """The client for a model id, chosen by its family prefix."""

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
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--section-level", type=int, default=DEFAULT_SECTION_LEVEL,
                        help="headings at or above this level start a section")
    parser.add_argument("--no-generated-sections", action="store_true",
                        help="never ask the subtitle generator for boundaries; "
                             "a source with no headings is then one section")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sections = SectionSettings(
        level=args.section_level, allow_generated=not args.no_generated_sections,
    )
    source_rows = load_source_manifest(args.source_manifest) if args.source_manifest else []
    paths = [args.transcript_dir / f"{transcript_id}.json" for transcript_id in (args.ids or [])]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        parser.error("missing transcripts: " + ", ".join(missing))
    if args.dry_run:
        def section_count(source: dict[str, Any]) -> int:
            # Dry run never calls the generator; a source with no headings
            # reports one section, which is what an offline run would do.
            return len(plan_sections(_segment_texts(source), level=sections.level).sections)

        plans = {path.stem: section_count(_load(path)[0]) for path in paths}
        plans.update({
            str(row["source_id"]): section_count(markdown_source_document(row)[0])
            for row in source_rows
        })
        print(json.dumps({
            "transcripts": args.ids or [],
            "sources": [row["source_id"] for row in source_rows], "model": args.model,
            "reasoning_effort": args.reasoning_effort,
            "max_output_tokens": args.max_output_tokens,
            "section_level": sections.level,
            "allow_generated_sections": sections.allow_generated,
            "sections_per_source": plans,
            "would_call_openai": False,
        }, ensure_ascii=False))
        return 0
    load_dotenv(PROJECT_ROOT / ".env")
    prompt_path = NOTES_PROMPT_PATH if source_rows else PROMPT_PATH
    prompt = prompt_path.read_text(encoding="utf-8")
    client = build_client(
        args.model, reasoning_effort=args.reasoning_effort,
        max_output_tokens=args.max_output_tokens,
    )
    counts = {"created": 0, "skipped": 0, "failed": 0}
    for path in paths:
        try:
            status, output = run_one(
                path, output_dir=args.output_dir, client=client, prompt=prompt,
                reasoning_effort=args.reasoning_effort, force=args.force, sections=sections,
            )
            counts[status] += 1
            print(f"{status}: {path.name} -> {output}")
        except (DetailedExtractionValidationError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            counts["failed"] += 1
            print(f"FAILED: {path.name}: {exc}")
    for source_row in source_rows:
        try:
            status, output = run_source(
                source_row, output_dir=args.output_dir, client=client, prompt=prompt,
                reasoning_effort=args.reasoning_effort, force=args.force, sections=sections,
            )
            counts[status] += 1
            print(f"{status}: {source_row['source_id']} -> {output}")
        except (DetailedExtractionValidationError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
            counts["failed"] += 1
            print(f"FAILED: {source_row['source_id']}: {exc}")
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
