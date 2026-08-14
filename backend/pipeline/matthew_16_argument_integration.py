"""Build the shared-knowledge increment for Matthew 16:1-12.

This module does not ask a model to reinterpret the sources.  It turns the
already reviewed comparison package and editorial decision patch into two
product projections over one claim set:

* a passage-ordered Matthew 16:1-12 exposition plan;
* cross-passage topic plans, including the independent ``small faith`` topic.

The output is deterministic and may be dry-run reviewed before it is ingested
into the PostgreSQL authoring store.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COMPARISON = ROOT / "output/claim-layer/matthew-16-notes/sermon-4-1-comparison/comparison-knowledge.json"
DEFAULT_PATCH = ROOT / "output/claim-layer/matthew-16-notes/sermon-4-1-comparison/composition-update-candidate.json"
DEFAULT_MANUSCRIPT = ROOT / "output/claim-layer/matthew-16-notes/sermon-4-1-comparison/trial-manuscript-matt16-1-12.md"
DEFAULT_OUTPUT = ROOT / "output/claim-layer/matthew-16-notes/sermon-4-1-comparison/shared-knowledge-integration"

EXPOSITION_PLAN_ID = "CP-matthew-16-1-12"
SMALL_FAITH_PLAN_ID = "CP-topic-small-faith"
SIGNS_PLAN_ID = "CP-topic-signs-revelation-scripture-authority"

SMALL_FAITH_TOPIC_ID = "disciple-small-faith"
SIGNS_TOPIC_ID = "signs-revelation-scripture-authority"

SMALL_FAITH_CLAIMS = {
    "DK-91b546f25db1-CL002",
    "DK-a26f0a7e9ba4-CL003",
    "DK-a26f0a7e9ba4-CL004",
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _all_section_claim_ids(section: dict[str, Any]) -> list[str]:
    return list(dict.fromkeys(
        list(section.get("baseline_claim_ids") or [])
        + list(section.get("sermon_claim_ids") or [])
    ))


def _normalized_evidence_steps(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        copy = dict(item)
        fragment_ids = list(copy.get("source_fragment_ids") or [])
        if not copy.get("source_fragment_id") and fragment_ids:
            copy["source_fragment_id"] = fragment_ids[0]
        normalized.append(copy)
    return normalized


def _section_action(role: str) -> str:
    return {
        "narrative_problem": "main_section",
        "sign_and_response": "main_section",
        "disciple_misunderstanding": "brief_note",
        "jesus_correction": "main_with_topic_link",
        "interpretive_conclusion": "main_section",
        "bounded_theological_significance": "main_with_topic_link",
    }.get(role, "main_section")


def _source_presentations(
    claim_ids: list[str],
    comparison: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Project claim evidence into listenable, non-fabricated sermon segments.

    Evidence anchors remain the fine-grained provenance layer.  This projection
    groups the anchors by their original continuous sermon interval so the
    public listening unit is understandable rather than a collection of tiny
    quotations.  Non-contiguous intervals remain separate records.
    """
    documents = {item["source_id"]: item for item in comparison.get("source_documents", [])}
    fragments = {item["fragment_id"]: item for item in comparison.get("source_fragments", [])}
    matching_steps = [
        item
        for item in comparison.get("evidence_steps", [])
        if set(item.get("produced_claim_ids") or []) & set(claim_ids)
    ]
    grouped: dict[tuple[str, int, int], dict[str, Any]] = {}
    mapped_claim_ids: set[str] = set()
    for step in matching_steps:
        produced = set(step.get("produced_claim_ids") or []) & set(claim_ids)
        for fragment_id in step.get("source_fragment_ids") or []:
            fragment = fragments.get(fragment_id) or {}
            document = documents.get(fragment.get("source_id")) or {}
            if document.get("source_type") != "sermon_transcript":
                continue
            start = fragment.get("media_time")
            end = fragment.get("media_end_time")
            if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or end <= start:
                continue
            key = (fragment["source_id"], int(start), int(end))
            item = grouped.setdefault(key, {
                "source_id": fragment["source_id"],
                "transcript_id": document.get("transcript_id"),
                "source_title": document.get("title"),
                "start_seconds": int(start),
                "end_seconds": int(end),
                "source_fragment_ids": [],
                "evidence_step_ids": [],
                "claim_ids": [],
            })
            item["source_fragment_ids"] = list(dict.fromkeys(item["source_fragment_ids"] + [fragment_id]))
            item["evidence_step_ids"] = list(dict.fromkeys(item["evidence_step_ids"] + [step["evidence_step_id"]]))
            item["claim_ids"] = list(dict.fromkeys(item["claim_ids"] + sorted(produced)))
            mapped_claim_ids.update(produced)

    # Evidence extraction can produce one record per paragraph even when the
    # paragraphs are consecutive in the recording.  Merge touching/overlapping
    # intervals from the same sermon so the public unit follows the lecturer's
    # continuous train of thought instead of exposing paragraph-sized clips.
    merged: list[dict[str, Any]] = []
    for item in sorted(grouped.values(), key=lambda value: (value["source_id"], value["start_seconds"])):
        previous = merged[-1] if merged else None
        if (
            previous
            and previous["source_id"] == item["source_id"]
            and item["start_seconds"] <= previous["end_seconds"] + 1
        ):
            previous["end_seconds"] = max(previous["end_seconds"], item["end_seconds"])
            for field in ("source_fragment_ids", "evidence_step_ids", "claim_ids"):
                previous[field] = list(dict.fromkeys(previous[field] + item[field]))
            continue
        merged.append(dict(item))

    presentations: list[dict[str, Any]] = []
    for item in merged:
        item["duration_seconds"] = item["end_seconds"] - item["start_seconds"]
        item["presentation_id"] = _stable_id(
            "SP", item["source_id"], str(item["start_seconds"]), str(item["end_seconds"])
        )
        item["label"] = "原聲教學片段"
        presentations.append(item)

    unmapped = [claim_id for claim_id in claim_ids if claim_id not in mapped_claim_ids]
    if not presentations:
        mode = "unavailable"
        note = "本段目前只有筆記講稿來源，沒有可播放的講道時間定位。"
    elif len(presentations) == 1:
        mode = "continuous"
        note = "原聲材料連續，可直接按本段編排聆聽。"
    else:
        mode = "segment_group"
        note = "同一編排段落由數段不連續原聲支持；依原講道時間排序呈現，不拼接成虛假的連續發言。"
    return presentations, {
        "mode": mode,
        "status": "complete" if presentations and not unmapped else ("partial" if presentations else "unavailable"),
        "mapped_claim_ids": sorted(mapped_claim_ids),
        "unmapped_claim_ids": unmapped,
        "note": note,
    }


