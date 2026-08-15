"""Artifact-backed multi-agent authoring runner for Matthew exposition articles.

This domain runner is intentionally separate from the retired API-level multi-agent
state machine. It writes staging artifacts only and never publishes a manuscript.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from backend.pipeline.matthew_exposition_authoring import (
    ADJUDICATION_SCHEMA,
    AUTHOR_RESULT_SCHEMA,
    EDITORIAL_REVIEW_SCHEMA,
    RECONSIDERATION_SCHEMA,
    REVISION_SCHEMA,
    AuthoringContractError,
    build_authoring_packet,
    canonical_json,
    deterministic_writing_warnings,
    generation_fingerprint,
    sha256_text,
    validate_author_result,
    validate_editorial_review,
    validate_revision_result,
    validate_strict_schema,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPTS = {
    "author": PROMPT_DIR / "matthew_exposition_author.md",
    "review": PROMPT_DIR / "matthew_exposition_independent_editorial_review.md",
    "adjudication": PROMPT_DIR / "matthew_exposition_editorial_adjudication.md",
    "reconsideration": PROMPT_DIR / "matthew_exposition_editorial_reconsideration.md",
    "revision": PROMPT_DIR / "matthew_exposition_author_revision.md",
}


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_prompt(name: str) -> str:
    return PROMPTS[name].read_text(encoding="utf-8")


def _client_generation_parameters(client: Any) -> dict[str, Any]:
    return {
        "max_output_tokens": getattr(client, "max_output_tokens", None),
        "timeout_seconds": getattr(client, "timeout_seconds", None),
        "temperature": 0.0,
    }


def _archive(path: Path) -> None:
    if not path.exists():
        return
    archive_dir = path.parent / "generations"
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path.replace(archive_dir / f"{path.stem}.{stamp}{path.suffix}")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run_cached_stage(
    *,
    path: Path,
    schema_version: str,
    fingerprint: str,
    producer: dict[str, Any],
    generate: Callable[[], dict[str, Any]],
    force: bool,
) -> tuple[dict[str, Any], bool]:
    if path.is_file() and not force:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("generation", {}).get("fingerprint") == fingerprint:
            return existing["result"], True
    _archive(path)
    result = generate()
    _write_json(
        path,
        {
            "schema_version": schema_version,
            "generation": {
                "fingerprint": fingerprint,
                "generated_at": _utcnow(),
                **producer,
            },
            "result": result,
        },
    )
    return result, False


def _canonicalize_findings(review: dict[str, Any], draft_sha: str) -> None:
    for index, finding in enumerate(review.get("findings", []), start=1):
        finding["finding_id"] = f"ERF-{draft_sha[:10]}-{index:03d}"


def _validate_exact_ids(items: list[dict[str, Any]], expected: set[str], field: str) -> None:
    received = [item.get("finding_id") for item in items]
    if len(received) != len(set(received)) or set(received) != expected:
        raise AuthoringContractError(
            f"{field} must cover each finding exactly once; expected={sorted(expected)}, received={received}"
        )


def run_authoring(
    *,
    plan_path: Path,
    knowledge_path: Path,
    contract_path: Path,
    publication_profile_path: Path,
    quality_profile_path: Path,
    output_dir: Path,
    openai_client: Any,
    claude_client: Any,
    force: bool = False,
) -> dict[str, Any]:
    packet = build_authoring_packet(
        plan_path=plan_path,
        knowledge_path=knowledge_path,
        contract_path=contract_path,
        publication_profile_path=publication_profile_path,
        quality_profile_path=quality_profile_path,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    packet_generation = {
        "fingerprint": packet["packet_sha256"],
        "generated_at": _utcnow(),
        "role": "packet_builder",
        "provider": "deterministic",
    }
    _write_json(
        output_dir / "authoring-packet.json",
        {
            "schema_version": "matthew-exposition-authoring-packet.v1",
            "generation": packet_generation,
            "result": packet,
        },
    )
    _write_json(
        output_dir / "base-manuscript-contract.json",
        {
            "schema_version": "matthew-exposition-base-contract-copy.v1",
            "generation": packet_generation,
            "result": packet["base_contract"],
        },
    )
    packet_text = canonical_json(packet)
    packet_sha = packet["packet_sha256"]

    author_prompt = _read_prompt("author")
    author_fingerprint = generation_fingerprint(
        inputs={
            "packet_sha256": packet_sha,
            "generation_parameters": _client_generation_parameters(openai_client),
        },
        prompt_text=author_prompt,
        schema=AUTHOR_RESULT_SCHEMA,
        model=openai_client.model,
        reasoning=getattr(openai_client, "reasoning_effort", "unknown"),
    )
    author_result, author_cached = _run_cached_stage(
        path=output_dir / "authoring.json",
        schema_version="matthew-exposition-authoring.v1",
        fingerprint=author_fingerprint,
        producer={"role": "author", "provider": "openai", "model": openai_client.model},
        generate=lambda: openai_client.generate_json(author_prompt, packet_text, AUTHOR_RESULT_SCHEMA),
        force=force,
    )
    validate_strict_schema(author_result, AUTHOR_RESULT_SCHEMA)
    valid_claim_ids = {
        item["claim_id"]
        for item in packet["knowledge"].get("claims", [])
        if isinstance(item, dict) and item.get("claim_id")
    }
    validate_author_result(
        author_result,
        contract=packet["base_contract"],
        plan=packet["plan"],
        valid_claim_ids=valid_claim_ids,
    )
    if author_result["status"] == "plan_change_required":
        return {
            "status": "plan_change_required",
            "authoring_path": str(output_dir / "authoring.json"),
            "author_cached": author_cached,
        }

    draft = author_result["manuscript_markdown"]
    (output_dir / "draft.md").write_text(draft, encoding="utf-8")
    draft_sha = sha256_text(draft)
    review_prompt = _read_prompt("review")
    review_input = canonical_json({"packet": packet, "author_result": author_result})
    review_fingerprint = generation_fingerprint(
        inputs={
            "packet_sha256": packet_sha,
            "draft_sha256": draft_sha,
            "generation_parameters": _client_generation_parameters(claude_client),
        },
        prompt_text=review_prompt,
        schema=EDITORIAL_REVIEW_SCHEMA,
        model=claude_client.model,
        reasoning="independent_review",
    )
    review, review_cached = _run_cached_stage(
        path=output_dir / "independent-editorial-review.json",
        schema_version="matthew-exposition-editorial-review.v1",
        fingerprint=review_fingerprint,
        producer={"role": "independent_editor", "provider": "anthropic", "model": claude_client.model},
        generate=lambda: claude_client.generate_json(review_prompt, review_input, EDITORIAL_REVIEW_SCHEMA),
        force=force,
    )
    validate_strict_schema(review, EDITORIAL_REVIEW_SCHEMA)
    _canonicalize_findings(review, draft_sha)
    review_outcome = validate_editorial_review(
        review,
        contract=packet["base_contract"],
        manuscript=draft,
        quality_profile=packet["quality_profile"],
    )
    writing_warnings = deterministic_writing_warnings(
        draft, packet["quality_profile"]
    )
    # Persist canonical finding IDs and deterministic checks even on a cache hit.
    review_artifact = json.loads((output_dir / "independent-editorial-review.json").read_text(encoding="utf-8"))
    review_artifact["result"] = review
    review_artifact["checks"] = {
        "deterministic_warnings": writing_warnings,
        "rubric_outcome": review_outcome,
    }
    _write_json(output_dir / "independent-editorial-review.json", review_artifact)

    findings = review.get("findings", [])
    if not review_outcome["passed"] and not findings:
        raise AuthoringContractError(
            "a failing editorial review must include at least one actionable finding"
        )
    if not findings and review_outcome["passed"]:
        return {
            "status": "editorial_pass_no_revision",
            "draft_path": str(output_dir / "draft.md"),
            "review_path": str(output_dir / "independent-editorial-review.json"),
            "author_cached": author_cached,
            "review_cached": review_cached,
        }

    finding_ids = {item["finding_id"] for item in findings}
    adjudication_prompt = _read_prompt("adjudication")
    adjudication_input = canonical_json(
        {
            "packet_sha256": packet_sha,
            "draft": draft,
            "review": review,
            "rubric_outcome": review_outcome,
            "deterministic_warnings": writing_warnings,
        }
    )
    adjudication_fingerprint = generation_fingerprint(
        inputs={
            "adjudication_input_sha256": sha256_text(adjudication_input),
            "generation_parameters": _client_generation_parameters(openai_client),
        },
        prompt_text=adjudication_prompt,
        schema=ADJUDICATION_SCHEMA,
        model=openai_client.model,
        reasoning=getattr(openai_client, "reasoning_effort", "unknown"),
    )
    adjudication, _ = _run_cached_stage(
        path=output_dir / "editorial-adjudication.json",
        schema_version="matthew-exposition-editorial-adjudication.v1",
        fingerprint=adjudication_fingerprint,
        producer={"role": "adjudicator", "provider": "openai", "model": openai_client.model},
        generate=lambda: openai_client.generate_json(
            adjudication_prompt, adjudication_input, ADJUDICATION_SCHEMA
        ),
        force=force,
    )
    validate_strict_schema(adjudication, ADJUDICATION_SCHEMA)
    _validate_exact_ids(adjudication["adjudications"], finding_ids, "adjudication")
    rejected_ids = {
        item["finding_id"] for item in adjudication["adjudications"] if item["decision"] == "reject"
    }
    maintained_ids: set[str] = set()
    withdrawn_ids: set[str] = set()
    if rejected_ids:
        reconsideration_prompt = _read_prompt("reconsideration")
        reconsideration_input = canonical_json(
            {"review": review, "adjudication": adjudication, "rejected_finding_ids": sorted(rejected_ids)}
        )
        reconsideration_fingerprint = generation_fingerprint(
            inputs={
                "reconsideration_input_sha256": sha256_text(reconsideration_input),
                "generation_parameters": _client_generation_parameters(claude_client),
            },
            prompt_text=reconsideration_prompt,
            schema=RECONSIDERATION_SCHEMA,
            model=claude_client.model,
            reasoning="reconsideration",
        )
        reconsideration, _ = _run_cached_stage(
            path=output_dir / "editorial-reconsideration.json",
            schema_version="matthew-exposition-editorial-reconsideration.v1",
            fingerprint=reconsideration_fingerprint,
            producer={"role": "reviewer_reconsideration", "provider": "anthropic", "model": claude_client.model},
            generate=lambda: claude_client.generate_json(
                reconsideration_prompt, reconsideration_input, RECONSIDERATION_SCHEMA
            ),
            force=force,
        )
        validate_strict_schema(reconsideration, RECONSIDERATION_SCHEMA)
        _validate_exact_ids(reconsideration["reconsiderations"], rejected_ids, "reconsideration")
        maintained_ids = {
            item["finding_id"] for item in reconsideration["reconsiderations"] if item["decision"] == "maintain"
        }
        withdrawn_ids = rejected_ids - maintained_ids

    accepted_ids = {
        item["finding_id"] for item in adjudication["adjudications"] if item["decision"] == "accept"
    }
    accepted_findings = [item for item in findings if item["finding_id"] in accepted_ids]
    consensus = {
        "schema_version": "matthew-exposition-reviewed-findings.v1",
        "accepted_finding_ids": sorted(accepted_ids),
        "withdrawn_finding_ids": sorted(withdrawn_ids),
        "human_required_finding_ids": sorted(maintained_ids),
    }
    _write_json(output_dir / "reviewed-editorial-findings.json", consensus)
    if maintained_ids:
        return {
            "status": "human_review_required",
            "human_required_finding_ids": sorted(maintained_ids),
            "consensus_path": str(output_dir / "reviewed-editorial-findings.json"),
        }
    if not accepted_findings and not review_outcome["passed"]:
        return {
            "status": "human_review_required",
            "reason": "rubric failed but no revision finding survived consensus",
            "consensus_path": str(output_dir / "reviewed-editorial-findings.json"),
        }
    if not accepted_findings:
        return {"status": "editorial_pass_after_adjudication", "draft_path": str(output_dir / "draft.md")}

    revision_prompt = _read_prompt("revision")
    revision_input = canonical_json(
        {"packet": packet, "draft": draft, "accepted_findings": accepted_findings}
    )
    revision_fingerprint = generation_fingerprint(
        inputs={
            "revision_input_sha256": sha256_text(revision_input),
            "generation_parameters": _client_generation_parameters(openai_client),
        },
        prompt_text=revision_prompt,
        schema=REVISION_SCHEMA,
        model=openai_client.model,
        reasoning=getattr(openai_client, "reasoning_effort", "unknown"),
    )
    revision, _ = _run_cached_stage(
        path=output_dir / "revision-01.json",
        schema_version="matthew-exposition-author-revision.v1",
        fingerprint=revision_fingerprint,
        producer={"role": "revision_author", "provider": "openai", "model": openai_client.model},
        generate=lambda: openai_client.generate_json(revision_prompt, revision_input, REVISION_SCHEMA),
        force=force,
    )
    validate_revision_result(
        revision,
        contract=packet["base_contract"],
        plan=packet["plan"],
        valid_claim_ids=valid_claim_ids,
    )
    dispositions = revision.get("finding_dispositions", [])
    _validate_exact_ids(dispositions, accepted_ids, "revision dispositions")
    blocking_ids = {item["finding_id"] for item in accepted_findings if item["blocking"]}
    deferred_blocking = {
        item["finding_id"] for item in dispositions
        if item["status"] == "deferred" and item["finding_id"] in blocking_ids
    }
    if deferred_blocking:
        raise AuthoringContractError(
            f"blocking findings cannot be deferred: {sorted(deferred_blocking)}"
        )
    if revision["status"] == "plan_change_required":
        return {"status": "plan_change_required_after_review", "revision_path": str(output_dir / "revision-01.json")}
    revised_draft = revision.get("manuscript_markdown", "")
    if not revised_draft.strip():
        raise AuthoringContractError("revision must return the complete manuscript")
    (output_dir / "revised-draft.md").write_text(revised_draft, encoding="utf-8")
    return {
        "status": "revised_requires_reaudit",
        "revised_draft_path": str(output_dir / "revised-draft.md"),
        "deterministic_warnings": deterministic_writing_warnings(
            revised_draft, packet["quality_profile"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--knowledge", type=Path, required=True)
    parser.add_argument("--base-contract", type=Path, required=True)
    parser.add_argument("--publication-profile", type=Path, required=True)
    parser.add_argument("--quality-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    packet = build_authoring_packet(
        plan_path=args.plan,
        knowledge_path=args.knowledge,
        contract_path=args.base_contract,
        publication_profile_path=args.publication_profile,
        quality_profile_path=args.quality_profile,
    )
    if args.dry_run:
        print(
            json.dumps(
                {
                    "status": "inputs_valid",
                    "packet_sha256": packet["packet_sha256"],
                    "would_call_models": False,
                    "would_publish": False,
                },
                ensure_ascii=False,
            )
        )
        return 0

    load_dotenv(PROJECT_ROOT / ".env")
    result = run_authoring(
        plan_path=args.plan,
        knowledge_path=args.knowledge,
        contract_path=args.base_contract,
        publication_profile_path=args.publication_profile,
        quality_profile_path=args.quality_profile,
        output_dir=args.output_dir,
        openai_client=Stage1OpenAIClient(
            model=args.openai_model,
            reasoning_effort=args.openai_reasoning_effort,
            timeout_seconds=args.timeout_seconds,
        ),
        claude_client=Stage1AnthropicClient(
            model=args.claude_model,
            timeout_seconds=args.timeout_seconds,
        ),
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    terminal_success = {"editorial_pass_no_revision", "editorial_pass_after_adjudication"}
    return 0 if result["status"] in terminal_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
