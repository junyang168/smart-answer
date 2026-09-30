"""Read-only triage of reviewed exegesis Claims with unresolved passage keys.

This produces candidates for a later passage-role adjudication and #395
partition manifest. It does not override the #409 ledger or assign final
primary partition ownership.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.sermon_search.bible_refs import normalize_ref
from backend.pipeline.viewpoint_passage_grouping_preflight import (
    build_preview,
    passage_sort_key,
)


# Only spelling/number conversions with explicit coordinates. Book-only and
# inferred scene references are deliberately absent.
RAW_LOCATOR_ALIASES = {
    "raw:哥林多前書七章十四節": "1Cor.7.14",
    "raw:約壹2:1": "1John.2.1",
    "raw:約壹2:2": "1John.2.2",
    "raw:約壹2:5": "1John.2.5",
    "raw:創世紀 15:5": "Gen.15.5",
    "raw:創世紀 3:10": "Gen.3.10",
    "raw:哥林多後書七章第一節": "2Cor.7.1",
    "raw:但以理書第九章": "Dan.9",
}

EXPLICIT_REFERENCE_OVERRIDES = {
    "哥林多前書 1-15": ["1Cor.1-1Cor.15"],
    "帖撒羅尼迦前書 5:2,8-9": ["1Thess.5.2", "1Thess.5.8-1Thess.5.9"],
    "約翰福音 21章": ["John.21"],
    "但以理書7": ["Dan.7"],
    "但以理書7章": ["Dan.7"],
}


def strict_normalized_ref(value: str) -> str | None:
    """Reject normalizers that silently consume just a prefix of a reference."""
    normalized = normalize_ref(value)
    if normalized is None or normalized.raw.strip() != value.strip():
        return None
    passage_sort_key(normalized.osis)
    return normalized.osis


def strict_normalized_keys(value: str) -> list[str] | None:
    """Preserve exact discontiguous or chapter-range coordinates explicitly."""
    if value in EXPLICIT_REFERENCE_OVERRIDES:
        keys = EXPLICIT_REFERENCE_OVERRIDES[value]
        for key in keys:
            passage_sort_key(key)
        return list(keys)
    normalized = strict_normalized_ref(value)
    return [normalized] if normalized is not None else None


def build_triage(*, ledger: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    preview = build_preview(ledger=ledger, packet=packet, batch_size=20)
    held_ids = {row["claim_id"] for row in preview["held_claims"]}
    claims = {row["claim_id"]: row for row in packet["claims"]}
    decisions = {row["claim_id"]: row for row in ledger["decisions"]}
    rows: list[dict[str, Any]] = []
    for claim_id in sorted(held_ids):
        decision = decisions[claim_id]
        claim = claims[claim_id]
        base = {
            "claim_id": claim_id,
            "claim_content_sha256": claim["claim_content_sha256"],
            "source_id": claim["source_id"],
        }
        if decision["passage_identity_status"] == "disputed":
            primary = decision["primary"]
            independent = decision["independent"]
            a = set(primary["interpreted_ref_indices"])
            b = set(independent["interpreted_ref_indices"])
            if a != b:
                common = a & b
                refs = claim.get("scripture_refs") or []
                common_keys: list[list[str] | None] = []
                if common and all(isinstance(index, int) and 0 <= index < len(refs) for index in common):
                    common_keys = [strict_normalized_keys(str(refs[index])) for index in sorted(common)]
                if common_keys and all(key is not None for key in common_keys):
                    flat_keys = [key for group in common_keys for key in group]
                    rows.append(base | {
                        "status": "provisional_shared_passage_candidate",
                        "reason_code": "REVIEWERS_SHARE_INTERPRETED_REFERENCE_EXTRA_INDICES_DISPUTED",
                        "primary_ref_indices": sorted(a),
                        "independent_ref_indices": sorted(b),
                        "agreed_ref_indices": sorted(common),
                        "candidate_passage_keys": sorted(set(flat_keys), key=passage_sort_key),
                        "additional_reference_dispute_retained": True,
                    })
                else:
                    rows.append(base | {
                        "status": "needs_independent_adjudication" if not common else "needs_locator_review",
                        "reason_code": "NO_SHARED_INTERPRETED_REFERENCE" if not common else "SHARED_REFERENCE_NOT_FULLY_NORMALIZED",
                        "primary_ref_indices": sorted(a),
                        "independent_ref_indices": sorted(b),
                        "agreed_ref_indices": sorted(common),
                        "candidate_passage_keys": [],
                    })
                continue
            refs = claim.get("scripture_refs") or []
            keys: list[list[str] | None] = []
            if a and all(isinstance(index, int) and 0 <= index < len(refs) for index in a):
                keys = [strict_normalized_keys(str(refs[index])) for index in sorted(a)]
            if not keys or any(key is None for key in keys):
                rows.append(base | {
                    "status": "needs_locator_review",
                    "reason_code": "AGREED_REFERENCE_NOT_FULLY_NORMALIZED",
                    "agreed_ref_indices": sorted(a),
                    "candidate_passage_keys": [],
                })
                continue
            rows.append(base | {
                "status": "provisional_primary_passage_candidate",
                "reason_code": "CLAIM_REFERENCE_INDICES_AGREE_EVIDENCE_REFS_DIFFER",
                "agreed_ref_indices": sorted(a),
                "candidate_passage_keys": sorted(
                    {key for group in keys for key in group}, key=passage_sort_key
                ),
                "evidence_reference_dispute_retained": True,
            })
            continue
        raw_keys = [key for key in decision["interpreted_passage_keys"] if key.startswith("raw:")]
        mapped = [RAW_LOCATOR_ALIASES.get(key) for key in raw_keys]
        if not raw_keys or any(key is None for key in mapped):
            rows.append(base | {
                "status": "needs_locator_review",
                "reason_code": "RAW_LOCATOR_HAS_NO_EXPLICIT_COORDINATES",
                "raw_locator_keys": raw_keys,
                "candidate_passage_keys": [],
            })
            continue
        existing = [key for key in decision["interpreted_passage_keys"] if not key.startswith("raw:")]
        keys = existing + mapped
        for key in keys:
            passage_sort_key(key)
        rows.append(base | {
            "status": "provisional_normalized_locator_candidate",
            "reason_code": "EXPLICIT_RAW_LOCATOR_ALIAS",
            "raw_locator_keys": raw_keys,
            "candidate_passage_keys": sorted(set(keys), key=passage_sort_key),
        })
    if len(rows) != len(held_ids) or {row["claim_id"] for row in rows} != held_ids:
        raise ValueError("triage must cover held exegesis Claims exactly once")
    counts = Counter(row["status"] for row in rows)
    result = {
        "schema_version": "wang_exegesis_passage_identity_triage_v1",
        "status": "review_only_not_a_ledger_override",
        "role_ledger_sha256": ledger["artifact_sha256"],
        "role_packet_sha256": packet["artifact_sha256"],
        "triage_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "exegesis_denominator": preview["exegesis_claim_count"],
        "held_denominator": len(held_ids),
        "status_counts": dict(sorted(counts.items())),
        "model_calls_executed": 0,
        "master_data_mutations": 0,
        "rows": rows,
    }
    result["artifact_sha256"] = sha256_json(result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role-ledger", type=Path, required=True)
    parser.add_argument("--role-packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable triage {args.output}")
    result = build_triage(
        ledger=json.loads(args.role_ledger.read_text(encoding="utf-8")),
        packet=json.loads(args.role_packet.read_text(encoding="utf-8")),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".partial")
    partial.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(args.output)
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