def build_integration_package(
    comparison: dict[str, Any],
    decision_patch: dict[str, Any],
    manuscript_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sections = list(decision_patch["decision_update"]["ordered_sections"])
    claims_by_id = {item["claim_id"]: item for item in comparison.get("claims", [])}
    section_claim_ids = list(dict.fromkeys(
        claim_id
        for section in sections
        for claim_id in _all_section_claim_ids(section)
    ))
    missing_claims = sorted(set(section_claim_ids) - set(claims_by_id))
    if missing_claims:
        raise ValueError(f"編排引用了不存在的主張：{', '.join(missing_claims)}")

    small_faith_missing = sorted(SMALL_FAITH_CLAIMS - set(claims_by_id))
    if small_faith_missing:
        raise ValueError(f"『小信』專題缺少主張：{', '.join(small_faith_missing)}")

    exposition_decisions: list[dict[str, Any]] = []
    route_specs: list[tuple[str, str, str, list[str], list[str]]] = []
    for section in sections:
        order = int(section["order"])
        decision_id = f"CD-M16-001-{order:02d}"
        claim_ids = _all_section_claim_ids(section)
        action = _section_action(str(section.get("role") or ""))
        decision: dict[str, Any] = {
            "decision_id": decision_id,
            "passage": section["passage"],
            "section_title": section["title"],
            "action": action,
            "decision": section["integration"],
            "rationale": "依經文次序整理已通過忠實度校對的筆記講稿，並用講道材料補足論證；不把跨經文專題全部塞入本段正文。",
            "claim_ids": claim_ids,
            "coverage": "available",
            "review_status": "candidate",
            "editorial_attribution": "editor",
            "section_role": section.get("role"),
        }
        if section.get("editorial_boundary"):
            decision["editorial_boundary"] = section["editorial_boundary"]
        presentations, presentation_summary = _source_presentations(claim_ids, comparison)
        decision["source_presentations"] = presentations
        decision["source_presentation_summary"] = presentation_summary
        if action == "main_with_topic_link":
            if section.get("role") == "jesus_correction":
                decision["topic_route_ids"] = [SMALL_FAITH_TOPIC_ID]
            elif section.get("role") == "bounded_theological_significance":
                decision["topic_route_ids"] = [SIGNS_TOPIC_ID]
        exposition_decisions.append(decision)
        for claim_id in claim_ids:
            route_specs.append((claim_id, "scripture_exposition", EXPOSITION_PLAN_ID, [decision_id], []))

    small_faith_claim_ids = [
        claim_id for claim_id in section_claim_ids if claim_id in SMALL_FAITH_CLAIMS
    ]
    signs_claim_ids = _all_section_claim_ids(sections[-1])

    small_faith_decision = {
        "decision_id": "CD-SMALL-FAITH-001",
        "action": "topic_core_section",
        "section_title": "『小信』不是信心份量不足，而是沒有理解並倚靠已領受的啟示",
        "decision": "把太16:8–10作為『小信』專題的一組核心材料；日後與太8:26、太17:20等經文及相關講道綜合，不在太16正文重複展開完整專論。",
        "rationale": "教授在不同經文反覆使用『小信』說明理解、推論與倚靠的問題，已超出單一段落的局部釋義。",
        "claim_ids": small_faith_claim_ids,
        "review_status": "candidate",
        "editorial_attribution": "editor",
        "corpus_scope": "matthew_16_1_12_seed",
    }
    signs_decision = {
        "decision_id": "CD-SIGNS-SCRIPTURE-001",
        "action": "topic_seed_section",
        "section_title": "神蹟是印證，不是信仰的根基",
        "decision": "以太16:1–12材料建立專題種子；待合併其他講道後再形成完整專論。",
        "rationale": "這是教授重要而且跨經文的神學判斷，太16正文只保留與本段直接相關的篇幅。",
        "claim_ids": signs_claim_ids,
        "review_status": "candidate",
        "editorial_attribution": "editor",
        "corpus_scope": "matthew_16_1_12_seed",
    }

    for claim_id in small_faith_claim_ids:
        route_specs.append((claim_id, "topic_research", SMALL_FAITH_PLAN_ID, ["CD-SMALL-FAITH-001"], [SMALL_FAITH_TOPIC_ID]))
    for claim_id in signs_claim_ids:
        route_specs.append((claim_id, "topic_research", SIGNS_PLAN_ID, ["CD-SIGNS-SCRIPTURE-001"], [SIGNS_TOPIC_ID]))

    consolidated_routes: dict[tuple[str, str, str], dict[str, list[str]]] = {}
    for claim_id, route_type, target_id, decision_ids, topic_ids in route_specs:
        bucket = consolidated_routes.setdefault(
            (claim_id, route_type, target_id),
            {"decision_ids": [], "topic_ids": []},
        )
        bucket["decision_ids"] = list(dict.fromkeys(bucket["decision_ids"] + decision_ids))
        bucket["topic_ids"] = list(dict.fromkeys(bucket["topic_ids"] + topic_ids))

    routes = []
    for (claim_id, route_type, target_id), route_data in consolidated_routes.items():
        routes.append({
            "route_id": _stable_id("ROUTE", claim_id, route_type, target_id),
            "claim_id": claim_id,
            "route_type": route_type,
            "target_id": target_id,
            "decision_ids": route_data["decision_ids"],
            "canonical_topic_ids": route_data["topic_ids"],
            "review_status": "candidate",
        })

    manuscript_sha256 = hashlib.sha256(manuscript_bytes).hexdigest()
    package = {
        "schema_version": "wang_shared_knowledge_increment_v1",
        "package_id": "SKI-M16-1-12-EXPOSITION-TOPICS-V1",
        "source_documents": comparison.get("source_documents", []),
        "source_fragments": comparison.get("source_fragments", []),
        "questions": comparison.get("questions", []),
        "observations": comparison.get("observations", []),
        "claims": comparison.get("claims", []),
        "evidence_steps": _normalized_evidence_steps(comparison.get("evidence_steps", [])),
        "knowledge_relations": comparison.get("knowledge_relations", []),
        "claim_relations": comparison.get("claim_relations", []),
        "position_nodes": comparison.get("position_nodes", []),
        "topic_nodes": [
            {
                "topic_id": SMALL_FAITH_TOPIC_ID,
                "label": "『小信』：信心、理解與倚靠",
                "parent_topic_id": "disciple-faith-trust",
                "aliases": ["小信", "信心小", "芥菜種的信心"],
                "definition": "整理教授如何以『小信』說明人未能理解、推論並倚靠已領受的啟示；不得簡化為抽象的信心份量大小。",
                "review_status": "candidate",
            },
            {
                "topic_id": SIGNS_TOPIC_ID,
                "label": "神蹟、啟示與聖經權威",
                "parent_topic_id": "bible-hermeneutics",
                "aliases": ["神蹟與聖經", "神蹟與信仰根基"],
                "definition": "整理教授關於神蹟如何印證神的作為、卻不能取代神的話成為信仰根基的論述。",
                "review_status": "candidate",
            },
        ],
        "knowledge_routes": routes,
        "cross_source_syntheses": [
            {
                "synthesis_id": "SYN-M16-1-12-EXPOSITION",
                "synthesis_type": "passage_exposition",
                "title": "太16:1–12：神蹟、酵與小信",
                "description": "以通過忠實度校對的筆記講稿為基線，補入相關講道論證，按經文次序形成釋經產品。",
                "claim_ids": section_claim_ids,
                "corpus_scope": "matthew_16_1_12",
                "review_status": "candidate",
                "manuscript_sha256": manuscript_sha256,
            },
            {
                "synthesis_id": "SYN-TOPIC-SMALL-FAITH-M16",
                "synthesis_type": "topic_seed",
                "title": "『小信』專題的太16材料",
                "description": "只記錄太16目前提供的主張；完整專題必須再合併其他經文和講道。",
                "claim_ids": small_faith_claim_ids,
                "corpus_scope": "matthew_16_1_12_seed",
                "review_status": "candidate",
            },
        ],
        "product_plans": [
            {
                "schema_version": "wang_composition_plan_v1",
                "plan_id": EXPOSITION_PLAN_ID,
                "title": "馬太福音16:1–12釋經編排",
                "description": "編輯部按經文次序，把已校對的筆記講稿與講道補充組織為一篇可溯源釋經；專題內容以連結延伸。",
                "product_type": "scripture_exposition",
                "decisions": exposition_decisions,
                "review_status": "candidate",
                "editorial_attribution": "editor",
                "manuscript_sha256": manuscript_sha256,
                "source_presentation_policy": {
                    "alignment": "composition_decision",
                    "public_unit_target_seconds": [180, 480],
                    "preserve_original_continuity": True,
                    "non_contiguous_mode": "segment_group",
                    "topic_usage": "embed_as_source_inside_topic_not_separate_audio_product",
                },
            },
            {
                "schema_version": "wang_composition_plan_v1",
                "plan_id": SMALL_FAITH_PLAN_ID,
                "title": "『小信』：信心、理解與倚靠",
                "description": "跨經文、跨講道的專題編排；太16只是第一組已整理材料。",
                "product_type": "topic_research",
                "decisions": [small_faith_decision],
                "review_status": "candidate",
                "editorial_attribution": "editor",
            },
            {
                "schema_version": "wang_composition_plan_v1",
                "plan_id": SIGNS_PLAN_ID,
                "title": "神蹟、啟示與聖經權威",
                "description": "跨經文專題種子；待其他講道材料合併後再成篇。",
                "product_type": "topic_research",
                "decisions": [signs_decision],
                "review_status": "candidate",
                "editorial_attribution": "editor",
            },
        ],
        "editorial_checks": list(decision_patch["decision_update"].get("editorial_checks") or []),
        "summary": {
            "source_claim_count": len(comparison.get("claims", [])),
            "exposition_claim_count": len(section_claim_ids),
            "small_faith_claim_count": len(small_faith_claim_ids),
            "route_count": len(routes),
            "product_plan_count": 3,
            "manuscript_sha256": manuscript_sha256,
        },
    }
    report = {
        "schema_version": "matthew_16_argument_integration_report_v1",
        "status": "ready_for_ingest",
        "package_id": package["package_id"],
        "exposition_plan_id": EXPOSITION_PLAN_ID,
        "topic_plan_ids": [SMALL_FAITH_PLAN_ID, SIGNS_PLAN_ID],
        "shared_claims_between_exposition_and_small_faith": small_faith_claim_ids,
        "separation_rule": "太16釋經回答本段經文；『小信』專題跨經文綜合。兩者共享主張與來源，不複製主張。",
        "findings": [],
        "summary": package["summary"],
    }
    return package, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--decision-patch", type=Path, default=DEFAULT_PATCH)
    parser.add_argument("--manuscript", type=Path, default=DEFAULT_MANUSCRIPT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    package, report = build_integration_package(
        _load(args.comparison),
        _load(args.decision_patch),
        args.manuscript.read_bytes(),
    )
    _write(args.output_dir / "incremental-package.json", package)
    _write(args.output_dir / "integration-report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
