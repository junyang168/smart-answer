"""Generate and ground a theological topic essay from an approved brief."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.manuscript_grounding_check import check_manuscript_grounding
from backend.pipeline.matthew_exposition_authoring import (
    canonical_json,
    generation_fingerprint,
    sha256_text,
    validate_strict_schema,
)
from backend.pipeline.theological_topic_authoring import (
    TOPIC_AUTHOR_SCHEMA,
    TOPIC_GROUNDING_REVISION_SCHEMA,
    build_topic_authoring_packet,
    editorial_instructions_by_claim,
    validate_topic_author_result,
    validate_topic_grounding_revision,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AUTHOR_PROMPT = (
    Path(__file__).resolve().parent
    / "prompts"
    / "theological_topic_essay_author.md"
)
GROUNDING_REVISION_PROMPT = (
    Path(__file__).resolve().parent
    / "prompts"
    / "theological_topic_grounding_revision.md"
)
MAX_GROUNDING_REVISIONS = 2


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _result(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value.get("result"), dict):
        return dict(value["result"])
    return value


def _archive(path: Path) -> None:
    if not path.exists():
        return
    archive = path.parent / "generations"
    archive.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path.replace(archive / f"{path.stem}.{stamp}{path.suffix}")


def _number_grounding_findings(
    findings: list[Mapping[str, Any]], *, manuscript_sha256: str
) -> list[dict[str, Any]]:
    return [
        {
            "finding_id": f"TGF-{manuscript_sha256[:10]}-{index:03d}",
            **dict(finding),
        }
        for index, finding in enumerate(findings, start=1)
    ]


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


def run_authoring(
    *,
    evidence_packet: Mapping[str, Any],
    approved_brief: Mapping[str, Any],
    publication_profile: Mapping[str, Any],
    quality_profile: Mapping[str, Any],
    output_dir: Path,
    author_client: Any,
    grounding_client: Any | None,
    force: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    packet = build_topic_authoring_packet(
        evidence_packet=evidence_packet,
        approved_brief=approved_brief,
        publication_profile=publication_profile,
        quality_profile=quality_profile,
    )
    _write_json(
        output_dir / "topic-authoring-packet.json",
        {
            "schema_version": "wang_theological_topic_authoring_packet_envelope_v1",
            "generation": {
                "fingerprint": packet["packet_sha256"],
                "generated_at": _utcnow(),
                "role": "topic_authoring_packet_builder",
                "provider": "deterministic",
            },
            "result": packet,
        },
    )
    prompt = AUTHOR_PROMPT.read_text(encoding="utf-8")
    payload = canonical_json(packet)
    fingerprint = generation_fingerprint(
        inputs={
            "packet_sha256": packet["packet_sha256"],
            "backend": getattr(author_client, "backend", "api"),
            "reasoning_effort": getattr(
                author_client, "reasoning_effort", "unknown"
            ),
        },
        prompt_text=prompt,
        schema=TOPIC_AUTHOR_SCHEMA,
        model=author_client.model,
        reasoning=getattr(author_client, "reasoning_effort", "unknown"),
    )

    def generate_author_result() -> dict[str, Any]:
        generated = author_client.generate_json(prompt, payload, TOPIC_AUTHOR_SCHEMA)
        try:
            validate_strict_schema(generated, TOPIC_AUTHOR_SCHEMA)
            validate_topic_author_result(generated, authoring_packet=packet)
        except Exception as exc:
            rejected = output_dir / "rejected-generations"
            rejected.mkdir(parents=True, exist_ok=True)
            _write_json(
                rejected / f"author.{sha256_json(generated)[:16]}.json",
                {
                    "schema_version": "wang_theological_topic_rejected_generation_v1",
                    "stage": "author",
                    "packet_sha256": packet["packet_sha256"],
                    "validation_error": f"{type(exc).__name__}: {exc}",
                    "result": generated,
                },
            )
            raise
        return generated

    author_result, author_cached = _run_cached_stage(
        path=output_dir / "topic-authoring.json",
        schema_version="wang_theological_topic_author_result_envelope_v1",
        fingerprint=fingerprint,
        producer={
            "role": "topic_author",
            "provider": getattr(author_client, "backend", "api"),
            "model": author_client.model,
        },
        generate=generate_author_result,
        force=force,
    )
    validate_topic_author_result(author_result, authoring_packet=packet)
    if author_result["status"] == "composition_change_required":
        status = {
            "status": "composition_change_required",
            "stage": "author",
            "packet_sha256": packet["packet_sha256"],
            "author_cached": author_cached,
            "requests": author_result["composition_change_requests"],
        }
        _write_json(output_dir / "workflow-status.json", status)
        return status

    manuscript = author_result["manuscript_markdown"]
    (output_dir / "draft.md").write_text(manuscript, encoding="utf-8")
    if grounding_client is None:
        status = {
            "status": "drafted_grounding_not_run",
            "stage": "author",
            "packet_sha256": packet["packet_sha256"],
            "manuscript_sha256": sha256_text(manuscript),
            "author_cached": author_cached,
        }
        _write_json(output_dir / "workflow-status.json", status)
        return status

    grounding_revision_prompt = GROUNDING_REVISION_PROMPT.read_text(encoding="utf-8")
    grounding_revision_count = 0
    grounding_revision_cached: list[bool] = []
    while True:
        grounding = check_manuscript_grounding(
            manuscript,
            packet["knowledge"],
            client=grounding_client,
            author_sections=author_result["sections"],
            instructions_by_claim=editorial_instructions_by_claim(
                authoring_packet=packet, author_result=author_result
            ),
            cache_dir=output_dir / "grounding-cache",
        )
        numbered_findings = _number_grounding_findings(
            grounding["findings"],
            manuscript_sha256=grounding["manuscript_sha256"],
        )
        grounding = {**grounding, "findings": numbered_findings}
        report_name = (
            "grounding-report.json"
            if grounding_revision_count == 0
            else f"grounding-report-{grounding_revision_count + 1:02d}.json"
        )
        _write_json(
            output_dir / report_name,
            {
                "schema_version": "wang_theological_topic_grounding_report_envelope_v1",
                "generation": {
                    "fingerprint": grounding["manuscript_sha256"],
                    "generated_at": _utcnow(),
                    "role": "paragraph_grounding_reviewer",
                    "provider": getattr(grounding_client, "backend", "api"),
                    "model": grounding_client.model,
                },
                "result": grounding,
            },
        )
        if grounding["passed"]:
            (output_dir / "grounded-draft.md").write_text(
                manuscript, encoding="utf-8"
            )
            break
        if (
            grounding_revision_count >= MAX_GROUNDING_REVISIONS
            or any(item["code"] != "unsupported_assertion" for item in numbered_findings)
        ):
            break

        baseline_sha = grounding["manuscript_sha256"]
        revision_payload = canonical_json(
            {
                "schema_version": "wang_theological_topic_grounding_revision_packet_v1",
                "authoring_packet": packet,
                "baseline_author_result": author_result,
                "baseline_manuscript_sha256": baseline_sha,
                "grounding_findings": numbered_findings,
            }
        )
        _write_json(
            output_dir / f"topic-grounding-revision-packet-{grounding_revision_count + 1:02d}.json",
            {
                "schema_version": "wang_theological_topic_grounding_revision_packet_envelope_v1",
                "generation": {
                    "fingerprint": sha256_text(revision_payload),
                    "generated_at": _utcnow(),
                    "role": "topic_grounding_revision_packet_builder",
                    "provider": "deterministic",
                },
                "result": json.loads(revision_payload),
            },
        )
        revision_fingerprint = generation_fingerprint(
            inputs={
                "packet_sha256": packet["packet_sha256"],
                "baseline_manuscript_sha256": baseline_sha,
                "grounding_findings_sha256": sha256_json(numbered_findings),
                "backend": getattr(author_client, "backend", "api"),
            },
            prompt_text=grounding_revision_prompt,
            schema=TOPIC_GROUNDING_REVISION_SCHEMA,
            model=author_client.model,
            reasoning=getattr(author_client, "reasoning_effort", "unknown"),
        )
        revision_number = grounding_revision_count + 1

        def generate_grounding_revision() -> dict[str, Any]:
            generated = author_client.generate_json(
                grounding_revision_prompt,
                revision_payload,
                TOPIC_GROUNDING_REVISION_SCHEMA,
            )
            validate_strict_schema(generated, TOPIC_GROUNDING_REVISION_SCHEMA)
            validate_topic_grounding_revision(
                generated,
                baseline_manuscript_sha256=baseline_sha,
                findings=numbered_findings,
                authoring_packet=packet,
            )
            return generated

        revision, revision_cached = _run_cached_stage(
            path=output_dir / f"topic-grounding-revision-{revision_number:02d}.json",
            schema_version="wang_theological_topic_grounding_revision_envelope_v1",
            fingerprint=revision_fingerprint,
            producer={
                "role": "topic_grounding_revision",
                "provider": getattr(author_client, "backend", "api"),
                "model": author_client.model,
            },
            generate=generate_grounding_revision,
            force=force,
        )
        grounding_revision_cached.append(revision_cached)
        author_result = revision["revised_author_result"]
        if author_result["status"] == "composition_change_required":
            status = {
                "status": "composition_change_required",
                "stage": "grounding_revision",
                "packet_sha256": packet["packet_sha256"],
                "baseline_manuscript_sha256": baseline_sha,
                "author_cached": author_cached,
                "grounding_revision_count": revision_number,
                "requests": author_result["composition_change_requests"],
            }
            _write_json(output_dir / "workflow-status.json", status)
            return status
        manuscript = author_result["manuscript_markdown"]
        grounding_revision_count = revision_number
        (output_dir / f"revised-draft-{revision_number:02d}.md").write_text(
            manuscript, encoding="utf-8"
        )
    status = {
        "status": "draft_grounded" if grounding["passed"] else "grounding_gate_failed",
        "stage": "grounding",
        "packet_sha256": packet["packet_sha256"],
        "manuscript_sha256": grounding["manuscript_sha256"],
        "author_cached": author_cached,
        "paragraphs_checked": grounding["paragraphs_checked"],
        "grounding_finding_count": len(grounding["findings"]),
        "grounding_revision_count": grounding_revision_count,
        "grounding_revision_cached": grounding_revision_cached,
    }
    _write_json(output_dir / "workflow-status.json", status)
    return status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--composition-dir", type=Path, required=True)
    parser.add_argument("--publication-profile", type=Path, required=True)
    parser.add_argument("--quality-profile", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--author-model", default="gpt-5.6-sol")
    parser.add_argument("--grounding-model", default="claude-sonnet-5")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--timeout-seconds", type=float, default=900.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--skip-grounding-gate", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    evidence = _result(args.composition_dir / "theological-evidence-packet.json")
    brief = _result(args.composition_dir / "theological-editorial-brief.json")
    publication = json.loads(args.publication_profile.read_text(encoding="utf-8"))
    quality = json.loads(args.quality_profile.read_text(encoding="utf-8"))
    if args.dry_run:
        packet = build_topic_authoring_packet(
            evidence_packet=evidence,
            approved_brief=brief,
            publication_profile=publication,
            quality_profile=quality,
        )
        print(
            json.dumps(
                {
                    "status": "inputs_valid",
                    "packet_sha256": packet["packet_sha256"],
                    "brief_sha256": brief["brief_sha256"],
                    "section_count": len(packet["editorial_decisions"]["sections"]),
                    "claim_count": len(packet["knowledge"]["claims"]),
                    "would_call_models": False,
                    "would_write_reader_prose": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    author_client = CodexSubscriptionClient(
        model=args.author_model,
        reasoning_effort=args.reasoning_effort,
        timeout_seconds=args.timeout_seconds,
    )
    grounding_client = (
        None
        if args.skip_grounding_gate
        else ClaudeSubscriptionClient(
            model=args.grounding_model,
            reasoning_effort="high",
            timeout_seconds=args.timeout_seconds,
        )
    )
    result = run_authoring(
        evidence_packet=evidence,
        approved_brief=brief,
        publication_profile=publication,
        quality_profile=quality,
        output_dir=args.output_dir,
        author_client=author_client,
        grounding_client=grounding_client,
        force=args.force,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"draft_grounded", "drafted_grounding_not_run"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
