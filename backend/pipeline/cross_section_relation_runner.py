"""Add the long-distance argument links windowed extraction could not see."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.corpus_survey_runner import PROJECT_ROOT
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
from backend.pipeline.cross_section_relation import (
    DISCOVERY_SCHEMA,
    PROMPT_PATH,
    CrossSectionValidationError,
    apply_proposals,
    build_catalogue,
    discovery_identity,
    record_positions,
    record_section_indexes,
    render_catalogue,
    validate_proposals,
)
from backend.pipeline.llm_usage import usage_row
from backend.pipeline.knowledge_package_merge import (
    KnowledgePackageMergeError,
    validate_merged_package,
)
from backend.pipeline.run_ledger import run_record
from backend.pipeline.source_keys import package_row_key
from backend.pipeline.stage1 import Stage1OpenAIClient
from backend.pipeline.detailed_knowledge_extraction_runner import (
    _package_artifact_sha256,
)

VALIDATION_ATTEMPTS = 3


def _atomic_artifact_write(path: Path, data: bytes) -> None:
    """Install a cache/package generation without exposing partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_bytes() == data:
        return
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _atomic_json_write(path: Path, payload: Any) -> None:
    _atomic_artifact_write(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def _artifact_sha256(package: dict[str, Any]) -> str:
    candidate = json.loads(json.dumps(package, ensure_ascii=False))
    (candidate.get("cross_section_relations") or {}).pop("artifact_sha256", None)
    return hashlib.sha256(
        json.dumps(
            candidate,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _stamp_artifact(package: dict[str, Any]) -> None:
    package.setdefault("cross_section_relations", {})["artifact_sha256"] = (
        _artifact_sha256(package)
    )


def _archive(path: Path) -> None:
    """Preserve the exact prior current artifact before any replacement."""

    if not path.is_file():
        return
    previous = path.read_bytes()
    previous_sha256 = hashlib.sha256(previous).hexdigest()
    archive = (
        path.parent / "generations" / f"{path.stem}.{previous_sha256[:16]}.json"
    )
    if not archive.exists():
        _atomic_artifact_write(archive, previous)


def _validate_input_package(package: dict[str, Any]) -> None:
    if package.get("complete") is not True:
        raise CrossSectionValidationError(
            "cross-section discovery requires a complete extraction package"
        )
    try:
        validate_merged_package(package)
    except KnowledgePackageMergeError as exc:
        raise CrossSectionValidationError(
            f"cross-section input violates graph integrity: {exc}"
        ) from exc
    extraction = package.get("extraction") or {}
    if extraction.get("artifact_sha256") != _package_artifact_sha256(package):
        raise CrossSectionValidationError(
            "cross-section input extraction artifact is incomplete or was modified"
        )


def _section_boundaries(package: dict[str, Any]) -> list[int]:
    """Where the package says its sections start.

    Read off the package rather than configured, so the two stages cannot drift
    apart: resection the source and this stage follows, with no second place to
    remember. Sentence-range chunks may repeat a row start; their fragments
    carry the authoritative extraction section index. Current extraction
    packages persist full ``sections`` rows; older packages persist only a
    ``boundaries`` list. Both encode the same topology and must remain readable.

    A genuinely absent plan is treated as one section. A present but malformed
    or unrecognised plan must fail closed instead of silently taking the
    single-section write-through path: doing that would certify that
    cross-section discovery ran when it actually skipped every relation.
    """

    extraction = package.get("extraction") or {}
    if "section_plan" not in extraction or extraction.get("section_plan") is None:
        return [0]
    plan = extraction["section_plan"]
    if not isinstance(plan, dict):
        raise CrossSectionValidationError(
            "cross-section input section_plan must be an object"
        )
    if not plan:
        raise CrossSectionValidationError(
            "cross-section input has an empty section_plan"
        )

    def checked(values: Any, *, field: str) -> list[int]:
        if not isinstance(values, list) or not values:
            raise CrossSectionValidationError(
                f"cross-section input section_plan.{field} must be a non-empty list"
            )
        boundaries: list[int] = []
        for index, value in enumerate(values):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise CrossSectionValidationError(
                    f"cross-section input section_plan.{field}[{index}] has "
                    "an invalid start boundary"
                )
            boundaries.append(value)
        if boundaries[0] != 0 or boundaries != sorted(boundaries):
            raise CrossSectionValidationError(
                f"cross-section input section_plan.{field} boundaries must "
                "start at 0 and be ordered"
            )
        return boundaries

    from_sections: list[int] | None = None
    if "sections" in plan:
        sections = plan["sections"]
        if not isinstance(sections, list) or not sections:
            raise CrossSectionValidationError(
                "cross-section input section_plan.sections must be a non-empty list"
            )
        if not all(isinstance(row, dict) and "start" in row for row in sections):
            raise CrossSectionValidationError(
                "cross-section input section_plan.sections rows require start"
            )
        from_sections = checked(
            [row["start"] for row in sections], field="sections"
        )
    from_boundaries: list[int] | None = None
    if "boundaries" in plan:
        from_boundaries = checked(plan["boundaries"], field="boundaries")

    resolved: list[int]
    if from_sections is not None and from_boundaries is not None:
        if from_sections != from_boundaries:
            raise CrossSectionValidationError(
                "cross-section input section_plan sections and boundaries disagree"
            )
        resolved = from_sections
    elif from_sections is not None:
        resolved = from_sections
    elif from_boundaries is not None:
        resolved = from_boundaries
    else:
        raise CrossSectionValidationError(
            "cross-section input has an unrecognised section_plan shape"
        )

    declared_count = plan.get("section_count")
    if declared_count is not None and (
        isinstance(declared_count, bool)
        or not isinstance(declared_count, int)
        or declared_count != len(resolved)
    ):
        raise CrossSectionValidationError(
            "cross-section input section_plan.section_count does not match topology"
        )
    return resolved


def _write_through(
    package: dict[str, Any], output_path: Path, *, identity: dict[str, Any]
) -> dict[str, Any]:
    """Emit the package unchanged, saying so, when there is nothing to relate."""

    updated = apply_proposals(package, {}, identity=identity)
    updated["cross_section_relations"]["skipped"] = "single_section"
    _stamp_artifact(updated)
    _archive(output_path)
    _atomic_json_write(output_path, updated)
    print(json.dumps({
        "package": str(output_path.name), "sections": 1,
        "skipped": "single_section",
        "evidence_relations_added": 0, "claim_relations_added": 0,
    }, ensure_ascii=False))
    return updated


def already_current(
    *, package_path: Path, output_path: Path, prompt: str, model_id: str,
    reasoning_effort: str = "medium", max_output_tokens: int = 16000,
    backend: str = "api",
) -> bool:
    """Whether the output on disk already answers this exact question.

    The same comparison `run` makes, lifted out so `main` can ask it *before*
    opening a ledger row. A run that recomputes nothing must not file one: the
    extraction runner opens its record after its own skip check for this
    reason, and this runner did not, so a re-run that did no work still wrote a
    fresh `cross_section` row -- newer than the review that had read the very
    same package, which pushed that review to 舊 for work nobody did.
    """

    raw = package_path.read_bytes()
    package = json.loads(raw.decode("utf-8"))
    _validate_input_package(package)
    if not output_path.is_file():
        return False
    identity = discovery_identity(
        package_sha256=hashlib.sha256(raw).hexdigest(), prompt=prompt,
        model_id=model_id, reasoning_effort=reasoning_effort,
        max_output_tokens=max_output_tokens,
        section_count=len(_section_boundaries(package)), backend=backend,
    )
    try:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        validate_merged_package(existing)
    except (OSError, json.JSONDecodeError, KnowledgePackageMergeError):
        return False
    stored = (existing.get("cross_section_relations") or {}).get("fingerprint_sha256")
    return (
        stored == identity["fingerprint_sha256"]
        and (existing.get("cross_section_relations") or {}).get("artifact_sha256")
        == _artifact_sha256(existing)
    )


def run(
    *,
    package_path: Path,
    output_path: Path,
    client: Stage1OpenAIClient | CodexSubscriptionClient,
    prompt: str,
    force: bool = False,
    usage_sink: list[dict[str, Any]] | None = None,
    record: Any | None = None,
) -> dict[str, Any]:
    raw = package_path.read_bytes()
    package = json.loads(raw.decode("utf-8"))
    _validate_input_package(package)
    boundaries = _section_boundaries(package)
    identity = discovery_identity(
        package_sha256=hashlib.sha256(raw).hexdigest(), prompt=prompt,
        model_id=client.model, section_count=len(boundaries),
        reasoning_effort=client.reasoning_effort,
        max_output_tokens=client.max_output_tokens,
        backend=getattr(client, "backend", "api").replace("_", "-"),
    )
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        stored = (existing.get("cross_section_relations") or {}).get("fingerprint_sha256")
        if (
            stored == identity["fingerprint_sha256"]
            and (existing.get("cross_section_relations") or {}).get("artifact_sha256")
            == _artifact_sha256(existing)
        ):
            return existing

    # One section means there is no cross-section relation to find, and asking
    # anyway is not merely wasteful: every proposal would be same-section, the
    # validator rejects those, and the stage fails after burning three model
    # calls. Writing the package through unchanged lets an orchestrator run
    # this stage for every source instead of having to know which ones were
    # sectioned -- and that knowledge is exactly what got skipped once already,
    # leaving （四）3 without cross-section relations while the 母本 beside it
    # had them.
    if len(boundaries) < 2:
        return _write_through(package, output_path, identity=identity)

    positions = record_positions(package)
    record_sections = record_section_indexes(
        package, positions=positions, boundaries=boundaries
    )
    catalogue = build_catalogue(package, positions)
    if not catalogue:
        raise CrossSectionValidationError(f"{package_path}: no anchored records to relate")
    section_of = {
        row["id"]: record_sections.get(
            row["id"],
            sum(1 for start in boundaries if start <= positions[row["id"]]),
        )
        for row in catalogue
    }
    user_input = (
        f"来源 ID：{package['source_documents'][0]['source_id']}\n"
        f"本篇共 {len(boundaries)} 个章节。**只能提出两端分属不同章节的关系**；"
        f"同一章节内的关系由抽取阶段负责，在此提出会被拒绝。\n\n"
        "以下是本篇已抽取的论证层对象清单，按段号排序：\n\n"
        + render_catalogue(catalogue, section_of)
    )

    last_error: CrossSectionValidationError | None = None
    response = None
    for attempt in range(1, VALIDATION_ATTEMPTS + 1):
        feedback = ""
        if last_error:
            feedback = (
                "\n\n===== 上一版未通过机械验证 =====\n"
                f"{last_error}\n"
                "请删除或修正所有被拒绝的关系，再重新输出完整 JSON。"
            )
        if record is not None:
            record.model_call_started()
        candidate = client.generate_json(
            prompt, feedback, DISCOVERY_SCHEMA, cache_prefix=user_input
        )
        # Every attempt is billed, including the ones validation rejects, so
        # the row has to carry all of them. Recording only the accepted call
        # would price a three-attempt run as though it were a one-attempt run.
        call_usage = usage_row(getattr(client, "last_usage", None), attempt)
        if usage_sink is not None:
            usage_sink.append(call_usage)
        if record is not None:
            record.usage([call_usage])
            record.model_call_completed()
        try:
            validate_proposals(
                candidate,
                package,
                positions=positions,
                boundaries=boundaries,
                sections=record_sections,
                identity=identity,
            )
            response = candidate
            break
        except CrossSectionValidationError as exc:
            last_error = exc
    if response is None:
        raise last_error or CrossSectionValidationError("cross-window discovery failed")

    updated = apply_proposals(package, response, identity=identity)
    try:
        validate_merged_package(updated)
    except KnowledgePackageMergeError as exc:
        raise CrossSectionValidationError(
            f"cross-section output violates graph integrity: {exc}"
        ) from exc
    _stamp_artifact(updated)
    _archive(output_path)
    _atomic_json_write(output_path, updated)
    print(json.dumps({
        "package": str(package_path.name),
        "records_considered": len(catalogue),
        "sections": len(boundaries),
        "evidence_relations_added": len(response.get("evidence_relations") or []),
        "claim_relations_added": len(response.get("claim_relations") or []),
    }, ensure_ascii=False))
    return updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--reasoning-effort", choices=["low", "medium", "high"], default="medium")
    parser.add_argument(
        "--backend", choices=["api", "codex-subscription"], default="api",
        help="structured-output transport; codex-subscription uses the ChatGPT login",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")
    package = json.loads(args.package.read_text(encoding="utf-8"))
    subject = package_row_key(package) or args.package.name
    prompt = PROMPT_PATH.read_text(encoding="utf-8")

    if not args.force and already_current(
        package_path=args.package, output_path=args.output,
        prompt=prompt, model_id=args.model,
        reasoning_effort=args.reasoning_effort, max_output_tokens=16000,
        backend=args.backend,
    ):
        print(json.dumps({
            "package": str(args.output.name), "status": "skipped",
            "reason": "matching cross-section fingerprint",
        }, ensure_ascii=False))
        return 0

    # The stage had no name in the ledger until now, so the overview could not
    # say whether a source had been through it. That is the one question worth
    # asking about this stage: it was skipped once already, silently.
    with run_record(subject=subject, stage="cross_section") as record:
        record.model(args.model)
        usage_rows: list[dict[str, Any]] = []
        client = (
            CodexSubscriptionClient(
                model=args.model, reasoning_effort=args.reasoning_effort,
                timeout_seconds=900, max_output_tokens=16000,
            )
            if args.backend == "codex-subscription"
            else Stage1OpenAIClient(
                model=args.model, reasoning_effort=args.reasoning_effort,
                timeout_seconds=600, max_retries=3, max_output_tokens=16000,
            )
        )
        updated = run(
            package_path=args.package,
            output_path=args.output,
            client=client,
            prompt=prompt,
            force=args.force,
            usage_sink=usage_rows,
            record=record,
        )
        # Without this the row prices a real model call at $0.00, which reads
        # as "this stage is free" rather than "nobody measured it" -- the same
        # false-free the ledger already guards against on failed runs.
        relations = updated.get("cross_section_relations") or {}
        record.quality({
            "evidence_relations_added": relations.get("evidence_relations_added"),
            "claim_relations_added": relations.get("claim_relations_added"),
            "skipped": relations.get("skipped"),
        })
        record.outputs(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
