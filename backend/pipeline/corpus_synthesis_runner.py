"""Build a reviewable cross-sermon candidate map from validated survey cards.

The synthesis is deliberately hierarchical: small sermon batches first,
followed by a corpus-level consolidation.  Every semantic result carries
claim references back to the mechanically validated first-pass artifacts.
Nothing produced here is automatically approved or published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.corpus_survey import validate_survey
from backend.pipeline.corpus_survey_runner import _load as _load_transcript
from backend.pipeline.stage1 import Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TRANSCRIPT_DIR = Path("/opt/homebrew/var/www/church/web/data/script_published")
CORPUS_SURVEY_ROOT = wang_platform_paths().corpus_survey_staging
DEFAULT_SURVEY_DIR = CORPUS_SURVEY_ROOT
DEFAULT_OUTPUT_DIR = CORPUS_SURVEY_ROOT / "synthesis/full-corpus"
PROMPT_PATH = Path("backend/pipeline/prompts/corpus_cross_sermon_synthesis.md")
BASELINE_PATH = CORPUS_SURVEY_ROOT / "synthesis/15_sample_thought_map_v1.json"

AXES = ["method", "theology", "passage_exegesis", "application", "development"]
BASELINE_RELATIONS = ["repeats", "extends", "splits", "new_candidate", "tension", "unrelated"]


BATCH_SCHEMA: dict[str, Any] = {
    "name": "wang_cross_sermon_batch_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "theme_candidates": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "theme_id": {"type": "string"},
                        "title": {"type": "string"},
                        "axis": {"type": "string", "enum": AXES},
                        "summary": {"type": "string"},
                        "claim_refs": {"type": "array", "items": {"type": "string"}},
                        "baseline_relation": {"type": "string", "enum": BASELINE_RELATIONS},
                        "baseline_system_ids": {"type": "array", "items": {"type": "string"}},
                        "relation_reason": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": [
                        "theme_id", "title", "axis", "summary", "claim_refs", "baseline_relation",
                        "baseline_system_ids", "relation_reason", "confidence"
                    ],
                },
            },
            "design_observations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "observation": {"type": "string"},
                        "claim_refs": {"type": "array", "items": {"type": "string"}},
                        "impact": {
                            "type": "string",
                            "enum": ["data_model", "workflow", "review", "publication", "qa_search"],
                        },
                    },
                    "required": ["observation", "claim_refs", "impact"],
                },
            },
        },
        "required": ["theme_candidates", "design_observations"],
    },
}


FINAL_SCHEMA: dict[str, Any] = {
    "name": "wang_full_corpus_candidate_map_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidate_systems": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "system_id": {"type": "string"},
                        "title": {"type": "string"},
                        "axis": {"type": "string", "enum": AXES},
                        "summary": {"type": "string"},
                        "claim_refs": {"type": "array", "items": {"type": "string"}},
                        "batch_theme_refs": {"type": "array", "items": {"type": "string"}},
                        "baseline_relation": {"type": "string", "enum": BASELINE_RELATIONS},
                        "baseline_system_ids": {"type": "array", "items": {"type": "string"}},
                        "relation_reason": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                        "review_questions": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [
                        "system_id", "title", "axis", "summary", "claim_refs", "batch_theme_refs", "baseline_relation",
                        "baseline_system_ids", "relation_reason", "confidence", "review_questions"
                    ],
                },
            },
            "design_findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "finding_id": {"type": "string"},
                        "finding": {"type": "string"},
                        "claim_refs": {"type": "array", "items": {"type": "string"}},
                        "recommendation": {"type": "string"},
                        "severity": {"type": "string", "enum": ["confirm", "minor_change", "major_change"]},
                    },
                    "required": ["finding_id", "finding", "claim_refs", "recommendation", "severity"],
                },
            },
            "unresolved_tensions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": "string"},
                        "claim_refs": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "description", "claim_refs"],
                },
            },
            "overall_assessment": {"type": "string"},
        },
        "required": ["candidate_systems", "design_findings", "unresolved_tensions", "overall_assessment"],
    },
}


def _load_current_surveys(transcript_dirs: list[Path], survey_dir: Path) -> list[dict[str, Any]]:
    by_source: dict[tuple[str, str], dict[str, Any]] = {}
    for path in survey_dir.glob("*.first-pass.json"):
        try:
            survey = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        source = survey.get("source", {})
        by_source[(str(source.get("transcript_id")), str(source.get("sha256")))] = survey

    surveys: list[dict[str, Any]] = []
    generation_fingerprints: set[str] = set()
    seen_transcript_ids: set[str] = set()
    for transcript_dir in transcript_dirs:
        for transcript_path in sorted(transcript_dir.glob("*.json")):
            if transcript_path.stem in seen_transcript_ids:
                continue
            try:
                transcript, raw = _load_transcript(transcript_path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            source_hash = hashlib.sha256(raw).hexdigest()
            survey = by_source.get((transcript_path.stem, source_hash))
            if survey is None:
                raise RuntimeError(f"missing current survey: {transcript_path.name}")
            extraction = survey.get("extraction") or {}
            extraction_fingerprint = extraction.get("fingerprint_sha256")
            generation_fingerprint = extraction.get("generation_fingerprint_sha256")
            if not extraction_fingerprint or not generation_fingerprint:
                raise RuntimeError(
                    f"legacy survey without extraction generation: {transcript_path.name}; "
                    "rerun the first-pass survey before synthesis"
                )
            validate_survey(
                survey,
                transcript,
                raw,
                expected_extraction_fingerprint=extraction_fingerprint,
            )
            generation_fingerprints.add(generation_fingerprint)
            surveys.append(survey)
            seen_transcript_ids.add(transcript_path.stem)
    if len(generation_fingerprints) > 1:
        raise RuntimeError(
            "mixed extraction generations are not allowed in one synthesis: "
            + ", ".join(sorted(generation_fingerprints))
        )
    return surveys


def _claim_ref(transcript_id: str, claim_id: str) -> str:
    return f"{transcript_id}::{claim_id}"


def _sermon_card(survey: dict[str, Any]) -> dict[str, Any]:
    transcript_id = survey["source"]["transcript_id"]
    clusters = {
        cluster["cluster_id"]: {
            "title": cluster.get("title", ""),
            "function": cluster.get("function", ""),
            "summary": cluster.get("summary", ""),
            "scripture_refs": cluster.get("scripture_refs", []),
            "topic_terms": cluster.get("topic_terms", []),
        }
        for cluster in survey.get("content_clusters", [])
    }
    claims = []
    for claim in survey.get("candidate_claims", []):
        claims.append(
            {
                "claim_ref": _claim_ref(transcript_id, claim["claim_id"]),
                "statement": claim.get("statement", ""),
                "claim_kind": claim.get("claim_kind", ""),
                "attribution": claim.get("attribution", ""),
                "scripture_refs": claim.get("scripture_refs", []),
                "cluster_ids": claim.get("cluster_ids", []),
            }
        )
    return {
        "transcript_id": transcript_id,
        "source_extraction_generation_sha256": survey["extraction"][
            "generation_fingerprint_sha256"
        ],
        "clusters": clusters,
        "claims": claims,
    }


def _all_claim_refs(cards: list[dict[str, Any]]) -> set[str]:
    return {claim["claim_ref"] for card in cards for claim in card["claims"]}


def _claim_id_alias(value: str) -> str:
    """Normalize only mechanical case/zero variations in a local claim ID."""
    match = re.fullmatch(r"([A-Za-z]+)0*([0-9]+)", value.strip())
    if match:
        return f"{match.group(1).casefold()}{int(match.group(2))}"
    return value.strip().casefold()


def _transcript_id_alias(value: str) -> str:
    """Normalize separator-only copying variations in a transcript ID."""
    return re.sub(r"[\W_]+", "", value, flags=re.UNICODE).casefold()


def _normalize_claim_refs(payload: dict[str, Any], valid_refs: set[str], fields: list[str]) -> None:
    """Restore a model-copied claim ref only when the match is unique.

    This permits ``C001`` -> ``c1`` and ``S_210405`` -> ``S 210405`` style
    mechanical copying variations.  It does not use semantic or fuzzy title
    matching and only repairs an alias when the full source/claim pair is
    unique.
    """
    aliases: dict[tuple[str, str], list[str]] = {}
    for ref in valid_refs:
        transcript_id, claim_id = ref.rsplit("::", 1)
        aliases.setdefault(
            (_transcript_id_alias(transcript_id), _claim_id_alias(claim_id)), []
        ).append(ref)
    for field in fields:
        for item in payload.get(field, []):
            normalized: list[str] = []
            for ref in item.get("claim_refs", []):
                if ref in valid_refs:
                    normalized.append(ref)
                    continue
                if "::" not in ref:
                    normalized.append(ref)
                    continue
                transcript_id, claim_id = ref.rsplit("::", 1)
                matches = aliases.get(
                    (_transcript_id_alias(transcript_id), _claim_id_alias(claim_id)), []
                )
                normalized.append(matches[0] if len(matches) == 1 else ref)
            item["claim_refs"] = normalized


def _validate_refs(payload: dict[str, Any], valid_refs: set[str], fields: list[str]) -> None:
    for field in fields:
        for item in payload.get(field, []):
            for ref in item.get("claim_refs", []):
                if ref not in valid_refs:
                    raise RuntimeError(f"{field}: unknown claim_ref {ref}")


def _batch_theme_catalog(
    batches: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Add globally unique refs to batch-local theme IDs."""
    prepared: list[dict[str, Any]] = []
    catalog: dict[str, dict[str, Any]] = {}
    for batch in batches:
        batch_copy = json.loads(json.dumps(batch, ensure_ascii=False))
        number = int(batch_copy["analysis"]["batch_number"])
        for theme in batch_copy.get("theme_candidates", []):
            theme_ref = f"B{number:02d}::{theme['theme_id']}"
            theme["batch_theme_ref"] = theme_ref
            if theme_ref in catalog:
                raise RuntimeError(f"duplicate batch_theme_ref {theme_ref}")
            catalog[theme_ref] = theme
        prepared.append(batch_copy)
    return prepared, catalog


