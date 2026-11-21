"""Rebuild filtered refinement windows from verified saved short-window replies."""

from copy import deepcopy
from difflib import SequenceMatcher

from opencc import OpenCC

from backend.pipeline.slide_change_pilot import file_sha, read_json
from backend.pipeline.slide_visual_evidence import check_schema
from backend.pipeline.transcript_gemini_audio import FLASH_SCHEMA, refinement_turns


def recover(refined, transcription, short_windows, corrections, prior_layout):
    refined = deepcopy(refined)
    report = read_json(short_windows / "summary.json")
    corrections = read_json(corrections)
    if file_sha(prior_layout) != corrections["layout_sha256"]:
        raise ValueError("GPT corrections do not match the prior layout")
    layout = {p["index"]: p for p in read_json(prior_layout)["value"]["paragraphs"]}
    filtered = {f'w{w["window"]}' for w in refined["unrefined_windows"]}
    originals = {t["id"]: t for t in refined["base_turns"]}
    output, bases, decisions, pending = [], [], [], []
    cc = OpenCC("s2t")

    for row in report:
        start, end = row["start"], row["end"]
        stem = f"{start}-{end}"
        request = read_json(short_windows / f"{stem}.request.json")["input"]
        words = [w for w in transcription["words"] if start <= (w["start"] + w["end"]) / 2 < end]
        if (request["start"], request["end"]) != (start, end) or refinement_turns(words) != request["speaker_turns"]:
            raise ValueError(f"Short window does not match frozen transcription: {stem}")
        if not {t["speaker"].split(":")[0] for t in request["speaker_turns"]} <= filtered:
            raise ValueError(f"Short window overlaps a previously refined source window: {stem}")

        turns = deepcopy(request["speaker_turns"])
        original = {t["id"]: t.copy() for t in turns}
        by_id = {t["id"]: t for t in turns}
        if row["result"] == "STOP":
            answer = read_json(short_windows / f"{stem}.edits.json")
            check_schema(answer, FLASH_SCHEMA)
            ids = [edit["id"] for edit in answer["edits"]]
            if len(ids) != len(set(ids)) or not set(ids) <= by_id.keys():
                raise ValueError("Short Flash response targets an invalid turn")
            for edit in answer["edits"]:
                text = cc.convert(edit["text"]).strip()
                if not text:
                    raise ValueError("Short Flash response erases a turn")
                by_id[edit["id"]]["text"] = text
            refined["omissions_for_review"].extend(answer["omissions"])
        else:
            pending.append({"start": start, "end": end, "reason": row["result"]})
            proposal, = [w for w in corrections["windows"] if (w["start"], w["end"]) == (start, end)]
            for edit in proposal["edits"]:
                prior = layout[edit["index"]]["text"]
                before, after = edit["before"], edit["after"]
                at = edit["position"]
                if prior[at:at + len(before)] != before or not before or not after:
                    raise ValueError("GPT correction differs from its frozen excerpt")
                old = [t for t in turns if t["text"].count(before) == 1]
                # Punctuation already added in the prior layout can make a long
                # proposal differ from the source. Reuse only its literal changed span.
                if not old:
                    matches = [op for op in SequenceMatcher(None, before, after, autojunk=False).get_opcodes()
                               if op[0] != "equal"]
                    if len(matches) == 1:
                        _, a, b, c, d = matches[0]
                        left, right = max(0, a - 5), min(len(before), b + 5)
                        needle = before[left:right]
                        old = [t for t in turns if t["text"].count(needle) == 1]
                        before, after = needle, before[left:a] + after[c:d] + before[b:right]
                if len(old) == 1:
                    old[0]["text"] = old[0]["text"].replace(before, after, 1)
                    decisions.append({**edit, "applied": True})
                else:
                    decisions.append({**edit, "applied": False})
        for turn in turns:
            old = original[turn["id"]]
            key = f'recovered-{start}-{turn["id"]}'
            output.append({**turn, "id": key})
            bases.append({**old, "id": key})
    retained = [t for t in refined["turns"] if t["speaker"].split(":")[0] not in filtered]
    retained_base = [originals[t["id"]] for t in retained]
    # Split windows must account for every original filtered ASR word.
    expected = [w for w in transcription["words"] if w["speaker"].split(":")[0] in filtered]
    for word in expected:
        clock = (word["start"] + word["end"]) / 2
        if sum(w["start"] <= clock < w["end"] for w in report) != 1:
            raise ValueError("Short-window recovery does not cover filtered ASR exactly once")
    refined.update(turns=sorted(retained + output, key=lambda t: t["start"]),
                   base_turns=sorted(retained_base + bases, key=lambda t: t["start"]),
                   unrefined_windows=pending, recovery_decisions=decisions)
    return refined
