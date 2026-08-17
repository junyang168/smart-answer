"""Deterministic validation metrics for a completed research batch.

AI-generated summaries are useful editorially, but must not be the source of
record for counts, coverage, or overlap.  This module calculates those facts
directly from the reviewed artifacts so the same checks can be repeated for
every batch.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "wang_research_batch_validation_v1"


def _claim_ids(plan: dict[str, Any]) -> set[str]:
    return {
        str(claim_id)
        for section in plan.get("sections") or []
        for claim_id in section.get("claim_ids") or []
        if claim_id
    }


def _overlap_findings(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report strong same-axis overlap without deciding that it is an error."""
    findings: list[dict[str, Any]] = []
    for left_index, left in enumerate(plans):
        left_ids = _claim_ids(left)
        if not left_ids:
            continue
        for right in plans[left_index + 1 :]:
            if left.get("axis") != right.get("axis"):
                continue
            right_ids = _claim_ids(right)
            if not right_ids:
                continue
            overlap = left_ids & right_ids
            smaller_coverage = len(overlap) / min(len(left_ids), len(right_ids))
            same_target = bool(
                left.get("scripture_target_id")
                and left.get("scripture_target_id") == right.get("scripture_target_id")
            ) or bool(
                left.get("canonical_topic_id")
                and left.get("canonical_topic_id") == right.get("canonical_topic_id")
            )
            if not same_target and smaller_coverage < 0.75:
                continue
            findings.append(
                {
                    "axis": left.get("axis"),
                    "left_title": left.get("title"),
                    "right_title": right.get("title"),
                    "same_target": same_target,
                    "overlap_claim_count": len(overlap),
                    "left_claim_count": len(left_ids),
                    "right_claim_count": len(right_ids),
                    "smaller_plan_coverage": round(smaller_coverage, 4),
                    "jaccard": round(len(overlap) / len(left_ids | right_ids), 4),
                }
            )
    return findings


def compute_validation_metrics(
    knowledge: dict[str, Any],
    reviewed_candidates: dict[str, Any],
    reviewed_relations: dict[str, Any],
) -> dict[str, Any]:
    final = reviewed_candidates.get("final") or reviewed_candidates
    plans = final.get("candidate_plans") or []
    claims = knowledge.get("claims") or []
    all_claim_ids = {str(row.get("claim_id")) for row in claims if row.get("claim_id")}

    plan_claim_sets = [_claim_ids(plan) for plan in plans]
    assigned = set().union(*plan_claim_sets) if plan_claim_sets else set()
    placements = [claim_id for ids in plan_claim_sets for claim_id in ids]
    placement_counts = Counter(placements)
    axis_counts = Counter(str(plan.get("axis") or "unknown") for plan in plans)
    axis_unique_claims: dict[str, int] = {}
    axis_placements: dict[str, int] = {}
    for axis in sorted(axis_counts):
        axis_plans = [plan for plan in plans if plan.get("axis") == axis]
        axis_sets = [_claim_ids(plan) for plan in axis_plans]
        axis_unique_claims[axis] = len(set().union(*axis_sets) if axis_sets else set())
        axis_placements[axis] = sum(len(ids) for ids in axis_sets)

    relation_result = reviewed_relations.get("result") or reviewed_relations
    reviewed_edges = relation_result.get("reviewed_relations") or []
    relation_types = Counter(str(row.get("relation_type") or "unknown") for row in reviewed_edges)
    relation_summary = relation_result.get("summary") or {}

    integrated_edges = knowledge.get("claim_relations") or []
    integrated_relation_types = Counter(
        str(row.get("relation_type") or "unknown") for row in integrated_edges
    )
    integrated_review_statuses = Counter(
        str(row.get("review_status") or "legacy_reviewed") for row in integrated_edges
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "batch_id": (knowledge.get("batch") or {}).get("batch_id"),
        "source_counts": {
            "documents": len(knowledge.get("source_documents") or []),
            "fragments": len(knowledge.get("source_fragments") or []),
            "questions": len(knowledge.get("questions") or []),
            "position_nodes": len(knowledge.get("position_nodes") or []),
            "observations": len(knowledge.get("observations") or []),
            "evidence_steps": len(knowledge.get("evidence_steps") or []),
            "claims": len(claims),
            "knowledge_relations": len(knowledge.get("knowledge_relations") or []),
            "claim_relations": len(knowledge.get("claim_relations") or []),
        },
        "candidate_projection": {
            "plan_count": len(plans),
            "axis_counts": dict(sorted(axis_counts.items())),
            "unique_assigned_claim_count": len(assigned),
            "claim_placement_count": len(placements),
            "multi_plan_claim_count": sum(1 for count in placement_counts.values() if count > 1),
            "unassigned_claim_ids": sorted(all_claim_ids - assigned),
            "unknown_claim_ids": sorted(assigned - all_claim_ids),
            "axis_unique_claim_counts": axis_unique_claims,
            "axis_claim_placement_counts": axis_placements,
            "overlap_findings": _overlap_findings(plans),
            "model_summary": final.get("summary") or "",
        },
        "cross_sermon_relations": {
            "reviewed_relation_count": len(reviewed_edges),
            "relation_type_counts": dict(sorted(relation_types.items())),
            "unassigned_claim_ids": sorted(
                map(str, relation_result.get("unassigned_claim_ids") or [])
            ),
            "human_review_item_count": len(
                relation_result.get("human_review_items")
                or reviewed_relations.get("human_review_items")
                or []
            ) or int(relation_summary.get("human_review_required") or 0),
        },
        "integrated_claim_graph": {
            "relation_count": len(integrated_edges),
            "relation_type_counts": dict(sorted(integrated_relation_types.items())),
            "review_status_counts": dict(sorted(integrated_review_statuses.items())),
            "accepted_cross_sermon_relation_count": sum(
                1 for row in integrated_edges if row.get("review_artifact_id")
            ),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-root", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = args.batch_root
    integrated_knowledge = root / "integration/candidate-shared-knowledge.json"
    knowledge_path = (
        integrated_knowledge
        if integrated_knowledge.exists()
        else root / "merged/research-batch-knowledge.json"
    )
    knowledge = json.loads(knowledge_path.read_text())
    candidates = json.loads((root / "candidate-projection/reviewed-candidates.json").read_text())
    relations = json.loads((root / "cross-sermon-relations/reviewed-relations.json").read_text())
    metrics = compute_validation_metrics(knowledge, candidates, relations)
    output = args.output or root / "validation-metrics.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
