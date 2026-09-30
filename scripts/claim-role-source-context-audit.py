#!/usr/bin/env python3
"""Read original source context for #409 unresolved Claims; never change master data.

This audit deliberately does not import backend pipeline code. It binds every
context window to the frozen Claim, source record, and physical source bytes.
It records evidence, not a new passage-role decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import load_dotenv


BOOK_CHAPTER = re.compile(
    r"[\u4e00-\u9fff]{1,12}(?:福音|前書|後書|前书|后书|書|书|記|记|篇|傳|传|錄|录)"
    r"\s*(?:第)?[一二三四五六七八九十百廿卅0-9]{1,5}\s*章"
)
CHAPTER_VERSE = re.compile(
    r"(?:第)?[一二三四五六七八九十百廿卅0-9]{1,5}\s*章\s*"
    r"(?:第)?[一二三四五六七八九十百廿卅0-9]{1,5}\s*(?:節|节)"
)
SHORT_REF = re.compile(r"(?:太|可|路|約|约|羅|罗|林前|林後|林后|提前|提後|提后)\s*\d{1,3}:\d{1,3}")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def json_sha(value: object) -> str:
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode())


def read_artifact(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    digest = value.pop("artifact_sha256", None)
    if digest != json_sha(value):
        raise ValueError(f"artifact SHA mismatch: {path}")
    value["artifact_sha256"] = digest
    return value


def live_text(text: str) -> str:
    return re.sub(r"~~([^~]+?)~~", "\n", text, flags=re.S)


def sermon_rows(raw: bytes) -> tuple[list[str], str]:
    value = json.loads(raw)
    rows = value if isinstance(value, list) else value.get("script")
    if not isinstance(rows, list):
        raise ValueError("sermon has no script list")
    body = []
    for item in rows:
        row = dict(item) if isinstance(item, dict) else {"text": str(item or "")}
        original = str(row.get("text") or "")
        if re.fullmatch(r"#{1,6}\s+.+", original.strip()):
            continue
        row["text"] = live_text(original)
        if (str(row.get("type") or "").strip().lower() in {"subtitle", "comment"}
                or str(row.get("index") or "").startswith("subtitle-")):
            continue
        body.append({k: v for k, v in row.items() if k not in {"type", "user_id"}})
    return [str(row.get("text") or "") for row in body], json_sha(body)


def notes_rows(raw: bytes) -> list[str]:
    text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return [block.strip() for block in re.split(r"\n[ \t]*\n+", text) if block.strip()]


def reference_trail(paragraphs: list[str], anchors: list[int],
                    *, lookback: int = 20, per_anchor: int = 3) -> list[dict]:
    """Expose earlier citation cues, never infer that the Claim owns them.

    Editorial subtitles are intentionally not retrieval boundaries: they are
    not professor-spoken evidence and sometimes split one continuing argument.
    """

    cues = []
    for anchor in sorted(set(anchors)):
        for index in range(anchor - 1, max(0, anchor - lookback) - 1, -1):
            text = paragraphs[index]
            matches = []
            for kind, pattern in (("book_chapter", BOOK_CHAPTER),
                                  ("chapter_verse", CHAPTER_VERSE),
                                  ("short_ref", SHORT_REF)):
                matches.extend((match.start(), match.end(), kind) for match in pattern.finditer(text))
            matches.sort()
            if not matches:
                continue
            start, end, kind = matches[0]
            cues.append({"for_anchor": f"S{anchor+1:04d}",
                         "paragraph_key": f"S{index+1:04d}",
                         "distance_paragraphs": anchor - index,
                         "cue_kind": kind,
                         "cue_excerpt": text[max(0, start - 60): min(len(text), end + 100)]})
            if sum(cue["for_anchor"] == f"S{anchor+1:04d}" for cue in cues) >= per_anchor:
                break
    return cues


def source_payloads(source_ids: list[str]) -> dict[str, dict]:
    load_dotenv()
    dsn = os.getenv("KNOWLEDGE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("missing read-only knowledge database URL")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        cur.execute("""SELECT object_id, payload FROM wang_knowledge.objects
                       WHERE collection='source_documents' AND object_id=ANY(%s)
                       AND retired_at IS NULL""", (source_ids,))
        result = {str(source_id): dict(payload) for source_id, payload in cur}
    if set(result) != set(source_ids):
        raise ValueError("frozen source is missing from current source records")
    return result


def matching_source(source: dict, frozen_sha: str) -> tuple[Path, bytes, list[str], str, str]:
    path = Path(source["source_path"])
    expected_body = str(source.get("source_body_sha256") or "")
    paths = [path]
    if source.get("source_type") == "sermon_transcript":
        paths += [path.parent.parent / folder / path.name
                  for folder in ("script_patched", "script_review", "script_published")]
    seen = set()
    candidates = []
    for candidate in paths:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        raw = candidate.read_bytes()
        file_sha = sha(raw)
        if source.get("source_type") == "notes_manuscript":
            rows, body_sha = notes_rows(raw), ""
        else:
            rows, body_sha = sermon_rows(raw)
        candidates.append((candidate, raw, rows, file_sha, body_sha))
    for candidate in candidates:
        if candidate[3] == frozen_sha:
            return candidate
    for candidate in candidates:
        if expected_body and candidate[4] == expected_body:
            return candidate
    raise ValueError(f"no frozen file or body match: {source['source_id']}")


def build(queue: dict, packet: dict, *, include_all: bool = False) -> dict:
    if queue["role_packet_sha256"] != packet["artifact_sha256"]:
        raise ValueError("queue and packet mismatch")
    claims = {row["claim_id"]: row for row in packet["claims"]}
    rows = ([row for row in queue["rows"]] if include_all else [
        row for row in queue["rows"] if not row["claim_scripture_refs"]
        and not any(step["scripture_refs"] for step in claims[row["claim_id"]]["evidence_steps"]
                    )])
    sources = source_payloads(sorted({row["source_id"] for row in rows}))
    cache = {}
    results = []
    for queue_row in rows:
        claim = claims[queue_row["claim_id"]]
        source_id = claim["source_id"]
        if source_id not in cache:
            cache[source_id] = matching_source(sources[source_id], claim["source_file_sha256"])
        path, raw, paragraphs, file_sha, body_sha = cache[source_id]
        fragments = [f for step in claim["evidence_steps"] for f in step["fragments"]]
        anchors = []
        for fragment in fragments:
            excerpt = fragment["verbatim_excerpt"]
            key = str(fragment["paragraph_key"] or "")
            index = int(key[1:]) - 1 if re.fullmatch(r"S\d{4}", key) else None
            if index is None or index >= len(paragraphs) or excerpt not in paragraphs[index]:
                matches = [i for i, text in enumerate(paragraphs) if excerpt in text]
                if len(matches) != 1:
                    raise ValueError(f"fragment locator not uniquely verified: {fragment['fragment_id']}")
                index = matches[0]
            anchors.append(index)
        window = sorted({i for index in anchors
                         for i in range(max(0, index - 1), min(len(paragraphs), index + 2))})
        results.append({
            "claim_id": claim["claim_id"], "source_id": source_id,
            "claim_content_sha256": claim["claim_content_sha256"],
            "source_content_sha256": claim["source_content_sha256"],
            "frozen_source_file_sha256": claim["source_file_sha256"],
            "source_path": str(path), "source_file_sha256": file_sha,
            "source_body_sha256": body_sha,
            "source_match": "exact_file" if file_sha == claim["source_file_sha256"] else "exact_body_alternate_file",
            "statement": claim["statement"], "reason_code": queue_row["reason_code"],
            "claim_scripture_refs": claim["scripture_refs"],
            "evidence_steps": [{"statement": step["statement"],
                                "scripture_refs": step["scripture_refs"]}
                               for step in claim["evidence_steps"]],
            "primary_role": queue_row["primary_role"],
            "independent_role": queue_row["independent_role"],
            "fragment_ids": [f["fragment_id"] for f in fragments],
            "anchor_indices": sorted(set(anchors)),
            "anchor_trail": reference_trail(paragraphs, anchors),
            "context": [{"paragraph_key": f"S{i+1:04d}", "text": paragraphs[i]} for i in window],
        })
    body = {
        "schema_version": "wang_claim_role_source_context_audit_v2",
        "status": "source_context_verified_not_role_adjudicated",
        "scope": "all_unresolved" if include_all else "no_claim_or_evidence_scripture_refs",
        "queue_sha256": queue["artifact_sha256"],
        "packet_sha256": packet["artifact_sha256"],
        "claim_count": len(results),
        "source_count": len(cache),
        "source_match_counts": dict(sorted(Counter(row["source_match"] for row in results).items())),
        "rows": results,
    }
    return body | {"artifact_sha256": json_sha(body)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queue", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all", action="store_true", help="include all queue rows")
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError(f"refusing to overwrite: {args.output}")
    result = build(read_artifact(args.queue), read_artifact(args.packet), include_all=args.all)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "rows"}, sort_keys=True))


if __name__ == "__main__":
    main()
