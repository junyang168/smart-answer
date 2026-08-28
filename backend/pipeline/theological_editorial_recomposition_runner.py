"""Formally amend an approved editorial brief from downstream structural findings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.matthew_exposition_authoring import canonical_json, generation_fingerprint, validate_strict_schema
from backend.pipeline.theological_editorial_composition_runner import (
    FINAL_REVIEW_PROMPT, REVISION_PROMPT, _review_packet, _run_cached_stage, _utcnow, _write_json,
)
from backend.pipeline.theological_editorial_synthesis import (
    BRIEF_REVISION_SCHEMA, BRIEF_REVIEW_SCHEMA, compile_approved_editorial_brief,
    validate_brief_review, validate_brief_revision, validate_theological_evidence_packet,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _result(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value.get("result") or value)


def _as_brief_finding(item: dict[str, Any]) -> dict[str, Any]:
    dimension = str(item["dimension_id"])
    code = {
        "argument_route_integrity": "argument_route_not_source_local",
        "positive_thesis_and_structural_fidelity": "unresolved_item_silently_harmonized",
        "reader_memory_center": "unresolved_item_silently_harmonized",
    }.get(dimension, "other")
    return {
        "finding_id": f"DOWNSTREAM-{item['finding_id']}",
        "code": code,
        "severity": "high" if item.get("severity") == "major" else "medium",
        "blocking": True,
        "record_ids": [str(item.get("section_id") or "")],
        "explanation": str(item["problem"]),
        "recommended_action": str(item["required_change"]),
    }


def run_recomposition(
    *, composition_dir: Path, downstream_review_path: Path, output_dir: Path,
    composition_client: Any, review_client: Any, force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence = _result(composition_dir / "theological-evidence-packet.json")
    approved = _result(composition_dir / "theological-editorial-brief.json")
    validate_theological_evidence_packet(evidence)
    candidate = dict(approved["editorial_decisions"])
    downstream = _result(downstream_review_path)
    blocking = [dict(item) for item in downstream["findings"] if item["blocking"]]
    if not blocking:
        raise ValueError("downstream review contains no blocking findings")
    review = {
        "schema_version": "wang_theological_editorial_brief_review_v1",
        "scope_confirmation": "editorial_structure_and_material_no_theological_judgment",
        "brief_candidate_sha256": sha256_json(candidate),
        "decision": "changes_required",
        "summary": "Reader-prose review found defects that are locked in the approved brief; reopen Composition rather than bypassing the brief in Author revision.",
        "findings": [_as_brief_finding(item) for item in blocking],
    }
    validate_brief_review(review, candidate=candidate)
    _write_json(output_dir / "downstream-composition-review.json", review)
    revision_prompt = REVISION_PROMPT.read_text(encoding="utf-8")
    payload = canonical_json({
        "evidence_packet": evidence, "baseline_candidate": candidate,
        "baseline_candidate_sha256": sha256_json(candidate), "baseline_review": review,
        "baseline_review_sha256": sha256_json(review),
    })
    fingerprint = generation_fingerprint(
        inputs={"evidence_packet_sha256": evidence["evidence_packet_sha256"], "baseline_candidate_sha256": sha256_json(candidate), "baseline_review_sha256": sha256_json(review), "backend": getattr(composition_client, "backend", "api")},
        prompt_text=revision_prompt, schema=BRIEF_REVISION_SCHEMA,
        model=composition_client.model, reasoning=getattr(composition_client, "reasoning_effort", "unknown"),
    )
    def generate_revision() -> dict[str, Any]:
        value = composition_client.generate_json(revision_prompt, payload, BRIEF_REVISION_SCHEMA)
        validate_strict_schema(value, BRIEF_REVISION_SCHEMA)
        validate_brief_revision(value, candidate=candidate, review=review, evidence_packet=evidence)
        return value
    revision, revision_cached = _run_cached_stage(
        path=output_dir / "theological-editorial-brief-revision.json",
        schema_version="wang_theological_editorial_brief_revision_envelope_v1",
        fingerprint=fingerprint,
        producer={"role": "downstream_composition_revision", "provider": getattr(composition_client, "backend", "api"), "model": composition_client.model},
        generate=generate_revision, force=force,
    )
    revised_candidate = revision["revised_candidate"]
    final_packet = _review_packet(
        evidence_packet=evidence, candidate=revised_candidate,
        revision_context={"baseline_candidate": candidate, "baseline_review": review, "finding_dispositions": revision["finding_dispositions"], "revision_sha256": sha256_json(revision)},
    )
    _write_json(output_dir / "theological-editorial-brief-final-review-packet.json", final_packet)
    final_prompt = FINAL_REVIEW_PROMPT.read_text(encoding="utf-8")
    final_fp = generation_fingerprint(
        inputs={"final_review_packet_sha256": sha256_json(final_packet), "revised_candidate_sha256": sha256_json(revised_candidate), "backend": getattr(review_client, "backend", "api")},
        prompt_text=final_prompt, schema=BRIEF_REVIEW_SCHEMA,
        model=review_client.model, reasoning="final_composition_review",
    )
    def generate_final() -> dict[str, Any]:
        value = review_client.generate_json(final_prompt, canonical_json(final_packet), BRIEF_REVIEW_SCHEMA)
        validate_strict_schema(value, BRIEF_REVIEW_SCHEMA)
        validate_brief_review(value, candidate=revised_candidate)
        return value
    final_review, final_cached = _run_cached_stage(
        path=output_dir / "theological-editorial-brief-final-review.json",
        schema_version="wang_theological_editorial_brief_final_review_envelope_v1",
        fingerprint=final_fp,
        producer={"role": "independent_final_composition_reviewer", "provider": getattr(review_client, "backend", "api"), "model": review_client.model},
        generate=generate_final, force=force,
    )
    if final_review["decision"] != "pass":
        status = {"status": "human_editor_required", "stage": "composition_final_review", "findings": final_review["findings"]}
        _write_json(output_dir / "workflow-status.json", status)
        return status
    brief = compile_approved_editorial_brief(candidate=revised_candidate, evidence_packet=evidence, review=final_review)
    for name, result, role in (
        ("theological-evidence-packet.json", evidence, "evidence_compiler_reused"),
        ("theological-editorial-brief.json", brief, "approved_brief_compiler"),
    ):
        _write_json(output_dir / name, {
            "schema_version": f"wang_{name.removesuffix('.json').replace('-', '_')}_envelope_v1",
            "generation": {"fingerprint": result.get("evidence_packet_sha256") or result.get("brief_sha256"), "generated_at": _utcnow(), "role": role, "provider": "deterministic"},
            "result": result,
        })
    status = {"status": "brief_approved", "stage": "downstream_recomposition_complete", "brief_sha256": brief["brief_sha256"], "revision_cached": revision_cached, "final_review_cached": final_cached}
    _write_json(output_dir / "workflow-status.json", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--composition-dir", type=Path, required=True)
    parser.add_argument("--downstream-review", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    result = run_recomposition(
        composition_dir=args.composition_dir, downstream_review_path=args.downstream_review,
        output_dir=args.output_dir,
        composition_client=CodexSubscriptionClient(model="gpt-5.6-sol", reasoning_effort="high", timeout_seconds=args.timeout_seconds),
        review_client=ClaudeSubscriptionClient(model="claude-opus-5", reasoning_effort="high", timeout_seconds=args.timeout_seconds),
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "brief_approved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
