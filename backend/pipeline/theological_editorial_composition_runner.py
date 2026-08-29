"""Compile, compose, and independently review a theological editorial brief."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dotenv import load_dotenv

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.matthew_exposition_authoring import (
    canonical_json,
    generation_fingerprint,
    validate_strict_schema,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient
from backend.pipeline.theological_editorial_synthesis import (
    BRIEF_CANDIDATE_SCHEMA,
    BRIEF_REVISION_SCHEMA,
    BRIEF_REVIEW_SCHEMA,
    brief_candidate_changed_paths,
    compile_approved_editorial_brief,
    compile_theological_evidence_packet,
    validate_brief_review,
    validate_brief_revision,
    validate_editorial_brief_candidate,
    validate_editorial_scope,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
COMPOSITION_PROMPT = PROMPT_DIR / "theological_editorial_composition.md"
REVIEW_PROMPT = PROMPT_DIR / "theological_editorial_brief_review.md"
REVISION_PROMPT = PROMPT_DIR / "theological_editorial_brief_revision.md"
FINAL_REVIEW_PROMPT = PROMPT_DIR / "theological_editorial_brief_final_review.md"
REQUIRED_COLLECTIONS = (
    "viewpoint_structures",
    "viewpoint_structure_revisions",
    "canonical_viewpoints",
    "viewpoint_revisions",
    "viewpoint_claim_links",
    "argument_routes",
    "argument_route_revisions",
    "argument_route_attestations",
    "viewpoint_relations",
    "claims",
    "evidence_steps",
    "source_fragments",
    "source_documents",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _archive(path: Path) -> None:
    if not path.exists():
        return
    archive = path.parent / "generations"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path.replace(archive / f"{path.stem}.{stamp}{path.suffix}")


def _run_cached_stage(
    *,
    path: Path,
    schema_version: str,
    fingerprint: str,
    producer: Mapping[str, Any],
    generate: Callable[[], dict[str, Any]],
    force: bool,
) -> tuple[dict[str, Any], bool]:
    if path.is_file() and not force:
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing.get("generation", {}).get("fingerprint") == fingerprint:
            return dict(existing["result"]), True
    _archive(path)
    result = generate()
    _write_json(
        path,
        {
            "schema_version": schema_version,
            "generation": {
                "fingerprint": fingerprint,
                "generated_at": _utcnow(),
                **dict(producer),
            },
            "result": result,
        },
    )
    return result, False


def _client_parameters(client: Any) -> dict[str, Any]:
    return {
        "backend": getattr(client, "backend", "api"),
        "max_output_tokens": getattr(client, "max_output_tokens", None),
        "timeout_seconds": getattr(client, "timeout_seconds", None),
        "temperature": 0.0,
    }


def _review_packet(
    *,
    evidence_packet: Mapping[str, Any],
    candidate: Mapping[str, Any],
    revision_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Give the reviewer the scoped evidence and complete originals, not the corpus."""

    packet = {
        "schema_version": "wang_theological_editorial_brief_review_packet_v2",
        "scope": evidence_packet["scope"],
        "structure": {
            "structure_revision_id": evidence_packet["structure"]["revision"][
                "structure_revision_id"
            ],
            "central_synthesis": evidence_packet["structure"]["revision"][
                "central_synthesis"
            ],
            "unresolved_items": evidence_packet["structure"]["revision"].get(
                "unresolved_items", []
            ),
        },
        "focal_viewpoints": [
            {
                "structure_role": item["structure_role"],
                "viewpoint_revision_id": item["revision"]["viewpoint_revision_id"],
                "core_proposition": item["revision"]["core_proposition"],
                "modality": item["revision"].get("proposition_signature", {}).get(
                    "modality"
                ),
                "member_claims": [
                    {
                        "claim_id": claim["claim_id"],
                        "statement": claim.get("statement") or claim.get("title"),
                    }
                    for claim in evidence_packet.get("claims", [])
                    if claim.get("claim_id") in set(item["member_claim_ids"])
                ],
                "argument_route_revision_ids": item[
                    "argument_route_revision_ids"
                ],
            }
            for item in evidence_packet.get("focal_viewpoints", [])
        ],
        "argument_routes": [
            {
                "argument_route_revision_id": item["revision"][
                    "argument_route_revision_id"
                ],
                "conclusion_viewpoint_revision_id": item["revision"][
                    "validated_against_conclusion_viewpoint_revision_id"
                ],
                "route_label": item["revision"]["route_label"],
                "ordered_inference_nodes": item["revision"][
                    "ordered_inference_nodes"
                ],
                "full_attestation_count": item["full_attestation_count"],
                "distinct_full_source_count": item[
                    "distinct_full_source_count"
                ],
            }
            for item in evidence_packet.get("argument_routes", [])
        ],
        "source_fragments": evidence_packet.get("source_fragments", []),
        "source_originals": evidence_packet["source_originals"],
        "relations": evidence_packet.get("relations", []),
        "compiler_findings": evidence_packet.get("compiler_findings", []),
        "candidate": candidate,
        "candidate_sha256": sha256_json(dict(candidate)),
        "review_boundary": {
            "include": [
                "editorial_structure",
                "positive_center",
                "material_sufficiency",
                "route_integrity",
                "modality_and_unresolved_items",
            ],
            "exclude": [
                "theological_correctness",
                "external_commentaries",
                "reader_prose",
                "program_audit",
            ],
        },
    }
    if revision_context is not None:
        context = dict(revision_context)
        baseline_candidate = context["baseline_candidate"]
        baseline_review = context["baseline_review"]
        context["deterministic_changed_fields"] = brief_candidate_changed_paths(
            baseline_candidate,
            candidate,
        )
        context["authorized_change_paths_by_finding"] = {
            str(item["finding_id"]): list(item.get("authorized_change_paths") or [])
            for item in baseline_review.get("findings") or []
        }
        packet["revision_context"] = context
    return packet


