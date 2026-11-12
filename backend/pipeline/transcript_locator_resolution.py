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
    if not segments or any(context_only(s["verbatim_excerpt"]) for s in segments):
        return None
    order = list(new["paragraphs"])
    selected = [order.index(s["paragraph_key"]) for s in segments]
    if selected != list(range(selected[0], selected[0] + len(selected))):
        return None
    target_clock = [
        {"media_time": new["paragraphs"][s["paragraph_key"]].get("start_time"),
         "media_end_time": new["paragraphs"][s["paragraph_key"]].get("end_time")}
        for s in segments
    ]
    reason = selection["reason"].strip()

    consumer = packet["consumer"]
    source_sha = new["body_sha256"]
    # Each selected passage must be within two paragraphs of the same witness.
    nearby = order[max(0, selected[-1] - 2):selected[0] + 3]
    parent_rules = {}
    for fid in consumer["context_fragment_ids"]:
        fragment_key = "source_fragments/" + fid
        state = records[fragment_key]
        fragment = state["payload"]
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


def review_batch(folder, batch, call, *, prompt=candidates.PROMPT, schema=candidates.SCHEMA):
    request = migration.immutable(folder / "request.json", {
        "model": "gpt-6.1-sol", "prompt": prompt,
        "schema": schema, "packets": batch,
    })
    reply = folder / "response.json"
    if not reply.exists():
        started = folder / "started.json"
        if started.exists():
            raise ValueError(f"Locator request has no saved response: {started}")
        migration.immutable(started, {"request": request})
        from backend.pipeline.media_batch.runtime import WaitingCapacity
        try:
            raw, answer = call(batch)
        except WaitingCapacity:
            # The capacity check runs before the provider call. Save the refusal
            # and leave this exact request eligible to resume.
            migration.immutable(folder / "capacity-wait.json", {"request": request, "provider_call_sent": False})
            started.unlink()
            raise
        migration.immutable(reply, {"raw": raw, "answer": answer})
    answer = migration.read(reply)["answer"]
    from backend.pipeline.slide_visual_evidence import check_schema
    check_schema(answer, schema)
    decisions = answer["decisions"]
    keys = {p["record_key"] for p in batch}
    if len(decisions) != len(batch) or {d["record_key"] for d in decisions} != keys:
        raise ValueError("Locator reply does not cover the exact packet")
    return {d["record_key"]: d for d in decisions}


def refine_boundaries(folder, packets, decisions, pending, records, new, clock_proofs, call):
    """Request smaller spans only within the previously selected live text."""
    windows = {
        packet["record_key"]: candidates.select_spans(packet, decisions[packet["record_key"]], new)
        for packet in packets if pending.get(packet["record_key"]) == "selected_quote_is_too_broad"
    }
    if not windows:
        return {}
    focused = []
    for packet in packets:
        key = packet["record_key"]
        if key not in windows:
            continue
        spans = windows[key]
        selected = {segment["paragraph_key"] for segment in spans}
        focused.append({
            **packet,
            "candidates": [p for p in packet["candidates"] if p["paragraph_key"] in selected],
            "boundary_review": {
                "instruction": "只縮短已定位的引文。限定在下列逐字範圍，只選同一次發言的必要文字；"
                               "保留原引用的講者、問題或回答。新版沒有該發言時回空。",
                "previous_spans": spans,
            },
        })
    replies = review_batch(folder, focused, call)
    valid_packets, valid_decisions = [], []
    for packet in focused:
        key = packet["record_key"]
        reply = replies[key]
        spans = candidates.select_spans(packet, reply, new)
        if spans and all(any(s["paragraph_key"] == prior["paragraph_key"]
                            and s["verbatim_excerpt"] in prior["verbatim_excerpt"]
                            for prior in windows[key]) for s in spans):
            valid_packets.append(packet)
            valid_decisions.append(reply)
    rules, _ = candidates.calibration_rules(
        valid_packets, {"decisions": valid_decisions}, records, new, clock_proofs,
    )
    return rules


