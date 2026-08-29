"""Review, audit, and publish a grounded theological topic essay."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.config.wang_platform_paths import wang_platform_paths
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.editorial_draft_repository import publish_editorial_draft
from backend.pipeline.manuscript_grounding_check import check_manuscript_grounding, extract_provenance_paragraphs
from backend.pipeline.matthew_exposition_authoring import (
    canonical_json,
    changed_markdown_paragraphs,
    generation_fingerprint,
    sha256_text,
)
from backend.pipeline.theological_topic_authoring import (
    TOPIC_EDITORIAL_REVIEW_SCHEMA,
    TOPIC_EDITORIAL_REVISION_SCHEMA,
    TOPIC_FINAL_DELTA_REVIEW_SCHEMA,
    TOPIC_GROUNDING_REVISION_SCHEMA,
    build_topic_editorial_review_packet,
    editorial_instructions_by_claim,
    evaluate_topic_editorial_review,
    validate_topic_author_result,
    validate_topic_editorial_review,
    validate_topic_editorial_revision,
    validate_topic_final_delta_review,
    validate_topic_grounding_revision,
)
from backend.pipeline.theological_topic_authoring_runner import (
    GROUNDING_REVISION_PROMPT, _number_grounding_findings, _run_cached_stage, _write_json,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
REVIEW_PROMPT = PROMPT_DIR / "theological_topic_editorial_review.md"
REVISION_PROMPT = PROMPT_DIR / "theological_topic_editorial_revision.md"
DELTA_PROMPT = PROMPT_DIR / "theological_topic_final_delta_review.md"
MAX_EDITORIAL_REVISIONS = 2

HARD_FAILURE_DIMENSIONS = {
    "unsupported_editorial_synthesis_attributed_to_professor": {"source_and_exegesis", "editorial_voice_restraint"},
    "material_tension_or_unresolved_relation_silently_harmonized": {"theological_tension_and_attribution"},
    "negative_material_displaces_positive_thesis": {"positive_thesis_and_structural_fidelity", "reader_memory_center"},
    "source_local_argument_routes_spliced": {"argument_route_integrity"},
    "exegetical_observation_inference_conclusion_chain_missing": {"argument_route_integrity"},
}
DELTA_IMPACTS = {
    "source_and_exegesis": {"editorial_voice_restraint", "theological_tension_and_attribution"},
    "positive_thesis_and_structural_fidelity": {"reader_memory_center", "argument_route_integrity"},
    "argument_route_integrity": {"source_and_exegesis", "positive_thesis_and_structural_fidelity"},
    "general_reader_readability": {"approved_written_style", "concision_without_compression"},
    "editorial_voice_restraint": {"source_and_exegesis", "theological_tension_and_attribution"},
    "theological_tension_and_attribution": {"editorial_voice_restraint"},
    "approved_written_style": {"general_reader_readability"},
    "concision_without_compression": {"general_reader_readability"},
    "pastoral_theological_landing": {"reader_memory_center"},
    "reader_memory_center": {"positive_thesis_and_structural_fidelity", "pastoral_theological_landing"},
}


def _read_result(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return dict(value.get("result") or value)


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _latest_grounded_author_result(authoring_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    status = json.loads((authoring_dir / "workflow-status.json").read_text(encoding="utf-8"))
    if status.get("status") != "draft_grounded":
        raise ValueError("quality review requires a draft_grounded authoring run")
    revision_count = int(status.get("grounding_revision_count") or 0)
    path = (
        authoring_dir / f"topic-grounding-revision-{revision_count:02d}.json"
        if revision_count
        else authoring_dir / "topic-authoring.json"
    )
    result = _read_result(path)
    author_result = dict(result.get("revised_author_result") or result)
    grounding_path = (
        authoring_dir / f"grounding-report-{revision_count + 1:02d}.json"
        if revision_count
        else authoring_dir / "grounding-report.json"
    )
    grounding = _read_result(grounding_path)
    if not grounding.get("passed") or grounding.get("manuscript_sha256") != sha256_text(author_result["manuscript_markdown"]):
        raise ValueError("grounding report is not a passing review of the selected manuscript")
    return author_result, grounding


def _affected_dimensions(findings: list[Mapping[str, Any]], quality_profile: Mapping[str, Any]) -> list[str]:
    ordered = [str(item["id"]) for item in quality_profile["dimensions"]]
    affected = {str(item["dimension_id"]) for item in findings if item.get("blocking")}
    for dimension in list(affected):
        affected.update(DELTA_IMPACTS.get(dimension, set()))
    return [item for item in ordered if item in affected]


def _merge_delta(
    *, baseline_review: Mapping[str, Any], delta: Mapping[str, Any],
    affected_dimensions: list[str], affected_hard_failures: list[str],
    quality_profile: Mapping[str, Any], revised_sha: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    scores = {str(item["dimension_id"]): dict(item) for item in baseline_review["dimension_scores"]}
    for item in delta["dimension_scores"]:
        scores[str(item["dimension_id"])] = dict(item)
    hard = {str(item["failure_id"]): dict(item) for item in baseline_review["hard_failure_assessments"]}
    for item in delta["hard_failure_assessments"]:
        hard[str(item["failure_id"])] = dict(item)
    merged = {
        "schema_version": "wang_theological_topic_editorial_review_merged_v1",
        "reviewed_manuscript_sha256": revised_sha,
        "scope_confirmation": "theological_topic_essay_quality",
        "summary": delta["summary"],
        "dimension_scores": [scores[str(item["id"])] for item in quality_profile["dimensions"]],
        "hard_failure_assessments": [hard[str(item)] for item in quality_profile["hard_failures"]],
        "findings": delta["findings"],
        "score_provenance": {
            "rescored_dimensions": affected_dimensions,
            "inherited_dimensions": [str(item["id"]) for item in quality_profile["dimensions"] if item["id"] not in affected_dimensions],
            "reassessed_hard_failures": affected_hard_failures,
        },
    }
    return merged, evaluate_topic_editorial_review(merged, quality_profile=quality_profile)


def build_topic_presentation_package(
    *, packet: Mapping[str, Any], author_result: Mapping[str, Any]
) -> dict[str, Any]:
    claims = {str(item["claim_id"]): item for item in packet["knowledge"]["claims"]}
    steps = {
        str(item["evidence_step_id"]): item
        for item in packet["knowledge"]["evidence_steps"]
    }
    fragments = {
        str(item["fragment_id"]): item
        for item in packet["knowledge"]["source_fragments"]
    }
    sources = {
        str(item["source_id"]): item
        for item in packet["knowledge"]["source_documents"]
    }
    decisions: list[dict[str, Any]] = []
    for section in author_result["sections"]:
        presentations: list[dict[str, Any]] = []
        seen: set[tuple[str, float, float]] = set()
        scripture_refs: list[str] = []
        for claim_id in section["claim_ids_used"]:
            claim = claims[str(claim_id)]
            scripture_refs.extend(str(value) for value in claim.get("scripture_refs") or [])
            for step_id in claim.get("evidence_step_ids") or []:
                step = steps.get(str(step_id))
                if not step:
                    continue
                fragment_ids = list(step.get("source_fragment_ids") or [])
                if step.get("source_fragment_id") and step["source_fragment_id"] not in fragment_ids:
                    fragment_ids.append(step["source_fragment_id"])
                for fragment_id in fragment_ids:
                    fragment = fragments.get(str(fragment_id))
                    if not fragment:
                        continue
                    source = sources.get(str(fragment["source_id"]))
                    if not source or source.get("source_type") != "sermon_transcript":
                        continue
                    start = fragment.get("media_time")
                    end = fragment.get("media_end_time")
                    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
                        continue
                    key = (str(source["source_id"]), float(start), float(end))
                    if key in seen:
                        continue
                    seen.add(key)
                    presentations.append({
                        "source_id": source["source_id"],
                        "start_seconds": start,
                        "end_seconds": end,
                        "claim_ids": [claim_id],
                        "fragment_ids": [fragment_id],
                    })
        decisions.append({
            "decision_id": section["section_id"],
            "section_title": next(
                item["heading"]
                for item in packet["editorial_decisions"]["sections"]
                if item["section_id"] == section["section_id"]
            ),
            "passage": "; ".join(dict.fromkeys(scripture_refs)),
            "source_presentations": presentations,
        })
    package = {
        "schema_version": "wang_theological_topic_presentation_package_v1",
        "source_documents": list(sources.values()),
        "product_plans": [{
            "plan_id": packet["scope"]["scope_id"],
            "decisions": decisions,
        }],
    }
    package["package_sha256"] = sha256_json(package)
    return package


def program_audit(
    *, packet: Mapping[str, Any], author_result: Mapping[str, Any], grounding: Mapping[str, Any],
    editorial_review: Mapping[str, Any], editorial_outcome: Mapping[str, Any],
    presentation_package: Mapping[str, Any],
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    manuscript = str(author_result["manuscript_markdown"])
    manuscript_sha = sha256_text(manuscript)
    try:
        validate_topic_author_result(author_result, authoring_packet=packet)
    except Exception as exc:
        errors.append({"code": "author_contract_invalid", "message": str(exc)})
    if grounding.get("passed") is not True or grounding.get("manuscript_sha256") != manuscript_sha:
        errors.append({"code": "grounding_not_bound_or_failed", "message": "final grounding report must pass and bind the final manuscript"})
    if editorial_review.get("reviewed_manuscript_sha256") != manuscript_sha or editorial_outcome.get("passed") is not True:
        errors.append({"code": "editorial_review_not_bound_or_failed", "message": "final editorial result must pass and bind the final manuscript"})
    provenance = extract_provenance_paragraphs(manuscript)
    substantive = [item for item in provenance if isinstance(item.get("provenance"), dict) and item["provenance"].get("attribution") in {"professor", "editorial_synthesis"}]
    if not substantive:
        errors.append({"code": "missing_substantive_provenance", "message": "no substantive provenance paragraphs"})
    decisions = packet["editorial_decisions"]
    headings = [f"## {item['heading']}" for item in decisions["sections"]]
    offsets = [manuscript.find(item) for item in headings]
    if any(item < 0 for item in offsets) or offsets != sorted(offsets):
        errors.append({"code": "brief_section_order_changed", "message": "final headings do not match approved brief order"})
    route_ids = {
        str(value)
        for section in decisions["sections"]
        for value in section["argument_route_revision_ids"]
    }
    used_routes = {str(value) for section in author_result["sections"] for value in section["argument_route_revision_ids_used"]}
    if used_routes != route_ids:
        errors.append({"code": "argument_route_manifest_mismatch", "message": "final route ledger does not bind every selected route"})
    viewpoint_ids = {
        str(value)
        for section in decisions["sections"]
        for value in section["viewpoint_revision_ids"]
    }
    used_viewpoints = {str(value) for section in author_result["sections"] for value in section["viewpoint_revision_ids_used"]}
    if used_viewpoints != viewpoint_ids:
        errors.append({"code": "viewpoint_manifest_mismatch", "message": "final viewpoint ledger does not bind every selected viewpoint"})
    claims = {str(item["claim_id"]): item for item in packet["knowledge"]["claims"]}
    steps = {str(item["evidence_step_id"]): item for item in packet["knowledge"]["evidence_steps"]}
    fragments = {str(item["fragment_id"]): item for item in packet["knowledge"]["source_fragments"]}
    sources = {str(item["source_id"]): item for item in packet["knowledge"]["source_documents"]}
    used_claim_ids = {
        str(value)
        for section in author_result["sections"]
        for value in section["claim_ids_used"]
    }
    for claim_id in sorted(used_claim_ids):
        claim = claims.get(claim_id)
        if not claim:
            errors.append({"code": "unknown_used_claim", "message": claim_id})
            continue
        evidence_ids = [str(value) for value in claim.get("evidence_step_ids") or []]
        if not evidence_ids:
            errors.append({"code": "used_claim_without_evidence", "message": claim_id})
        for evidence_id in evidence_ids:
            step = steps.get(evidence_id)
            if not step:
                errors.append({"code": "missing_evidence_step", "message": f"{claim_id}: {evidence_id}"})
                continue
            fragment_ids = list(step.get("source_fragment_ids") or [])
            if step.get("source_fragment_id") and step["source_fragment_id"] not in fragment_ids:
                fragment_ids.append(step["source_fragment_id"])
            if not fragment_ids:
                errors.append({"code": "evidence_without_source_fragment", "message": evidence_id})
            for fragment_id in fragment_ids:
                fragment = fragments.get(str(fragment_id))
                if not fragment:
                    errors.append({"code": "missing_source_fragment", "message": f"{evidence_id}: {fragment_id}"})
                    continue
                source = sources.get(str(fragment.get("source_id") or ""))
                if not source:
                    errors.append({"code": "missing_source_document", "message": str(fragment.get("source_id") or "")})
                    continue
                if fragment.get("source_sha256") != source.get("source_sha256"):
                    errors.append({"code": "source_fragment_sha_mismatch", "message": str(fragment_id)})
                if source.get("source_type") == "sermon_transcript" and (
                    not isinstance(fragment.get("media_time"), (int, float))
                    or not isinstance(fragment.get("media_end_time"), (int, float))
                    or fragment["media_end_time"] <= fragment["media_time"]
                ):
                    errors.append({"code": "sermon_fragment_missing_audio_anchor", "message": str(fragment_id)})
    plan = presentation_package["product_plans"][0]
    presentation_sections = {str(item["decision_id"]): item for item in plan["decisions"]}
    for section in author_result["sections"]:
        decision = presentation_sections.get(str(section["section_id"]))
        if not decision:
            errors.append({"code": "missing_presentation_section", "message": str(section["section_id"])})
            continue
        if not decision["source_presentations"]:
            section_source_types: set[str] = set()
            for claim_id in section["claim_ids_used"]:
                claim = claims.get(str(claim_id)) or {}
                for evidence_id in claim.get("evidence_step_ids") or []:
                    step = steps.get(str(evidence_id)) or {}
                    fragment_ids = list(step.get("source_fragment_ids") or [])
                    if step.get("source_fragment_id") and step["source_fragment_id"] not in fragment_ids:
                        fragment_ids.append(step["source_fragment_id"])
                    for fragment_id in fragment_ids:
                        fragment = fragments.get(str(fragment_id)) or {}
                        source = sources.get(str(fragment.get("source_id") or "")) or {}
                        if source.get("source_type"):
                            section_source_types.add(str(source["source_type"]))
            finding = {
                "message": str(section["section_id"]),
                "source_types": sorted(section_source_types),
            }
            if "sermon_transcript" in section_source_types:
                errors.append({
                    "code": "section_without_audio_presentation",
                    **finding,
                })
            else:
                warnings.append({
                    "code": "section_has_text_source_only_no_audio",
                    **finding,
                })
    return {
        "schema_version": "wang_theological_topic_program_audit_v1",
        "status": "pass" if not errors else "fail",
        "summary": {"error_total": len(errors), "warning_total": len(warnings)},
        "errors": errors,
        "warnings": warnings,
        "fingerprint": {
            "draft_sha256": manuscript_sha,
            "authoring_packet_sha256": packet["packet_sha256"],
            "scope_sha256": packet["input_bindings"]["scope_sha256"],
            "evidence_packet_sha256": packet["input_bindings"]["evidence_packet_sha256"],
            "brief_sha256": packet["input_bindings"]["brief_sha256"],
            "publication_profile_sha256": packet["input_bindings"]["publication_profile_sha256"],
            "quality_profile_sha256": packet["input_bindings"]["quality_profile_sha256"],
            "editorial_review_sha256": sha256_json(dict(editorial_review)),
            "grounding_report_sha256": sha256_json(dict(grounding)),
            "presentation_package_sha256": presentation_package["package_sha256"],
            "viewpoint_revision_ids": sorted(viewpoint_ids),
            "argument_route_revision_ids": sorted(route_ids),
            "route_out_viewpoint_revision_ids": sorted(
                str(item["viewpoint_revision_id"])
                for item in decisions["viewpoint_coverage"]
                if item["disposition"] == "route_out"
            ),
        },
    }


def run_quality(
    *, authoring_dir: Path, output_dir: Path, reviewer_client: Any,
    revision_client: Any, repository_root: Path | None = None, force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    packet = _read_result(authoring_dir / "topic-authoring-packet.json")
    author_result, grounding = _latest_grounded_author_result(authoring_dir)
    validate_topic_author_result(author_result, authoring_packet=packet)
    quality_profile = packet["quality_profile"]
    review_packet = build_topic_editorial_review_packet(authoring_packet=packet, author_result=author_result)
    _write_json(output_dir / "independent-editorial-review-packet.json", review_packet)
    review_prompt = REVIEW_PROMPT.read_text(encoding="utf-8")
    review_fingerprint = generation_fingerprint(
        inputs={"review_packet_sha256": review_packet["packet_sha256"], "backend": getattr(reviewer_client, "backend", "api")},
        prompt_text=review_prompt, schema=TOPIC_EDITORIAL_REVIEW_SCHEMA,
        model=reviewer_client.model, reasoning=getattr(reviewer_client, "reasoning_effort", "unknown"),
    )
    def generate_review() -> dict[str, Any]:
        value = reviewer_client.generate_json(
            review_prompt, canonical_json(review_packet), TOPIC_EDITORIAL_REVIEW_SCHEMA
        )
        validate_topic_editorial_review(value, review_packet=review_packet)
        return value

    review, review_cached = _run_cached_stage(
        path=output_dir / "independent-editorial-review.json",
        schema_version="wang_theological_topic_editorial_review_envelope_v1",
        fingerprint=review_fingerprint,
        producer={"role": "independent_editorial_reviewer", "provider": getattr(reviewer_client, "backend", "api"), "model": reviewer_client.model},
        generate=generate_review,
        force=force,
    )
    outcome = validate_topic_editorial_review(review, review_packet=review_packet)
    _write_json(output_dir / "independent-editorial-review-outcome.json", {**outcome, "manuscript_sha256": review_packet["manuscript_sha256"]})
    current_review: dict[str, Any] = dict(review)
    current_outcome = outcome
    revision_count = 0
    revision_cached: list[bool] = []
    delta_cached: list[bool] = []

    while not current_outcome["passed"] and revision_count < MAX_EDITORIAL_REVISIONS:
        blocking = [dict(item) for item in current_review["findings"] if item["blocking"]]
        if not blocking:
            break
        baseline_result = author_result
        baseline_manuscript = str(baseline_result["manuscript_markdown"])
        baseline_sha = sha256_text(baseline_manuscript)
        revision_prompt = REVISION_PROMPT.read_text(encoding="utf-8")
        revision_payload = canonical_json({
            "schema_version": "wang_theological_topic_editorial_revision_packet_v1",
            "authoring_packet": packet,
            "baseline_author_result": baseline_result,
            "baseline_manuscript_sha256": baseline_sha,
            "blocking_findings": blocking,
        })
        revision_number = revision_count + 1
        _write_json(
            output_dir / f"editorial-revision-packet-{revision_number:02d}.json",
            {
                "schema_version": "wang_theological_topic_editorial_revision_packet_envelope_v1",
                "packet_sha256": sha256_text(revision_payload),
                "result": json.loads(revision_payload),
            },
        )
        revision_fp = generation_fingerprint(
            inputs={"packet_sha256": packet["packet_sha256"], "baseline_manuscript_sha256": baseline_sha, "findings_sha256": sha256_json(blocking), "backend": getattr(revision_client, "backend", "api")},
            prompt_text=revision_prompt, schema=TOPIC_EDITORIAL_REVISION_SCHEMA,
            model=revision_client.model, reasoning=getattr(revision_client, "reasoning_effort", "unknown"),
        )
        def generate_revision() -> dict[str, Any]:
            value = revision_client.generate_json(revision_prompt, revision_payload, TOPIC_EDITORIAL_REVISION_SCHEMA)
            validate_topic_editorial_revision(value, baseline_manuscript_sha256=baseline_sha, findings=blocking, authoring_packet=packet)
            return value
        revision, cached = _run_cached_stage(
            path=output_dir / f"editorial-revision-{revision_number:02d}.json",
            schema_version="wang_theological_topic_editorial_revision_envelope_v1",
            fingerprint=revision_fp,
            producer={"role": "topic_editorial_revision", "provider": getattr(revision_client, "backend", "api"), "model": revision_client.model},
            generate=generate_revision, force=force,
        )
        revision_cached.append(cached)
        author_result = dict(revision["revised_author_result"])
        if author_result["status"] == "composition_change_required":
            return {"status": "composition_change_required", "stage": "editorial_revision", "revision_round": revision_number, "requests": author_result["composition_change_requests"]}
        revised_manuscript = str(author_result["manuscript_markdown"])
        revised_grounding = check_manuscript_grounding(
            revised_manuscript, packet["knowledge"], client=reviewer_client,
            author_sections=author_result["sections"],
            instructions_by_claim=editorial_instructions_by_claim(authoring_packet=packet, author_result=author_result),
            cache_dir=authoring_dir / "grounding-cache",
        )
        _write_json(output_dir / f"editorial-revision-grounding-{revision_number:02d}.json", revised_grounding)
        if not revised_grounding["passed"]:
            numbered = _number_grounding_findings(
                revised_grounding["findings"],
                manuscript_sha256=revised_grounding["manuscript_sha256"],
            )
            if any(item["code"] != "unsupported_assertion" for item in numbered):
                return {"status": "grounding_gate_failed", "stage": "editorial_revision", "revision_round": revision_number, "finding_count": len(numbered)}
            repair_prompt = GROUNDING_REVISION_PROMPT.read_text(encoding="utf-8")
            repair_payload = canonical_json({
                "schema_version": "wang_theological_topic_grounding_revision_packet_v1",
                "authoring_packet": packet,
                "baseline_author_result": author_result,
                "baseline_manuscript_sha256": revised_grounding["manuscript_sha256"],
                "grounding_findings": numbered,
            })
            _write_json(
                output_dir / f"editorial-revision-grounding-repair-packet-{revision_number:02d}.json",
                {
                    "schema_version": "wang_theological_topic_grounding_revision_packet_envelope_v1",
                    "packet_sha256": sha256_text(repair_payload),
                    "result": json.loads(repair_payload),
                },
            )
            repair_fp = generation_fingerprint(
                inputs={
                    "packet_sha256": packet["packet_sha256"],
                    "baseline_manuscript_sha256": revised_grounding["manuscript_sha256"],
                    "grounding_findings_sha256": sha256_json(numbered),
                    "backend": getattr(revision_client, "backend", "api"),
                },
                prompt_text=repair_prompt, schema=TOPIC_GROUNDING_REVISION_SCHEMA,
                model=revision_client.model, reasoning=getattr(revision_client, "reasoning_effort", "unknown"),
            )
            def generate_repair() -> dict[str, Any]:
                value = revision_client.generate_json(repair_prompt, repair_payload, TOPIC_GROUNDING_REVISION_SCHEMA)
                validate_topic_grounding_revision(
                    value,
                    baseline_manuscript_sha256=revised_grounding["manuscript_sha256"],
                    findings=numbered,
                    authoring_packet=packet,
                )
                return value
            repair, _ = _run_cached_stage(
                path=output_dir / f"editorial-revision-grounding-repair-{revision_number:02d}.json",
                schema_version="wang_theological_topic_grounding_revision_envelope_v1",
                fingerprint=repair_fp,
                producer={"role": "editorial_revision_grounding_repair", "provider": getattr(revision_client, "backend", "api"), "model": revision_client.model},
                generate=generate_repair, force=force,
            )
            author_result = dict(repair["revised_author_result"])
            if author_result["status"] == "composition_change_required":
                return {"status": "composition_change_required", "stage": "editorial_revision_grounding_repair", "revision_round": revision_number, "requests": author_result["composition_change_requests"]}
            revised_manuscript = str(author_result["manuscript_markdown"])
            revised_grounding = check_manuscript_grounding(
                revised_manuscript, packet["knowledge"], client=reviewer_client,
                author_sections=author_result["sections"],
                instructions_by_claim=editorial_instructions_by_claim(authoring_packet=packet, author_result=author_result),
                cache_dir=authoring_dir / "grounding-cache",
            )
            _write_json(output_dir / f"editorial-revision-grounding-repaired-{revision_number:02d}.json", revised_grounding)
            if not revised_grounding["passed"]:
                return {"status": "grounding_gate_failed", "stage": "editorial_revision_grounding_repair", "revision_round": revision_number, "finding_count": len(revised_grounding["findings"])}
        grounding = revised_grounding
        affected = _affected_dimensions(blocking, quality_profile)
        affected_hard = [failure for failure in quality_profile["hard_failures"] if HARD_FAILURE_DIMENSIONS.get(str(failure), set()).intersection(affected)]
        changes = changed_markdown_paragraphs(
            baseline_manuscript, revised_manuscript
        )
        delta_packet = {
            "schema_version": "wang_theological_topic_final_delta_review_packet_v1",
            "baseline_manuscript_sha256": baseline_sha,
            "reviewed_manuscript_sha256": sha256_text(revised_manuscript),
            "baseline_review": current_review,
            "accepted_findings": blocking,
            "finding_dispositions": revision["finding_dispositions"],
            "changed_paragraphs": changes,
            "affected_dimensions": [item for item in quality_profile["dimensions"] if item["id"] in affected],
            "affected_hard_failure_ids": affected_hard,
        }
        _write_json(
            output_dir / f"final-delta-review-packet-{revision_number:02d}.json",
            {
                "schema_version": "wang_theological_topic_final_delta_review_packet_envelope_v1",
                "packet_sha256": sha256_json(delta_packet),
                "result": delta_packet,
            },
        )
        delta_prompt = DELTA_PROMPT.read_text(encoding="utf-8")
        delta_fp = generation_fingerprint(
            inputs={"delta_packet_sha256": sha256_json(delta_packet), "backend": getattr(reviewer_client, "backend", "api")},
            prompt_text=delta_prompt, schema=TOPIC_FINAL_DELTA_REVIEW_SCHEMA,
            model=reviewer_client.model, reasoning=getattr(reviewer_client, "reasoning_effort", "unknown"),
        )
        def generate_delta() -> dict[str, Any]:
            value = reviewer_client.generate_json(delta_prompt, canonical_json(delta_packet), TOPIC_FINAL_DELTA_REVIEW_SCHEMA)
            validate_topic_final_delta_review(
                value, baseline_manuscript_sha256=baseline_sha, revised_manuscript=revised_manuscript,
                affected_dimension_ids=affected, affected_hard_failure_ids=affected_hard,
                findings=blocking, quality_profile=quality_profile,
            )
            return value
        delta, cached = _run_cached_stage(
            path=output_dir / f"final-delta-review-{revision_number:02d}.json",
            schema_version="wang_theological_topic_final_delta_review_envelope_v1",
            fingerprint=delta_fp,
            producer={"role": "final_delta_reviewer", "provider": getattr(reviewer_client, "backend", "api"), "model": reviewer_client.model},
            generate=generate_delta, force=force,
        )
        delta_cached.append(cached)
        current_review, current_outcome = _merge_delta(
            baseline_review=current_review, delta=delta, affected_dimensions=affected,
            affected_hard_failures=affected_hard, quality_profile=quality_profile,
            revised_sha=sha256_text(revised_manuscript),
        )
        revision_count = revision_number
        _write_json(output_dir / f"merged-editorial-review-{revision_number:02d}.json", current_review)
        _write_json(output_dir / f"merged-editorial-outcome-{revision_number:02d}.json", current_outcome)

    if not current_outcome["passed"]:
        status = {"status": "editorial_gate_failed", "revision_count": revision_count, "outcome": current_outcome}
        _write_json(output_dir / "workflow-status.json", status)
        return status

    manuscript = str(author_result["manuscript_markdown"])
    (output_dir / "final.md").write_text(manuscript, encoding="utf-8")
    final_review = {**current_review, "reviewed_manuscript_sha256": sha256_text(manuscript)}
    presentation_package = build_topic_presentation_package(
        packet=packet, author_result=author_result
    )
    _write_json(output_dir / "presentation-package.json", presentation_package)
    audit = program_audit(
        packet=packet, author_result=author_result, grounding=grounding,
        editorial_review=final_review, editorial_outcome=current_outcome,
        presentation_package=presentation_package,
    )
    _write_json(output_dir / "program-audit.json", {"draft_id": packet["scope"]["scope_id"], **audit})
    if audit["status"] != "pass":
        status = {"status": "program_audit_failed", "audit": audit}
        _write_json(output_dir / "workflow-status.json", status)
        return status
    publication_review = {
        "schema_version": "automated-editorial-review.v1",
        "reviewed_draft_sha256": sha256_text(manuscript),
        "manuscript_sha256": sha256_text(manuscript),
        "passed": True,
        "total_score": current_outcome["total_score"],
        "total_score_decides_nothing": True,
        "hard_gate_failures": [],
        "declared_hard_failures": [],
        "checks": {"rubric_outcome": {**current_outcome, "manuscript_sha256": sha256_text(manuscript)}},
        "review": final_review,
    }
    _write_json(output_dir / "publication-editorial-review.json", publication_review)
    draft_id = str(packet["scope"]["scope_id"])
    title = str(packet["editorial_decisions"]["article_title"])
    manifest = {
        "schema_version": "editorial-draft-manifest.v1",
        "drafts": [{
            "draft_id": draft_id, "candidate_id": draft_id, "title": title,
            "passage": str(packet["scope"].get("passage") or "太 16:18"),
            "public_slug": str(packet["scope"].get("public_slug") or draft_id.lower()),
            "public_topics": list(packet["scope"].get("topic_labels") or [title]),
            "relative_path": "final.md",
            "presentation_package_path": "presentation-package.json",
            "audit_config": {
                "plan_id": draft_id,
                "audit_output_path": "program-audit.json",
                "editorial_review_path": "publication-editorial-review.json",
                "publication_decision_path": "automated-publication-decision.json",
                "decision_sections": [
                    {
                        "decision_id": section["section_id"],
                        "markdown_heading": next(
                            item["heading"]
                            for item in packet["editorial_decisions"]["sections"]
                            if item["section_id"] == section["section_id"]
                        ),
                    }
                    for section in author_result["sections"]
                ],
            },
        }],
    }
    _write_json(output_dir / "editorial-draft-manifest.json", manifest)
    decision = {
        "schema_version": "automated-publication-decision.v1", "draft_id": draft_id,
        "decision": "approved", "approval_authority": "automated_quality_gates",
        "human_approval": False, "manuscript_sha256": _file_sha(output_dir / "final.md"),
        "editorial_review_passed": True, "editorial_total_score": current_outcome["total_score"],
        "total_score_decides_nothing": True, "technical_audit_status": "pass",
        "technical_audit_sha256": _file_sha(output_dir / "program-audit.json"),
        "editorial_review_path": "publication-editorial-review.json",
        "editorial_review_sha256": _file_sha(output_dir / "publication-editorial-review.json"),
        "brief_sha256": packet["input_bindings"]["brief_sha256"],
        "evidence_packet_sha256": packet["input_bindings"]["evidence_packet_sha256"],
    }
    _write_json(output_dir / "automated-publication-decision.json", decision)
    publication = publish_editorial_draft(output_dir / "editorial-draft-manifest.json", draft_id, destination_root=repository_root)
    status = {
        "status": "workflow_published", "manuscript_sha256": sha256_text(manuscript),
        "editorial_review_cached": review_cached, "editorial_revision_count": revision_count,
        "revision_cached": revision_cached, "delta_review_cached": delta_cached,
        "program_audit_error_total": 0, "publication": publication,
    }
    _write_json(output_dir / "workflow-status.json", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authoring-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path)
    parser.add_argument("--reviewer-model", default="claude-opus-5")
    parser.add_argument("--revision-model", default="gpt-5.6-sol")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    load_dotenv(PROJECT_ROOT / ".env")
    repository = args.repository_root or wang_platform_paths().repository
    result = run_quality(
        authoring_dir=args.authoring_dir, output_dir=args.output_dir,
        reviewer_client=ClaudeSubscriptionClient(model=args.reviewer_model, reasoning_effort="high", timeout_seconds=args.timeout_seconds),
        revision_client=CodexSubscriptionClient(model=args.revision_model, reasoning_effort="high", timeout_seconds=args.timeout_seconds),
        repository_root=repository, force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "workflow_published" else 2


if __name__ == "__main__":
    raise SystemExit(main())