def run_composition(
    *,
    scope: Mapping[str, Any],
    records: Mapping[str, Sequence[Mapping[str, Any]]],
    output_dir: Path,
    openai_client: Any,
    claude_client: Any,
    force: bool = False,
) -> dict[str, Any]:
    validate_editorial_scope(scope)
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = compile_theological_evidence_packet(scope=scope, records=records)
    _write_json(
        output_dir / "theological-evidence-packet.json",
        {
            "schema_version": "wang_theological_evidence_packet_envelope_v1",
            "generation": {
                "fingerprint": evidence["evidence_packet_sha256"],
                "generated_at": _utcnow(),
                "role": "evidence_compiler",
                "provider": "deterministic",
            },
            "result": evidence,
        },
    )
    if evidence["compiler_readiness"] != "ready_for_composition":
        result = {
            "status": "insufficient_material",
            "stage": "evidence_compile",
            "scope_sha256": scope["scope_sha256"],
            "evidence_packet_sha256": evidence["evidence_packet_sha256"],
            "reason_codes": [
                item["code"]
                for item in evidence["compiler_findings"]
                if item["severity"] == "error"
            ],
        }
        _write_json(output_dir / "workflow-status.json", result)
        return result

    composition_prompt = COMPOSITION_PROMPT.read_text(encoding="utf-8")
    composition_input = canonical_json(evidence)
    composition_fingerprint = generation_fingerprint(
        inputs={
            "evidence_packet_sha256": evidence["evidence_packet_sha256"],
            "generation_parameters": _client_parameters(openai_client),
        },
        prompt_text=composition_prompt,
        schema=BRIEF_CANDIDATE_SCHEMA,
        model=openai_client.model,
        reasoning=getattr(openai_client, "reasoning_effort", "unknown"),
    )

    def generate_candidate() -> dict[str, Any]:
        generated = openai_client.generate_json(
            composition_prompt, composition_input, BRIEF_CANDIDATE_SCHEMA
        )
        try:
            validate_strict_schema(generated, BRIEF_CANDIDATE_SCHEMA)
            validate_editorial_brief_candidate(generated, evidence_packet=evidence)
        except Exception as exc:
            rejected = output_dir / "rejected-generations"
            rejected.mkdir(parents=True, exist_ok=True)
            _write_json(
                rejected / f"brief-candidate.{sha256_json(generated)[:16]}.json",
                {
                    "schema_version": (
                        "wang_theological_editorial_rejected_generation_v1"
                    ),
                    "stage": "composition",
                    "evidence_packet_sha256": evidence[
                        "evidence_packet_sha256"
                    ],
                    "validation_error": f"{type(exc).__name__}: {exc}",
                    "result": generated,
                },
            )
            raise
        return generated

    candidate, candidate_cached = _run_cached_stage(
        path=output_dir / "theological-editorial-brief-candidate.json",
        schema_version="wang_theological_editorial_brief_candidate_envelope_v2",
        fingerprint=composition_fingerprint,
        producer={
            "role": "composition_agent",
            "provider": getattr(openai_client, "backend", "openai_api"),
            "model": openai_client.model,
        },
        generate=generate_candidate,
        force=force,
    )
    validate_editorial_brief_candidate(candidate, evidence_packet=evidence)

    if candidate["status"] != "ready":
        result = {
            "status": candidate["status"],
            "stage": "composition",
            "scope_sha256": scope["scope_sha256"],
            "evidence_packet_sha256": evidence["evidence_packet_sha256"],
            "candidate_sha256": sha256_json(candidate),
            "stop_reasons": candidate["stop_reasons"],
            "candidate_cached": candidate_cached,
        }
        _write_json(output_dir / "workflow-status.json", result)
        return result

    review_packet = _review_packet(evidence_packet=evidence, candidate=candidate)
    _write_json(
        output_dir / "theological-editorial-brief-review-packet.json",
        {
            "schema_version": (
                "wang_theological_editorial_brief_review_packet_envelope_v1"
            ),
            "generation": {
                "fingerprint": sha256_json(review_packet),
                "generated_at": _utcnow(),
                "role": "brief_review_packet_builder",
                "provider": "deterministic",
            },
            "result": review_packet,
        },
    )
    review_prompt = REVIEW_PROMPT.read_text(encoding="utf-8")
    review_input = canonical_json(review_packet)
    review_fingerprint = generation_fingerprint(
        inputs={
            "review_packet_sha256": sha256_json(review_packet),
            "brief_candidate_sha256": sha256_json(candidate),
            "generation_parameters": _client_parameters(claude_client),
        },
        prompt_text=review_prompt,
        schema=BRIEF_REVIEW_SCHEMA,
        model=claude_client.model,
        reasoning="independent_composition_review",
    )

    def generate_review() -> dict[str, Any]:
        generated = claude_client.generate_json(
            review_prompt, review_input, BRIEF_REVIEW_SCHEMA
        )
        try:
            validate_strict_schema(generated, BRIEF_REVIEW_SCHEMA)
            validate_brief_review(generated, candidate=candidate)
        except Exception as exc:
            rejected = output_dir / "rejected-generations"
            rejected.mkdir(parents=True, exist_ok=True)
            _write_json(
                rejected / f"brief-review.{sha256_json(generated)[:16]}.json",
                {
                    "schema_version": (
                        "wang_theological_editorial_rejected_generation_v1"
                    ),
                    "stage": "composition_review",
                    "brief_candidate_sha256": sha256_json(candidate),
                    "validation_error": f"{type(exc).__name__}: {exc}",
                    "result": generated,
                },
            )
            raise
        return generated

    review, review_cached = _run_cached_stage(
        path=output_dir / "theological-editorial-brief-review.json",
        schema_version="wang_theological_editorial_brief_review_envelope_v3",
        fingerprint=review_fingerprint,
        producer={
            "role": "independent_composition_reviewer",
            "provider": getattr(claude_client, "backend", "anthropic_api"),
            "model": claude_client.model,
        },
        generate=generate_review,
        force=force,
    )
    validate_brief_review(review, candidate=candidate)
    if review["decision"] not in {"pass", "changes_required"}:
        result = {
            "status": review["decision"],
            "stage": "composition_review",
            "scope_sha256": scope["scope_sha256"],
            "evidence_packet_sha256": evidence["evidence_packet_sha256"],
            "candidate_sha256": sha256_json(candidate),
            "review_sha256": sha256_json(review),
            "findings": review["findings"],
            "candidate_cached": candidate_cached,
            "review_cached": review_cached,
        }
        _write_json(output_dir / "workflow-status.json", result)
        return result

    effective_candidate = candidate
    effective_review = review
    if review["decision"] == "changes_required":
        revision_prompt = REVISION_PROMPT.read_text(encoding="utf-8")
        revision_input = canonical_json(
            {
                "evidence_packet": evidence,
                "baseline_candidate": candidate,
                "baseline_candidate_sha256": sha256_json(candidate),
                "baseline_review": review,
                "baseline_review_sha256": sha256_json(review),
            }
        )
        revision_fingerprint = generation_fingerprint(
            inputs={
                "evidence_packet_sha256": evidence["evidence_packet_sha256"],
                "baseline_candidate_sha256": sha256_json(candidate),
                "baseline_review_sha256": sha256_json(review),
                "generation_parameters": _client_parameters(openai_client),
            },
            prompt_text=revision_prompt,
            schema=BRIEF_REVISION_SCHEMA,
            model=openai_client.model,
            reasoning=getattr(openai_client, "reasoning_effort", "unknown"),
        )

        def generate_revision() -> dict[str, Any]:
            generated = openai_client.generate_json(
                revision_prompt, revision_input, BRIEF_REVISION_SCHEMA
            )
            try:
                validate_strict_schema(generated, BRIEF_REVISION_SCHEMA)
                validate_brief_revision(
                    generated,
                    candidate=candidate,
                    review=review,
                    evidence_packet=evidence,
                )
            except Exception as exc:
                rejected = output_dir / "rejected-generations"
                rejected.mkdir(parents=True, exist_ok=True)
                _write_json(
                    rejected / f"brief-revision.{sha256_json(generated)[:16]}.json",
                    {
                        "schema_version": (
                            "wang_theological_editorial_rejected_generation_v1"
                        ),
                        "stage": "composition_revision",
                        "baseline_candidate_sha256": sha256_json(candidate),
                        "baseline_review_sha256": sha256_json(review),
                        "validation_error": f"{type(exc).__name__}: {exc}",
                        "result": generated,
                    },
                )
                raise
            return generated

        revision, revision_cached = _run_cached_stage(
            path=output_dir / "theological-editorial-brief-revision.json",
            schema_version="wang_theological_editorial_brief_revision_envelope_v3",
            fingerprint=revision_fingerprint,
            producer={
                "role": "composition_revision_agent",
                "provider": getattr(
                    openai_client, "backend", "openai_api"
                ),
                "model": openai_client.model,
            },
            generate=generate_revision,
            force=force,
        )
        validate_brief_revision(
            revision,
            candidate=candidate,
            review=review,
            evidence_packet=evidence,
        )
        revised_candidate = revision["revised_candidate"]
        if revised_candidate["status"] != "ready":
            result = {
                "status": revised_candidate["status"],
                "stage": "composition_revision",
                "scope_sha256": scope["scope_sha256"],
                "evidence_packet_sha256": evidence[
                    "evidence_packet_sha256"
                ],
                "candidate_sha256": sha256_json(candidate),
                "review_sha256": sha256_json(review),
                "revision_sha256": sha256_json(revision),
                "stop_reasons": revised_candidate["stop_reasons"],
                "revision_cached": revision_cached,
            }
            _write_json(output_dir / "workflow-status.json", result)
            return result

        final_packet = _review_packet(
            evidence_packet=evidence,
            candidate=revised_candidate,
            revision_context={
                "baseline_candidate": candidate,
                "baseline_review": review,
                "finding_dispositions": revision["finding_dispositions"],
                "collateral_changes": revision["collateral_changes"],
                "revision_sha256": sha256_json(revision),
            },
        )
        _write_json(
            output_dir / "theological-editorial-brief-final-review-packet.json",
            {
                "schema_version": (
                    "wang_theological_editorial_brief_final_review_packet_envelope_v2"
                ),
                "generation": {
                    "fingerprint": sha256_json(final_packet),
                    "generated_at": _utcnow(),
                    "role": "final_brief_review_packet_builder",
                    "provider": "deterministic",
                },
                "result": final_packet,
            },
        )
        final_prompt = FINAL_REVIEW_PROMPT.read_text(encoding="utf-8")
        final_input = canonical_json(final_packet)
        final_fingerprint = generation_fingerprint(
            inputs={
                "final_review_packet_sha256": sha256_json(final_packet),
                "revised_candidate_sha256": sha256_json(revised_candidate),
                "generation_parameters": _client_parameters(claude_client),
            },
            prompt_text=final_prompt,
            schema=BRIEF_REVIEW_SCHEMA,
            model=claude_client.model,
            reasoning="final_composition_review",
        )

        def generate_final_review() -> dict[str, Any]:
            generated = claude_client.generate_json(
                final_prompt, final_input, BRIEF_REVIEW_SCHEMA
            )
            try:
                validate_strict_schema(generated, BRIEF_REVIEW_SCHEMA)
                validate_brief_review(generated, candidate=revised_candidate)
            except Exception as exc:
                rejected = output_dir / "rejected-generations"
                rejected.mkdir(parents=True, exist_ok=True)
                _write_json(
                    rejected / f"brief-final-review.{sha256_json(generated)[:16]}.json",
                    {
                        "schema_version": (
                            "wang_theological_editorial_rejected_generation_v1"
                        ),
                        "stage": "composition_final_review",
                        "revised_candidate_sha256": sha256_json(
                            revised_candidate
                        ),
                        "validation_error": f"{type(exc).__name__}: {exc}",
                        "result": generated,
                    },
                )
                raise
            return generated

        final_review, final_review_cached = _run_cached_stage(
            path=output_dir / "theological-editorial-brief-final-review.json",
            schema_version=(
                "wang_theological_editorial_brief_final_review_envelope_v3"
            ),
            fingerprint=final_fingerprint,
            producer={
                "role": "independent_final_composition_reviewer",
                "provider": getattr(
                    claude_client, "backend", "anthropic_api"
                ),
                "model": claude_client.model,
            },
            generate=generate_final_review,
            force=force,
        )
        validate_brief_review(final_review, candidate=revised_candidate)
        if final_review["decision"] != "pass":
            result = {
                "status": "human_editor_required",
                "stage": "composition_final_review",
                "scope_sha256": scope["scope_sha256"],
                "evidence_packet_sha256": evidence[
                    "evidence_packet_sha256"
                ],
                "revised_candidate_sha256": sha256_json(revised_candidate),
                "final_review_sha256": sha256_json(final_review),
                "findings": final_review["findings"],
                "revision_cached": revision_cached,
                "final_review_cached": final_review_cached,
            }
            _write_json(output_dir / "workflow-status.json", result)
            return result
        effective_candidate = revised_candidate
        effective_review = final_review

    brief = compile_approved_editorial_brief(
        candidate=effective_candidate,
        evidence_packet=evidence,
        review=effective_review,
    )
    _write_json(
        output_dir / "theological-editorial-brief.json",
        {
            "schema_version": "wang_theological_editorial_brief_envelope_v2",
            "generation": {
                "fingerprint": brief["brief_sha256"],
                "generated_at": _utcnow(),
                "role": "approved_brief_compiler",
                "provider": "deterministic",
            },
            "result": brief,
        },
    )
    result = {
        "status": "brief_approved",
        "stage": "composition_complete",
        "scope_sha256": scope["scope_sha256"],
        "evidence_packet_sha256": evidence["evidence_packet_sha256"],
        "brief_sha256": brief["brief_sha256"],
        "candidate_cached": candidate_cached,
        "review_cached": review_cached,
        "composition_revised": review["decision"] == "changes_required",
    }
    _write_json(output_dir / "workflow-status.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--composition-model", default="gpt-5.6-sol")
    parser.add_argument("--review-model", default="claude-opus-5")
    parser.add_argument(
        "--composition-provider",
        choices=("codex-subscription", "openai-api"),
        default="codex-subscription",
    )
    parser.add_argument(
        "--review-provider",
        choices=("claude-subscription", "anthropic-api"),
        default="claude-subscription",
    )
    parser.add_argument("--composition-reasoning-effort", default="high")
    parser.add_argument("--review-reasoning-effort", default="high")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    scope = json.loads(args.scope.read_text(encoding="utf-8"))
    validate_editorial_scope(scope)
    store = PostgresKnowledgeStore()
    records = {
        collection: store.list_records(collection)
        for collection in REQUIRED_COLLECTIONS
    }
    if args.dry_run:
        evidence = compile_theological_evidence_packet(scope=scope, records=records)
        _write_json(args.output_dir / "theological-evidence-packet.json", evidence)
        print(
            json.dumps(
                {
                    "status": evidence["compiler_readiness"],
                    "scope_sha256": scope["scope_sha256"],
                    "evidence_packet_sha256": evidence[
                        "evidence_packet_sha256"
                    ],
                    "viewpoint_count": len(evidence["focal_viewpoints"]),
                    "argument_route_count": len(evidence["argument_routes"]),
                    "claim_count": len(evidence["claims"]),
                    "source_count": len(evidence["source_documents"]),
                    "compiler_findings": evidence["compiler_findings"],
                    "would_call_models": False,
                    "would_write_reader_prose": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    if args.composition_provider == "codex-subscription":
        openai_client = CodexSubscriptionClient(
            model=args.composition_model,
            timeout_seconds=args.timeout_seconds,
            reasoning_effort=args.composition_reasoning_effort,
        )
    else:
        openai_client = Stage1OpenAIClient(
            model=args.composition_model,
            timeout_seconds=args.timeout_seconds,
            reasoning_effort=args.composition_reasoning_effort,
        )
    if args.review_provider == "claude-subscription":
        claude_client = ClaudeSubscriptionClient(
            model=args.review_model,
            timeout_seconds=args.timeout_seconds,
            reasoning_effort=args.review_reasoning_effort,
        )
    else:
        claude_client = Stage1AnthropicClient(
            model=args.review_model,
            timeout_seconds=args.timeout_seconds,
        )
    result = run_composition(
        scope=scope,
        records=records,
        output_dir=args.output_dir,
        openai_client=openai_client,
        claude_client=claude_client,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "brief_approved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