def _validate_batch_theme_refs(final: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> None:
    for system in final.get("candidate_systems", []):
        refs = system.get("batch_theme_refs", [])
        if not refs:
            raise RuntimeError(f"{system.get('system_id')}: at least one batch_theme_ref is required")
        for ref in refs:
            if ref not in catalog:
                raise RuntimeError(f"{system.get('system_id')}: unknown batch_theme_ref {ref}")


def _expand_system_evidence(final: dict[str, Any], catalog: dict[str, dict[str, Any]]) -> None:
    """Calculate coverage from selected batch themes, not model estimates."""
    for system in final.get("candidate_systems", []):
        representative = list(dict.fromkeys(system.get("claim_refs", [])))
        expanded: list[str] = []
        for theme_ref in system.get("batch_theme_refs", []):
            expanded.extend(catalog[theme_ref].get("claim_refs", []))
        system["representative_claim_refs"] = representative
        system["claim_refs"] = list(dict.fromkeys(expanded))


def _batch_cache_path(output_dir: Path, number: int) -> Path:
    return output_dir / f"batch-{number:02d}.candidate.json"


def _source_digest(
    cards: list[dict[str, Any]],
    model: str,
    effort: str,
    prompt: str,
    max_output_tokens: int,
) -> str:
    raw = json.dumps(
        {
            "cards": cards,
            "model": model,
            "effort": effort,
            "max_output_tokens": max_output_tokens,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "response_schema_sha256": hashlib.sha256(
                json.dumps(BATCH_SCHEMA, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _run_batches(
    cards: list[dict[str, Any]],
    baseline: dict[str, Any],
    output_dir: Path,
    client: Stage1OpenAIClient,
    prompt: str,
    batch_size: int,
    force: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    valid_refs = _all_claim_refs(cards)
    baseline_brief = [
        {"system_id": item["system_id"], "title": item["title"], "summary": item["summary"]}
        for item in baseline.get("systems", [])
    ]
    for offset in range(0, len(cards), batch_size):
        number = offset // batch_size + 1
        batch = cards[offset : offset + batch_size]
        digest = _source_digest(
            batch,
            client.model,
            client.reasoning_effort,
            prompt,
            client.max_output_tokens,
        )
        cache_path = _batch_cache_path(output_dir, number)
        if cache_path.exists() and not force:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("analysis", {}).get("source_digest") == digest:
                _validate_refs(cached, valid_refs, ["theme_candidates", "design_observations"])
                results.append(cached)
                print(f"batch {number}: skipped")
                continue
        request = {
            "stage": "batch",
            "baseline_systems": baseline_brief,
            "sermon_cards": batch,
        }
        response = client.generate_json(prompt, json.dumps(request, ensure_ascii=False), BATCH_SCHEMA)
        _normalize_claim_refs(response, valid_refs, ["theme_candidates", "design_observations"])
        _validate_refs(response, valid_refs, ["theme_candidates", "design_observations"])
        response["analysis"] = {
            "status": "candidate",
            "batch_number": number,
            "source_digest": digest,
            "source_extraction_generation_sha256": batch[0].get(
                "source_extraction_generation_sha256"
            ) if batch else None,
            "model": client.model,
            "reasoning_effort": client.reasoning_effort,
            "max_output_tokens": client.max_output_tokens,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "transcript_ids": [card["transcript_id"] for card in batch],
        }
        cache_path.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        results.append(response)
        print(f"batch {number}: created")
    return results


def _enrich_counts(final: dict[str, Any]) -> None:
    for item in final.get("candidate_systems", []):
        sermon_ids = sorted({ref.rsplit("::", 1)[0] for ref in item.get("claim_refs", [])})
        item["sermon_ids"] = sermon_ids
        item["sermon_count"] = len(sermon_ids)
        item["claim_count"] = len(set(item.get("claim_refs", [])))


def _render_markdown(final: dict[str, Any]) -> str:
    lines = [
        "# 王守仁教授讲道全语料思想地图（候选版）",
        "",
        "> 本报告由已通过机械来源校验的第一遍普查汇总而成。所有跨讲道归组仍为 candidate，尚未经过人工批准，也不是事实核查结论。",
        "",
        "## 总体判断",
        "",
        final.get("overall_assessment", ""),
        "",
        "## 候选主干与支线",
        "",
    ]
    for item in final.get("candidate_systems", []):
        lines.extend(
            [
                f"### {item['title']}",
                "",
                f"- 轴线：`{item['axis']}`",
                f"- 批次主题证据覆盖：{item.get('sermon_count', 0)} 篇讲道，{item.get('claim_count', 0)} 条候选主张",
                f"- 与十五篇基线关系：`{item['baseline_relation']}` — {', '.join(item['baseline_system_ids']) or '无'}",
                f"- 归组把握：`{item['confidence']}`",
                "",
                item["summary"],
                "",
                f"归组理由：{item['relation_reason']}",
                "",
            ]
        )
        if item.get("review_questions"):
            lines.append("待审问题：")
            lines.append("")
            lines.extend(f"- {question}" for question in item["review_questions"])
            lines.append("")
        lines.append("代表主张引用：")
        lines.append("")
        lines.extend(f"- `{ref}`" for ref in item.get("representative_claim_refs", [])[:12])
        lines.append("")
    lines.extend(["## 对整体设计的检查", ""])
    for finding in final.get("design_findings", []):
        lines.extend(
            [
                f"### {finding['finding_id']} · {finding['severity']}",
                "",
                finding["finding"],
                "",
                f"建议：{finding['recommendation']}",
                "",
            ]
        )
    lines.extend(["## 尚未解决的张力", ""])
    for item in final.get("unresolved_tensions", []):
        lines.extend([f"### {item['title']}", "", item["description"], ""])
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transcript-dir",
        type=Path,
        action="append",
        dest="transcript_dirs",
        help="Source directory; repeat to combine workflow stages. Earlier directories take precedence for duplicate IDs.",
    )
    parser.add_argument("--survey-dir", type=Path, default=DEFAULT_SURVEY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--baseline", type=Path, default=BASELINE_PATH)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--max-output-tokens", type=int, default=10000)
    parser.add_argument("--force-batches", action="store_true")
    parser.add_argument("--force-final", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    transcript_dirs = args.transcript_dirs or [DEFAULT_TRANSCRIPT_DIR]
    surveys = _load_current_surveys(transcript_dirs, args.survey_dir)
    extraction_generation = surveys[0]["extraction"]["generation_fingerprint_sha256"] if surveys else None
    cards = [_sermon_card(survey) for survey in surveys]
    valid_refs = _all_claim_refs(cards)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    client = Stage1OpenAIClient(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=240,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )

    batches = _run_batches(
        cards, baseline, args.output_dir, client, prompt, args.batch_size, args.force_batches
    )
    prepared_batches, batch_theme_catalog = _batch_theme_catalog(batches)
    synthesis_version = "full-corpus-candidate-v2-computed-coverage"
    final_path = args.output_dir / "full-corpus-thought-map-candidate-v1.json"
    final_digest = hashlib.sha256(
        json.dumps(
            {
                "version": synthesis_version,
                "batches": prepared_batches,
                "baseline": baseline,
                "model": args.model,
                "reasoning_effort": args.reasoning_effort,
                "max_output_tokens": args.max_output_tokens,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "response_schema_sha256": hashlib.sha256(
                    json.dumps(FINAL_SCHEMA, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if final_path.exists() and not args.force_final:
        final = json.loads(final_path.read_text(encoding="utf-8"))
        if final.get("analysis", {}).get("source_digest") == final_digest:
            print("final: skipped")
            return 0

    final_request = {
        "stage": "corpus_final",
        "corpus_scope": {"surveyed_sermons": len(cards), "candidate_claims": len(valid_refs)},
        "baseline": baseline,
        "batch_candidates": prepared_batches,
    }
    final: dict[str, Any] | None = None
    validation_error: RuntimeError | None = None
    for attempt in range(3):
        request_for_attempt = dict(final_request)
        if validation_error is not None:
            request_for_attempt["mechanical_validation_feedback"] = (
                f"上一版未通过来源引用校验：{validation_error}。请重新输出完整 JSON。"
                "claim_refs 只能逐字复制 batch_candidates 中实际存在的引用；不得猜测、改写讲道 ID 或主张 ID。"
            )
        candidate = client.generate_json(
            prompt,
            json.dumps(request_for_attempt, ensure_ascii=False),
            FINAL_SCHEMA,
        )
        _normalize_claim_refs(
            candidate,
            valid_refs,
            ["candidate_systems", "design_findings", "unresolved_tensions"],
        )
        try:
            _validate_refs(
                candidate,
                valid_refs,
                ["candidate_systems", "design_findings", "unresolved_tensions"],
            )
            _validate_batch_theme_refs(candidate, batch_theme_catalog)
        except RuntimeError as exc:
            validation_error = exc
            print(f"final validation retry {attempt + 1}: {exc}")
            continue
        final = candidate
        break
    if final is None:
        raise validation_error or RuntimeError("final synthesis validation failed")
    _expand_system_evidence(final, batch_theme_catalog)
    _enrich_counts(final)
    final["analysis"] = {
        "status": "candidate",
        "synthesis_version": synthesis_version,
        "source_digest": final_digest,
        "surveyed_sermon_count": len(cards),
        "source_stage_counts": {
            stage: sum(
                survey.get("source", {}).get("publication_status") == stage
                for survey in surveys
            )
            for stage in sorted(
                {
                    str(survey.get("source", {}).get("publication_status"))
                    for survey in surveys
                }
            )
        },
        "candidate_claim_count": len(valid_refs),
        "batch_count": len(batches),
        "model": args.model,
        "reasoning_effort": args.reasoning_effort,
        "source_extraction_generation_sha256": extraction_generation,
    }
    final_path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown_path = args.output_dir / "full-corpus-thought-map-candidate-v1.md"
    markdown_path.write_text(_render_markdown(final), encoding="utf-8")
    print(f"final: created -> {final_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
