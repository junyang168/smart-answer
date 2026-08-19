"""Run the second pass over one source's unaccounted sentences.

Batched, because a source can leave over a hundred sentences unaccounted for
and one call cannot hold them all. Batching is deterministic and by sentence
order, so a rerun asks the same questions in the same groups; the combined
result must cover every question exactly once, which is checked after
recombination and not assumed from the parts.

Validation failures are repaired the way extraction repairs them: the previous
answer is sent back with the specific errors, since a response that named
fourteen sentences correctly and fabricated one quotation should be corrected,
not thrown away.

Writes a report. It does not write to the store and approves nothing -- every
product is `candidate`, and `background_only` still needs a person.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from backend.pipeline.sentence_ledger import build_inventory, reconcile, summarise
from backend.pipeline.sentence_ledger_runner import load_segments, place_fragments
from backend.pipeline.sentence_ledger_second_pass import (
    PROMPT_PATH,
    SECOND_PASS_SCHEMA,
    SecondPassQuestion,
    SecondPassValidationError,
    build_questions,
    render_request,
    response_fingerprint,
    validate_response,
    verdict_counts,
)
from backend.pipeline.stage1 import Stage1OpenAIClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: Sentences per call. Small enough that one fabricated quotation costs one
#: batch rather than the whole run, large enough that the paragraph context of
#: neighbouring sentences is usually shared.
DEFAULT_BATCH_SIZE = 20
VALIDATION_ATTEMPTS = 3


def batch(questions: Sequence[SecondPassQuestion], size: int) -> list[list[SecondPassQuestion]]:
    return [list(questions[i : i + size]) for i in range(0, len(questions), size)]


def _feedback(error: SecondPassValidationError, previous: dict[str, Any]) -> str:
    return (
        "\n\n===== 上一版 JSON（必须以此为基础修复）=====\n"
        + json.dumps(previous, ensure_ascii=False)
        + "\n\n===== 机械验证反馈 =====\n"
        + f"上一版未通过机械验证：{error}。\n"
        "请保留上一版中其余有效判定，只修复所有同类机械错误，再重新输出完整 JSON。"
        "每个 supporting_excerpt 必须从对应段落连续逐字复制；每一句必须恰好判定一次。"
    )


def run_batch(
    questions: Sequence[SecondPassQuestion], *, client: Stage1OpenAIClient, prompt: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request = render_request(questions)
    last_error: SecondPassValidationError | None = None
    previous: dict[str, Any] | None = None
    usage: list[dict[str, Any]] = []

    for attempt in range(1, VALIDATION_ATTEMPTS + 1):
        feedback = _feedback(last_error, previous) if last_error and previous else ""
        candidate = client.generate_json(
            prompt, feedback, SECOND_PASS_SCHEMA, cache_prefix=request
        )
        usage.append({"attempt": attempt, "usage": _usage(client.last_usage)})
        try:
            validate_response(candidate, questions)
            return candidate, usage
        except SecondPassValidationError as exc:
            last_error, previous = exc, candidate
    raise last_error or SecondPassValidationError("second pass validation failed")


def _usage(usage: Any) -> dict[str, Any]:
    details = getattr(usage, "prompt_tokens_details", None)
    return {
        "prompt_tokens": getattr(usage, "prompt_tokens", None),
        "cached_tokens": getattr(details, "cached_tokens", None),
        "completion_tokens": getattr(usage, "completion_tokens", None),
    }


def combine(results: Sequence[dict[str, Any]], questions: Sequence[SecondPassQuestion]) -> dict[str, Any]:
    """Merge the batches, then re-check coverage over the whole set.

    Each batch was validated against its own questions; that does not by itself
    prove the union covers every question exactly once, and the whole point of
    this stage is that nothing silently goes missing.
    """

    merged = {"verdicts": [row for result in results for row in result.get("verdicts", [])]}
    validate_response(merged, questions)
    return merged


def run(
    source_path: Path,
    package_path: Path,
    *,
    client: Stage1OpenAIClient,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
) -> dict[str, Any]:
    package = json.loads(package_path.read_text(encoding="utf-8"))
    source_id = str(package["source_documents"][0]["source_id"])
    segments = load_segments(source_path)
    inventory = build_inventory(segments, source_id=source_id)
    spans, _ = place_fragments(package, segments)
    rows = reconcile(inventory, spans, reconciled_against=package_path.name)
    before = summarise(rows)

    questions = build_questions(
        rows, {s.sentence_id: s for s in inventory}, dict(segments)
    )
    if limit is not None:
        questions = questions[:limit]

    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    results: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    for group in batch(questions, batch_size):
        result, group_usage = run_batch(group, client=client, prompt=prompt)
        results.append(result)
        usage.extend(group_usage)

    merged = combine(results, questions)
    return {
        "source_id": source_id,
        "fingerprint": response_fingerprint(prompt, questions),
        "before": {
            "represented": before.represented,
            "excluded": before.excluded,
            "unprocessed": before.unprocessed,
        },
        "asked": len(questions),
        "batches": len(results),
        "verdicts": verdict_counts(merged),
        "usage": usage,
        "answers": merged,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int, help="only the first N unaccounted sentences")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=32000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.dry_run:
        package = json.loads(args.package.read_text(encoding="utf-8"))
        source_id = str(package["source_documents"][0]["source_id"])
        segments = load_segments(args.source)
        inventory = build_inventory(segments, source_id=source_id)
        spans, _ = place_fragments(package, segments)
        rows = reconcile(inventory, spans)
        questions = build_questions(rows, {s.sentence_id: s for s in inventory}, dict(segments))
        if args.limit is not None:
            questions = questions[: args.limit]
        print(json.dumps({
            "source_id": source_id, "unaccounted": len(questions),
            "batches": len(batch(questions, args.batch_size)),
            "would_call_openai": False,
        }, ensure_ascii=False))
        return 0

    load_dotenv(PROJECT_ROOT / ".env")
    client = Stage1OpenAIClient(
        model=args.model, reasoning_effort=args.reasoning_effort,
        timeout_seconds=600, max_retries=3, max_output_tokens=args.max_output_tokens,
    )
    report = run(args.source, args.package, client=client,
                 batch_size=args.batch_size, limit=args.limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("source_id", "asked", "batches", "verdicts")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
