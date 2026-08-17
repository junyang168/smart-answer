"""Generate, double-review, and optionally ingest product candidates."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config.wang_platform_paths import wang_platform_paths
from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.candidate_projection import (
    CANDIDATE_SCHEMA,
    PLAN_ADJUDICATION_SCHEMA,
    PLAN_RECONSIDERATION_SCHEMA,
    PLAN_REVIEW_SCHEMA,
    SCOPE,
    build_incremental_package,
    projection_input,
    stable_plan_key,
    validate_candidates,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_ROOT = (
    wang_platform_paths().claim_layer_staging
    / "research-batches/RB-COVENANT-LAW-VALIDATION-01"
)
PROMPT_DIR = Path("backend/pipeline/prompts")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fingerprint(value: Any) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    archive = path.parent / "generations"
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / f"{path.stem}.{_sha(path.read_bytes())[:12]}{path.suffix}"
    if not target.exists():
        shutil.copy2(path, target)


def _write(path: Path, payload: dict[str, Any]) -> None:
    _archive(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _generate_valid(
    client: Any,
    prompt: str,
    user_input: str,
    schema: dict[str, Any],
    validator: Any,
    attempts: int = 3,
) -> dict[str, Any]:
    # `user_input` is identical on every attempt; only the feedback tail varies,
    # so it goes in its own cached block instead of being re-sent at full price.
    feedback = ""
    last_error: Exception | None = None
    for _ in range(attempts):
        response = client.generate_json(prompt, feedback, schema, cache_prefix=user_input)
        try:
            validator(response)
            return response
        except ValueError as exc:
            last_error = exc
            feedback = (
                "\n\n上一版未通过机械验证：" + str(exc)
                + "。请重新输出完整 JSON，不得只给修正片段。"
            )
    raise ValueError(f"candidate model output remained invalid: {last_error}")


def _plan_claim_ids(plan: dict[str, Any]) -> set[str]:
    return {
        str(claim_id)
        for section in plan.get("sections") or []
        for claim_id in section.get("claim_ids") or []
    }


def _plan_review_input(source: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    claim_ids = _plan_claim_ids(plan)
    return {
        "scope": SCOPE,
        "candidate_plan": plan,
        "claims": [row for row in source["claims"] if row["claim_id"] in claim_ids],
        "reviewed_cross_sermon_relations": [
            row for row in source.get("reviewed_cross_sermon_relations") or []
            if str(row.get("source_claim_id") or row.get("from_claim_id") or "") in claim_ids
            or str(row.get("target_claim_id") or row.get("to_claim_id") or "") in claim_ids
        ],
        "canonical_topics": source.get("canonical_topics") or [],
        "scripture_targets": source.get("scripture_targets") or [],
        "policy": source.get("policy") or {},
    }


def _validate_plan_review(
    response: dict[str, Any], source: dict[str, Any], original: dict[str, Any]
) -> None:
    if response.get("scope_confirmation") != SCOPE:
        raise ValueError("candidate plan review scope not confirmed")
    decision = response.get("decision")
    replacements = response.get("replacement_plans") or []
    if decision == "approve":
        if replacements:
            raise ValueError("approved plan cannot include replacements")
        return
    if decision != "replace" or not replacements:
        raise ValueError("replacement decision requires replacement plans")
    local_claim_ids = _plan_claim_ids(original)
    local_source = dict(source)
    local_source["claims"] = [
        row for row in source["claims"] if row["claim_id"] in local_claim_ids
    ]
    candidate = {
        "scope_confirmation": SCOPE,
        "candidate_plans": replacements,
        "unassigned_claim_ids": [],
        "summary": response.get("reason") or "",
    }
    validate_candidates(candidate, local_source)
    replacement_ids = set().union(*(_plan_claim_ids(row) for row in replacements))
    if replacement_ids != local_claim_ids:
        raise ValueError("replacement plans must cover exactly the original claim set")


def run(
    *,
    knowledge_path: Path,
    relations_path: Path,
    output_dir: Path,
    store: PostgresKnowledgeStore,
    openai_client: Stage1OpenAIClient,
    claude_client: Stage1AnthropicClient,
    apply: bool,
    force: bool,
) -> dict[str, Any]:
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    relations = json.loads(relations_path.read_text(encoding="utf-8"))
    current = store.compile_package(package_id="CANDIDATE-PROJECTION-CONTEXT")
    source = projection_input(knowledge, relations, current.get("topic_nodes") or [])
    source_text = json.dumps(source, ensure_ascii=False, indent=2)
    prompts = {
        name: (PROMPT_DIR / f"candidate_projection_{name}.md").read_text(encoding="utf-8")
        for name in (
            "discovery", "plan_review", "plan_adjudication",
            "plan_reconsideration",
        )
    }
    generation = {
        "schema_version": "wang_candidate_projection_runner_v1",
        "source_sha256": _fingerprint(source),
        "prompt_sha256": {name: _sha(text.encode("utf-8")) for name, text in prompts.items()},
        "openai_model": openai_client.model,
        "claude_model": claude_client.model,
    }
    generation["fingerprint_sha256"] = _fingerprint(generation)
    final_path = output_dir / "reviewed-candidates.json"
    package_path = output_dir / "candidate-package.json"
    if not force and final_path.is_file() and package_path.is_file():
        cached = json.loads(final_path.read_text(encoding="utf-8"))
        if (cached.get("generation") or {}).get("fingerprint_sha256") == generation["fingerprint_sha256"]:
            package = json.loads(package_path.read_text(encoding="utf-8"))
            ingest = store.ingest_package(
                package, source_kind="reviewed_candidate_projection", apply=apply,
                metadata={"input_path": str(package_path)},
            )
            return {"status": "cached", "ingest": ingest, "output": str(final_path)}

    discovery_path = output_dir / "discovery.json"
    discovery_generation = {
        "schema_version": generation["schema_version"],
        "source_sha256": generation["source_sha256"],
        "prompt_sha256": generation["prompt_sha256"]["discovery"],
        "model": openai_client.model,
    }
    discovery_generation["fingerprint_sha256"] = _fingerprint(discovery_generation)
    discovery = None
    if not force and discovery_path.is_file():
        cached_discovery = json.loads(discovery_path.read_text(encoding="utf-8"))
        cached_result = cached_discovery.get("result")
        # Older generations included all review prompts in the discovery
        # fingerprint.  Validate and reuse their deterministic result when the
        # source and discovery model still match.
        cached_generation = cached_discovery.get("generation") or {}
        source_matches = cached_generation.get("source_sha256") == generation["source_sha256"]
        model_matches = cached_generation.get("openai_model") == openai_client.model
        try:
            if source_matches and model_matches and isinstance(cached_result, dict):
                validate_candidates(cached_result, source)
                discovery = cached_result
        except ValueError:
            discovery = None
    if discovery is None:
        discovery = _generate_valid(
            openai_client, prompts["discovery"], source_text, CANDIDATE_SCHEMA,
            lambda value: validate_candidates(value, source),
        )
    _write(discovery_path, {"generation": discovery_generation, "result": discovery})

    parts_dir = output_dir / "plan-reviews"
    parts_dir.mkdir(parents=True, exist_ok=True)

    def review_one(index_plan: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        index, plan = index_plan
        key = stable_plan_key(plan)
        part_path = parts_dir / f"{index + 1:02d}-{key}.json"
        part_generation = dict(generation)
        part_generation["plan_sha256"] = _fingerprint(plan)
        part_generation["fingerprint_sha256"] = _fingerprint(part_generation)
        if not force and part_path.is_file():
            cached_part = json.loads(part_path.read_text(encoding="utf-8"))
            if (cached_part.get("generation") or {}).get("fingerprint_sha256") == part_generation["fingerprint_sha256"]:
                return index, cached_part["result"]
        part_source = _plan_review_input(source, plan)
        response = _generate_valid(
            claude_client,
            prompts["plan_review"],
            json.dumps(part_source, ensure_ascii=False, indent=2),
            PLAN_REVIEW_SCHEMA,
            lambda value: _validate_plan_review(value, source, plan),
        )
        _write(part_path, {"generation": part_generation, "result": response})
        return index, response

    reviews_by_index: dict[int, dict[str, Any]] = {}
    # Small independent requests prevent one long-thinking response from
    # consuming the entire output budget.  Four workers preserve provider
    # headroom while keeping a 205-sermon operation practical.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(review_one, pair): pair[0]
            for pair in enumerate(discovery["candidate_plans"])
        }
        for future in as_completed(futures):
            index, response = future.result()
            reviews_by_index[index] = response

    final_plans: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []
    human_items: list[dict[str, Any]] = []
    for index, plan in enumerate(discovery["candidate_plans"]):
        review = reviews_by_index[index]
        record: dict[str, Any] = {
            "plan_index": index,
            "plan_key": stable_plan_key(plan),
            "openai_plan": plan,
            "claude_review": review,
        }
        if review["decision"] == "approve":
            record["consensus"] = "approved_original"
            final_plans.append(plan)
            review_records.append(record)
            continue
        adjudication_input = {
            "source": _plan_review_input(source, plan),
            "openai_plan": plan,
            "claude_review": review,
        }
        adjudication = _generate_valid(
            openai_client,
            prompts["plan_adjudication"],
            json.dumps(adjudication_input, ensure_ascii=False, indent=2),
            PLAN_ADJUDICATION_SCHEMA,
            lambda value: None if value.get("scope_confirmation") == SCOPE
            else (_ for _ in ()).throw(ValueError("scope")),
        )
        record["openai_adjudication"] = adjudication
        if adjudication["decision"] == "accept_claude":
            record["consensus"] = "accepted_claude_replacement"
            final_plans.extend(review["replacement_plans"])
            review_records.append(record)
            continue
        reconsideration_input = dict(adjudication_input)
        reconsideration_input["openai_adjudication"] = adjudication
        reconsideration = _generate_valid(
            claude_client,
            prompts["plan_reconsideration"],
            json.dumps(reconsideration_input, ensure_ascii=False, indent=2),
            PLAN_RECONSIDERATION_SCHEMA,
            lambda value: None if value.get("scope_confirmation") == SCOPE
            else (_ for _ in ()).throw(ValueError("scope")),
        )
        record["claude_reconsideration"] = reconsideration
        if reconsideration["decision"] == "accept_openai":
            record["consensus"] = "approved_original_after_reconsideration"
        else:
            record["consensus"] = "human_review_required"
            human_items.append(record)
        # These are still candidate plans, never published conclusions.  Keep
        # the original visible in the review workbench while flagging the
        # unresolved disagreement instead of hiding the entire batch.
        final_plans.append(plan)
        review_records.append(record)

    final = {
        "scope_confirmation": SCOPE,
        "candidate_plans": final_plans,
        "unassigned_claim_ids": discovery.get("unassigned_claim_ids") or [],
        "summary": discovery.get("summary") or "",
    }
    _write(
        output_dir / "independent-review.json",
        {"generation": generation, "result": review_records},
    )

    validate_candidates(final, source)
    batch_id = str((knowledge.get("batch") or {}).get("batch_id") or "RB-UNKNOWN")
    package = build_incremental_package(
        batch_id=batch_id,
        reviewed_payload=final,
        canonical_topics=current.get("topic_nodes") or [],
    )
    result = {
        "schema_version": "wang_candidate_projection_consensus_v1",
        "generation": generation,
        "status": "human_review_required" if human_items else "ai_consensus",
        "final": final,
        "plan_reviews": review_records,
        "human_review_items": human_items,
    }
    _write(final_path, result)
    _write(package_path, package)
    ingest = store.ingest_package(
        package, source_kind="reviewed_candidate_projection", apply=apply,
        metadata={"input_path": str(package_path)},
    )
    return {
        "status": result["status"], "human_review_count": len(human_items),
        "ingest": ingest, "output": str(final_path), "package": str(package_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    parser.add_argument("--database-url")
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    result = run(
        knowledge_path=args.batch_root / "merged" / "research-batch-knowledge.json",
        relations_path=args.batch_root / "cross-sermon-relations" / "reviewed-relations.json",
        output_dir=args.batch_root / "candidate-projection",
        store=PostgresKnowledgeStore(args.database_url),
        openai_client=Stage1OpenAIClient(
            model=args.openai_model, reasoning_effort=args.openai_reasoning_effort,
            timeout_seconds=360, max_retries=3, max_output_tokens=20000,
        ),
        claude_client=Stage1AnthropicClient(
            model=args.claude_model, timeout_seconds=360, max_retries=3,
            max_output_tokens=20000,
        ),
        apply=args.apply,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
