"""Per-paragraph source bindings for a draft-first essay, derived without a model.

The review page shows the owner, paragraph by paragraph, where the professor
said it. Writing and review already record that: the author's self-check
lands every checklist item on a verbatim sentence, each item leads through
its viewpoint, Claims and evidence to source fragments the Claim layer
verified, and the reviewer notes where it found support for paragraphs no
item covers (`paragraph_sources`). This module follows those links. A model
used to propose the spans after the fact (owner, 2026-10-24: 「审核的时候没有做过吗」);
every span is still checked character for character against the source and
dropped with a recorded finding when it does not survive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.draft_first_author_runner import argument_route_charter
from backend.pipeline.draft_first_checklist import anchor_in
from backend.pipeline.matthew_exposition_authoring import sha256_text

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIN_EXCERPT_CHARS = 20
MAX_SPANS_PER_PARAGRAPH = 3
#: Character-bigram overlap below this marks a span as low_overlap: verbatim
#: in the source but bearing little visible relation to the paragraph it
#: claims to support. Flagged, never silently kept as clean (#293).
LOW_OVERLAP_FLOOR = 0.10


def _bigrams(value: str) -> set[str]:
    text = re.sub(r"\s+", "", value)
    return {text[i : i + 2] for i in range(len(text) - 1)}


def excerpt_overlap(paragraph_text: str, excerpt: str) -> float:
    """Share of the excerpt's character bigrams that appear in the paragraph."""

    excerpt_grams = _bigrams(excerpt)
    if not excerpt_grams:
        return 0.0
    return len(excerpt_grams & _bigrams(paragraph_text)) / len(excerpt_grams)

def reader_paragraphs(markdown: str) -> list[dict[str, Any]]:
    """Reader-visible paragraphs in document order, headings excluded.

    This split is the shared contract between binding generation and the
    review read model — both sides must segment the manuscript identically
    or the paragraph SHAs stop matching.
    """

    paragraphs: list[dict[str, Any]] = []
    for block in re.split(r"\n\s*\n", markdown):
        text = block.strip()
        if not text or re.fullmatch(r"#{1,6}\s+.*", text.splitlines()[0]) and len(text.splitlines()) == 1:
            continue
        paragraphs.append(
            {
                "paragraph_index": len(paragraphs),
                "paragraph_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text,
            }
        )
    return paragraphs


def verify_bindings(
    proposed: Mapping[str, Any],
    *,
    paragraphs: list[dict[str, Any]],
    packet: Mapping[str, Any],
) -> dict[str, Any]:
    """Keep only spans that are verbatim in their source; record the rest."""

    originals = (packet.get("source_originals") or {}).get("originals") or []
    content_by_source = {
        str(item.get("source_id") or ""): str(item.get("content") or "")
        for item in originals
    }
    known_routes = {
        str(item.get("route_revision_id") or "")
        for item in argument_route_charter(dict(packet))
    }
    rows = {int(item["paragraph_index"]): item for item in proposed["bindings"]}
    findings: list[dict[str, Any]] = []
    verified: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        index = paragraph["paragraph_index"]
        row = rows.get(index) or {"spans": []}
        spans: list[dict[str, str]] = []
        for span in list(row.get("spans") or [])[:MAX_SPANS_PER_PARAGRAPH]:
            source_id = str(span.get("source_id") or "")
            excerpt = str(span.get("excerpt") or "").strip()
            content = content_by_source.get(source_id)
            if content is None:
                findings.append(
                    {"paragraph_index": index, "code": "unknown_source", "source_id": source_id}
                )
                continue
            if len(excerpt) < MIN_EXCERPT_CHARS:
                findings.append(
                    {"paragraph_index": index, "code": "excerpt_too_short", "excerpt": excerpt}
                )
                continue
            if excerpt not in content:
                findings.append(
                    {
                        "paragraph_index": index,
                        "code": "excerpt_not_verbatim",
                        "source_id": source_id,
                        "excerpt": excerpt[:80],
                    }
                )
                continue
            overlap = excerpt_overlap(str(paragraph["text"]), excerpt)
            if overlap < LOW_OVERLAP_FLOOR:
                findings.append(
                    {
                        "paragraph_index": index,
                        "code": "low_overlap",
                        "source_id": source_id,
                        "overlap": round(overlap, 3),
                        "excerpt": excerpt[:80],
                    }
                )
            spans.append(
                {
                    "source_id": source_id,
                    "excerpt": excerpt,
                    "overlap": round(overlap, 3),
                }
            )
        route_revision_id = row.get("route_revision_id")
        if route_revision_id is not None and str(route_revision_id) not in known_routes:
            findings.append(
                {
                    "paragraph_index": index,
                    "code": "unknown_route",
                    "route_revision_id": str(route_revision_id),
                }
            )
            route_revision_id = None
        verified.append(
            {
                "paragraph_index": index,
                "paragraph_sha256": paragraph["paragraph_sha256"],
                "route_revision_id": route_revision_id,
                "spans": spans,
            }
        )
    missing = sorted(set(rows) - {p["paragraph_index"] for p in paragraphs})
    for index in missing:
        findings.append({"paragraph_index": index, "code": "unknown_paragraph"})
    return {"bindings": verified, "findings": findings}


