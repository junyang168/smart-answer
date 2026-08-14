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
    pending_topic_identity_ids,
    resolve_topic_identity_package,
    stable_family_key,
    validate_discovery,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_ROOT = Path("output/claim-layer/research-batches/RB-COVENANT-LAW-CORE-NINE-01")
PROMPT_DIR = Path("backend/pipeline/prompts")
RUNNER_SCHEMA_VERSION = "wang_topic_structure_runner_v2"


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fingerprint(value: Any) -> str:
    return _sha(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def _generation_inputs_match(cached: dict[str, Any], current: dict[str, Any]) -> bool:
    """Allow a reviewed result to migrate formats without calling either model again."""
    keys = {
        "source_sha256", "prompt_sha256", "openai_model", "claude_model",
        "openai_reasoning_effort", "response_schema_sha256",
    }
    return all(cached.get(key) == current.get(key) for key in keys)


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


def _render_report(result: dict[str, Any], package: dict[str, Any] | None = None) -> str:
    final = result["final"]
    reconciliations = (package or {}).get("topic_identity_reconciliations") or []
    pending = [
        row for row in reconciliations
        if row.get("status") in {"pending_match", "pending_new"}
    ]
    lines = [
        "# 母题—子专题—篇章段落候选报告", "",
        "> 本报告是从共享主张图自动发现、经双模型复核的编辑候选，不是教授原话，也不是已批准目录。", "",
        f"- 状态：`{result['status']}`",
        f"- 母题：{len(final['topic_families'])} 个",
        f"- 尚未归组主张：{len(final.get('unassigned_claim_ids') or [])} 条",
        f"- 需人工处理：{len(result.get('human_review_items') or [])} 项",
        f"- 待确认主题身份：{len(pending)} 项", "",
    ]
    if pending:
        lines.extend([
            "## 主题身分待确认", "",
            "以下主题与库中既有主题共用主张，但名称不同。系统不会自动合并；请人工判断是否为同一主题。", "",
        ])
        for row in pending:
            best = (row.get("candidate_matches") or [{}])[0]
            if best:
                lines.append(
                    f"- **{row['label']}**（候选 `{row['candidate_topic_id']}`）↔ "
                    f"`{best.get('existing_topic_id')}`（{best.get('existing_label')}）："
                    f"共用 {best.get('shared_claim_count')} 条主张，Jaccard {best.get('jaccard')}"
                )
            else:
                lines.append(
                    f"- **{row['label']}**（候选 `{row['candidate_topic_id']}`）："
                    "库中没有明确匹配，需确认是否建立新主题。"
                )
        lines.append("")
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


def _require_scope(response: dict[str, Any]) -> None:
    if response.get("scope_confirmation") != SCOPE:
        raise ValueError("scope not confirmed")


def existing_topic_index(store: PostgresKnowledgeStore | None) -> dict[str, dict[str, Any]]:
    """Read the topics already in the store, with the claims currently routed to them.

    Without this the run cannot tell a genuinely new subject from one that was
    already named in an earlier batch, and would silently fork the taxonomy.
    """
    if store is None:
        return {}
    package = store.compile_package()
    index: dict[str, dict[str, Any]] = {
        str(row["topic_id"]): {
            "label": row.get("label"),
            "parent_topic_id": row.get("parent_topic_id"),
            "topic_level": row.get("topic_level")
                or ("subtopic" if row.get("parent_topic_id") else "family"),
            "claim_ids": set(),
        }
        for row in package.get("topic_nodes") or []
        if row.get("topic_id")
    }
    for route in package.get("knowledge_routes") or []:
        claim_id = str(route.get("claim_id") or "")
        if not claim_id:
            continue
        for topic_id in route.get("canonical_topic_ids") or []:
            entry = index.get(str(topic_id))
            if entry is not None:
                entry["claim_ids"].add(claim_id)
    return {
        topic_id: {
            "label": row["label"],
            "parent_topic_id": row["parent_topic_id"],
            "topic_level": row["topic_level"],
            "claim_ids": sorted(row["claim_ids"]),
        }
        for topic_id, row in index.items()
    }


def _reconciliation_only_package(package: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "wang_topic_identity_reconciliation_queue_v1",
        "package_id": f"{package['package_id']}-IDENTITY-QUEUE",
        "topic_identity_reconciliations": package.get("topic_identity_reconciliations") or [],
    }


def _apply_topic_package(
    *, store: PostgresKnowledgeStore, package: dict[str, Any], package_path: Path,
    output_dir: Path, identity_resolutions: dict[str, Any] | None,
) -> dict[str, Any]:
    """Persist the review queue first; write canonical records only after resolution."""
    queue_ingest = store.ingest_package(
        _reconciliation_only_package(package),
        source_kind="topic_identity_reconciliation_queue",
        apply=True,
        metadata={"input_path": str(package_path)},
    )
    pending = pending_topic_identity_ids(package)
    if pending and not identity_resolutions:
        return {
            "status": "identity_review_required",
            "pending_identity_candidates": pending,
            "queue_ingest": queue_ingest,
            "canonical_ingest": None,
        }
    decisions = (identity_resolutions or {}).get("resolutions", identity_resolutions or {})
    resolved = resolve_topic_identity_package(package, decisions)
    resolved_path = output_dir / "canonical-write-package.json"
    _write(resolved_path, resolved)
    canonical_ingest = store.ingest_package(
        resolved,
        source_kind="resolved_topic_structure",
        apply=True,
        metadata={"input_path": str(resolved_path), "candidate_package": str(package_path)},
    )
    return {
        "status": "canonical_applied",
        "pending_identity_candidates": [],
        "queue_ingest": queue_ingest,
        "canonical_ingest": canonical_ingest,
        "canonical_package": str(resolved_path),
    }


def _generate_valid(client: Any, prompt: str, source: dict[str, Any], schema: dict[str, Any], validator: Any) -> dict[str, Any]:
    original = json.dumps(source, ensure_ascii=False, indent=2)
    feedback = ""
    last_error: Exception | None = None
    for _ in range(3):
        response = client.generate_json(prompt, feedback, schema, cache_prefix=original)
        try:
            validator(response)
            return response
        except ValueError as exc:
            last_error = exc
            feedback = "\n\n上一版未通过机械验证：" + str(exc) + "。请重新输出完整 JSON。"
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
    identity_resolutions_path: Path | None = None,
) -> dict[str, Any]:
    knowledge = json.loads(knowledge_path.read_text(encoding="utf-8"))
    source = discovery_input(knowledge)
    store = PostgresKnowledgeStore(database_url) if (apply or database_url) else None
    existing_topics = existing_topic_index(store)
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
    identity_resolutions = (
        json.loads(identity_resolutions_path.read_text(encoding="utf-8"))
        if identity_resolutions_path else None
    )
    if not force and final_path.is_file() and package_path.is_file():
        cached = json.loads(final_path.read_text(encoding="utf-8"))
        cached_package = json.loads(package_path.read_text(encoding="utf-8"))
        # v1/v2 packages wrote discovered candidates directly into canonical
        # collections.  The reviewed editorial result is still reusable; only
        # rebuild its persistence projection under the v3 identity rules.
        if (
            cached_package.get("schema_version") != "wang_topic_structure_incremental_v3"
            and _generation_inputs_match(cached.get("generation") or {}, generation)
        ):
            validate_discovery(cached["final"], source)
            batch_id = str((knowledge.get("batch") or {}).get("batch_id") or "RB-UNKNOWN")
            cached_package = build_incremental_package(
                batch_id=batch_id,
                reviewed_payload=cached["final"],
                existing_topics=existing_topics,
            )
            cached["generation"] = generation
            _write(final_path, cached)
            _write(package_path, cached_package)
        if (cached.get("generation") or {}).get("fingerprint_sha256") == generation["fingerprint_sha256"]:
            (output_dir / "topic-structure-report.md").write_text(
                _render_report(cached, cached_package),
                encoding="utf-8",
            )
            ingest = None
            if apply:
                ingest = _apply_topic_package(
                    store=store, package=cached_package, package_path=package_path,
                    output_dir=output_dir, identity_resolutions=identity_resolutions,
                )
            return {
                "status": ingest["status"] if ingest else "cached",
                "output": str(final_path), "package": str(package_path), "ingest": ingest,
            }

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
                _require_scope,
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
                    _require_scope,
                )
                record["claude_reconsideration"] = reconsideration
                if reconsideration["decision"] == "accept_openai":
                    record["consensus"] = "approved_original_after_reconsideration"
                else:
                    record["consensus"] = "human_review_required"
                    human_items.append(record)
                final_families.append(family)
        records.append(record)

    # Persist the reviews before the global check.  A late validation failure
    # would otherwise discard every model call made in this run.
    _write(output_dir / "independent-review.json", {"generation": generation, "result": records})
    final = {
        "scope_confirmation": SCOPE,
        "topic_families": final_families,
        "unassigned_claim_ids": discovery.get("unassigned_claim_ids") or [],
        "summary": discovery.get("summary") or "",
    }
    validate_discovery(final, source)
    batch_id = str((knowledge.get("batch") or {}).get("batch_id") or "RB-UNKNOWN")
    package = build_incremental_package(
        batch_id=batch_id, reviewed_payload=final, existing_topics=existing_topics
    )
    pending_identities = pending_topic_identity_ids(package)
    result = {
        "schema_version": "wang_topic_structure_consensus_v1",
        "generation": generation,
        "status": "human_review_required" if human_items else "ai_consensus",
        "final": final,
        "family_reviews": records,
        "human_review_items": human_items,
    }
    _write(final_path, result)
    _write(package_path, package)
    (output_dir / "topic-structure-report.md").write_text(
        _render_report(result, package), encoding="utf-8"
    )
    ingest = None
    if apply:
        ingest = _apply_topic_package(
            store=store, package=package, package_path=package_path,
            output_dir=output_dir, identity_resolutions=identity_resolutions,
        )
    return {
        "status": ingest["status"] if ingest else result["status"],
        "family_count": len(final_families),
        "human_review_count": len(human_items),
        "pending_identity_candidates": pending_identities,
        "output": str(final_path),
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
    parser.add_argument("--identity-resolutions", type=Path)
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
        identity_resolutions_path=args.identity_resolutions,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
