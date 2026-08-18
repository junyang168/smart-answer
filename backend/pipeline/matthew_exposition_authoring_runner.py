"""Artifact-backed multi-agent authoring runner for Matthew exposition articles.

This domain runner is intentionally separate from the retired API-level multi-agent
state machine. It writes staging artifacts only and never publishes a manuscript.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

from backend.pipeline.manuscript_grounding_check import check_manuscript_grounding
from backend.pipeline.matthew_exposition_authoring import (
    ADJUDICATION_SCHEMA,
    AUTHOR_RESULT_SCHEMA,
    EDITORIAL_REVIEW_SCHEMA,
    EDITORIAL_REVIEW_PACKET_MAX_BYTES,
    FINAL_DELTA_REVIEW_SCHEMA,
    FINAL_REVIEW_MAX_ATTEMPTS,
    FINAL_REVIEW_TIMEOUT_MAX_SECONDS,
    FINAL_REVIEW_TIMEOUT_MIN_SECONDS,
    RECONSIDERATION_SCHEMA,
    REVISION_SCHEMA,
    AuthoringContractError,
    build_authoring_packet,
    build_authoring_packet_from_store,
    evaluate_editorial_review,
    hard_failures_after_adjudication,
    out_of_scope_dimensions,
    build_editorial_review_packet,
    build_final_delta_review_packet,
    canonical_json,
    deterministic_writing_warnings,
    generation_fingerprint,
    merge_final_delta_review,
    sha256_text,
    validate_author_result,
    validate_editorial_review,
    validate_final_delta_review,
    validate_revision_result,
    validate_strict_schema,
)
from backend.pipeline.stage1 import Stage1AnthropicClient, Stage1OpenAIClient
from backend.pipeline.editorial_draft_audit import write_editorial_draft_audit
from backend.pipeline.editorial_draft_repository import publish_automated_editorial_draft


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
PROMPTS = {
    "author": PROMPT_DIR / "matthew_exposition_author.md",
    "review": PROMPT_DIR / "matthew_exposition_independent_editorial_review.md",
    "adjudication": PROMPT_DIR / "matthew_exposition_editorial_adjudication.md",
    "reconsideration": PROMPT_DIR / "matthew_exposition_editorial_reconsideration.md",
    "revision": PROMPT_DIR / "matthew_exposition_author_revision.md",
    "grounding_revision": PROMPT_DIR / "matthew_exposition_grounding_revision.md",
    "delta_review": PROMPT_DIR / "matthew_exposition_final_delta_review.md",
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


def _final_review_timeout(client: Any) -> float:
    configured = float(getattr(client, "timeout_seconds", 240.0) or 240.0)
    return max(
        FINAL_REVIEW_TIMEOUT_MIN_SECONDS,
        min(configured, FINAL_REVIEW_TIMEOUT_MAX_SECONDS),
    )


def _call_final_reviewer(
    client: Any,
    prompt: str,
    payload: str,
    schema: dict[str, Any],
) -> dict[str, Any]:
    # Stage1 clients perform their own transport/JSON retry loop.  Capping it
    # here guarantees one retry at most; schema validation happens after this
    # call and is deliberately never retried.
    if hasattr(client, "max_retries"):
        client.max_retries = min(
            max(1, int(client.max_retries)), FINAL_REVIEW_MAX_ATTEMPTS
        )
    return client.generate_json(
        prompt,
        payload,
        schema,
        timeout_seconds=_final_review_timeout(client),
    )


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


def _heading_text(anchor: str) -> str:
    return anchor.strip().lstrip("#").strip()


def _build_program_audit_manifest(
    *,
    template: dict[str, Any],
    draft_id: str,
    sections: list[dict[str, Any]],
    scripture_heading: str = "經文與問題",
) -> dict[str, Any]:
    staged = json.loads(json.dumps(template, ensure_ascii=False))
    drafts = [item for item in staged.get("drafts", []) if item.get("draft_id") == draft_id]
    if len(drafts) != 1:
        raise AuthoringContractError(
            f"program audit template must contain draft_id exactly once: {draft_id}"
        )
    draft = drafts[0]
    config = draft.get("audit_config") or {}
    heading_by_decision: dict[str, str] = {}
    for section in sections:
        heading = _heading_text(str(section.get("output_anchor") or ""))
        if not heading:
            raise AuthoringContractError("program audit section has no output anchor")
        for decision_id in section.get("decision_ids", []):
            if decision_id in heading_by_decision:
                raise AuthoringContractError(
                    f"program audit decision is mapped twice: {decision_id}"
                )
            heading_by_decision[decision_id] = heading

    old_heading_decisions: dict[str, list[str]] = {}
    for mapping in config.get("decision_sections", []):
        decision_id = str(mapping.get("decision_id") or "")
        old_heading = str(mapping.get("markdown_heading") or "")
        old_heading_decisions.setdefault(old_heading, []).append(decision_id)
        if decision_id not in heading_by_decision:
            raise AuthoringContractError(
                f"program audit template decision missing from author ledger: {decision_id}"
            )
        mapping["markdown_heading"] = heading_by_decision[decision_id]

    for quotation in config.get("required_scripture_quotations", []):
        old_heading = str(quotation.get("markdown_heading") or "")
        decision_ids = old_heading_decisions.get(old_heading, [])
        mapped_headings = {
            heading_by_decision[decision_id]
            for decision_id in decision_ids
            if decision_id in heading_by_decision
        }
        if len(mapped_headings) != 1:
            raise AuthoringContractError(
                f"cannot remap scripture quotation heading: {old_heading}"
            )
        # Author Agent articles quote the complete passage once under the
        # central Scripture section. Keep every literal marker, but verify it
        # there instead of forcing repeated quotations under reader sections.
        quotation["markdown_heading"] = scripture_heading

    draft["relative_path"] = "manuscript.md"
    draft["presentation_package_path"] = "knowledge-snapshot.json"
    config["knowledge_snapshot_path"] = "knowledge-snapshot.json"
    config["audit_output_path"] = "program-audit.json"
    return {"schema_version": staged["schema_version"], "drafts": [draft]}


def _require_audit_draft(parser: Any, manifest_path: Path, draft_id: str) -> None:
    """Fail at argument-parse time on a manifest that cannot name the draft."""

    try:
        template = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        parser.error(f"--program-audit-manifest is not readable JSON: {exc}")
    matches = [
        item
        for item in template.get("drafts", [])
        if isinstance(item, dict) and item.get("draft_id") == draft_id
    ]
    if len(matches) != 1:
        parser.error(
            f"--program-audit-manifest must contain draft_id exactly once: "
            f"{draft_id} (found {len(matches)})"
        )


def _run_program_audit_stage(
    *,
    template_path: Path,
    draft_id: str,
    knowledge_path: Path,
    output_dir: Path,
    manuscript: str,
    manuscript_sections: list[dict[str, Any]],
) -> dict[str, Any]:
    template = json.loads(template_path.read_text(encoding="utf-8"))
    manifest = _build_program_audit_manifest(
        template=template,
        draft_id=draft_id,
        sections=manuscript_sections,
    )
    audit_dir = output_dir / "program-audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "manuscript.md").write_text(manuscript, encoding="utf-8")
    shutil.copyfile(knowledge_path, audit_dir / "knowledge-snapshot.json")
    manifest_path = audit_dir / "editorial-draft-manifest.json"
    _write_json(manifest_path, manifest)
    audit_path = write_editorial_draft_audit(manifest_path, draft_id)
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    return {
        "status": audit["status"],
        "path": str(audit_path),
        "manifest_path": str(manifest_path),
        "summary": audit["summary"],
        "manuscript_sha256": sha256_text(manuscript),
    }


def _professor_source_texts(packet: dict[str, Any]) -> list[str]:
    """Every text in the packet that carries the professor's own wording.

    Rule 8e sends the author to the sermon transcript segments for his exact
    phrasing; a quote in the draft is checked back against the same texts,
    plus the base manuscripts, so a quote lifted from the approved notes is
    not reported as invented.
    """

    return [
        *(
            text
            for segments in (packet.get("sermon_transcript_texts") or {}).values()
            for text in segments.values()
        ),
        *(packet.get("base_manuscript_texts") or {}).values(),
    ]


def _run_grounding_stage(
    *,
    draft: str,
    packet: dict[str, Any],
    author_sections: list[dict[str, Any]],
    output_dir: Path,
    claude_client: Any,
    force: bool,
    skip: bool,
    report_name: str = "grounding-report.json",
) -> dict[str, Any] | None:
    """Check every attributed paragraph against the material it declares.

    Returns None when the gate is disabled. The report is written whether or
    not it passes, so a failed run leaves the evidence behind rather than only
    a status string.
    """

    if skip:
        return None
    report = check_manuscript_grounding(
        draft,
        packet["knowledge"],
        client=claude_client,
        author_sections=author_sections,
        # A claim created from a base-manuscript sentence carries the
        # editorial instruction itself; there is no contract checklist to
        # read it from any more.
        transcript_texts=packet.get("sermon_transcript_texts") or {},
        # Shared across the repair rounds of one run: a paragraph the repair
        # did not touch keeps the verdict it was already given, so the gate
        # converges instead of re-rolling every paragraph each attempt.
        cache_dir=output_dir / "grounding-cache",
    )
    _write_json(
        output_dir / report_name,
        {
            "schema_version": "matthew-exposition-grounding-report-envelope.v1",
            "generation": {
                "fingerprint": report["manuscript_sha256"],
                "generated_at": _utcnow(),
                "role": "grounding_reviewer",
                "provider": "anthropic",
                "model": claude_client.model,
            },
            "result": report,
        },
    )
    return report


def _repair_grounding(
    *,
    manuscript: str,
    sections: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    output_dir: Path,
    openai_client: Any,
    attempt: int,
    name: str,
    force: bool,
) -> dict[str, Any]:
    """Rewrite the paragraphs a grounding report named, and nothing else."""

    prompt = _read_prompt("grounding_revision")
    repair_input = canonical_json(
        {
            "manuscript_markdown": manuscript,
            "findings": findings,
            # The ledger travels with the draft so the repair can return it
            # unchanged; regenerating it from scratch loses decision_ids and
            # anchors that the repair has no reason to touch.
            "sections": sections,
        }
    )
    fingerprint = generation_fingerprint(
        inputs={
            "repair_input_sha256": sha256_text(repair_input),
            "generation_parameters": _client_generation_parameters(openai_client),
        },
        prompt_text=prompt,
        schema=AUTHOR_RESULT_SCHEMA,
        model=openai_client.model,
        reasoning=f"grounding_repair_{attempt}",
    )
    repaired, _ = _run_cached_stage(
        path=output_dir / name,
        schema_version="matthew-exposition-authoring.v1",
        fingerprint=fingerprint,
        producer={
            "role": "grounding_repair_author",
            "provider": "openai",
            "model": openai_client.model,
            "attempt": attempt,
        },
        generate=lambda: openai_client.generate_json(
            prompt, repair_input, AUTHOR_RESULT_SCHEMA
        ),
        force=force,
    )
    return repaired


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
    auto_accept_maintained_findings: bool = False,
    seed_author_result: dict[str, Any] | None = None,
    revision_round: int = 1,
    max_revision_rounds: int = 1,
    continuation_review: dict[str, Any] | None = None,
    continuation_outcome: dict[str, Any] | None = None,
    program_audit_manifest_path: Path | None = None,
    program_audit_draft_id: str | None = None,
    repository_root: Path | None = None,
    packet: dict[str, Any] | None = None,
    skip_grounding_gate: bool = False,
    grounding_attempt: int = 1,
    max_grounding_attempts: int = 3,
) -> dict[str, Any]:
    # Both program-audit inputs, and the snapshot file the audit copies, are
    # checked before the first model call. They are only *used* on a passing
    # editorial path, at the very end of the run -- so a missing one used to
    # surface after the author, the grounding gate and both reviewers had been
    # paid for, and `knowledge_path=None` surfaced as a TypeError out of
    # `shutil.copyfile` rather than as a reviewable status.
    if (program_audit_manifest_path is None) != (program_audit_draft_id is None):
        raise AuthoringContractError(
            "program audit requires both manifest path and draft_id"
        )
    if program_audit_manifest_path is not None and knowledge_path is None:
        raise AuthoringContractError(
            "program audit needs the knowledge snapshot the packet was built "
            "from; pass the compiled snapshot's path as knowledge_path"
        )

    # A caller that read the plan and its contract from the authoring store
    # passes the built packet directly; `plan_path` / `contract_path` are then
    # unused. They remain required for the file-based path, which is still the
    # only one the CLI exposes.
    if packet is None:
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

    def finish_editorial_pass(
        *,
        editorial_status: str,
        manuscript: str,
        manuscript_sections: list[dict[str, Any]],
        editorial_review: dict[str, Any],
        editorial_outcome: dict[str, Any],
        result: dict[str, Any],
    ) -> dict[str, Any]:
        # The gate ran before the writing reviewer, on the author's draft. The
        # revision then rewrote prose to satisfy editorial findings and nothing
        # re-checked it, so the manuscript that actually publishes had never
        # passed the gate at all -- and the revision prompt's own warning names
        # exactly what it introduces: motives, causes and transitions the
        # material does not carry, added to complete a chain or to smooth a
        # sentence. Checking here covers every finishing path; the per-paragraph
        # cache means an unchanged paragraph costs nothing and only what the
        # revision actually rewrote is paid for.
        final_grounding = _run_grounding_stage(
            draft=manuscript,
            packet=packet,
            author_sections=manuscript_sections,
            output_dir=output_dir,
            claude_client=claude_client,
            force=force,
            skip=skip_grounding_gate,
            report_name="final-grounding-report.json",
        )
        if final_grounding is not None and not final_grounding["passed"]:
            # The pre-review gate repairs and retries; this one used to only
            # stop. That asymmetry is not defensible: the revision is exactly
            # where an unsupported clause gets introduced, so the gate that
            # catches it is the one most in need of a way back. Matthew
            # 16:21-23 failed here on a five-word framing its paragraph had not
            # declared material for, with the whole editorial pass already
            # paid for.
            #
            # Repair, then re-enter through the same recursion the pre-review
            # gate uses, so the repaired draft is re-grounded before any
            # reviewer is paid again.
            if grounding_attempt < max_grounding_attempts and all(
                finding["code"] == "unsupported_assertion"
                for finding in final_grounding["findings"]
            ):
                repaired = _repair_grounding(
                    manuscript=manuscript,
                    sections=manuscript_sections,
                    findings=final_grounding["findings"],
                    output_dir=output_dir,
                    openai_client=openai_client,
                    attempt=grounding_attempt,
                    name=f"final-grounding-repair-{grounding_attempt:02d}.json",
                    force=force,
                )
                return run_authoring(
                    packet=packet,
                    plan_path=plan_path,
                    knowledge_path=knowledge_path,
                    contract_path=contract_path,
                    publication_profile_path=publication_profile_path,
                    quality_profile_path=quality_profile_path,
                    output_dir=output_dir,
                    openai_client=openai_client,
                    claude_client=claude_client,
                    force=force,
                    auto_accept_maintained_findings=auto_accept_maintained_findings,
                    seed_author_result=repaired,
                    revision_round=revision_round,
                    max_revision_rounds=max_revision_rounds,
                    program_audit_manifest_path=program_audit_manifest_path,
                    program_audit_draft_id=program_audit_draft_id,
                    repository_root=repository_root,
                    skip_grounding_gate=skip_grounding_gate,
                    grounding_attempt=grounding_attempt + 1,
                    max_grounding_attempts=max_grounding_attempts,
                )
            return {
                **result,
                "status": "final_grounding_gate_failed",
                "editorial_status": editorial_status,
                "grounding_attempts": grounding_attempt,
                "final_grounding_report_path": str(
                    output_dir / "final-grounding-report.json"
                ),
                "unsupported_paragraph_count": len(final_grounding["findings"]),
            }
        if program_audit_manifest_path is None or program_audit_draft_id is None:
            return result
        audit = _run_program_audit_stage(
            template_path=program_audit_manifest_path,
            draft_id=program_audit_draft_id,
            knowledge_path=knowledge_path,
            output_dir=output_dir,
            manuscript=manuscript,
            manuscript_sections=manuscript_sections,
        )
        if audit["status"] not in {"pass", "pass_with_warnings"}:
            return {
                **result,
                "status": "program_audit_failed",
                "editorial_status": editorial_status,
                "program_audit": audit,
            }
        if (
            editorial_outcome.get("manuscript_sha256") != sha256_text(manuscript)
            or editorial_outcome.get("passed") is not True
            or editorial_outcome.get("hard_gate_failures")
            or editorial_outcome.get("declared_hard_failures")
        ):
            raise AuthoringContractError(
                "automatic publication requires a bound passing editorial outcome"
            )
        audit_dir = Path(audit["manifest_path"]).parent
        publication_review_path = audit_dir / "publication-editorial-review.json"
        _write_json(
            publication_review_path,
            {
                "schema_version": "automated-editorial-review.v1",
                "reviewed_draft_sha256": editorial_outcome["manuscript_sha256"],
                "manuscript_sha256": editorial_outcome["manuscript_sha256"],
                "passed": True,
                "total_score": editorial_outcome["total_score"],
                "hard_gate_failures": editorial_outcome["hard_gate_failures"],
                "declared_hard_failures": editorial_outcome[
                    "declared_hard_failures"
                ],
                "review": editorial_review,
            },
        )
        publication = publish_automated_editorial_draft(
            Path(audit["manifest_path"]),
            program_audit_draft_id,
            publication_review_path,
            destination_root=repository_root,
        )
        return {
            **result,
            "status": "workflow_published",
            "editorial_status": editorial_status,
            "program_audit": audit,
            "publication_decision_path": publication[
                "publication_decision_path"
            ],
            "publication": publication,
        }

    # A grounding repair recurses into the same output directory. Writing its
    # seeded result over `authoring.json` destroyed the author's own artifact,
    # whose fingerprint is what lets a re-invocation skip the author call: the
    # seed's fingerprint is keyed on the seed manuscript, so a fresh run never
    # matched it and re-drafted the whole article from scratch. One
    # interrupted run cost six full drafts.
    author_path = (
        output_dir / "authoring.json"
        if grounding_attempt == 1
        else output_dir / f"authoring-grounding-{grounding_attempt:02d}.json"
    )
    if seed_author_result is None:
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
            path=author_path,
            schema_version="matthew-exposition-authoring.v1",
            fingerprint=author_fingerprint,
            producer={"role": "author", "provider": "openai", "model": openai_client.model},
            generate=lambda: openai_client.generate_json(
                author_prompt, packet_text, AUTHOR_RESULT_SCHEMA
            ),
            force=force,
        )
    else:
        seeded_author_result = {
            "status": "drafted",
            "manuscript_markdown": seed_author_result["manuscript_markdown"],
            "sections": seed_author_result["sections"],
            "plan_change_requests": seed_author_result.get("plan_change_requests", []),
        }
        author_fingerprint = generation_fingerprint(
            inputs={
                "packet_sha256": packet_sha,
                "seed_manuscript_sha256": sha256_text(
                    seeded_author_result["manuscript_markdown"]
                ),
                "revision_round": revision_round,
            },
            prompt_text="deterministic revision-round seed",
            schema=AUTHOR_RESULT_SCHEMA,
            model="deterministic",
            reasoning="verified_prior_revision",
        )
        author_result, author_cached = _run_cached_stage(
            path=author_path,
            schema_version="matthew-exposition-authoring.v1",
            fingerprint=author_fingerprint,
            producer={
                "role": "revision_round_seed",
                "provider": "deterministic",
                "revision_round": revision_round,
            },
            generate=lambda: seeded_author_result,
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
            "authoring_path": str(author_path),
            "author_cached": author_cached,
        }

    draft = author_result["manuscript_markdown"]

    # Grounding runs before the writing reviewer, not after it. The rubric
    # scores whether a paragraph reads like a complete argument; it cannot
    # tell an inference the sources support from one the author supplied,
    # because the two are indistinguishable in form. Letting an ungrounded
    # draft reach review means the score is being computed over prose whose
    # factual basis nothing has checked.
    grounding_report = _run_grounding_stage(
        draft=draft,
        packet=packet,
        author_sections=author_result.get("sections") or [],
        output_dir=output_dir,
        claude_client=claude_client,
        force=force,
        skip=skip_grounding_gate,
    )
    # A one-word drift from the material must not discard the whole draft.
    # The gate runs before the writing reviewer, so without this there is no
    # path back: the author gets one attempt and any paragraph that overreaches
    # ends the run. Feed the findings back for a bounded, targeted repair.
    if (
        grounding_report is not None
        and not grounding_report["passed"]
        and grounding_attempt < max_grounding_attempts
        and all(f["code"] == "unsupported_assertion" for f in grounding_report["findings"])
    ):
        repaired = _repair_grounding(
            manuscript=draft,
            sections=author_result["sections"],
            findings=grounding_report["findings"],
            output_dir=output_dir,
            openai_client=openai_client,
            attempt=grounding_attempt,
            name=f"grounding-repair-{grounding_attempt:02d}.json",
            force=force,
        )
        return run_authoring(
            packet=packet,
            plan_path=plan_path,
            knowledge_path=knowledge_path,
            contract_path=contract_path,
            publication_profile_path=publication_profile_path,
            quality_profile_path=quality_profile_path,
            output_dir=output_dir,
            openai_client=openai_client,
            claude_client=claude_client,
            force=force,
            auto_accept_maintained_findings=auto_accept_maintained_findings,
            seed_author_result=repaired,
            revision_round=revision_round,
            max_revision_rounds=max_revision_rounds,
            continuation_review=continuation_review,
            continuation_outcome=continuation_outcome,
            program_audit_manifest_path=program_audit_manifest_path,
            program_audit_draft_id=program_audit_draft_id,
            repository_root=repository_root,
            skip_grounding_gate=skip_grounding_gate,
            grounding_attempt=grounding_attempt + 1,
            max_grounding_attempts=max_grounding_attempts,
        )
    if grounding_report is not None and not grounding_report["passed"]:
        return {
            "status": "grounding_gate_failed",
            "grounding_attempts": grounding_attempt,
            "grounding_report_path": str(output_dir / "grounding-report.json"),
            "authoring_path": str(author_path),
            "author_cached": author_cached,
            "unsupported_paragraph_count": len(grounding_report["findings"]),
        }
    (output_dir / "draft.md").write_text(draft, encoding="utf-8")
    draft_sha = sha256_text(draft)
    if (continuation_review is None) != (continuation_outcome is None):
        raise AuthoringContractError(
            "revision continuation requires both baseline review and outcome"
        )

    # Built only on the branch that calls a reviewer; an inherited delta
    # review has no packet of its own to carry the slice forward.
    editorial_review_packet: dict[str, Any] | None = None
    if continuation_review is not None and continuation_outcome is not None:
        # A later revision round continues directly from the preceding Delta
        # Review. It must not call a second reviewer before revising again.
        validate_strict_schema(continuation_review, EDITORIAL_REVIEW_SCHEMA)
        verified_override = validate_editorial_review(
            continuation_review,
            contract=packet["base_contract"],
            manuscript=draft,
            quality_profile=packet["quality_profile"],
            # This is the merged review inherited from the previous round, not
            # a fresh assessment. "Below the threshold with nothing blocking
            # left" is a legitimate state here, and stopping for a human is
            # already how the runner handles it.
            require_blocking_finding_when_failing=False,
        )
        comparable_override = {
            key: value
            for key, value in continuation_outcome.items()
            if key != "manuscript_sha256"
        }
        if comparable_override != verified_override:
            raise AuthoringContractError("revision continuation outcome is not verified")
        if continuation_outcome.get("manuscript_sha256") != draft_sha:
            raise AuthoringContractError(
                "revision continuation SHA does not match manuscript"
            )
        review = json.loads(json.dumps(continuation_review))
        review_outcome = dict(continuation_outcome)
        review_cached = True
        _write_json(
            output_dir / "independent-editorial-review.json",
            {
                "schema_version": "matthew-exposition-inherited-delta-review.v1",
                "generation": {
                    "fingerprint": sha256_text(canonical_json(review)),
                    "generated_at": _utcnow(),
                    "role": "verified_delta_review_inheritance",
                    "provider": "deterministic",
                },
                "result": review,
                "checks": {"rubric_outcome": review_outcome},
            },
        )
    else:
        review_prompt = _read_prompt("review")
        editorial_review_packet = build_editorial_review_packet(
            authoring_packet=packet,
            author_result=author_result,
        )
        _write_json(
            output_dir / "editorial-review-packet.json",
            {
                "schema_version": "matthew-exposition-editorial-review-packet-envelope.v1",
                "generation": {
                    "fingerprint": sha256_text(canonical_json(editorial_review_packet)),
                    "generated_at": _utcnow(),
                    "role": "editorial_packet_builder",
                    "provider": "deterministic",
                },
                "result": editorial_review_packet,
            },
        )
        review_input = canonical_json(editorial_review_packet)
        review_fingerprint = generation_fingerprint(
            inputs={
                "editorial_review_packet_sha256": sha256_text(review_input),
                "draft_sha256": draft_sha,
                "generation_parameters": {
                    **_client_generation_parameters(claude_client),
                    "timeout_seconds": _final_review_timeout(claude_client),
                    "max_attempts": FINAL_REVIEW_MAX_ATTEMPTS,
                },
            },
            prompt_text=review_prompt,
            schema=EDITORIAL_REVIEW_SCHEMA,
            model=claude_client.model,
            reasoning="independent_review",
        )

        def generate_initial_review() -> dict[str, Any]:
            generated = _call_final_reviewer(
                claude_client, review_prompt, review_input, EDITORIAL_REVIEW_SCHEMA
            )
            # Literal anchors and all other contracts are verified before the
            # response can be persisted or used by another stage.
            validate_editorial_review(
                generated,
                contract=packet["base_contract"],
                manuscript=draft,
                quality_profile=packet["quality_profile"],
            )
            return generated

        review, review_cached = _run_cached_stage(
            path=output_dir / "independent-editorial-review.json",
            schema_version="matthew-exposition-editorial-review.v1",
            fingerprint=review_fingerprint,
            producer={
                "role": "independent_editor",
                "provider": "anthropic",
                "model": claude_client.model,
            },
            generate=generate_initial_review,
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
        review_outcome["manuscript_sha256"] = draft_sha
    writing_warnings = deterministic_writing_warnings(
        draft,
        packet["quality_profile"],
        source_texts=_professor_source_texts(packet),
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
        result = {
            "status": "editorial_pass_no_revision",
            "draft_path": str(output_dir / "draft.md"),
            "review_path": str(output_dir / "independent-editorial-review.json"),
            "author_cached": author_cached,
            "review_cached": review_cached,
        }
        return finish_editorial_pass(
            editorial_status=result["status"],
            manuscript=draft,
            manuscript_sections=author_result["sections"],
            editorial_review=review,
            editorial_outcome=review_outcome,
            result=result,
        )

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
    auto_accepted_ids: set[str] = set()
    if auto_accept_maintained_findings and maintained_ids:
        # Fully automated staging takes the conservative direction: a
        # reviewer-maintained blocking concern is revised, never silently
        # waived. This does not grant publication approval.
        auto_accepted_ids = set(maintained_ids)
        accepted_ids.update(auto_accepted_ids)
        maintained_ids.clear()
    accepted_findings = [item for item in findings if item["finding_id"] in accepted_ids]

    # A hard failure rests on the finding that evidenced it. If adjudication
    # rejected that finding, the declaration went with it -- otherwise the run
    # deadlocks: nothing left to revise, but a one-vote veto still standing.
    kept_failures, withdrawn_failures = hard_failures_after_adjudication(
        review, withdrawn_ids
    )
    if withdrawn_failures:
        review["hard_failures"] = kept_failures
        review_outcome = evaluate_editorial_review(
            review,
            packet["quality_profile"],
            out_of_scope_dimensions(packet["base_contract"]),
        )
        review_outcome["manuscript_sha256"] = draft_sha

    consensus = {
        "schema_version": "matthew-exposition-reviewed-findings.v1",
        "accepted_finding_ids": sorted(accepted_ids),
        "auto_accepted_maintained_finding_ids": sorted(auto_accepted_ids),
        "withdrawn_finding_ids": sorted(withdrawn_ids),
        "human_required_finding_ids": sorted(maintained_ids),
        "withdrawn_hard_failures": withdrawn_failures,
        "rubric_outcome_after_adjudication": review_outcome,
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
        result = {
            "status": "editorial_pass_after_adjudication",
            "draft_path": str(output_dir / "draft.md"),
        }
        return finish_editorial_pass(
            editorial_status=result["status"],
            manuscript=draft,
            manuscript_sections=author_result["sections"],
            editorial_review=review,
            editorial_outcome=review_outcome,
            result=result,
        )

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
    # A revision asking for a plan change is a legitimate outcome: the author
    # has found that satisfying an accepted finding would need material the
    # CompositionPlan does not authorise. Handle it before validation, which
    # rejects the combination of that status with a manuscript -- correctly,
    # since a handoff must not double as a final draft, but a model returning
    # both should end the run with a reviewable status rather than a traceback.
    if revision.get("status") == "plan_change_required":
        return {
            "status": "plan_change_required_after_review",
            "revision_path": str(output_dir / "revision-01.json"),
            "plan_change_requests": revision.get("plan_change_requests", []),
            "returned_manuscript_with_handoff": bool(revision.get("manuscript_markdown")),
        }
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
    revised_draft = revision.get("manuscript_markdown", "")
    if not revised_draft.strip():
        raise AuthoringContractError("revision must return the complete manuscript")
    (output_dir / "revised-draft.md").write_text(revised_draft, encoding="utf-8")
    delta_packet = build_final_delta_review_packet(
        baseline_review=review,
        baseline_outcome=review_outcome,
        baseline_manuscript=draft,
        revised_manuscript=revised_draft,
        accepted_findings=accepted_findings,
        dispositions=dispositions,
        quality_profile=packet["quality_profile"],
        contract=packet["base_contract"],
        baseline_sections=author_result["sections"],
        # The same slice the first reviewer scored against, not a freshly
        # built one: the two rounds must agree on what the sources say.
        source_slice=(editorial_review_packet or {}).get("source_slice"),
    )
    _write_json(
        output_dir / "final-delta-review-packet.json",
        {
            "schema_version": "matthew-exposition-final-delta-review-packet-envelope.v1",
            "generation": {
                "fingerprint": sha256_text(canonical_json(delta_packet)),
                "generated_at": _utcnow(),
                "role": "final_delta_packet_builder",
                "provider": "deterministic",
            },
            "result": delta_packet,
        },
    )
    delta_prompt = _read_prompt("delta_review")
    delta_input = canonical_json(delta_packet)
    delta_fingerprint = generation_fingerprint(
        inputs={
            "delta_packet_sha256": sha256_text(delta_input),
            "manuscript_sha256": delta_packet["manuscript_sha256"],
            "generation_parameters": {
                **_client_generation_parameters(claude_client),
                "timeout_seconds": _final_review_timeout(claude_client),
                "max_attempts": FINAL_REVIEW_MAX_ATTEMPTS,
            },
        },
        prompt_text=delta_prompt,
        schema=FINAL_DELTA_REVIEW_SCHEMA,
        model=claude_client.model,
        reasoning="final_delta_review",
    )

    def generate_delta_review() -> dict[str, Any]:
        generated = _call_final_reviewer(
            claude_client, delta_prompt, delta_input, FINAL_DELTA_REVIEW_SCHEMA
        )
        validate_final_delta_review(
            generated,
            packet=delta_packet,
            revised_manuscript=revised_draft,
            quality_profile=packet["quality_profile"],
        )
        return generated

    delta_review, delta_cached = _run_cached_stage(
        path=output_dir / "final-delta-editorial-review.json",
        schema_version="matthew-exposition-final-delta-review.v1",
        fingerprint=delta_fingerprint,
        producer={
            "role": "final_delta_editor",
            "provider": "anthropic",
            "model": claude_client.model,
        },
        generate=generate_delta_review,
        force=force,
    )
    validate_final_delta_review(
        delta_review,
        packet=delta_packet,
        revised_manuscript=revised_draft,
        quality_profile=packet["quality_profile"],
    )
    _canonicalize_findings(delta_review, delta_packet["manuscript_sha256"])
    merged_review, final_outcome = merge_final_delta_review(
        contract=packet["base_contract"],
        baseline_review=review,
        baseline_outcome=review_outcome,
        delta_review=delta_review,
        packet=delta_packet,
        quality_profile=packet["quality_profile"],
    )
    delta_artifact = json.loads(
        (output_dir / "final-delta-editorial-review.json").read_text(encoding="utf-8")
    )
    delta_artifact["result"] = delta_review
    delta_artifact["checks"] = {
        "merged_review": merged_review,
        "rubric_outcome": final_outcome,
        "deterministic_warnings": deterministic_writing_warnings(
            revised_draft,
            packet["quality_profile"],
            source_texts=_professor_source_texts(packet),
        ),
    }
    _write_json(output_dir / "final-delta-editorial-review.json", delta_artifact)
    if final_outcome["passed"] and not delta_review["findings"]:
        status = "editorial_pass_after_delta_review"
    elif not delta_review["findings"]:
        status = "editorial_threshold_unmet_no_actionable_delta"
    else:
        status = "revision_required_after_delta_review"
    if (
        delta_review["findings"]
        and revision_round < max_revision_rounds
    ):
        next_output_dir = output_dir / f"round-{revision_round + 1:02d}"
        next_result = run_authoring(
            plan_path=plan_path,
            knowledge_path=knowledge_path,
            contract_path=contract_path,
            publication_profile_path=publication_profile_path,
            quality_profile_path=quality_profile_path,
            output_dir=next_output_dir,
            openai_client=openai_client,
            claude_client=claude_client,
            force=force,
            auto_accept_maintained_findings=auto_accept_maintained_findings,
            seed_author_result=revision,
            revision_round=revision_round + 1,
            max_revision_rounds=max_revision_rounds,
            continuation_review={
                key: value
                for key, value in merged_review.items()
                if key != "score_provenance"
            },
            continuation_outcome=final_outcome,
            program_audit_manifest_path=program_audit_manifest_path,
            program_audit_draft_id=program_audit_draft_id,
            repository_root=repository_root,
            # Carry the packet so a store-sourced run does not silently fall
            # back to rebuilding from files on its second revision round.
            packet=packet,
            skip_grounding_gate=skip_grounding_gate,
        )
        return {
            **next_result,
            "continued_from_revision_round": revision_round,
            "prior_round_outcome": final_outcome,
        }
    result = {
        "status": status,
        "revised_draft_path": str(output_dir / "revised-draft.md"),
        "delta_review_path": str(output_dir / "final-delta-editorial-review.json"),
        "delta_review_cached": delta_cached,
        "rubric_outcome": final_outcome,
    }
    if status == "editorial_pass_after_delta_review":
        return finish_editorial_pass(
            editorial_status=status,
            manuscript=revised_draft,
            manuscript_sections=revision["sections"],
            editorial_review=merged_review,
            editorial_outcome=final_outcome,
            result=result,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # PostgreSQL is the authoring authority. `--plan-id` reads the plan and its
    # contract from there; `--plan` / `--base-contract` read them from local
    # JSON, which predates the store and is kept until every article has been
    # migrated.
    parser.add_argument(
        "--plan-id",
        help=(
            "CompositionPlan id to read from the authoring store, including its "
            "authoring contract. Mutually exclusive with --plan/--base-contract."
        ),
    )
    parser.add_argument("--plan", type=Path)
    parser.add_argument(
        "--knowledge",
        type=Path,
        help=(
            "Knowledge snapshot file. Omit with --plan-id to compile the "
            "snapshot from the authoring store instead, so claims promoted "
            "there are visible to the author."
        ),
    )
    parser.add_argument("--base-contract", type=Path)
    parser.add_argument("--publication-profile", type=Path, required=True)
    parser.add_argument("--quality-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--openai-model", default="gpt-5.6-sol")
    parser.add_argument("--claude-model", default="claude-sonnet-5")
    parser.add_argument("--openai-reasoning-effort", default="medium")
    parser.add_argument("--timeout-seconds", type=float, default=240.0)
    parser.add_argument("--claude-max-output-tokens", type=int, default=64000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--auto-accept-maintained-findings",
        action="store_true",
        help=(
            "Continue staging by conservatively sending reviewer-maintained "
            "findings to revision; never grants publication approval."
        ),
    )
    parser.add_argument(
        "--max-revision-rounds",
        type=int,
        choices=(1, 2),
        default=2,
        help="Maximum automatic author/review revision rounds (default: 2).",
    )
    parser.add_argument("--program-audit-manifest", type=Path)
    parser.add_argument("--program-audit-draft-id")
    parser.add_argument(
        "--repository-root",
        type=Path,
        help=(
            "Override Wang repository destination; defaults to "
            "DATA_BASE_DIR/wang-knowledge-platform/repository."
        ),
    )
    parser.add_argument(
        "--max-grounding-attempts",
        type=int,
        default=3,
        help=(
            "Grounding checks per run, so at most this many minus one targeted "
            "repairs (default: 3). The bound exists so a run cannot loop, not "
            "to cap it at any particular number: it was 2 when every attempt "
            "re-checked every paragraph, which was both expensive and unable "
            "to converge. With per-paragraph verdicts cached, a repair costs "
            "only the paragraphs it rewrote."
        ),
    )
    parser.add_argument(
        "--skip-grounding-gate",
        action="store_true",
        help=(
            "Skip the per-paragraph grounding check. Diagnostic only: it removes "
            "the only check that a paragraph does not assert more than its sources."
        ),
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.plan_id and (args.plan or args.base_contract):
        parser.error("--plan-id cannot be combined with --plan or --base-contract")
    if not args.plan_id and not (args.plan and args.base_contract):
        parser.error("either --plan-id, or both --plan and --base-contract, are required")
    if not args.plan_id and not args.knowledge:
        parser.error("--knowledge is required with --plan/--base-contract")
    if bool(args.program_audit_manifest) != bool(args.program_audit_draft_id):
        parser.error(
            "--program-audit-manifest and --program-audit-draft-id must be given together"
        )
    # The manifest is not read until the run has already passed editorial
    # review, so a draft_id that is absent or duplicated there used to end a
    # fully paid run. It costs one file read to say so before anything starts.
    if args.program_audit_manifest:
        _require_audit_draft(parser, args.program_audit_manifest, args.program_audit_draft_id)

    # Written beside the run's other artifacts rather than into a temporary
    # directory that disappears with the packet builder: the Program Audit
    # copies this file at the end of the run, and it is the record of what the
    # author actually wrote against.
    knowledge_path = args.knowledge
    if args.plan_id:
        load_dotenv(PROJECT_ROOT / ".env")
        from backend.api.canonical_repository.postgres_store import (
            PostgresKnowledgeStore,
        )

        compiled_snapshot_path = (
            None if args.dry_run or args.knowledge
            else args.output_dir / "compiled-knowledge-snapshot.json"
        )
        packet = build_authoring_packet_from_store(
            plan_id=args.plan_id,
            store=PostgresKnowledgeStore(),
            knowledge_path=args.knowledge,  # None -> compile from the store
            compiled_snapshot_path=compiled_snapshot_path,
            publication_profile_path=args.publication_profile,
            quality_profile_path=args.quality_profile,
        )
        if compiled_snapshot_path is not None:
            knowledge_path = compiled_snapshot_path
    else:
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
        packet=packet,
        plan_path=args.plan,
        knowledge_path=knowledge_path,
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
            max_retries=FINAL_REVIEW_MAX_ATTEMPTS,
            # The editorial review scores ten dimensions with evidence for each
            # and then writes its findings, while an adaptive-thinking model
            # spends its reasoning from this same budget. At the 20000 default
            # the review came back truncated mid-string.
            max_output_tokens=args.claude_max_output_tokens,
        ),
        force=args.force,
        auto_accept_maintained_findings=args.auto_accept_maintained_findings,
        max_revision_rounds=args.max_revision_rounds,
        program_audit_manifest_path=args.program_audit_manifest,
        program_audit_draft_id=args.program_audit_draft_id,
        skip_grounding_gate=args.skip_grounding_gate,
        max_grounding_attempts=args.max_grounding_attempts,
        repository_root=args.repository_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    terminal_success = {
        "editorial_pass_no_revision",
        "editorial_pass_after_adjudication",
        "editorial_pass_after_delta_review",
        "workflow_published",
    }
    return 0 if result["status"] in terminal_success else 2


if __name__ == "__main__":
    raise SystemExit(main())
