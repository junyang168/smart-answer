"""Create a deterministic claim-scoped slice of a reviewed knowledge package.

This utility is for bounded comparisons such as one passage in a notes package
against one sermon.  It never changes claim or evidence content; it keeps the
selected claims, their evidence, and all source records needed for provenance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def slice_package(package: dict[str, Any], claim_ids: set[str]) -> dict[str, Any]:
    claim_index = {str(row["claim_id"]): row for row in package.get("claims", [])}
    missing = sorted(claim_ids - set(claim_index))
    if missing:
        raise ValueError("unknown claim ids: " + ", ".join(missing))

    claims = [row for row in package.get("claims", []) if str(row["claim_id"]) in claim_ids]
    evidence_ids = {
        str(evidence_id)
        for claim in claims
        for evidence_id in claim.get("evidence_step_ids", [])
    }
    evidence = []
    for row in package.get("evidence_steps", []):
        if str(row.get("evidence_step_id")) not in evidence_ids:
            continue
        item = dict(row)
        if "produced_claim_ids" in item:
            item["produced_claim_ids"] = [
                claim_id
                for claim_id in item.get("produced_claim_ids") or []
                if str(claim_id) in claim_ids
            ]
        evidence.append(item)
    position_ids = {
        str(position_id)
        for claim in claims
        for position_id in claim.get("opposed_position_ids", [])
    }
    positions = [
        row for row in package.get("position_nodes", [])
        if str(row.get("position_id")) in position_ids
    ]
    fragment_ids = {
        str(fragment_id)
        for row in evidence
        for fragment_id in (
            row.get("source_fragment_ids")
            or ([row.get("source_fragment_id")] if row.get("source_fragment_id") else [])
        )
    }
    fragment_ids.update(
        str(fragment_id)
        for row in positions
        for fragment_id in (
            row.get("source_fragment_ids")
            or ([row.get("source_fragment_id")] if row.get("source_fragment_id") else [])
        )
    )
    fragments = [
        row for row in package.get("source_fragments", [])
        if str(row.get("fragment_id") or row.get("source_fragment_id")) in fragment_ids
    ]
    source_ids = {str(row.get("source_id")) for row in fragments if row.get("source_id")}
    transcript_ids = {
        str(row.get("transcript_id")) for row in fragments if row.get("transcript_id")
    }
    # Evidence records in older packages can point straight to a transcript.
    transcript_ids.update(
        str(row.get("transcript_id")) for row in evidence if row.get("transcript_id")
    )
    sources = [
        row for row in package.get("source_documents", [])
        if str(row.get("source_id")) in source_ids
        or str(row.get("transcript_id")) in transcript_ids
    ]
    relations = [
        row for row in package.get("claim_relations", [])
        if str(row.get("source_claim_id")) in claim_ids
        and str(row.get("target_claim_id")) in claim_ids
    ]

    result = dict(package)
    result.update(
        {
            "package_id": f"{package.get('package_id', 'knowledge')}-claim-slice",
            "source_documents": sources,
            "source_fragments": fragments,
            "evidence_steps": evidence,
            "claims": claims,
            "claim_relations": relations,
            "questions": [],
            "position_nodes": positions,
            "observations": [],
            "knowledge_relations": [],
            "source_packages": [package.get("package_id")],
            "summary": {
                "source_documents_count": len(sources),
                "source_fragments_count": len(fragments),
                "evidence_steps_count": len(evidence),
                "claims_count": len(claims),
                "claim_relations_count": len(relations),
                "position_nodes_count": len(positions),
            },
            "slice_scope": {
                "selection_policy": "explicit_reviewed_claim_ids",
                "claim_ids": sorted(claim_ids),
            },
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--claim-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = json.loads(args.input.read_text(encoding="utf-8"))
    result = slice_package(package, set(args.claim_id))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"output": str(args.output), **result["summary"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
