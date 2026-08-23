"""Compile zero-call, fail-closed evidence packets for identity hypotheses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.api.canonical_repository.store import RepositoryStore
from backend.api.canonical_repository.viewpoint_identity_hypotheses import (
    IdentityHypothesisIndex,
    build_identity_evidence_review_plan,
)
from backend.api.canonical_repository.viewpoint_source_attestation import (
    IdentitySourceEligibilityArtifact,
)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_immutable(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if _read(path) != payload:
            raise ValueError(f"immutable artifact differs at {path}")
        return
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def build_plan(
    *,
    hypothesis_index_path: Path,
    claim_manifest_path: Path,
    coverage_snapshot_path: Path,
    repository_root: Path,
    output_dir: Path,
    source_attestation_path: Path | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    hypothesis_index = IdentityHypothesisIndex.model_validate(
        _read(hypothesis_index_path)
    )
    store = PostgresKnowledgeStore(database_url)
    collections = {
        name: store.list_records(name)
        for name in (
            "claims",
            "evidence_steps",
            "source_fragments",
            "claim_relations",
            "claim_relation_constraints",
            "viewpoint_claim_links",
        )
    }
    citations = list(RepositoryStore(repository_root).list_citations())
    plan, packets = build_identity_evidence_review_plan(
        hypothesis_index=hypothesis_index,
        claim_manifest=_read(claim_manifest_path),
        coverage_snapshot=_read(coverage_snapshot_path),
        claims=collections["claims"],
        evidence_steps=collections["evidence_steps"],
        source_fragments=collections["source_fragments"],
        citations=citations,
        claim_relations=collections["claim_relations"],
        constraints=collections["claim_relation_constraints"],
        existing_links=collections["viewpoint_claim_links"],
        source_eligibility_artifact=(
            IdentitySourceEligibilityArtifact.model_validate(
                _read(source_attestation_path)
            )
            if source_attestation_path else None
        ),
    )
    for hypothesis_id, packet in sorted(packets.items()):
        _write_immutable(
            output_dir / "packets" / f"{hypothesis_id}.json",
            packet.model_dump(mode="json"),
        )
    payload = plan.model_dump(mode="json")
    _write_immutable(output_dir / "identity-evidence-review-plan.json", payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hypothesis-index", type=Path, required=True)
    parser.add_argument("--claim-manifest", type=Path, required=True)
    parser.add_argument("--coverage-snapshot", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-attestation", type=Path)
    parser.add_argument("--database-url")
    args = parser.parse_args()
    payload = build_plan(
        hypothesis_index_path=args.hypothesis_index,
        claim_manifest_path=args.claim_manifest,
        coverage_snapshot_path=args.coverage_snapshot,
        repository_root=args.repository_root,
        output_dir=args.output_dir,
        source_attestation_path=args.source_attestation,
        database_url=args.database_url,
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "artifact_sha256": payload["artifact_sha256"],
                **payload["statistics"],
                "model_calls_executed": 0,
                "master_data_mutations": 0,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
