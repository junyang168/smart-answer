"""Independently verify passage-exception evidence against current source files.

This intentionally imports no backend pipeline or repository reader. It reads the
current source path from PostgreSQL in a read-only transaction, opens each file,
and checks the frozen file SHA and every verbatim fragment. It never writes DB.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def json_sha(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def checked_artifact(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    expected = value.get("artifact_sha256")
    actual = json_sha({key: item for key, item in value.items() if key != "artifact_sha256"})
    if expected != actual:
        raise ValueError(f"artifact SHA mismatch: {path}")
    return value


def script_texts(path: Path, raw: bytes) -> list[str]:
    text = raw.decode("utf-8")
    if path.suffix.lower() != ".json":
        return [text]
    source = json.loads(text)
    script = source if isinstance(source, list) else source.get("script")
    if not isinstance(script, list):
        raise ValueError(f"source script is not a list: {path}")
    if any(not isinstance(row, dict) or not isinstance(row.get("text"), str) for row in script):
        raise ValueError(f"source script has non-text row: {path}")
    return [row["text"] for row in script]


def verify(
    exceptions: dict[str, Any], packet: dict[str, Any], source_paths: dict[str, Path]
) -> dict[str, Any]:
    packet_claims = {row["claim_id"]: row for row in packet["claims"]}
    rows = exceptions["rows"]
    if len(rows) != exceptions["exception_count"]:
        raise ValueError("exception count mismatch")
    if len({row["claim_id"] for row in rows}) != len(rows):
        raise ValueError("duplicate exception Claim")
    required_sources = {row["source_id"] for row in rows}
    if set(source_paths) != required_sources:
        raise ValueError("source path set does not match exceptions")

    source_cache: dict[str, tuple[Path, str, list[str]]] = {}
    claim_checks = []
    for row in rows:
        claim_id = row["claim_id"]
        claim = packet_claims.get(claim_id)
        if claim is None:
            raise ValueError(f"Claim missing from frozen packet: {claim_id}")
        if claim["source_id"] != row["source_id"] or claim["claim_content_sha256"] != row["claim_content_sha256"]:
            raise ValueError(f"Claim/source SHA mismatch: {claim_id}")
        source_id = row["source_id"]
        if source_id not in source_cache:
            path = source_paths[source_id]
            raw = path.read_bytes()
            source_cache[source_id] = (path, hashlib.sha256(raw).hexdigest(), script_texts(path, raw))
        path, source_sha, texts = source_cache[source_id]
        if source_sha != claim["source_file_sha256"]:
            raise ValueError(f"source file SHA mismatch: {claim_id}: {path}")
        fragments = [
            fragment for step in row["evidence_steps"] for fragment in step.get("fragments", [])
        ]
        for fragment in fragments:
            excerpt = fragment["verbatim_excerpt"]
            if not excerpt or not any(excerpt in text for text in texts):
                raise ValueError(f"fragment not found in original source: {claim_id}: {fragment['fragment_id']}")
        claim_checks.append({
            "claim_id": claim_id,
            "source_id": source_id,
            "claim_content_sha256": claim["claim_content_sha256"],
            "verified_fragment_count": len(fragments),
        })
    report = {
        "schema_version": "wang_exegesis_passage_exception_source_verification_v1",
        "status": "verified_read_only_no_adjudication",
        "exception_packet_sha256": exceptions["artifact_sha256"],
        "role_packet_sha256": packet["artifact_sha256"],
        "verified_claim_count": len(claim_checks),
        "verified_source_count": len(source_cache),
        "verified_fragment_count": sum(row["verified_fragment_count"] for row in claim_checks),
        "source_checks": [
            {"source_id": source_id, "path": str(path), "source_file_sha256": source_sha}
            for source_id, (path, source_sha, _) in sorted(source_cache.items())
        ],
        "claim_checks": sorted(claim_checks, key=lambda row: row["claim_id"]),
        "database_mutations": 0,
    }
    report["artifact_sha256"] = json_sha(report)
    return report


def current_source_paths(source_ids: list[str]) -> dict[str, Path]:
    import psycopg

    dsn = os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("KNOWLEDGE_DATABASE_URL or DATABASE_URL is required")
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT object_id, payload->>'source_path' "
                "FROM wang_knowledge.objects "
                "WHERE collection='source_documents' AND retired_at IS NULL "
                "AND object_id=ANY(%s)",
                (source_ids,),
            )
            found = {source_id: Path(path) for source_id, path in cursor.fetchall() if path}
    if set(found) != set(source_ids):
        raise ValueError(f"missing current source records: {sorted(set(source_ids) - set(found))}")
    return found


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exceptions", type=Path, required=True)
    parser.add_argument("--role-packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(f"refusing to overwrite immutable report: {args.output}")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    exceptions = checked_artifact(args.exceptions)
    packet = checked_artifact(args.role_packet)
    if exceptions["role_packet_sha256"] != packet["artifact_sha256"]:
        raise ValueError("exception packet and role packet differ")
    source_ids = sorted({row["source_id"] for row in exceptions["rows"]})
    report = verify(exceptions, packet, current_source_paths(source_ids))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    partial = args.output.with_suffix(args.output.suffix + ".partial")
    partial.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(args.output)
    print(json.dumps({key: value for key, value in report.items() if key not in {"source_checks", "claim_checks"}}, indent=2))


if __name__ == "__main__":
    main()