def _fragment_ids(item: Mapping[str, Any], index: Mapping[str, Any]) -> list[str]:
    """Source fragments behind one checklist item, in packet order."""

    kind = item.get("kind")
    if kind == "observation":
        support = index["observations"].get(str(item["item_id"]).split(":", 1)[1]) or {}
        return [str(b["source_fragment_id"]) for b in support.get("evidence_bindings") or []]
    if kind == "route":
        route = index["routes"].get(str(item["item_id"]).split(":", 1)[1]) or {}
        return [str(f) for a in route.get("attestations") or [] for b in a.get("step_bindings") or []
                for f in b.get("source_fragment_ids") or []]
    revisions = item.get("viewpoint_revision_ids") or [item.get("viewpoint_revision_id")]
    ids: list[str] = []
    for revision in revisions:
        viewpoint = index["viewpoints"].get(str(revision)) or {}
        for claim_id in viewpoint.get("member_claim_ids") or []:
            claim = index["claims"].get(str(claim_id)) or {}
            steps = claim.get("eligible_evidence_step_ids") or claim.get("evidence_step_ids") or []
            for step_id in steps:
                step = index["steps"].get(str(step_id)) or {}
                ids.extend(str(f) for f in step.get("source_fragment_ids") or [step.get("source_fragment_id")] if f)
    return ids


_VERSE_LABEL = re.compile(r"[（(]([^（）()]*\d+\s*[:：]\s*\d+[^（）()]*)[)）]\s*$")


def _verse_range(slug: str) -> tuple[str, int, int, int]:
    parts = slug.split("-")
    start = int(parts[2])
    return parts[0], int(parts[1]), start, int(parts[3]) if len(parts) > 3 else start


def verse_mentions(content: str) -> list[tuple[int, str]]:
    """Where a source names a verse: written (「太16:13」) or spoken (「第七章第三節」)."""

    from backend.api.scripture import _SINGLE_REFERENCE_PATTERN, _resolve_book_slug, spoken_references

    found = list(spoken_references(content))
    for match in _SINGLE_REFERENCE_PATTERN.finditer(content):
        book = _resolve_book_slug(match.group("book"))
        if book is None:
            # A greedy book token swallows the words before it (「說馬太福音」).
            book = next((_resolve_book_slug(match.group("book")[-n:]) for n in range(len(match.group("book")) - 1, 0, -1)
                         if _resolve_book_slug(match.group("book")[-n:])), None)
        if book:
            end = match.group("end") or match.group("start")
            found.append((match.start("book"), f"{book}-{match.group('chapter')}-{match.group('start')}-{end}"))
    return sorted(found)


def _read_aloud(text: str, content: str) -> str | None:
    """The source's own words for a quoted verse, if the professor read it.

    Matching ignores punctuation and the glyph variants the quote check folds,
    so the excerpt is cut from the source as written, never from the verse.
    """

    from backend.pipeline.draft_first_scripture import _ELISION, normalize

    words, at = [], []
    for position, char in enumerate(content):
        folded = normalize(char)
        if folded:
            words.append(folded)
            at.append(position)
    haystack = "".join(words)
    segment = max((normalize(part) for part in _ELISION.split(text)), key=len, default="")
    index = haystack.find(segment) if len(segment) >= 8 else -1
    if index < 0:
        return None
    excerpt = content[at[index]: at[index + len(segment) - 1] + 1]
    return excerpt[:150]


