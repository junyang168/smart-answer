"""Build scoped locator packets and validate model-selected paragraph locations."""

from difflib import SequenceMatcher
import json
import re

from backend.pipeline.transcript_evidence_calibration import audio_position
from backend.pipeline.transcript_evidence_migration import literal_layout, positions
from backend.pipeline.slide_visual_evidence import obj

PROMPT = """Locate the same occurrence in the new transcript. Use the old quotation,
surrounding speech, saved playback range and speaker turns. Consult the direct
consumer's statement and ordered citations, especially the resolved citations
immediately before and after the target. Repeated target IDs can refer to
separate spoken occurrences. Use the surrounding citations to distinguish them.
For a consumer whose citations are only verbal prompts, locate the actual
question or explanation described by its statement. Use its quoted prompts as
context, never as the substantive citation. The new transcript is authoritative. Differences in wording, speaker labels or scripture references
do not rule out a location. Select only supplied paragraph IDs. For each
paragraph select the smallest continuous span that expresses the old citation,
including both question and answer if the old citation contains both. Return
exact beginning and ending marker strings copied from the supplied live text.
Each marker must occur exactly once in the paragraph. A single short quotation
can use the same marker for both boundaries. Do not include other speaker turns
or adjacent points merely because they share the paragraph. For a single
occurrence select consecutive paragraphs. For repeated occurrences select one
distinct paragraph per occurrence in playback order. When the location or
boundaries are uncertain, return an empty spans list. Return one decision per
record. Do not paraphrase or decide what a Claim means."""
SCHEMA = obj({"decisions": {"type": "array", "items": obj({
    "record_key": {"type": "string"},
    "spans": {"type": "array", "items": obj({
        "paragraph_key": {"type": "string"},
        "start_marker": {"type": "string"},
        "end_marker": {"type": "string"},
    }), "maxItems": 4},
    "reason": {"type": "string"},
})}})


def select_spans(packet, decision, new):
    """Turn unique boundary markers into exact, live, paragraph-local slices."""
    allowed = {p["paragraph_key"]: p for p in packet["candidates"]}
    spans = decision["spans"]
    if not spans or len(spans) > 4 or not decision["reason"].strip():
        return []
    selected = []
    for span in spans:
        key = span["paragraph_key"]
        if key not in allowed:
            return []
        live = new["paragraphs"][key]["text"]
        if live != allowed[key]["text"]:
            raise ValueError("Candidate differs from current live paragraph")
        start_marker, end_marker = span["start_marker"], span["end_marker"]
        starts, ends = positions(live, start_marker), positions(live, end_marker)
        if len(starts) != 1 or len(ends) != 1 or ends[0] < starts[0]:
            return []
        start, end = starts[0], ends[0] + len(end_marker)
        if end < start + len(start_marker):
            return []
        quote = live[start:end]
        if not quote.strip() or "~~" in quote or len(positions(live, quote)) != 1:
            return []
        selected.append({"paragraph_key": key, "verbatim_excerpt": quote})
    return selected


def consumer_context(row, records, resolved):
    """Preserve citation slots, including repeated references to the same ID."""
    target = row["record_key"]
    consumers = []
    for key in row.get("direct_consumers", ()):
        state = records.get(key)
        if not state or state.get("retired"):
            continue
        payload = state["payload"]
        fragment_ids = payload.get("source_fragment_ids") or [payload.get("source_fragment_id")]
        citations = []
        for fragment_id in fragment_ids:
            fragment_key = f"source_fragments/{fragment_id}"
            fragment = records.get(fragment_key, {}).get("payload")
            if fragment is None:
                continue
            location = resolved.get(fragment_key, {})
            paragraph_keys = location.get("target_paragraphs") or [location.get("target_paragraph")]
            citations.append({
                "target": fragment_key == target,
                "old_quote": fragment.get("verbatim_excerpt", ""),
                "resolved_paragraphs": [key for key in paragraph_keys if key],
            })
        if any(citation["target"] for citation in citations):
            consumers.append({
                "record_key": key,
                "statement": payload.get("statement") or payload.get("text") or "",
                "citations": citations,
            })
    return consumers


def ordered_repeat_selection(packet, selected, allowed):
    """Accept distinct literal occurrences only when citation slots agree."""
    counts = {
        sum(citation["target"] for citation in consumer["citations"])
        for consumer in packet.get("consumers", ())
    }
    quote = literal_layout(packet["old_quote"])[0]
    if not quote or counts != {len(selected)} or len(selected) < 2:
        return False
    for consumer in packet["consumers"]:
        remaining = iter(selected)
        for i, citation in enumerate(consumer["citations"]):
            if not citation["target"]:
                continue
            key = next(remaining)
            if quote not in literal_layout(allowed[key]["text"])[0]:
                return False
            preceding = {k for c in consumer["citations"][:i] for k in c["resolved_paragraphs"]}
            following = {k for c in consumer["citations"][i + 1:] for k in c["resolved_paragraphs"]}
            if any(k > key for k in preceding) or any(k < key for k in following):
                return False
    return True


