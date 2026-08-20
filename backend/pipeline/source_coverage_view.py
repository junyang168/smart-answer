"""Read one source's own text beside the claim-layer records anchored into it.

`argument_layer_view` answers "what does this source argue" by drawing the
nodes and their relations.  It cannot answer the question
`source_to_claim_layer_ledger_v1` puts first:

    來源裡的材料，有沒有全部進到論證圖裡？

That question is only answerable with the source *text* as the denominator, so
this module loads the transcript or manuscript itself, places every
`source_fragment` back on the segment it was cut from, and reports what is left
over.  Highlighted text is what the claim layer took; unhighlighted text is
material no record has ever claimed.

Nothing here writes, and nothing here is a gate.  The ledger's `represented` /
`excluded` / `unprocessed` verdicts are records a human or a runner decides
(#83/#84); this is the read-only view that shows what those verdicts would be
looking at today.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
from backend.pipeline.base_contract_coverage import sentence_spans
from backend.pipeline.knowledge_source import live_script, markdown_blocks

# Collections that can carry a `source_fragment_id`, i.e. that can be placed on
# the source text.  `claims` deliberately is not one of them: a claim reaches
# the text only through the evidence steps that produced it, and pretending
# otherwise would invent an anchor the store does not hold.
ANCHORED_COLLECTIONS = ("evidence_steps", "observations", "questions", "position_nodes")
KIND_BY_COLLECTION = {
    "evidence_steps": "step",
    "observations": "observation",
    "questions": "question",
    "position_nodes": "position",
}

# Ids grew in layers; the same trimming `argument_layer_view` does keeps a card
# readable without hiding which record it is.
DK_ID = re.compile(r"DK-([0-9a-f]{6,})")

# A segment that is nothing but a Markdown heading.  Half the segments of a
# manuscript are headings, and counting them as material the claim layer failed
# to take would overstate the gap by roughly a factor of two.  They are still
# reported — filtering them out of the inventory would be an exclusion no one
# recorded — but they are labelled so the residue can be read for what it is.
HEADING_RE = re.compile(r"^#{1,6}\s+\S")


def segment_key(position: int) -> str:
    """The key extraction uses for the segment at `position` (zero-based)."""
    return f"S{position + 1:04d}"


def _label(object_id: str) -> str:
    text = DK_ID.sub("", object_id)
    text = re.sub(r"^(AI-ADJ|ES|EV|OQ|FR)-", "", text)
    return text.strip("-") or object_id


def _as_list(value: Any) -> list[str]:
    return [str(item) for item in value if item] if isinstance(value, list) else []


def _fragment_ids(payload: dict[str, Any]) -> list[str]:
    ids = _as_list(payload.get("source_fragment_ids"))
    primary = payload.get("source_fragment_id")
    if primary:
        ids = [str(primary)] + [item for item in ids if item != str(primary)]
    return list(dict.fromkeys(ids))


def transcript_dirs(data_base_path: Path) -> list[Path]:
    """Where a transcript is looked up when the store recorded no path.

    The pilot's two lectures (`SRC-L3`, `SRC-L4`) predate `source_path`; they
    name a `transcript_id` and nothing else.  Stage order matches
    `sermon_converter_service.SERMON_TRANSCRIPT_DIRS`, so this view reads the
    same file the rest of the platform would.
    """
    return [
        data_base_path / "script_published",
        data_base_path / "script_review",
        data_base_path / "script_patched",
    ]


def resolve_source_path(document: dict[str, Any], search_dirs: Iterable[Path]) -> Optional[Path]:
    recorded = str(document.get("source_path") or "").strip()
    if recorded:
        path = Path(recorded)
        return path if path.is_file() else None
    transcript_id = str(document.get("transcript_id") or "").strip()
    if not transcript_id:
        return None
    for directory in search_dirs:
        candidate = Path(directory) / f"{transcript_id}.json"
        if candidate.is_file():
            return candidate
    return None


def load_segments(document: dict[str, Any], path: Path) -> tuple[list[dict[str, Any]], str]:
    """Segment a source exactly the way extraction segmented it.

    A second segmentation would put every existing anchor off by an unknown
    amount, so both branches here are the ones the extraction path already
    uses: `markdown_blocks` for a manuscript, the transcript's own `script`
    list for a sermon.  Returns the segments and the file's SHA256, because a
    source edited since extraction invalidates every anchor into it and the
    reader must be able to say so.
    """
    raw = path.read_bytes()
    if str(document.get("source_type") or "") == "notes_manuscript":
        blocks = markdown_blocks(raw.decode("utf-8"))
        script: list[dict[str, Any]] = [
            {"index": position + 1, "text": block} for position, block in enumerate(blocks)
        ]
    else:
        parsed = json.loads(raw)
        script = parsed.get("script", []) if isinstance(parsed, dict) else parsed
        if not isinstance(script, list):
            raise ValueError(f"{path}: transcript has no script list")
        # A struck-through span was deleted by a proofreader, so it is not a
        # gap in coverage and must not be shown to a reviewer as one.
        script = live_script(script)

    segments = []
    for position, item in enumerate(script):
        text = str((item or {}).get("text") or "")
        stripped = text.strip()
        segments.append(
            {
                "ordinal": position,
                "key": segment_key(position),
                "index": (item or {}).get("index"),
                "text": text,
                "is_heading": bool(HEADING_RE.match(stripped)) and "\n" not in stripped,
                "start_time": (item or {}).get("start_time"),
                "end_time": (item or {}).get("end_time"),
                "type": (item or {}).get("type") or "",
                "spans": [],
                "fragment_ids": [],
            }
        )
    return segments, hashlib.sha256(raw).hexdigest()


class _SegmentIndex:
    """The three ways a stored fragment can name its place in a source.

    Only the first is current.  The other two exist because 143 pilot
    fragments address a segment by the transcript's own `index`, and 18
    hand-built manuscript fragments name no segment at all and can be placed
    only by their own verbatim text.  Dropping either group would report
    material as unextracted when it demonstrably was extracted.
    """

    def __init__(self, segments: list[dict[str, Any]]):
        self.texts = [segment["text"] for segment in segments]
        self.by_key = {segment["key"]: segment["ordinal"] for segment in segments}
        counts: dict[str, int] = defaultdict(int)
        for segment in segments:
            counts[str(segment["index"])] += 1
        self.by_index = {
            str(segment["index"]): segment["ordinal"]
            for segment in segments
            if counts[str(segment["index"])] == 1
        }

    def _by_text(self, excerpt: str) -> list[int]:
        return [position for position, text in enumerate(self.texts) if excerpt in text]

    def place(self, paragraph_key: str, excerpt: str) -> tuple[Optional[int], str]:
        if paragraph_key in self.by_key:
            return self.by_key[paragraph_key], "segment_key"
        if paragraph_key in self.by_index:
            return self.by_index[paragraph_key], "segment_index"
        if not excerpt:
            return None, "no_excerpt"
        found = self._by_text(excerpt)
        if len(found) == 1:
            return found[0], "verbatim_search"
        return None, "ambiguous_excerpt" if found else "not_in_source"

    def elsewhere(self, excerpt: str) -> Optional[int]:
        """The one segment holding this excerpt, when its own anchor missed."""
        found = self._by_text(excerpt)
        return found[0] if len(found) == 1 else None


def _place_fragments(
    fragments: list[dict[str, Any]], segments: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Put every fragment back on its segment, and say how it got there."""
    index = _SegmentIndex(segments)
    placed: dict[str, dict[str, Any]] = {}
    for payload in fragments:
        fragment_id = str(payload.get("fragment_id") or "")
        excerpt = str(payload.get("verbatim_excerpt") or "").strip()
        ordinal, method = index.place(str(payload.get("paragraph_key") or ""), excerpt)
        entry = {
            "id": fragment_id,
            "excerpt": excerpt,
            "paragraph_key": payload.get("paragraph_key"),
            "source_segment_index": payload.get("source_segment_index"),
            "media_time": payload.get("media_time"),
            "media_end_time": payload.get("media_end_time"),
            "anchor_state": payload.get("anchor_state") or "",
            "review_status": payload.get("review_status") or "",
            "segment_ordinal": ordinal,
            "anchor_method": method,
            "char_start": None,
            "char_end": None,
            "found_at_ordinal": None,
            "node_ids": [],
        }
        if ordinal is not None:
            text = segments[ordinal]["text"]
            start = text.find(excerpt) if excerpt else -1
            if start >= 0:
                entry["char_start"] = start
                entry["char_end"] = start + len(excerpt)
                segments[ordinal]["spans"].append((start, start + len(excerpt), fragment_id))
                segments[ordinal]["fragment_ids"].append(fragment_id)
            else:
                # The anchor still names a segment, but the words are no longer
                # there.  Naming where they did survive turns "broken" into a
                # repair someone can make.
                entry["anchor_method"] = "excerpt_moved" if excerpt else "empty_excerpt"
                entry["segment_ordinal"] = None
                entry["found_at_ordinal"] = index.elsewhere(excerpt) if excerpt else None
        placed[fragment_id] = entry
    return placed


