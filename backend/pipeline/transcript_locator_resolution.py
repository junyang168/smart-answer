"""Resolve scoped transcript exceptions with durable, reusable model decisions."""

from backend.pipeline import transcript_claim_migration as migration
from backend.pipeline import transcript_locator_candidates as candidates
from backend.pipeline.transcript_evidence_apply import context_only
from backend.pipeline.transcript_evidence_calibration import audio_position
from backend.pipeline.transcript_evidence_migration import literal_layout, positions


def consumer_packets(bundle, records, new):
    packets = []
    paragraphs = new["paragraphs"]
    for consumer in bundle["consumer_exceptions"]:
        key = consumer["record_key"]
        clocks = [
            records["source_fragments/" + fid]["payload"]
            for fid in consumer["context_fragment_ids"]
        ]
        if not clocks or not consumer["statement"].strip():
            continue
        hints = {pk for citation in consumer["citations"] for pk in citation["target_paragraphs"]}
        clock = clocks[0]
        packet = candidates.candidate_packet(
            {"record_key": key, "quote": consumer["statement"],
             "old_paragraph": clock.get("paragraph_key"), "target_paragraphs": sorted(hints)},
            {}, paragraphs, clock,
        )
        if packet:
            packet.update(consumer=consumer, old_paragraph=consumer["statement"])
            packets.append(packet)
    return packets


def consumer_rule(packet, selection, records, new):
    """Resolve a consumer only with a clock-bound literal witness for its prompt."""
    segments = candidates.select_spans(packet, selection, new)
    if len(segments) != 1 or context_only(segments[0]["verbatim_excerpt"]):
        return None
    key = segments[0]["paragraph_key"]
    paragraph = new["paragraphs"][key]
    reason = selection["reason"].strip()

    consumer = packet["consumer"]
    source_sha = new["body_sha256"]
    order = list(new["paragraphs"])
    center = order.index(key)
    nearby = order[max(0, center - 2):center + 3]
    parent_rules = {}
    for fid in consumer["context_fragment_ids"]:
        fragment_key = "source_fragments/" + fid
        state = records[fragment_key]
        fragment = state["payload"]
        target_clock = [{"media_time": paragraph.get("start_time"),
                         "media_end_time": paragraph.get("end_time")}]
        if not audio_position(fragment, target_clock)["consistent"]:
            continue
        citations = [c for c in consumer["citations"] if c["record_key"] == fragment_key]
        bound_keys = {c.get("bound_paragraph") for c in citations}
        if bound_keys & set(nearby):
            break
        needle = literal_layout(fragment.get("verbatim_excerpt", ""))[0]
        witnesses = []
        if not needle:
            continue
        for pk in nearby:
            source = new["paragraphs"][pk]
            body = source["text"]
            normalized, offsets = literal_layout(body)
            interval = [{"media_time": source.get("start_time"), "media_end_time": source.get("end_time")}]
            if not audio_position(fragment, interval)["consistent"]:
                continue
            for pos in positions(normalized, needle):
                quote = body[offsets[pos]:offsets[pos + len(needle) - 1] + 1]
                if body.count(quote) == 1 and "~~" not in quote:
                    witnesses.append({"paragraph_key": pk, "verbatim_excerpt": quote})
        if len(witnesses) == 1:
            parent_rules[fragment_key] = {
                "before_pin": [state["revision"], state["content_sha256"]],
                "target_source_sha256": source_sha, "mode": "rebind",
                "reason": "唯一且符合舊音訊區間的口頭提示，只保留語境。",
                "segments": witnesses,
            }
            break
    else:
        return None

    state = records[packet["record_key"]]
    rule = {"before_pin": [state["revision"], state["content_sha256"]],
            "target_source_sha256": source_sha, "mode": "rebind",
            "reason": reason, "segments": segments}
    return rule, parent_rules


def resolve_packets(root, bundle, records, new, prior, *, call=None):
    """Run bounded batches, replay saved replies and emit a calibration input."""
    from backend.pipeline.transcript_locator_migration import run_lock

    call = call or candidates.review_packets
    fragment_rules = dict(prior.get("fragments", {}))
    consumer_rules = dict(prior.get("consumers", {}))
    unresolved = {key: "no_scoped_candidate" for key in bundle["unscoped"]}
    consumers = consumer_packets(bundle, records, new)
    packets = bundle["packets"] + consumers
    available = {p["record_key"] for p in consumers}
    for row in bundle["consumer_exceptions"]:
        if row["record_key"] not in available:
            unresolved[row["record_key"]] = "no_scoped_consumer_candidate"

    with run_lock(root) as root:
        for offset in range(0, len(packets), 6):
            batch = packets[offset:offset + 6]
            folder = root / f"batch-{offset // 6 + 1:03d}"
            request = migration.immutable(folder / "request.json", {
                "model": "gpt-6.1-sol", "prompt": candidates.PROMPT,
                "schema": candidates.SCHEMA, "packets": batch,
            })
            reply = folder / "response.json"
            if not reply.exists():
                started = folder / "started.json"
                if started.exists():
                    raise ValueError(f"Locator request has no saved response: {started}")
                migration.immutable(started, {"request": request})
                raw, answer = call(batch)
                migration.immutable(reply, {"raw": raw, "answer": answer})
            answer = migration.read(reply)["answer"]
            from backend.pipeline.slide_visual_evidence import check_schema
            check_schema(answer, candidates.SCHEMA)
            decisions = answer["decisions"]
            keys = {p["record_key"] for p in batch}
            if len(decisions) != len(batch) or {d["record_key"] for d in decisions} != keys:
                raise ValueError("Locator reply does not cover the exact packet")
            by_key = {d["record_key"]: d for d in decisions}
            fragment_batch = [p for p in batch if "consumer" not in p]
            rules, pending = candidates.calibration_rules(
                fragment_batch, {"decisions": [by_key[p["record_key"]] for p in fragment_batch]},
                records, new, bundle["clock_proofs"],
            )
            fragment_rules.update(rules)
            unresolved.update(pending)
            for packet in batch:
                if "consumer" not in packet:
                    continue
                key = packet["record_key"]
                result = consumer_rule(packet, by_key[key], records, new)
                if not result:
                    unresolved[key] = "no_verified_substantive_consumer_location"
                    continue
                rule, parents = result
                if any(k in fragment_rules and fragment_rules[k]["segments"] != v["segments"]
                       for k, v in parents.items()):
                    unresolved[key] = "conflicting_context_location"
                    continue
                fragment_rules.update(parents)
                consumer_rules[key] = rule
        calibration = root / "calibration.json"
        migration.immutable(calibration, {
            "schema_version": "wang_transcript_locator_calibration_v1",
            "technical_locator_only": True,
            "fragments": fragment_rules, "consumers": consumer_rules,
        })
        migration.immutable(root / "unresolved.json", unresolved)
    return calibration
