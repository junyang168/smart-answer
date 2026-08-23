"""Classify every atomic unit against one Matthew 16 viewpoint truth condition."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_proposition_units import (
    ClaimAtomicDecompositionArtifact,
    PropositionUnitCandidate,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    StructuredJsonReviewerAdapter,
)
from backend.pipeline.claude_subscription_client import ClaudeSubscriptionClient
from backend.pipeline.codex_subscription_client import CodexSubscriptionClient


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROMPT_DIR = Path(__file__).with_name("prompts")


class StrictCoverageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AtomicUnitIdentityDecision(StrictCoverageModel):
    proposition_unit_id: str
    disposition: Literal["equivalent", "different_truth_condition", "unknown"]
    rationale: str = Field(min_length=1)


class AtomicIdentityCoverageAssessment(StrictCoverageModel):
    schema_version: Literal["wang_matthew16_atomic_identity_coverage_assessment_v1"] = (
        "wang_matthew16_atomic_identity_coverage_assessment_v1"
    )
    atomic_execution_artifact_sha256: str
    target_proposition: str
    decisions: list[AtomicUnitIdentityDecision] = Field(min_length=2)

    @model_validator(mode="before")
    @classmethod
    def canonicalize_decisions(cls, value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("decisions"), list):
            return value
        result = dict(value)
        result["decisions"] = sorted(
            value["decisions"], key=lambda item: str(item.get("proposition_unit_id", ""))
        )
        return result

    @model_validator(mode="after")
    def validate_decisions(self) -> "AtomicIdentityCoverageAssessment":
        ids = [item.proposition_unit_id for item in self.decisions]
        if ids != sorted(set(ids)):
            raise ValueError("identity coverage decisions must be sorted and unique")
        return self


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_new(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_unit_universe(
    decomposition_dir: Path,
) -> tuple[list[PropositionUnitCandidate], list[str]]:
    units: list[PropositionUnitCandidate] = []
    decomposition_shas: list[str] = []
    for path in sorted(decomposition_dir.glob("*.json")):
        artifact = ClaimAtomicDecompositionArtifact.model_validate(_read(path))
        units.extend(artifact.proposition_units)
        decomposition_shas.append(artifact.artifact_sha256)
    units.sort(key=lambda item: item.proposition_unit_id)
    ids = [item.proposition_unit_id for item in units]
    if len(ids) < 2 or ids != sorted(set(ids)):
        raise ValueError("atomic identity coverage requires a unique complete unit universe")
    return units, sorted(decomposition_shas)


def validate_assessment_binding(
    *,
    role: str,
    assessment: AtomicIdentityCoverageAssessment,
    atomic_execution_artifact_sha256: str,
    target_proposition: str,
    universe_ids: list[str],
) -> None:
    if assessment.atomic_execution_artifact_sha256 != atomic_execution_artifact_sha256:
        raise ValueError(f"{role} assessment atomic execution binding mismatch")
    if assessment.target_proposition != target_proposition:
        raise ValueError(f"{role} assessment target proposition mismatch")
    decision_ids = [item.proposition_unit_id for item in assessment.decisions]
    if decision_ids != universe_ids:
        raise ValueError(f"{role} assessment does not cover the exact unit universe")


def run_coverage(
    *,
    atomic_execution: dict[str, Any],
    decomposition_dir: Path,
    target_proposition: str,
    output_dir: Path,
    proposal_model: str,
    blind_model: str,
    reasoning_effort: str,
) -> dict[str, Any]:
    units, decomposition_shas = _load_unit_universe(decomposition_dir)
    execution_unit_ids = sorted(
        unit_id
        for item in atomic_execution["decompositions"]
        for unit_id in item["proposition_unit_ids"]
    )
    universe_ids = [item.proposition_unit_id for item in units]
    if execution_unit_ids != universe_ids:
        raise ValueError("decomposition directory does not match atomic execution universe")
    payload = {
        "atomic_execution_artifact_sha256": atomic_execution["artifact_sha256"],
        "target_proposition": target_proposition,
        "decision_rule": (
            "equivalent only when the unit and target have the same truth condition in "
            "the Matthew 16:18 context; wording may differ"
        ),
        "proposition_units": [item.model_dump(mode="json") for item in units],
    }
    adapters = [
        StructuredJsonReviewerAdapter(
            client=CodexSubscriptionClient(
                model=proposal_model,
                reasoning_effort=reasoning_effort,
                timeout_seconds=900,
                max_output_tokens=12000,
            ),
            prompt=(PROMPT_DIR / "matthew16_atomic_identity_coverage_proposal.md").read_text(
                encoding="utf-8"
            ),
            response_model=AtomicIdentityCoverageAssessment,
            schema_name="wang_matthew16_atomic_identity_coverage_proposal_v1",
        ),
        StructuredJsonReviewerAdapter(
            client=ClaudeSubscriptionClient(
                model=blind_model,
                reasoning_effort=reasoning_effort,
                timeout_seconds=900,
                max_output_tokens=12000,
            ),
            prompt=(PROMPT_DIR / "matthew16_atomic_identity_coverage_blind.md").read_text(
                encoding="utf-8"
            ),
            response_model=AtomicIdentityCoverageAssessment,
            schema_name="wang_matthew16_atomic_identity_coverage_blind_v1",
        ),
    ]
    assessments = []
    for role, adapter in zip(("proposal", "blind"), adapters, strict=True):
        raw = dict(adapter.generate(payload))
        assessment = AtomicIdentityCoverageAssessment.model_validate(raw)
        validate_assessment_binding(
            role=role,
            assessment=assessment,
            atomic_execution_artifact_sha256=atomic_execution["artifact_sha256"],
            target_proposition=target_proposition,
            universe_ids=universe_ids,
        )
        artifact = {
            "schema_version": "wang_matthew16_atomic_identity_coverage_call_v1",
            "role": role,
            "model_id": adapter.model_id,
            "backend": adapter.backend,
            "prompt_sha256": adapter.prompt_sha256,
            "generation_config_sha256": adapter.generation_config_sha256,
            "assessment": assessment.model_dump(mode="json"),
        }
        artifact["artifact_sha256"] = sha256_json(artifact)
        _write_new(output_dir / f"{role}-assessment.json", artifact)
        assessments.append((assessment, artifact, adapter))
    first, second = assessments[0][0], assessments[1][0]
    first_map = {item.proposition_unit_id: item.disposition for item in first.decisions}
    second_map = {item.proposition_unit_id: item.disposition for item in second.decisions}
    disagreements = sorted(
        unit_id for unit_id in universe_ids if first_map[unit_id] != second_map[unit_id]
    )
    unknowns = sorted(
        unit_id
        for unit_id in universe_ids
        if first_map[unit_id] == "unknown" or second_map[unit_id] == "unknown"
    )
    members = sorted(
        unit_id
        for unit_id in universe_ids
        if first_map[unit_id] == second_map[unit_id] == "equivalent"
    )
    adjacent = sorted(set(universe_ids) - set(members))
    synthesis_eligible = not disagreements and not unknowns and len(members) >= 2
    boundary = {
        "schema_version": "wang_matthew16_atomic_identity_coverage_run_v1",
        "atomic_execution_artifact_sha256": atomic_execution["artifact_sha256"],
        "decomposition_artifact_sha256s": decomposition_shas,
        "target_proposition": target_proposition,
        "unit_universe_ids": universe_ids,
        "participant_unit_ids": members,
        "adjacent_unit_ids": adjacent,
        "disagreement_unit_ids": disagreements,
        "unknown_unit_ids": unknowns,
        "semantic_agreement": not disagreements,
        "synthesis_eligible": synthesis_eligible,
        "assessment_artifact_sha256s": [item[1]["artifact_sha256"] for item in assessments],
        "model_ids": sorted(item[2].model_id for item in assessments),
        "semantic_call_count": 2,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    boundary["artifact_sha256"] = sha256_json(boundary)
    _write_new(output_dir / "atomic-identity-coverage-run.json", boundary)
    report = {
        "schema_version": "wang_matthew16_atomic_identity_execution_v2",
        "boundary_run_artifact_sha256": boundary["artifact_sha256"],
        "participant_unit_ids": members,
        "adjacent_unit_ids": adjacent,
        "unit_universe_count": len(universe_ids),
        "semantic_agreement": boundary["semantic_agreement"],
        "synthesis_eligible": synthesis_eligible,
        "model_ids": boundary["model_ids"],
        "semantic_call_count": 2,
        "master_data_mutations": 0,
        "apply_allowed": False,
    }
    report["artifact_sha256"] = sha256_json(report)
    _write_new(output_dir / "atomic-identity-execution.json", report)
    return report


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--atomic-execution", type=Path, required=True)
    parser.add_argument("--decomposition-dir", type=Path, required=True)
    parser.add_argument("--target-proposition", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--proposal-model", default="gpt-5.6-sol")
    parser.add_argument("--blind-model", default="claude-opus-5")
    parser.add_argument("--reasoning-effort", choices=("high", "xhigh"), default="high")
    args = parser.parse_args()
    report = run_coverage(
        atomic_execution=_read(args.atomic_execution),
        decomposition_dir=args.decomposition_dir,
        target_proposition=args.target_proposition,
        output_dir=args.output_dir,
        proposal_model=args.proposal_model,
        blind_model=args.blind_model,
        reasoning_effort=args.reasoning_effort,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
