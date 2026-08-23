"""Synthesize the first complete read-only Matthew 16 viewpoint pilot."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.matthew16_viewpoint_candidate import (
    AdjacentPropositionUnit,
    ArticleViewpointAcceptance,
    PilotViewpointMember,
    build_matthew16_viewpoint_pilot,
)
from backend.api.canonical_repository.matthew16_viewpoint_pilot import file_sha256
from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_proposition_units import (
    ClaimAtomicDecompositionArtifact,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_artifact_sha(payload: dict[str, Any], label: str) -> None:
    expected = payload.get("artifact_sha256")
    body = {key: value for key, value in payload.items() if key != "artifact_sha256"}
    if not isinstance(expected, str) or expected != sha256_json(body):
        raise ValueError(f"{label} artifact SHA mismatch")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", type=Path, required=True)
    parser.add_argument("--atomic-execution", type=Path, required=True)
    parser.add_argument("--atomic-identity-execution", type=Path, required=True)
    parser.add_argument("--boundary-run", type=Path, required=True)
    parser.add_argument("--decomposition-dir", type=Path, required=True)
    parser.add_argument("--article", type=Path, required=True)
    parser.add_argument("--article-draft-id", required=True)
    parser.add_argument("--article-proposition", required=True)
    parser.add_argument("--core-proposition", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    scope = _read(args.scope)
    atomic_execution = _read(args.atomic_execution)
    identity_execution = _read(args.atomic_identity_execution)
    boundary = _read(args.boundary_run)
    for payload, label in (
        (scope, "scope"),
        (atomic_execution, "atomic execution"),
        (identity_execution, "atomic identity execution"),
        (boundary, "atomic identity coverage boundary"),
    ):
        _validate_artifact_sha(payload, label)
    if not (
        boundary.get("schema_version")
        == "wang_matthew16_atomic_identity_coverage_run_v1"
        and boundary.get("semantic_agreement")
        and boundary.get("synthesis_eligible")
        and boundary.get("artifact_sha256")
        == identity_execution["boundary_run_artifact_sha256"]
    ):
        raise ValueError("atomic identity boundary is not synthesis-eligible")
    if not (
        identity_execution.get("schema_version")
        == "wang_matthew16_atomic_identity_execution_v2"
        and boundary["target_proposition"] == args.core_proposition
        and identity_execution["participant_unit_ids"]
        == boundary["participant_unit_ids"]
        and identity_execution["adjacent_unit_ids"] == boundary["adjacent_unit_ids"]
        and identity_execution["unit_universe_count"]
        == len(boundary["unit_universe_ids"])
    ):
        raise ValueError("atomic identity coverage bindings are inconsistent")
    member_ids = set(identity_execution["participant_unit_ids"])
    members = []
    adjacent = []
    seen: set[str] = set()
    decomposition_shas: list[str] = []
    for path in sorted(args.decomposition_dir.glob("*.json")):
        decomposition = ClaimAtomicDecompositionArtifact.model_validate(_read(path))
        decomposition_shas.append(decomposition.artifact_sha256)
        for unit in decomposition.proposition_units:
            seen.add(unit.proposition_unit_id)
            if unit.proposition_unit_id in member_ids:
                members.append(PilotViewpointMember(proposition_unit=unit, parent_claim=decomposition.claim))
            else:
                adjacent.append(
                    AdjacentPropositionUnit(
                        proposition_unit_id=unit.proposition_unit_id,
                        parent_claim_id=unit.parent_claim_id,
                        unit_statement=unit.unit_statement,
                    )
                )
    if not (
        sorted(seen) == boundary["unit_universe_ids"]
        and sorted(decomposition_shas) == boundary["decomposition_artifact_sha256s"]
        and sorted(item.proposition_unit_id for item in adjacent)
        == boundary["adjacent_unit_ids"]
    ):
        raise ValueError("identity execution does not close the atomic unit universe")
    manuscript = args.article.read_text(encoding="utf-8")
    start = manuscript.find(args.article_proposition)
    if start < 0 or manuscript.find(args.article_proposition, start + 1) >= 0:
        raise ValueError("article proposition must occur exactly once")
    article_acceptance = ArticleViewpointAcceptance(
        draft_id=args.article_draft_id,
        manuscript_sha256=file_sha256(args.article),
        article_proposition=args.article_proposition,
        start_char=start,
        end_char=start + len(args.article_proposition),
        supporting_proposition_unit_ids=sorted(member_ids),
    )
    artifact = build_matthew16_viewpoint_pilot(
        core_proposition=args.core_proposition,
        members=members,
        adjacent_non_members=adjacent,
        article_acceptance=article_acceptance,
        parent_scope_artifact_sha256=scope["artifact_sha256"],
        atomic_execution_artifact_sha256=atomic_execution["artifact_sha256"],
        atomic_identity_execution_artifact_sha256=identity_execution["artifact_sha256"],
        boundary_run_artifact_sha256=boundary["artifact_sha256"],
        model_ids=identity_execution["model_ids"],
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable pilot artifact {args.output}")
    temporary = args.output.with_suffix(args.output.suffix + ".partial")
    temporary.write_text(json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({
        "viewpoint_candidate_id": artifact.viewpoint_candidate_id,
        "member_count": len(artifact.members),
        "adjacent_non_member_count": len(artifact.adjacent_non_members),
        "article_acceptance": artifact.article_acceptance.status,
        "consumer_eligibility": artifact.consumer_eligibility,
        "artifact_sha256": artifact.artifact_sha256,
        "master_data_mutations": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
