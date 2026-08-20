"""Add the long-distance argument links windowed extraction could not see."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.pipeline.corpus_survey_runner import PROJECT_ROOT
from backend.pipeline.cross_section_relation import (
    DISCOVERY_SCHEMA,
    PROMPT_PATH,
    CrossSectionValidationError,
    apply_proposals,
    build_catalogue,
    discovery_identity,
    record_positions,
    render_catalogue,
    validate_proposals,
)
from backend.pipeline.llm_usage import usage_row
from backend.pipeline.run_ledger import run_record
from backend.pipeline.stage1 import Stage1OpenAIClient

VALIDATION_ATTEMPTS = 3


def _section_boundaries(package: dict[str, Any]) -> list[int]:
    """Where the package says its sections start.

    Read off the package rather than configured, so the two stages cannot drift
    apart: resection the source and this stage follows, with no second place to
    remember. A package with no plan is treated as one section, which makes
    every proposal same-section and therefore rejected -- the safe direction.
    """

    plan = (package.get("extraction") or {}).get("section_plan") or {}
    return [int(value) for value in plan.get("boundaries") or [0]]


def _write_through(
    package: dict[str, Any], output_path: Path, *, identity: dict[str, Any]
) -> dict[str, Any]:
    """Emit the package unchanged, saying so, when there is nothing to relate."""

    updated = apply_proposals(package, {}, identity=identity)
    updated["cross_section_relations"]["skipped"] = "single_section"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "package": str(output_path.name), "sections": 1,
        "skipped": "single_section",
        "evidence_relations_added": 0, "claim_relations_added": 0,
    }, ensure_ascii=False))
    return updated


def run(
    *,
    package_path: Path,
    output_path: Path,
    client: Stage1OpenAIClient,
    prompt: str,
    force: bool = False,
    usage_sink: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    raw = package_path.read_bytes()
    package = json.loads(raw.decode("utf-8"))
    boundaries = _section_boundaries(package)
    identity = discovery_identity(
        package_sha256=hashlib.sha256(raw).hexdigest(), prompt=prompt,
        model_id=client.model, section_count=len(boundaries),
    )
    if output_path.is_file() and not force:
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        stored = (existing.get("cross_section_relations") or {}).get("fingerprint_sha256")
        if stored == identity["fingerprint_sha256"]:
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
    catalogue = build_catalogue(package, positions)
    if not catalogue:
        raise CrossSectionValidationError(f"{package_path}: no anchored records to relate")
    section_of = {
        row["id"]: sum(1 for start in boundaries if start <= positions[row["id"]])
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
        candidate = client.generate_json(
            prompt, feedback, DISCOVERY_SCHEMA, cache_prefix=user_input
        )
        # Every attempt is billed, including the ones validation rejects, so
        # the row has to carry all of them. Recording only the accepted call
        # would price a three-attempt run as though it were a one-attempt run.
        if usage_sink is not None:
            usage_sink.append(usage_row(getattr(client, "last_usage", None), attempt))
        try:
            validate_proposals(candidate, package, positions=positions, boundaries=boundaries)
            response = candidate
            break
        except CrossSectionValidationError as exc:
            last_error = exc
    if response is None:
        raise last_error or CrossSectionValidationError("cross-window discovery failed")

    updated = apply_proposals(package, response, identity=identity)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.is_file():
        archive = output_path.parent / "generations" / f"{output_path.stem}.{identity['fingerprint_sha256'][:12]}.json"
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            shutil.copy2(output_path, archive)
    output_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
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
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    load_dotenv(PROJECT_ROOT / ".env")
    package = json.loads(args.package.read_text(encoding="utf-8"))
    documents = package.get("source_documents") or []
    subject = str(
        (documents[0].get("source_id") if documents else None) or args.package.name
    )
    # The stage had no name in the ledger until now, so the overview could not
    # say whether a source had been through it. That is the one question worth
    # asking about this stage: it was skipped once already, silently.
    with run_record(subject=subject, stage="cross_section") as record:
        record.model(args.model)
        usage_rows: list[dict[str, Any]] = []
        updated = run(
            package_path=args.package,
            output_path=args.output,
            client=Stage1OpenAIClient(
                model=args.model, reasoning_effort=args.reasoning_effort,
                timeout_seconds=600, max_retries=3, max_output_tokens=16000,
            ),
            prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            force=args.force,
            usage_sink=usage_rows,
        )
        # Without this the row prices a real model call at $0.00, which reads
        # as "this stage is free" rather than "nobody measured it" -- the same
        # false-free the ledger already guards against on failed runs.
        record.usage(usage_rows)
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
