from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


SCRIPT_PATTERN = re.compile(
    r'<script\s+id=["\']d["\']\s+type=["\']application/json["\']>(.*?)</script>',
    re.DOTALL,
)


def _canonical_evidence_id(lecture: str, local_evidence_id: str) -> str:
    lecture_number = "".join(character for character in lecture if character.isdigit())
    if not lecture_number:
        raise ValueError(f"Cannot derive canonical evidence prefix from lecture: {lecture}")
    return f"L{lecture_number}-{local_evidence_id}"


def _attach_claim_memberships(nodes: list[dict[str, Any]], claims_payload: dict[str, Any]) -> None:
    nodes_by_id = {node["id"]: node for node in nodes}
    missing_evidence: list[str] = []
    for claim in claims_payload.get("claims", []):
        group_id = claim.get("group_id")
        if not group_id:
            continue
        for occurrence in claim.get("occurrences", []):
            lecture = occurrence.get("lecture", "")
            local_ids = occurrence.get("local_source_evidence_ids", occurrence.get("source_evidence_ids", []))
            for local_id in local_ids:
                canonical_id = _canonical_evidence_id(lecture, local_id)
                node = nodes_by_id.get(canonical_id)
                if not node:
                    missing_evidence.append(f"{claim.get('claim_id')}:{canonical_id}")
                    continue
                memberships = node.setdefault("claim_group_ids", [])
                if group_id not in memberships:
                    memberships.append(group_id)

    if missing_evidence:
        raise ValueError("Claims reference missing canonical evidence nodes: " + ", ".join(missing_evidence))

    represented_groups = {
        group_id
        for node in nodes
        for group_id in node.get("claim_group_ids", ([node.get("topic")] if node.get("topic") else []))
    }
    missing_groups = [
        f"{claim.get('claim_id')}:{claim.get('group_id')}"
        for claim in claims_payload.get("claims", [])
        if claim.get("group_id") not in represented_groups
    ]
    if missing_groups:
        raise ValueError("Every claim group must have at least one evidence node: " + ", ".join(missing_groups))


def export_graph(source: Path, destination: Path, claims_path: Path | None = None) -> dict:
    html = source.read_text(encoding="utf-8")
    match = SCRIPT_PATTERN.search(html)
    if not match:
        raise ValueError(f"No embedded argument graph found in {source}")

    payload = json.loads(match.group(1))
    nodes = payload.get("nodes", [])
    if claims_path:
        _attach_claim_memberships(nodes, json.loads(claims_path.read_text(encoding="utf-8")))
    else:
        for node in nodes:
            node.setdefault("claim_group_ids", [node["topic"]] if node.get("topic") else [])

    relations = []
    for index, edge in enumerate(payload.get("edges", []), start=1):
        relations.append(
            {
                "relation_id": f"AR-{index:04d}",
                "source_evidence_id": edge["s"],
                "target_evidence_id": edge["d"],
                "relation_type": "supports" if edge.get("k") == "sup" else "answers",
                "review_status": "candidate",
            }
        )

    result = {
        "schema_version": "wang_argument_graph_v1",
        "source": {
            "path": str(source),
            "sha256": hashlib.sha256(html.encode("utf-8")).hexdigest(),
        },
        "evidence_nodes": nodes,
        "relations": relations,
        "claim_groups": payload.get("bands", []),
        "framework_candidate": {
            **payload.get("framework", {}),
            "evidence_step_ids": payload.get("framework", {}).get("claims", []),
        } if isinstance(payload.get("framework"), dict) else payload.get("framework", []),
        "argument_lanes": payload.get("lanes", []),
        "summary": {
            "evidence_count": len(payload.get("nodes", [])),
            "relation_count": len(relations),
            "support_count": sum(r["relation_type"] == "supports" for r in relations),
            "answer_count": sum(r["relation_type"] == "answers" for r in relations),
        },
    }
    if isinstance(result["framework_candidate"], dict):
        result["framework_candidate"].pop("claims", None)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the embedded claim graph as reviewable JSON")
    parser.add_argument("--source", type=Path, default=Path("output/claim-layer/claim-graph.html"))
    parser.add_argument("--output", type=Path, default=Path("output/claim-layer/argument_graph.json"))
    parser.add_argument("--claims", type=Path, default=Path("output/claim-layer/claims.json"))
    args = parser.parse_args()
    result = export_graph(args.source, args.output, args.claims)
    print(json.dumps(result["summary"], ensure_ascii=False))


if __name__ == "__main__":
    main()