def scripture_bindings(paragraph_text: str, packet: Mapping[str, Any]) -> list[dict[str, str]]:
    """Where the professor reads or cites each verse a quote block shows.

    Owner, 2026-10-24: a verse the article quotes with no source behind it
    can be taken for the article's own use of Scripture. A block is bound to
    the professor reading it, else to his naming the verse.
    """

    from backend.api.scripture import reference_slugs

    lines = [line[1:].strip() for line in paragraph_text.splitlines() if line.startswith(">") and line[1:].strip()]
    slugs = [slug for line in lines for label in _VERSE_LABEL.findall(line) for slug in reference_slugs(label)]
    spans: list[dict[str, str]] = []
    for item in (packet.get("source_originals") or {}).get("originals") or []:
        content, source_id = str(item.get("content") or ""), str(item.get("source_id") or "")
        for line in lines:
            excerpt = _read_aloud(_VERSE_LABEL.sub("", line), content)
            if excerpt and len(excerpt) >= MIN_EXCERPT_CHARS:
                spans.append({"source_id": source_id, "excerpt": excerpt, "basis": "scripture_read"})
    if spans:
        return spans
    for item in (packet.get("source_originals") or {}).get("originals") or []:
        content, source_id = str(item.get("content") or ""), str(item.get("source_id") or "")
        for position, mention in verse_mentions(content):
            book, chapter, start, end = _verse_range(mention)
            if any(b == book and c == chapter and start <= e and s <= end
                   for b, c, s, e in map(_verse_range, slugs)):
                excerpt = content[position: position + 120].split("\n\n")[0]
                if len(excerpt) < MIN_EXCERPT_CHARS:
                    excerpt = content[position: position + 40]
                spans.append({"source_id": source_id, "excerpt": excerpt, "basis": "scripture_cited"})
                break
    return spans


MATCH_CANDIDATES = 3
MATCH_SCHEMA: dict[str, Any] = {
    "name": "wang_draft_first_source_match_v1",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "matches": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "paragraph_index": {"type": "integer"},
                        "candidate_index": {"type": "integer"},
                        "reason": {"type": "string"},
                    },
                    "required": ["paragraph_index", "candidate_index", "reason"],
                },
            }
        },
        "required": ["matches"],
    },
}


