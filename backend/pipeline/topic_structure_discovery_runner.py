"""Discover and independently review topic-family composition candidates."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient
from backend.pipeline.topic_structure_discovery import (
    ADJUDICATION_SCHEMA,
    DISCOVERY_SCHEMA,
    RECONSIDERATION_SCHEMA,
    REVIEW_SCHEMA,
    SCOPE,
    build_incremental_package,
    discovery_input,
    family_claim_ids,
    stable_family_key,
    validate_discovery,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_ROOT = Path("output/claim-layer/research-batches/RB-COVENANT-LAW-CORE-NINE-01")
PROMPT_DIR = Path("backend/pipeline/prompts")
RUNNER_SCHEMA_VERSION = "wang_topic_structure_runner_v1"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fingerprint(value: Any) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _archive(path: Path) -> None:
    if not path.is_file():
        return
    target_dir = path.parent / "generations"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{path.stem}.{_sha(path.read_bytes())[:12]}{path.suffix}"
    if not target.exists():
        shutil.copy2(path, target)


def _write(path: Path, payload: dict[str, Any]) -> None:
    _archive(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _render_report(result: dict[str, Any]) -> str:
    final = result["final"]
    lines = [
        "# 母题—子专题—篇章段落候选报告", "",
        "> 本报告是从共享主张图自动发现、经双模型复核的编辑候选，不是教授原话，也不是已批准目录。", "",
        f"- 状态：`{result['status']}`",
        f"- 母题：{len(final['topic_families'])} 个",
        f"- 尚未归组主张：{len(final.get('unassigned_claim_ids') or [])} 条",
        f"- 需人工处理：{len(result.get('human_review_items') or [])} 项", "",
    ]
    review_by_key = {row["family_key"]: row for row in result.get("family_reviews") or []}
    for family_index, family in enumerate(final["topic_families"], start=1):
        review = review_by_key.get(stable_family_key(family))
        lines.extend([
            f"## {family_index}. {family['title']}", "",
            f"**统摄问题：** {family['organizing_question']}", "",
            f"**编排理由：** {family['editorial_rationale']}", "",
            f"**主张数量：** {len(family_claim_ids(family))}", "",
        ])
        if review:
            lines.extend([
                f"**双模型结果：** `{review['consensus']}`", "",
                f"**Claude 复核理由：** {review['claude_review']['reason']}", "",
            ])
        for subtopic_index, subtopic in enumerate(family["subtopics"], start=1):
            lines.extend([
                f"### {family_index}.{subtopic_index} {subtopic['title']}", "",
                f"**中心问题：** {subtopic['central_question']}", "",
                f"**编排理由：** {subtopic['editorial_rationale']}", "",
            ])
            for section_index, section in enumerate(subtopic["sections"], start=1):
                lines.append(
                    f"{section_index}. **{section['title']}**（`{section['role']}`，"
                    f"{len(section['claim_ids'])} 条主张）— {section['purpose']}"
                )
            lines.append("")
    if final.get("unassigned_claim_ids"):
        lines.extend(["## 尚未归组", "", "、".join(final["unassigned_claim_ids"]), ""])
    return "\n".join(lines).rstrip() + "\n"


def _generate_valid(client: Any, prompt: str, source: dict[str, Any], schema: dict[str, Any], validator: Any) -> dict[str, Any]:
    original = json.dumps(source, ensure_ascii=False, indent=2)
    current = original
    last_error: Exception | None = None
    for _ in range(3):
        response = client.generate_json(prompt, current, schema)
        try:
            validator(response)
            return response
        except ValueError as exc:
            last_error = exc
            current = original + "\n\n上一版未通过机械验证：" + str(exc) + "。请重新输出完整 JSON。"
    raise ValueError(f"topic structure output remained invalid: {last_error}")


def _family_source(source: dict[str, Any], family: dict[str, Any]) -> dict[str, Any]:
    ids = family_claim_ids(family)
    return {
        "scope": SCOPE,
        "candidate_family": family,
        "claims": [row for row in source["claims"] if row["claim_id"] in ids],
        "claim_relations": [
            row for row in source.get("claim_relations") or []
            if row.get("source_claim_id") in ids or row.get("target_claim_id") in ids
        ],
        "policy": source["policy"],
    }


def _validate_review(response: dict[str, Any], source: dict[str, Any], family: dict[str, Any]) -> None:
    if response.get("scope_confirmation") != SCOPE:
        raise ValueError("review scope not confirmed")
    replacements = response.get("replacement_families") or []
    if response.get("decision") == "approve":
        if replacements:
            raise ValueError("approved family cannot contain replacements")
        return
    if response.get("decision") != "replace" or not replacements:
        raise ValueError("replace requires replacement families")
    original_ids = family_claim_ids(family)
    local_source = dict(source)
    local_source["claims"] = [row for row in source["claims"] if row["claim_id"] in original_ids]
    candidate = {
        "scope_confirmation": SCOPE,
        "topic_families": replacements,
        "unassigned_claim_ids": [],
        "summary": response.get("reason") or "",
    }
    validate_discovery(candidate, local_source)
    replacement_ids = set().union(*(family_claim_ids(row) for row in replacements))
    if replacement_ids != original_ids:
        raise ValueError("replacement families must preserve the exact claim set")


def run(
    *, knowledge_path: Path, output_dir: Path,
    openai_client: Stage1OpenAIClient, claude_client: Stage1AnthropicClient,
    force: bool = False, apply: bool = False, database_url: str | None = None,
) -> dict[str, Any]:
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    source = discovery_input(knowledge)
    prompts = {
        name: (PROMPT_DIR / f"topic_structure_{name}.md").read_text(encoding="utf-8")
        for name in ("discovery", "family_review", "family_adjudication", "family_reconsideration")
    }
    generation = {
        "schema_version": RUNNER_SCHEMA_VERSION,
        "source_sha256": _fingerprint(source),
        "prompt_sha256": {name: _sha(value.encode("utf-8")) for name, value in prompts.items()},
        "openai_model": openai_client.model,
        "claude_model": claude_client.model,
        "openai_reasoning_effort": getattr(openai_client, "reasoning_effort", None),
        "response_schema_sha256": _fingerprint({
            "discovery": DISCOVERY_SCHEMA, "review": REVIEW_SCHEMA,
            "adjudication": ADJUDICATION_SCHEMA, "reconsideration": RECONSIDERATION_SCHEMA,
        }),
    }
    generation["fingerprint_sha256"] = _fingerprint(generation)
    final_path = output_dir / "reviewed-topic-structure.json"
    package_path = output_dir / "candidate-package.json"
    if not force and final_path.is_file() and package_path.is_file():
        cached = json.loads(final_path.read_text(encoding="utf-8"))
        if (cached.get("generation") or {}).get("fingerprint_sha256") == generation["fingerprint_sha256"]:
            (output_dir / "topic-structure-report.md").write_text(_render_report(cached), encoding="utf-8")
            return {"status": "cached", "output": str(final_path), "package": str(package_path)}

    discovery = _generate_valid(
        openai_client, prompts["discovery"], source, DISCOVERY_SCHEMA,
        lambda value: validate_discovery(value, source),
    )
    _write(output_dir / "discovery.json", {"generation": generation, "result": discovery})

    def review_one(index_family: tuple[int, dict[str, Any]]) -> tuple[int, dict[str, Any]]:
        index, family = index_family
        response = _generate_valid(
            claude_client, prompts["family_review"], _family_source(source, family), REVIEW_SCHEMA,
            lambda value: _validate_review(value, source, family),
        )
        return index, response

    reviews: dict[int, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(review_one, pair) for pair in enumerate(discovery["topic_families"])]
        for future in as_completed(futures):
            index, response = future.result()
            reviews[index] = response

    final_families: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    human_items: list[dict[str, Any]] = []
    for index, family in enumerate(discovery["topic_families"]):
        review = reviews[index]
        record: dict[str, Any] = {
            "family_key": stable_family_key(family), "openai_family": family, "claude_review": review,
        }
        if review["decision"] == "approve":
            record["consensus"] = "approved_original"
            final_families.append(family)
        else:
            adjudication_source = {
                "source": _family_source(source, family), "openai_family": family, "claude_review": review,
            }
            adjudication = _generate_valid(
                openai_client, prompts["family_adjudication"], adjudication_source,
                ADJUDICATION_SCHEMA,
                lambda value: None if value.get("scope_confirmation") == SCOPE else (_ for _ in ()).throw(ValueError("scope")),
            )
            record["openai_adjudication"] = adjudication
            if adjudication["decision"] == "accept_claude":
                record["consensus"] = "accepted_claude_replacement"
                final_families.extend(review["replacement_families"])
            else:
                reconsideration = _generate_valid(
                    claude_client, prompts["family_reconsideration"],
                    {**adjudication_source, "openai_adjudication": adjudication},
                    RECONSIDERATION_SCHEMA,
                    lambda value: None if value.get("scope_confirmation") == SCOPE else (_ for _ in ()).throw(ValueError("scope")),
                )
                record["claude_reconsideration"] = reconsideration
                if reconsideration["decision"] == "accept_openai":
                    record["consensus"] = "approved_original_after_reconsideration"
                else:
                    record["consensus"] = "human_review_required"
                    human_items.append(record)
                final_families.append(family)
        records.append(record)

    final = {
        "scope_confirmation": SCOPE,
        "topic_families": final_families,
        "unassigned_claim_ids": discovery.get("unassigned_claim_ids") or [],
        "summary": discovery.get("summary") or "",
    }
    validate_discovery(final, source)
    batch_id = str((knowledge.get("batch") or {}).get("batch_id") or "RB-UNKNOWN")
    package = build_incremental_package(batch_id=batch_id, reviewed_payload=final)
    result = {
        "schema_version": "wang_topic_structure_consensus_v1",
        "generation": generation,
        "status": "human_review_required" if human_items else "ai_consensus",
        "final": final,
        "family_reviews": records,
        "human_review_items": human_items,
    }
    _write(output_dir / "independent-review.json", {"generation": generation, "result": records})
    _write(final_path, result)
    _write(package_path, package)
    (output_dir / "topic-structure-report.md").write_text(_render_report(result), encoding="utf-8")
    ingest = None
    if apply:
        ingest = PostgresKnowledgeStore(database_url).ingest_package(
            package, source_kind="reviewed_topic_structure", apply=True,
            metadata={"input_path": str(package_path)},
        )
    return {
        "status": result["status"], "family_count": len(final_families),
        "human_review_count": len(human_items), "output": str(final_path),
        "package": str(package_path), "ingest": ingest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-root", type=Path, default=DEFAULT_BATCH_ROOT)
    parser.add_argument("--knowledge-path", type=Path)
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--database-url")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    knowledge_path = args.knowledge_path or args.batch_root / "integration" / "candidate-shared-knowledge.json"
    result = run(
        knowledge_path=knowledge_path,
        output_dir=args.batch_root / "topic-structure",
        openai_client=Stage1OpenAIClient(
            model=args.openai_model, reasoning_effort=args.openai_reasoning_effort,
            timeout_seconds=360, max_retries=3, max_output_tokens=30000,
        ),
        claude_client=Stage1AnthropicClient(
            model=args.claude_model, timeout_seconds=360, max_retries=3, max_output_tokens=30000,
        ),
        force=args.force, apply=args.apply, database_url=args.database_url,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
