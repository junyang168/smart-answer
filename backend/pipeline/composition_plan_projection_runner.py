"""Project a reviewed CompositionPlan into an auditable knowledge package."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from backend.pipeline.matthew_16_argument_integration import _source_presentations


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def reconcile_published_media_times(
    knowledge: dict[str, Any], timed_transcript_dir: Path
) -> dict[str, Any]:
    """Add timing only when a bound excerpt has one exact published match."""
    result = copy.deepcopy(knowledge)
    documents = {
        str(row.get("source_id")): row for row in result.get("source_documents", [])
    }
    cache: dict[str, tuple[list[dict[str, Any]], Path, str]] = {}
    for fragment in result.get("source_fragments", []):
        if isinstance(fragment.get("media_time"), (int, float)) and isinstance(
            fragment.get("media_end_time"), (int, float)
        ):
            continue
        document = documents.get(str(fragment.get("source_id"))) or {}
        if document.get("source_type") != "sermon_transcript":
            continue
        transcript_id = str(document.get("transcript_id") or "")
        path = timed_transcript_dir / f"{transcript_id}.json"
        if not transcript_id or not path.is_file():
            continue
        if transcript_id not in cache:
            raw = path.read_bytes()
            parsed = json.loads(raw)
            segments = parsed.get("script", []) if isinstance(parsed, dict) else parsed
            cache[transcript_id] = (
                list(segments or []),
                path,
                hashlib.sha256(raw).hexdigest(),
            )
        segments, resolved_path, published_sha = cache[transcript_id]
        excerpt = str(fragment.get("verbatim_excerpt") or "")
        matches = [
            (position, segment)
            for position, segment in enumerate(segments)
            if excerpt
            and excerpt in str(segment.get("text") or "")
            and isinstance(segment.get("start_time"), (int, float))
            and isinstance(segment.get("end_time"), (int, float))
            and segment["end_time"] > segment["start_time"]
        ]
        if len(matches) != 1:
            continue
        position, segment = matches[0]
        fragment["media_time"] = segment["start_time"]
        fragment["media_end_time"] = segment["end_time"]
        fragment["media_timing_source"] = {
            "match_policy": "unique_exact_verbatim_excerpt",
            "published_path": str(resolved_path),
            "published_sha256": published_sha,
            "published_segment_position": position,
            "published_segment_index": segment.get("index"),
        }
    return result


def build_projection(
    knowledge: dict[str, Any],
    plan: dict[str, Any],
    *,
    manuscript_bytes: bytes | None = None,
    timed_transcript_dir: Path | None = None,
) -> dict[str, Any]:
    result = (
        reconcile_published_media_times(knowledge, timed_transcript_dir)
        if timed_transcript_dir
        else copy.deepcopy(knowledge)
    )
    claim_ids = {str(row.get("claim_id")) for row in result.get("claims", [])}
    decisions = []
    route_buckets: dict[str, list[str]] = {}
    topic_routes: dict[str, set[str]] = {}
    for source_decision in plan.get("decisions", []):
        decision = copy.deepcopy(source_decision)
        used = [str(value) for value in decision.get("claim_ids", []) if value]
        missing = sorted(set(used) - claim_ids)
        if missing:
            raise ValueError(
                f"{decision.get('decision_id')}: unknown claim ids: {', '.join(missing)}"
            )
        presentations, summary = _source_presentations(used, result)
        decision["source_presentations"] = presentations
        decision["source_presentation_summary"] = summary
        decisions.append(decision)
        for claim_id in used:
            route_buckets.setdefault(claim_id, []).append(str(decision["decision_id"]))
            for topic_id in decision.get("topic_route_ids") or []:
                topic_routes.setdefault(str(topic_id), set()).add(claim_id)

    projected_plan = copy.deepcopy(plan)
    projected_plan["decisions"] = decisions
    projected_plan["source_presentation_policy"] = {
        "alignment": "composition_decision",
        "preserve_original_continuity": True,
        "non_contiguous_mode": "segment_group",
        "missing_media_policy": "unavailable_without_fabrication",
    }
    if manuscript_bytes is not None:
        projected_plan["manuscript_sha256"] = hashlib.sha256(manuscript_bytes).hexdigest()

    routes = list(result.get("knowledge_routes", []))
    for claim_id, decision_ids in sorted(route_buckets.items()):
        routes.append(
            {
                "route_id": _stable_id("ROUTE", claim_id, "scripture_exposition", plan["plan_id"]),
                "claim_id": claim_id,
                "route_type": "scripture_exposition",
                "target_id": plan["plan_id"],
                "decision_ids": list(dict.fromkeys(decision_ids)),
                "canonical_topic_ids": [],
                "review_status": "candidate",
            }
        )
    for topic_id, routed_claim_ids in sorted(topic_routes.items()):
        for claim_id in sorted(routed_claim_ids):
            routes.append(
                {
                    "route_id": _stable_id("ROUTE", claim_id, "topic_research", topic_id),
                    "claim_id": claim_id,
                    "route_type": "topic_research",
                    "target_id": topic_id,
                    "decision_ids": [],
                    "canonical_topic_ids": [topic_id],
                    "review_status": "candidate",
                }
            )

    existing_topic_ids = {
        str(row.get("topic_id")) for row in result.get("topic_nodes", [])
    }
    topic_nodes = list(result.get("topic_nodes", []))
    for topic_id in sorted(topic_routes):
        if topic_id not in existing_topic_ids:
            topic_nodes.append(
                {
                    "topic_id": topic_id,
                    "label": topic_id.replace("-", " "),
                    "definition": "由篇章计划提出的跨经文专题候选；待专题轴独立整理与人工审核。",
                    "review_status": "candidate",
                }
            )

    result.update(
        {
            "schema_version": "wang_shared_knowledge_v1.2",
            "package_id": f"{knowledge.get('package_id', 'KNOWLEDGE')}-{plan['plan_id']}",
            "knowledge_routes": routes,
            "topic_nodes": topic_nodes,
            "product_plans": [projected_plan],
            "projection": {
                "kind": "reviewed_composition_plan",
                "plan_id": plan["plan_id"],
                "publication_state": "candidate_not_active",
            },
            "summary": {
                "source_documents_count": len(result.get("source_documents", [])),
                "source_fragments_count": len(result.get("source_fragments", [])),
                "evidence_steps_count": len(result.get("evidence_steps", [])),
                "claims_count": len(result.get("claims", [])),
                "claim_relations_count": len(result.get("claim_relations", [])),
                "knowledge_routes_count": len(routes),
                "composition_decisions_count": len(decisions),
                "source_presentations_count": sum(
                    len(row.get("source_presentations", [])) for row in decisions
                ),
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge", required=True, type=Path)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--manuscript", type=Path)
    parser.add_argument("--timed-transcript-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    knowledge = json.loads(args.knowledge.read_text(encoding="utf-8"))
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    manuscript_bytes = args.manuscript.read_bytes() if args.manuscript else None
    result = build_projection(
        knowledge,
        plan,
        manuscript_bytes=manuscript_bytes,
        timed_transcript_dir=args.timed_transcript_dir,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