def question_context_rule(packet, decision, records, new):
    """Keep the old quotation and capture a separate, checked playback context."""
    key = packet["record_key"]
    if key.startswith("questions/"):
        ids = packet["consumer"]["context_fragment_ids"]
        if len(ids) != 1:
            return None
        key = "source_fragments/" + ids[0]
    elif (not packet.get("consumers")
          or any(not x["record_key"].startswith("questions/") for x in packet["consumers"])):
        return None
    state = records[key]
    spans = candidates.select_spans(packet, decision, new)
    if not spans or any(context_only(s["verbatim_excerpt"]) for s in spans):
        return None
    order = list(new["paragraphs"])
    selected = [order.index(s["paragraph_key"]) for s in spans]
    if selected != list(range(selected[0], selected[0] + len(selected))):
        return None
    intervals = [{"media_time": new["paragraphs"][s["paragraph_key"]]["start_time"],
                  "media_end_time": new["paragraphs"][s["paragraph_key"]]["end_time"]}
                 for s in spans]
    if not audio_position(state["payload"], intervals)["consistent"]:
        return None
    return key, {
        "before_pin": [state["revision"], state["content_sha256"]],
        "target_source_sha256": new["body_sha256"], "mode": "question_context_only",
        "segments": spans, "reason": decision["reason"].strip(),
    }


def resolve_packets(root, bundle, records, new, prior, *, call=None, statement_first=False):
    """Run bounded batches, replay saved replies and emit a calibration input."""
    from backend.pipeline.transcript_locator_migration import run_lock

    legacy_call = call or candidates.review_packets
    prompt = candidates.STATEMENT_PROMPT if statement_first else candidates.PROMPT
    schema = candidates.STATEMENT_SCHEMA if statement_first else candidates.SCHEMA
    if call is None:
        def call(batch):
            return candidates.review_packets(batch, prompt=prompt, schema=schema)
    fragment_rules = dict(prior.get("fragments", {}))
    consumer_rules = dict(prior.get("consumers", {}))
    unresolved = {key: "no_scoped_candidate" for key in bundle["unscoped"]}
    consumers = consumer_packets(bundle, records, new)
    packets = [p for p in bundle["packets"] + consumers if p["record_key"] not in fragment_rules]
    available = {p["record_key"] for p in consumers}
    for row in bundle["consumer_exceptions"]:
        if row["record_key"] not in available:
            unresolved[row["record_key"]] = "no_scoped_consumer_candidate"

    with run_lock(root) as root:
        for offset in range(0, len(packets), 6):
            batch = packets[offset:offset + 6]
            folder = root / f"batch-{offset // 6 + 1:03d}"
            by_key = review_batch(folder, batch, call, prompt=prompt, schema=schema)
            if statement_first:
                actionable = []
                for packet in batch:
                    key = packet["record_key"]
                    decision = by_key[key]
                    disposition = decision["disposition"]
                    if disposition == "verbatim_support":
                        actionable.append(packet)
                        continue
                    if disposition == "question_context_only":
                        result = question_context_rule(packet, decision, records, new)
                        if result:
                            parent, rule = result
                            if parent not in fragment_rules:
                                fragment_rules[parent] = rule
                                continue
                    unresolved[key] = disposition
                batch = actionable
            fragment_batch = [p for p in batch if "consumer" not in p]
            rules, pending = candidates.calibration_rules(
                fragment_batch, {"decisions": [by_key[p["record_key"]] for p in fragment_batch]},
                records, new, bundle["clock_proofs"],
            )
            narrowed = refine_boundaries(folder / "boundary-review", fragment_batch, by_key,
                                         pending, records, new, bundle["clock_proofs"], legacy_call)
            fragment_rules.update(rules)
            fragment_rules.update(narrowed)
            unresolved.update({k: v for k, v in pending.items() if k not in narrowed})
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
