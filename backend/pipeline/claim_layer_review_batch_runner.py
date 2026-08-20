"""Run a large curated claim-layer review as deterministic smaller batches."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

from backend.pipeline.claim_layer_review_batch import (
    merge_review_artifacts,
    split_claim_layer_package,
    split_claim_layer_package_by_source,
)
from backend.pipeline.corpus_ai_review_runner import (
    DEFAULT_TRANSCRIPT_DIRS,
    PROMPT_PATH,
    PROJECT_ROOT,
    run_claim_layer,
)
from backend.pipeline.knowledge_package import live_claims
from backend.pipeline.llm_usage import usage_summary
from backend.pipeline.stage1 import Stage1AnthropicClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--batch-size", type=int, default=38)
    parser.add_argument(
        "--partition",
        choices=("claim_count", "source"),
        default="claim_count",
        help="Use source before cross-source synthesis; claim_count for already synthesized packages.",
    )
    parser.add_argument("--model", default="claude-sonnet-5")
    parser.add_argument("--max-output-tokens", type=int, default=24000)
    parser.add_argument("--spot-check-percent", type=int, default=10)
    parser.add_argument("--transcript-dir", action="append", type=Path, dest="transcript_dirs")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--merge-existing",
        action="store_true",
        help="Rebuild the combined artifact from existing batch reviews without calling Claude.",
    )
    args = parser.parse_args()

    package = json.loads(args.package.read_text(encoding="utf-8"))
    batches = (
        split_claim_layer_package_by_source(package)
        if args.partition == "source"
        else split_claim_layer_package(package, batch_size=args.batch_size)
    )
    batch_dir = args.output.parent / f"{args.output.stem}-batches"
    input_dir = batch_dir / "inputs"
    review_dir = batch_dir / "reviews"
    input_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    batch_paths: list[Path] = []
    for index, batch in enumerate(batches, start=1):
        path = input_dir / f"batch-{index:02d}.json"
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        batch_paths.append(path)

    if args.dry_run:
        print(
            json.dumps(
                {
                    "source_claims": len(live_claims(package)),
                    "batch_count": len(batches),
                    "batch_claim_counts": [len(batch["claims"]) for batch in batches],
                    "all_source_counts": [len(batch.get("source_documents") or []) for batch in batches],
                    "partition": args.partition,
                    "model": args.model,
                    "would_call_anthropic": False,
                },
                ensure_ascii=False,
            )
        )
        return 0

    client = None
    prompt = ""
    if not args.merge_existing:
        load_dotenv(PROJECT_ROOT / ".env")
        prompt = PROMPT_PATH.read_text(encoding="utf-8")
        client = Stage1AnthropicClient(
            model=args.model,
            timeout_seconds=240,
            max_retries=1,
            max_output_tokens=args.max_output_tokens,
        )
    artifacts = []
    for index, (path, batch) in enumerate(zip(batch_paths, batches), start=1):
        review_path = review_dir / f"batch-{index:02d}.independent-review.json"
        if args.merge_existing:
            if not review_path.is_file():
                raise FileNotFoundError(f"missing existing batch review: {review_path}")
            artifact = json.loads(review_path.read_text(encoding="utf-8"))
            status = "loaded"
            output = review_path
        else:
            status, output = run_claim_layer(
                path,
                transcript_dirs=args.transcript_dirs or DEFAULT_TRANSCRIPT_DIRS,
                output_path=review_path,
                client=client,
                prompt=prompt,
                spot_check_percent=args.spot_check_percent,
                force=args.force,
            )
            artifact = json.loads(output.read_text(encoding="utf-8"))
        # Older review artifacts predate persisted partition metadata. Restore
        # it from the immutable batch input before deterministic recombination.
        artifact.setdefault("source", {})["review_batch"] = batch["review_batch"]
        print(f"[{index}/{len(batch_paths)}] {status}: {output}", flush=True)
        artifacts.append(artifact)

    combined = merge_review_artifacts(
        artifacts,
        source_package=package,
        source_package_path=args.package,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": "created", "output": str(args.output)}, ensure_ascii=False))
    if combined.get("usage"):
        print(json.dumps(
            usage_summary(str(args.package.name), combined["usage"]),
            ensure_ascii=False,
        ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
