"""Classify and normalize scripture-reference occurrences in v1 surveys."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.corpus_scripture_enrichment import (
    ScriptureEnrichmentValidationError,
    build_reference_inventory,
    classification_context,
    load_json,
    make_enrichment,
    validate_enrichment,
)
from backend.pipeline.corpus_survey_runner import PROJECT_ROOT, _slug
from backend.pipeline.stage1 import Stage1OpenAIClient


DEFAULT_SURVEY_DIR = Path("output/corpus-survey")
DEFAULT_OUTPUT_DIR = Path("output/corpus-survey/scripture-v2")
PROMPT_PATH = Path("backend/pipeline/prompts/corpus_scripture_role_enrichment.md")

ROLE_SCHEMA: dict[str, Any] = {
    "name": "wang_corpus_scripture_role_classification_v2",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "classifications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ref_key": {"type": "string"},
                        "role": {
                            "type": "string",
                            "enum": [
                                "primary_passage", "parallel_passage", "lexical_support",
                                "historical_background", "theological_support", "counterexample",
                                "application_basis", "unclassified",
                            ],
                        },
                        "role_reason": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    },
                    "required": ["ref_key", "role", "role_reason", "confidence"],
                },
            }
        },
        "required": ["classifications"],
    },
}


def _survey_paths(survey_dir: Path, ids: set[str] | None) -> list[Path]:
    paths: list[Path] = []
    for path in sorted(survey_dir.glob("*.first-pass.json")):
        try:
            transcript_id = load_json(path).get("source", {}).get("transcript_id")
        except (OSError, json.JSONDecodeError, ScriptureEnrichmentValidationError):
            continue
        if ids is None or transcript_id in ids:
            paths.append(path)
    return paths


def run_one(
    survey_path: Path,
    *,
    output_dir: Path,
    client: Stage1OpenAIClient,
    system_prompt: str,
    force: bool,
) -> tuple[str, Path]:
    survey = load_json(survey_path)
    transcript_id = survey.get("source", {}).get("transcript_id")
    if not transcript_id:
        raise ScriptureEnrichmentValidationError(f"{survey_path}: missing transcript_id")
    output_path = output_dir / f"{_slug(transcript_id)}.scripture-v2.json"
    if output_path.exists() and not force:
        existing = load_json(output_path)
        try:
            validate_enrichment(existing, survey, survey_path)
            return "skipped", output_path
        except ScriptureEnrichmentValidationError:
            pass

    inventory = build_reference_inventory(survey)
    if not inventory:
        response = {"classifications": []}
    else:
        context = classification_context(survey, inventory)
        user_prompt = (
            f"逐字稿 ID：{transcript_id}\n"
            f"讲道标题：{transcript_id}（标题只作导航，不是分类证据）\n\n"
            "请逐条分类以下经文 occurrence，并返回每一个 ref_key：\n"
            + json.dumps(context, ensure_ascii=False, indent=2)
        )
        response = client.generate_json(system_prompt, user_prompt, ROLE_SCHEMA)

    returned_keys = [item.get("ref_key") for item in response.get("classifications") or []]
    expected_keys = [item["ref_key"] for item in inventory]
    if len(returned_keys) != len(set(returned_keys)) or set(returned_keys) != set(expected_keys):
        raise ScriptureEnrichmentValidationError("model classification did not cover each ref_key exactly once")

    enrichment = make_enrichment(
        survey, survey_path, inventory, response,
        model=client.model, reasoning_effort=client.reasoning_effort,
    )
    validate_enrichment(enrichment, survey, survey_path)
    output_path.write_text(json.dumps(enrichment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "created", output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--survey-dir", type=Path, default=DEFAULT_SURVEY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--ids", nargs="*")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--model", default="gpt-5.6-terra")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--pause-seconds", type=float, default=0.5)
    parser.add_argument("--max-output-tokens", type=int, default=8000)
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = _survey_paths(args.survey_dir, set(args.ids) if args.ids else None)
    if args.limit is not None:
        paths = paths[: args.limit]
    client = Stage1OpenAIClient(
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=180,
        max_retries=3,
        max_output_tokens=args.max_output_tokens,
    )
    system_prompt = PROMPT_PATH.read_text(encoding="utf-8")
    counts = {"created": 0, "skipped": 0, "failed": 0}
    for position, path in enumerate(paths, start=1):
        try:
            status, output = run_one(
                path, output_dir=args.output_dir, client=client,
                system_prompt=system_prompt, force=args.force,
            )
            counts[status] += 1
            print(f"[{position}/{len(paths)}] {status}: {output}")
        except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
            counts["failed"] += 1
            print(f"[{position}/{len(paths)}] FAILED: {path}: {exc}")
        if position < len(paths) and args.pause_seconds:
            time.sleep(args.pause_seconds)
    print(json.dumps(counts, ensure_ascii=False))
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