def candidate_passages(paragraph_text: str, packet: Mapping[str, Any], limit: int = MATCH_CANDIDATES) -> list[dict[str, Any]]:
    """The source windows that read most like a paragraph, cut from the source as written.

    A window about as long as the paragraph slides over each source; its score
    is the share of the paragraph's character bigrams it contains. A whole
    sermon block would score high on anything, which is why it is a window.
    """

    from backend.pipeline.draft_first_scripture import normalize

    target = normalize(paragraph_text)
    grams = {target[i:i + 2] for i in range(len(target) - 1)}
    if len(grams) < 8:
        return []
    width, step = int(len(target) * 1.3), max(4, len(target) // 8)
    found: list[dict[str, Any]] = []
    for item in (packet.get("source_originals") or {}).get("originals") or []:
        content = str(item.get("content") or "")
        words, at = [], []
        for position, char in enumerate(content):
            folded = normalize(char)
            if folded:
                words.append(folded)
                at.append(position)
        haystack = "".join(words)
        best: tuple[float, int] = (0.0, 0)
        for start in range(0, max(1, len(haystack) - width), step):
            window = haystack[start:start + width]
            score = len(grams & {window[i:i + 2] for i in range(len(window) - 1)}) / len(grams)
            best = max(best, (score, start))
        if best[0] and at:
            start = at[best[1]]
            end = at[min(best[1] + width, len(at)) - 1] + 1
            found.append({"source_id": str(item.get("source_id")), "excerpt": content[start:end], "score": round(best[0], 3)})
    return sorted(found, key=lambda row: -row["score"])[:limit]


def confirm_matches(client: Any, paragraphs: list[dict[str, Any]], packet: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Ask a model which candidate passage is each paragraph's source; it only chooses.

    The program narrows, the model judges correspondence, and the chosen
    excerpt -- already verbatim -- is verified again by `verify_bindings`.
    """

    offered = {p["paragraph_index"]: candidate_passages(p["text"], packet) for p in paragraphs}
    offered = {index: rows for index, rows in offered.items() if rows}
    if not offered:
        return []
    text = {p["paragraph_index"]: p["text"] for p in paragraphs}
    reply = client.generate_json(
        (Path(__file__).with_name("prompts") / "draft_first_source_match.md").read_text(encoding="utf-8"),
        json.dumps({"paragraphs": [{"paragraph_index": index, "text": text[index],
                                    "candidates": [{"source_id": c["source_id"], "excerpt": c["excerpt"]} for c in rows]}
                                   for index, rows in offered.items()]}, ensure_ascii=False, sort_keys=True),
        MATCH_SCHEMA,
    )
    chosen = []
    for match in reply["matches"]:
        rows = offered.get(int(match["paragraph_index"])) or []
        if 0 <= int(match["candidate_index"]) < len(rows):
            row = rows[int(match["candidate_index"])]
            chosen.append({"paragraph_index": int(match["paragraph_index"]), "source_id": row["source_id"],
                           "excerpt": row["excerpt"], "reason": str(match["reason"])})
    return chosen


def derive_bindings(
    manuscript: str,
    packet: Mapping[str, Any],
    self_check: list[Mapping[str, Any]],
    checklist: list[Mapping[str, Any]],
    paragraph_sources: list[Mapping[str, Any]] = (),
    matches: list[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Proposed bindings (the shape `verify_bindings` checks) and what each came from."""

    paragraphs = reader_paragraphs(manuscript)
    index = {
        "viewpoints": {str(v["revision"].get("viewpoint_revision_id")): v for v in packet.get("focal_viewpoints") or []},
        "claims": {str(c["claim_id"]): c for c in packet.get("claims") or []},
        "steps": {str(e["evidence_step_id"]): e for e in packet.get("evidence_steps") or []},
        "fragments": {str(f["fragment_id"]): f for f in packet.get("source_fragments") or []},
        "observations": {str(o["observation_support_id"]): o for o in packet.get("observation_supports") or []},
        "routes": {str(r["revision"].get("argument_route_revision_id")): r for r in packet.get("argument_routes") or []},
    }
    items = {str(item["item_id"]): item for item in checklist}
    rows = [{"paragraph_index": p["paragraph_index"], "route_revision_id": None, "spans": [], "basis": []}
            for p in paragraphs]

    def home(anchor: str) -> dict[str, Any] | None:
        for paragraph, row in zip(paragraphs, rows):
            if anchor and anchor_in(paragraph["text"], anchor):
                return row
        return None

    for disposition in self_check:
        item = items.get(str(disposition.get("item_id")))
        row = home(str(disposition.get("anchor") or "")) if disposition.get("status") == "landed" else None
        if item is None or row is None:
            continue
        row["basis"].append(item["item_id"])
        if item.get("kind") == "route" and row["route_revision_id"] is None:
            row["route_revision_id"] = str(item["item_id"]).split(":", 1)[1]
        for fragment_id in _fragment_ids(item, index):
            fragment = index["fragments"].get(fragment_id) or {}
            excerpt = str(fragment.get("verbatim_excerpt") or "").strip()
            if len(excerpt) >= MIN_EXCERPT_CHARS:
                row["spans"].append({"source_id": str(fragment["source_id"]), "excerpt": excerpt})
    for source in paragraph_sources:
        row = home(str(source.get("anchor") or ""))
        if row is not None:
            row["basis"].append("review")
            row["spans"].append({"source_id": str(source["source_id"]), "excerpt": str(source["excerpt"]).strip()})
    for match in matches:
        row = rows[int(match["paragraph_index"])]
        row["basis"].append("model_match")
        row["spans"].append({"source_id": str(match["source_id"]), "excerpt": str(match["excerpt"])})
    for paragraph, row in zip(paragraphs, rows):
        if paragraph["text"].startswith(">"):
            found = scripture_bindings(paragraph["text"], packet)
            row["basis"].extend(sorted({span.pop("basis") for span in found}))
            row["spans"].extend(found)
    # A lead-in (「耶穌隨即問他們：」) introduces the verse after it and has
    # that verse's source.
    for index, (paragraph, row) in enumerate(zip(paragraphs, rows)):
        if not row["spans"] and paragraph["text"].rstrip().endswith(("：", ":")) and index + 1 < len(rows):
            row["basis"].append("lead_in")
            row["spans"] = [dict(span) for span in rows[index + 1]["spans"]]
    for paragraph, row in zip(paragraphs, rows):
        # The spans that read most like the paragraph, each once; the
        # reviewer's own finds stay in, they are why the paragraph is bound.
        unique = {(span["source_id"], span["excerpt"]): span for span in row["spans"]}
        row["spans"] = sorted(unique.values(), key=lambda span: -excerpt_overlap(paragraph["text"], span["excerpt"]))
        row["spans"] = row["spans"][:MAX_SPANS_PER_PARAGRAPH]
    return {"bindings": rows}


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review-dir", type=Path, required=True,
                        help="a draft_first_review_runner output: final.md, final-self-check.json, round-*/review.json")
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--no-model", action="store_true",
                        help="bind only what the self-check, review and verse matching give")
    args = parser.parse_args()

    from backend.pipeline.draft_first_checklist import build_checklist

    raw = json.loads(args.packet.read_text(encoding="utf-8"))
    packet = raw.get("result", raw)
    manuscript = (args.review_dir / "final.md").read_text(encoding="utf-8")
    self_check = json.loads((args.review_dir / "final-self-check.json").read_text(encoding="utf-8"))
    last_review = max(args.review_dir.glob("round-*/review.json"), key=lambda path: path.parent.name)
    review = json.loads(last_review.read_text(encoding="utf-8"))
    paragraphs = reader_paragraphs(manuscript)
    checklist = build_checklist(packet)
    sources = list(review.get("paragraph_sources") or [])
    first = derive_bindings(manuscript, packet, self_check, checklist, sources)
    # What writing and review did not bind, a model matches against program
    # candidates -- except the closing summary, which answers in the
    # article's own words (owner, 2026-10-24) and the footnotes.
    prose = [p for p in paragraphs if not p["text"].startswith((">", "[^"))]
    closing = prose[-1]["paragraph_index"] if prose else -1
    unbound = [p for p, row in zip(paragraphs, first["bindings"])
               if not row["spans"] and p in prose and p["paragraph_index"] != closing
               and not p["text"].rstrip().endswith(("：", ":"))]
    client = None
    matches: list[dict[str, Any]] = []
    if unbound and not args.no_model:
        from backend.pipeline.codex_subscription_client import CodexSubscriptionClient
        client = CodexSubscriptionClient(model=args.model, reasoning_effort="high")
        matches = confirm_matches(client, unbound, packet)
    proposed = derive_bindings(manuscript, packet, self_check, checklist, sources, matches)
    result = verify_bindings(proposed, paragraphs=paragraphs, packet=packet)
    basis = {row["paragraph_index"]: row["basis"] for row in proposed["bindings"]}
    for row in result["bindings"]:
        row["basis"] = basis[row["paragraph_index"]]
    bound = sum(1 for item in result["bindings"] if item["spans"])
    record = {
        "schema_version": "wang_draft_first_source_bindings_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "manuscript_sha256": sha256_text(manuscript),
        "evidence_packet_sha256": packet.get("evidence_packet_sha256"),
        "derivation": "self_check_review_verses_and_model_match",
        "match_model": f"{client.backend}:{client.model}" if client else None,
        "matches": matches,
        "review_sha256": sha256_text(last_review.read_text(encoding="utf-8")),
        "paragraphs_total": len(paragraphs),
        "paragraphs_bound": bound,
        "bindings": result["bindings"],
        "findings": result["findings"],
    }
    record["bindings_sha256"] = sha256_json(record)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"paragraphs_total": len(paragraphs), "paragraphs_bound": bound,
                      "findings": len(result["findings"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