def _flatten_spans(spans: list[tuple[int, int, str]]) -> list[dict[str, Any]]:
    """Turn overlapping fragment spans into runs the page can paint in order.

    Two fragments quoting overlapping words is normal — a step and the
    observation behind it often cut the same sentence — and a renderer cannot
    nest two highlights over one character.  Splitting at every boundary keeps
    both fragments reachable from the run they share.
    """
    if not spans:
        return []
    edges = sorted({edge for start, end, _ in spans for edge in (start, end)})
    runs: list[dict[str, Any]] = []
    for start, end in zip(edges, edges[1:]):
        owners = [fragment_id for span_start, span_end, fragment_id in spans if span_start < end and span_end > start]
        if owners:
            runs.append({"start": start, "end": end, "fragment_ids": owners})
    return runs


def _sentence_rows(text: str, runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sentences of one segment, each marked covered or not.

    Sentence is the granularity `source_to_claim_layer_ledger_v1` §13.1 settled
    on, and it is the granularity a reviewer asks in: *that* sentence was never
    extracted.  Coverage is span overlap, never similarity — the ledger is
    explicit that a threshold is the silent loss it exists to remove.
    """
    rows = []
    for start, end in sentence_spans(text):
        covered = any(run["start"] < end and run["end"] > start for run in runs)
        rows.append({"start": start, "end": end, "covered": covered})
    return rows


class SourceCoverageReader:
    def __init__(self, store: PostgresKnowledgeStore, data_base_path: Path):
        self.store = store
        self.search_dirs = transcript_dirs(data_base_path)

    def _rows(self, collections: Iterable[str]) -> list[tuple[str, str, str, dict[str, Any]]]:
        with self.store.connect() as conn:
            cursor = conn.execute(
                """SELECT collection, object_id, review_status, payload
                     FROM wang_knowledge.objects
                    WHERE retired_at IS NULL AND collection = ANY(%s)""",
                (list(collections),),
            )
            return [(row[0], row[1], row[2], row[3]) for row in cursor.fetchall()]

    def load(self) -> dict[str, Any]:
        rows = self._rows(("source_documents", "source_fragments", "claims") + ANCHORED_COLLECTIONS)
        documents: dict[str, dict[str, Any]] = {}
        fragments_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
        nodes: list[tuple[str, str, str, dict[str, Any]]] = []
        claims: dict[str, dict[str, Any]] = {}
        for collection, object_id, review_status, payload in rows:
            if collection == "source_documents":
                documents[object_id] = payload
            elif collection == "source_fragments":
                fragments_by_source[str(payload.get("source_id") or "")].append(payload)
            elif collection == "claims":
                claims[object_id] = {**payload, "review_status": review_status}
            else:
                nodes.append((collection, object_id, review_status, payload))
        return {
            "documents": documents,
            "fragments_by_source": fragments_by_source,
            "nodes": nodes,
            "claims": claims,
        }

    def source_ids(self) -> list[str]:
        return sorted(self.load()["documents"])

    def build(self, source_id: str, corpus: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        corpus = corpus or self.load()
        document = corpus["documents"].get(source_id)
        if document is None:
            raise KeyError(source_id)

        path = resolve_source_path(document, self.search_dirs)
        meta = {
            "source_id": source_id,
            "title": document.get("title") or document.get("transcript_id") or source_id,
            "source_type": document.get("source_type") or "",
            "transcript_id": document.get("transcript_id"),
            "project_id": document.get("project_id"),
            "source_path": str(path) if path else str(document.get("source_path") or ""),
            "recorded_sha256": document.get("source_sha256"),
            "file_state": "missing",
            "file_sha256": None,
        }
        if path is None:
            return {"source": {**meta, "stats": _empty_stats()}, "segments": [], "fragments": {}, "nodes": {}, "claims": {}}

        segments, file_sha256 = load_segments(document, path)
        meta["file_sha256"] = file_sha256
        # A source edited after extraction does not merely lose a few anchors;
        # every offset in it is now a guess.  The page has to say which it is.
        meta["file_state"] = "current" if file_sha256 == meta["recorded_sha256"] else "drifted"

        fragments = _place_fragments(corpus["fragments_by_source"].get(source_id, []), segments)
        nodes, claims = self._attach(corpus, fragments)

        for segment in segments:
            runs = _flatten_spans(segment.pop("spans"))
            segment["runs"] = runs
            segment["sentences"] = _sentence_rows(segment["text"], runs)
            segment["covered_chars"] = sum(run["end"] - run["start"] for run in runs)
            segment["node_ids"] = list(
                dict.fromkeys(
                    node_id
                    for fragment_id in segment["fragment_ids"]
                    for node_id in fragments[fragment_id]["node_ids"]
                )
            )

        return {
            "source": {**meta, "stats": _stats(segments, fragments, nodes, claims)},
            "segments": segments,
            "fragments": fragments,
            "nodes": nodes,
            "claims": claims,
        }

    def _attach(
        self, corpus: dict[str, Any], fragments: dict[str, dict[str, Any]]
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Every record anchored into this source, and the claims they produce.

        A node belongs to a source because one of its fragments does — not
        because its id happens to carry the source's hash.  Hand-built records
        name no source in their id, and they are exactly the records a reviewer
        most needs to see beside the manuscript they were written against.
        """
        nodes: dict[str, Any] = {}
        claims: dict[str, Any] = {}
        claim_ids: set[str] = set()
        for collection, object_id, review_status, payload in corpus["nodes"]:
            owned = [item for item in _fragment_ids(payload) if item in fragments]
            if not owned:
                continue
            produced = _as_list(payload.get("produced_claim_ids")) + _as_list(payload.get("answer_claim_ids"))
            claim_ids.update(produced)
            nodes[object_id] = {
                "id": object_id,
                "label": _label(object_id),
                "kind": KIND_BY_COLLECTION[collection],
                "statement": payload.get("statement") or payload.get("text") or payload.get("title") or "",
                "review_status": review_status,
                "step_type": payload.get("step_type") or "",
                "discourse_role": payload.get("discourse_role") or "",
                "support_eligibility": payload.get("support_eligibility") or "",
                "observation_type": payload.get("observation_type") or "",
                "argument_role": payload.get("argument_role") or "",
                "answer_state": payload.get("answer_state") or "",
                "attribution": payload.get("attribution") or "",
                "scripture_refs": [str(item) for item in payload.get("scripture_refs") or []],
                "fragment_ids": owned,
                "claim_ids": list(dict.fromkeys(produced)),
            }
            for fragment_id in owned:
                fragments[fragment_id]["node_ids"].append(object_id)

        # A claim also reaches this source through the steps it lists.  The two
        # directions are not mirrors — a step can omit `produced_claim_ids`
        # while the claim still names the step — so a claim listed by only one
        # of them is still this source's claim.
        for claim_id, payload in corpus["claims"].items():
            steps = _as_list(payload.get("evidence_step_ids"))
            if claim_id not in claim_ids and not any(step in nodes for step in steps):
                continue
            fragment_ids = list(
                dict.fromkeys(
                    fragment_id
                    for step in steps
                    if step in nodes
                    for fragment_id in nodes[step]["fragment_ids"]
                )
            )
            fragment_ids += [
                fragment_id
                for node in nodes.values()
                if claim_id in node["claim_ids"]
                for fragment_id in node["fragment_ids"]
                if fragment_id not in fragment_ids
            ]
            ordinals = [
                fragments[fragment_id]["segment_ordinal"]
                for fragment_id in fragment_ids
                if fragments[fragment_id]["segment_ordinal"] is not None
            ]
            claims[claim_id] = {
                "id": claim_id,
                "label": _label(claim_id),
                "statement": payload.get("statement") or payload.get("title") or "",
                "claim_type": payload.get("claim_type") or "",
                "maturity": payload.get("maturity") or "",
                "attribution": payload.get("attribution") or "",
                "review_status": payload.get("review_status") or "",
                "scripture_refs": [str(item) for item in payload.get("scripture_refs") or []],
                "evidence_step_ids": [step for step in steps if step in nodes],
                # A claim can rest on steps cut from other sermons.  Showing only
                # the local ones without saying so would make a cross-source
                # claim look like it was argued entirely from this one page.
                "foreign_evidence_steps": sum(1 for step in steps if step not in nodes),
                "fragment_ids": fragment_ids,
                # Where the claim first appears in the source, which is the
                # order the panel lists claims in.
                "first_ordinal": min(ordinals) if ordinals else None,
            }
        return nodes, claims


def _empty_stats() -> dict[str, int]:
    return {
        "segments": 0,
        "segments_covered": 0,
        "heading_segments": 0,
        "sentences": 0,
        "sentences_covered": 0,
        "chars": 0,
        "chars_covered": 0,
        "fragments": 0,
        "fragments_placed": 0,
        "steps": 0,
        "observations": 0,
        "questions": 0,
        "positions": 0,
        "claims": 0,
    }


def _stats(
    segments: list[dict[str, Any]],
    fragments: dict[str, Any],
    nodes: dict[str, Any],
    claims: dict[str, Any],
) -> dict[str, int]:
    sentences = [row for segment in segments for row in segment["sentences"]]
    kinds = defaultdict(int)
    for node in nodes.values():
        kinds[node["kind"]] += 1
    return {
        "segments": len(segments),
        "segments_covered": sum(1 for segment in segments if segment["fragment_ids"]),
        "heading_segments": sum(1 for segment in segments if segment["is_heading"]),
        "sentences": len(sentences),
        "sentences_covered": sum(1 for row in sentences if row["covered"]),
        "chars": sum(len(segment["text"]) for segment in segments),
        "chars_covered": sum(segment["covered_chars"] for segment in segments),
        "fragments": len(fragments),
        "fragments_placed": sum(1 for item in fragments.values() if item["segment_ordinal"] is not None),
        "steps": kinds["step"],
        "observations": kinds["observation"],
        "questions": kinds["question"],
        "positions": kinds["position"],
        "claims": len(claims),
    }