def candidate_packet(row, old_paragraphs, new_paragraphs, clock, *, limit=8, consumers=()):
    """Rank within the old playback interval and retain adjacent candidate turns."""
    ordered = list(new_paragraphs)
    clock_matches = [
        key for key, p in new_paragraphs.items()
        if audio_position(clock, [{"media_time": p.get("start_time"),
                                  "media_end_time": p.get("end_time")}])["consistent"]
    ]
    hints = set(row.get("target_paragraphs") or ()) & set(ordered)
    if not clock_matches and not hints:
        return None
    scope = set(clock_matches)
    if not scope:
        for key in hints:
            i = ordered.index(key)
            scope.update(ordered[max(0, i - 2):i + 3])
    old_key = row["old_paragraph"]
    quote = literal_layout(row["quote"])[0]
    old_text = old_paragraphs.get(old_key, {}).get("text", "")
    old_context = literal_layout(old_text)[0]

    context_queries = {
        literal_layout(text)[0] for consumer in consumers
        for text in [consumer["statement"], *(c["old_quote"] for c in consumer["citations"] if not c["target"])]
        if text
    }

    def score(key):
        new = literal_layout(new_paragraphs[key]["text"])[0]
        overlap = SequenceMatcher(None, quote, new, autojunk=False).find_longest_match().size
        context = SequenceMatcher(None, old_context, new, autojunk=False).find_longest_match().size
        if len(quote) < 12 and context_queries:
            matches = [SequenceMatcher(None, query, new, autojunk=False).find_longest_match().size
                       for query in context_queries]
            return (key in hints, sum(n for n in matches if n >= 2), overlap, context)
        return (bool(quote and quote in new), key in hints, overlap, context)

    scoped_live = [key for key in ordered if key in scope and new_paragraphs[key]["text"].strip()]
    ranked = sorted(scoped_live, key=score, reverse=True)
    # Keep several ranked anchors before spending the remaining slots on neighbors.
    ordinals = {key: i for i, key in enumerate(ordered)}
    live_positions = {key: i for i, key in enumerate(scoped_live)}
    anchors = ranked[:max(1, limit // 2)]
    shortlist = set(anchors)
    for key in dict.fromkeys([*anchors, *ranked]):
        if len(shortlist) >= limit:
            break
        shortlist.add(key)
        i = live_positions[key]
        for neighbor in scoped_live[max(0, i - 1):i + 2]:
            if len(shortlist) < limit and abs(ordinals[neighbor] - ordinals[key]) <= 2:
                shortlist.add(neighbor)
    if not shortlist:
        return None
    candidates = [
        {"paragraph_key": key, "ordinal": ordinals[key], "text": p["text"],
         "start_time": p.get("start_time"), "end_time": p.get("end_time")}
        for key, p in new_paragraphs.items() if key in shortlist
    ]
    return {"record_key": row["record_key"], "old_quote": row["quote"],
            "old_paragraph": old_text, "old_interval": [clock.get("media_time"),
                                                       clock.get("media_end_time")],
            "candidates": candidates}


# A leading standalone acknowledgment can be dropped from an overlong candidate.
# Require punctuation so prefixes of substantive clauses such as 「對他來說」 stay intact.
LEADING_ACKNOWLEDGMENTS = re.compile(
    r"^(?:(?:yeah|yes|okay|ok|對啊|對呀|對|沒有錯|沒錯)[，、。,.!！]+\s*)+",
    re.IGNORECASE,
)


def narrow_overlong_quote(segments, new, limit, *, old_quote=""):
    """Recover a unique literal suffix without crossing a paragraph or a turn."""
    if len(segments) != 1:
        return None
    segment = segments[0]
    quote = segment["verbatim_excerpt"]
    # Prefer the old substantive quote itself when it survives verbatim except
    # for layout and ASCII letter case. The cut stays inside the selected span.
    needle = literal_layout(old_quote)[0]
    normalized, offsets = literal_layout(quote)
    ascii_lower = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
    hits = positions(normalized.translate(ascii_lower), needle.translate(ascii_lower))
    if 8 <= len(needle) <= limit and len(hits) == 1:
        start = hits[0]
        narrowed = quote[offsets[start]:offsets[start + len(needle) - 1] + 1]
    else:
        narrowed = LEADING_ACKNOWLEDGMENTS.sub("", quote, count=1).lstrip()
    if (narrowed == quote or not narrowed or "**" in narrowed
            or len(literal_layout(narrowed)[0]) > limit):
        return None
    paragraph = new["paragraphs"][segment["paragraph_key"]]["text"]
    if len(positions(paragraph, narrowed)) != 1:
        return None
    from backend.pipeline.transcript_evidence_apply import context_only
    if context_only(narrowed):
        return None
    return [{**segment, "verbatim_excerpt": narrowed}]


def calibration_rules(packets, answer, records, new, clock_proofs):
    """Accept only supplied, consecutive, clock-compatible live paragraphs."""
    from backend.pipeline.transcript_evidence_apply import context_only

    by_key = {p["record_key"]: p for p in packets}
    decisions = answer["decisions"]
    if len(decisions) != len(by_key) or {d["record_key"] for d in decisions} != set(by_key):
        raise ValueError("Model decisions do not cover the exact candidate packet")
    rules, unresolved = {}, {}
    for decision in decisions:
        key = decision["record_key"]
        packet = by_key[key]
        allowed = {p["paragraph_key"]: p for p in packet["candidates"]}
        segments = select_spans(packet, decision, new)
        selected = [segment["paragraph_key"] for segment in segments]
        reason = decision["reason"].strip()
        if not selected:
            unresolved[key] = "no_valid_candidate_selection"
            continue
        ordinals = [allowed[k]["ordinal"] for k in selected]
        repeated = any(
            sum(citation["target"] for citation in consumer["citations"]) > 1
            for consumer in packet.get("consumers", ())
        )
        if repeated:
            if ordinals != sorted(set(ordinals)) or not ordered_repeat_selection(packet, selected, allowed):
                unresolved[key] = "unverified_repeated_occurrences"
                continue
        elif ordinals != list(range(ordinals[0], ordinals[0] + len(ordinals))):
            unresolved[key] = "nonconsecutive_candidate_selection"
            continue
        state = records[key]
        if not context_only(packet["old_quote"]) and all(
            context_only(segment["verbatim_excerpt"]) for segment in segments
        ):
            unresolved[key] = "selection_is_only_conversational_context"
            continue
        old_length = len(literal_layout(packet["old_quote"])[0])
        new_length = sum(len(literal_layout(s["verbatim_excerpt"])[0]) for s in segments)
        limit = max(24, old_length * 2 * (len(segments) if repeated else 1))
        if new_length > limit:
            narrowed = None if repeated else narrow_overlong_quote(segments, new, limit, old_quote=packet["old_quote"])
            if narrowed is None:
                unresolved[key] = "selected_quote_is_too_broad"
                continue
            segments = narrowed
            reason += "；程式縮至新版唯一的實質引文。"

        if repeated and any(
            literal_layout(packet["old_quote"])[0] not in literal_layout(segment["verbatim_excerpt"])[0]
            for segment in segments
        ):
            unresolved[key] = "unverified_repeated_occurrences"
            continue
        clock = dict(zip(("media_time", "media_end_time"), packet["old_interval"]))
        fragment = state["payload"]
        proof = clock_proofs.get(key)
        if proof is not None:
            from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval
            clock = verified_legacy_interval(fragment, proof)
        elif any(clock[k] != fragment.get(k) for k in clock):
            raise ValueError("Packet clock is not bound to the saved fragment")
        intervals = [{"media_time": new["paragraphs"][k].get("start_time"),
                      "media_end_time": new["paragraphs"][k].get("end_time")} for k in selected]
        if not audio_position(clock, intervals)["consistent"]:
            unresolved[key] = "no_verified_clock_match"
            continue
        rule = {"before_pin": [state["revision"], state["content_sha256"]],
                "target_source_sha256": new["body_sha256"], "mode": "rebind",
                "reason": reason, "segments": segments}
        if proof is not None:
            rule["legacy_clock"] = proof
        if repeated:
            rule["consumer_occurrence_segments"] = {
                consumer["record_key"]: list(range(len(selected)))
                for consumer in packet["consumers"]
            }
        rules[key] = rule
    return rules, unresolved


def review_packets(packets):
    """Use the shared Sol capacity limit for a caller-supplied bounded batch."""
    from backend.pipeline.full_video_transcript import cleanup_generate
    from backend.pipeline.slide_pilot_models import VisionReviewer
    from backend.pipeline.slide_visual_evidence import check_schema

    adapter = VisionReviewer("openai", PROMPT, SCHEMA)
    raw = cleanup_generate(adapter, json.dumps(packets, ensure_ascii=False))
    answer = json.loads(adapter.text(raw))
    check_schema(answer, SCHEMA)
    return raw, answer


def prepare_packets(preview, records, old, new, legacy_proof, *, comparison=None):
    """Scope every unresolved fragment from fresh alignment and the old clock."""
    from backend.pipeline.transcript_evidence_calibration import verified_legacy_interval

    fragment_rows = {row["record_key"]: row for row in (comparison or {}).get("fragments", ())}
    resolved = {
        key: row for key, row in fragment_rows.items()
        if row["status"] in {"auto_rebind", "already_current", "relocate_changed_quote"}
    }
    consumer_exceptions = []
    source_order = list(new["paragraphs"])
    source_positions = {key: index for index, key in enumerate(source_order)}
    packets, proofs, unscoped = [], {}, []
    for row in preview["exceptions"]:
        key = row["record_key"]
        payload = records[key]["payload"]
        if not key.startswith("source_fragments/"):
            consumer_exceptions.append({
                **row,
                "statement": payload.get("statement") or payload.get("text") or "",
                "citations": [
                    {"record_key": fragment_key,
                     "old_quote": records[fragment_key]["payload"].get("verbatim_excerpt"),
                     "target_paragraphs": fragment_rows.get(fragment_key, {}).get("target_paragraphs") or [],
                     "bound_paragraph": (fragment_rows.get(fragment_key, {}).get("applied_fields") or {}).get("paragraph_key")}
                    for fid in payload.get("source_fragment_ids") or [payload.get("source_fragment_id")]
                    if (fragment_key := "source_fragments/" + str(fid)) in records
                ],
            })
            continue
        fragment = payload
        clock = fragment
        if fragment.get("media_time") is None or fragment.get("media_end_time") is None:
            try:
                clock = verified_legacy_interval(fragment, legacy_proof)
            except (ValueError, KeyError, TypeError):
                # A known old paragraph can scope the model even if the stored
                # quotation cannot itself prove that clock for a later write.
                paragraph = old["paragraphs"].get(row["old_paragraph"], {})
                witness = {**fragment, "verbatim_excerpt": paragraph.get("text", "")}
                try:
                    scope = verified_legacy_interval(witness, legacy_proof)
                except (ValueError, KeyError, TypeError):
                    scope = fragment
            else:
                scope = clock
                proofs[key] = legacy_proof
        else:
            scope = clock
        consumers = consumer_context(row, records, resolved)
        neighbors = set(row.get("target_paragraphs") or ())
        for consumer in consumers:
            for citation in consumer["citations"]:
                if citation["target"]:
                    continue
                for paragraph in citation["resolved_paragraphs"]:
                    if paragraph in source_positions:
                        i = source_positions[paragraph]
                        neighbors.update(source_order[max(0, i - 1):i + 2])
        packet = candidate_packet(
            {**row, "target_paragraphs": sorted(neighbors)},
            old["paragraphs"], new["paragraphs"], scope, consumers=consumers,
        )
        if packet is None:
            unscoped.append(key)
            continue
        packet["search_interval"] = packet["old_interval"]
        packet["old_interval"] = [clock.get("media_time"), clock.get("media_end_time")]
        packet["consumers"] = consumers
        packets.append(packet)
    return {"packets": packets, "clock_proofs": proofs, "unscoped": unscoped,
            "consumer_exceptions": consumer_exceptions}


def load_run_packets(root, backup):
    """Build a repeatable, read-only handoff from the frozen migration plan."""
    from pathlib import Path

    from backend.pipeline import transcript_claim_migration as migration
    from backend.pipeline import transcript_evidence_apply as evidence
    from backend.pipeline import transcript_locator_migration as locator

    root = root.resolve()
    manifest = locator.load(root)
    plan = migration.read(root / "evidence/plan.json")
    migration.cm.source.verify_file(plan["records"])
    records = migration.read(plan["records"]["path"])
    intent = manifest["intent"]
    if len(intent["old_paths"]) != 1:
        raise ValueError("Candidate preparation requires one explicit legacy source")
    old_path = Path(intent["old_paths"][0])
    old_bound = migration.binding(old_path)
    old = migration.cm.source.source_snapshot(old_bound)
    new = evidence.snap(intent["published"])
    backup = backup.resolve()
    item = migration.read(backup)["intent"]["item"]
    proof = {
        "item": item, "source_file": old_bound,
        "backup_manifest": migration.binding(backup),
        "timeline_file": migration.binding(backup.parent / "production/script" / f"{item}.json"),
    }
    migration.cm.source.verify_file(plan["report"])
    result = prepare_packets(
        migration.read(root / "preview.json"), records, old, new, proof,
        comparison=migration.read(plan["report"]["path"]),
    )
    return manifest, records, new, result


def prepare_run_packets(root, backup, output):
    from backend.pipeline import transcript_claim_migration as migration

    _, _, _, result = load_run_packets(root, backup)
    migration.immutable(output, result)
    return {**{key: len(result[key]) for key in ("packets", "unscoped", "consumer_exceptions")},
            "output": str(output.resolve())}


def main():
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locator-run", type=Path, required=True)
    parser.add_argument("--backup-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare_run_packets(args.locator_run, args.backup_manifest, args.output)))


if __name__ == "__main__":
    main()
