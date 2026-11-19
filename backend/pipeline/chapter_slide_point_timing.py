"""Check whether a displayed point follows the discussion on its slide."""

import hashlib
import json
import re


_VERSE_QUOTE = re.compile(
    r"(?P<reference>(?:第?[一二三四五六七八九十百零兩两\d]+章[一二三四五六七八九十百零兩两\d]+[節节]|\d+[:：]\d+))"
    r"\s*(?:末尾|開頭|結尾)?的?\s*「(?P<quote>[^「」]+)」[。.]?"
)


_CRITERIA = {
    "full": "指定句正在提出、講解或總結這個要點。完整答案可在同張後文說出。",
    "partial": "指定句只順帶提及相同詞語、經節或相近的主題。",
    "none": "指定句在講另一件事。",
    "uncertain": "不能確定。",
}


def _match_question(meaning, sid, sentence):
    if meaning.endswith(("？", "?")):
        return {"type": "choice",
                "instructions": f"原文 {sid}「{sentence}」是否已開始談這個具體問題「{meaning}」？"
                                "投影片可以用問句帶領聽眾。教授開始讀直接相關的經文、提出問題，"
                                "或開始處理這個問題，均可作為起點。可用同一輪發言的相鄰句補足。"
                                "若後一句已轉入另一個具體要點，按前一句實際開始談的內容判斷。",
                "criteria": {"full": "這句已帶入或正在談同一個具體問題。",
                             "none": "只有泛泛過場、順帶提及，或已轉到另一個具體問題。",
                             "uncertain": "不能確定。"}}
    if verse_quote := _VERSE_QUOTE.fullmatch(meaning):
        reference, quote = (verse_quote[name] for name in ("reference", "quote"))
        return {
            "type": "choice",
            "instructions": f"{sid} 是否把「{quote}」歸給{reference}，符合要點「{meaning}」？"
                            "相同詞句出現在別節不算。",
            "criteria": {
                "full": "本句將這些話引作指定經節的內容。",
                "none": "本句沒有這樣引用，或這些話歸於另一節。",
                "uncertain": "現有語境無法確認出處。",
            },
        }
    return {"type": "choice", "criteria": _CRITERIA,
            "instructions": f"{sid}「{sentence}」是否正在談「{meaning}」所對應的同一個具體問題或意思？"
                            "用同張完整原文確認要點有支持。答案完整且屬於這個問題，就可以在開始談它時先顯示。"
                            "相關問題的開場、講解和總結均可。泛泛過場、同一大主題或相同經節本身不夠。"}


def _ask_batches(state, questions, ask, model, max_bytes):
    # For every batch share precisely this slide's source. Never drop context for
    # pronouns or borrow source from the next slide.
    overhead = len(json.dumps({"model": model, "state": state, "questions": {}},
                              ensure_ascii=False).encode())
    batches, batch = [], {}
    used = overhead
    for key, question in questions.items():
        size = len(json.dumps({key: question}, ensure_ascii=False).encode())
        if overhead + size > max_bytes:
            raise ValueError("A slide source and one timing question exceed Jev's request size")
        if used + size > max_bytes:
            batches.append(batch)
            batch, used = {}, overhead
        batch[key] = question
        used += size
    answers = {}
    for batch in [*batches, batch]:
        answers.update(ask(state, batch))
    return answers


def evaluate_points(packet, ask, *, model, max_bytes, locks=None):
    locks = locks or {}
    slides = {slide["first"]: slide for slide in packet["slides"]}
    lines = {line["id"]: line for line in packet["source"]}
    results = []
    for first, slide in slides.items():
        if not slide["points"]:
            continue
        source = {sid: line["text"] for sid, line in lines.items() if first <= sid <= slide["last"]}
        if not source:
            raise ValueError("A displayed slide has no timing source")
        questions = {}
        exact = {}

        for index, point in enumerate(slide["points"]):
            if (first, index) in locks:
                continue
            meaning = point["text"]
            target = meaning.strip().rstrip("。．.!！")
            current = point["sentence"]
            if target and source.get(current, "").strip().rstrip("。．.!！") == target:
                exact[f"{index}:whole:{current}"] = "full"
                continue
            for sid, sentence in source.items():
                name = f"{index}:whole:{sid}"
                if target and sentence.strip().rstrip("。．.!！") == target:
                    exact[name] = "full"
                else:
                    questions[name] = _match_question(meaning, sid, sentence)

        answers = _ask_batches({"原文": source}, questions, ask, model, max_bytes) if questions else {}
        answers.update(exact)

        for index, point in enumerate(slide["points"]):
            current, text = point["sentence"], point["text"]
            item = {"slide": first, "point": index, "sentence": current}
            if lock := locks.get((first, index)):
                results.append({**item, "status": "correct", "expected_sentence": current,
                                "delta_seconds": 0.0, "evidence": lock["evidence"]})
                continue
            supported = [sid for sid in source if answers.get(f"{index}:whole:{sid}") == "full"]
            expected = (min(supported, key=lambda sid: abs(lines[sid]["start"] - lines[current]["start"]))
                        if supported and current in source else None)
            if current not in source or expected is None:
                reason = "要點綁定的句子不在本張" if current not in source else "Jev 未確認本張原文從哪句開始講這個要點"
                evidence = f"{current}「{text}」：{reason}，須核對。"
                results.append({**item, "status": "unresolved", "evidence": evidence})
                continue
            delta = round(lines[current]["start"] - lines[expected]["start"], 2)
            status = "correct" if current == expected or not delta else "early" if delta < 0 else "late"
            evidence = (f"{current}「{text}」{expected} 正在談這個要點「{source[expected]}」。"
                        if status == "correct" else
                        f"{current}「{text}」出現{'太早' if delta < 0 else '太晚'} {abs(delta):g} 秒，"
                        f"應綁 {expected}「{source[expected]}」。")
            results.append({**item, "status": status, "expected_sentence": expected,
                            "delta_seconds": delta, "evidence": evidence})
    return results


def timing_adjustments(slides, source, results):
    """Keep only Jev-backed moves inside the original slide with distinct destinations."""
    expected = {(slide["first"], i): point["sentence"]
                for slide in slides for i, point in enumerate(slide.get("points", []))}
    actual = {(row["slide"], row["point"]): row["sentence"] for row in results}
    if len(actual) != len(results) or actual != expected:
        raise ValueError("Point timing results do not cover the original points")
    moves = {(row["slide"], row["point"]): row["expected_sentence"]
             for row in results if row["status"] in {"early", "late"}}
    by_id = {line["id"]: i for i, line in enumerate(source)}
    adjustments = []
    for slide in slides:
        first = slide["first"]
        points = slide.get("points", [])
        final = [moves.get((first, i), point["sentence"]) for i, point in enumerate(points)]
        if final == [point["sentence"] for point in points]:
            continue
        if len(set(final)) != len(final) or any(
            sid not in by_id or not by_id[first] <= by_id[sid] <= by_id[slide["last"]] for sid in final
        ):
            continue
        adjustments.extend(
            {"slide": first, "point": i, "from": point["sentence"], "to": sid}
            for i, (point, sid) in enumerate(zip(points, final, strict=True))
            if point["sentence"] != sid
        )
    return adjustments


def apply_adjustments(slides, source, adjustments):
    """Project saved timing only, without modifying the author's text or anchors."""
    if not adjustments:
        return slides
    anchors = {(row["slide"], row["point"]): row for row in adjustments}
    if len(anchors) != len(adjustments):
        raise ValueError("Duplicate point timing adjustment")
    order = {line["id"]: i for i, line in enumerate(source)}
    output = []
    for slide in slides:
        points = []
        changed = False
        for i, point in enumerate(slide.get("points", [])):
            row = anchors.pop((slide["first"], i), None)
            if row is not None:
                sid = row["to"]
                if (row["from"] != point["sentence"] or sid not in order
                        or not order[slide["first"]] <= order[sid] <= order[slide["last"]]):
                    raise ValueError("Point timing adjustment does not match its original slide")
                point = {**point, "sentence": sid}
                changed = True
            points.append(point)
        if changed:
            if len({point["sentence"] for point in points}) != len(points):
                raise ValueError("Point timing adjustment causes a sentence collision")
            output.append({**slide, "points": sorted(points, key=lambda point: order[point["sentence"]])})
        else:
            output.append(slide)
    if anchors:
        raise ValueError("Point timing adjustment does not match an original point")
    return output


PLAN_KEY = "point_timing_plan"


def owner_locks(record, source):
    from backend.pipeline.chapter_slide_cases import cases_for, resolve_cases

    locks = {}
    for case in resolve_cases(cases_for(record["lecture"]), source, partial=True):
        if case["kind"] != "point" or not case.get("source_text"):
            continue
        for slide in record["slides"]:
            for i, point in enumerate(slide["points"]):
                matches = (point["text"] == case["point_text"] if "point_text" in case
                           else re.search(case["pattern"], point["text"]))
                if matches and point["sentence"] == case["at"]:
                    key = (slide["first"], i)
                    locks[key] = {"slide": slide["first"], "point": i, "sentence": case["at"],
                                  "evidence": f"已確認的時間 {case['at']}：{case['why']}"}
    return locks


def make_plan(record, source, points, model):
    from backend.pipeline.chapter_slide_cases import cases_for, check_cases

    slides = record["slides"]
    cases = cases_for(record["lecture"])
    locks = owner_locks(record, source)
    effective = [row if (row["slide"], row["point"]) not in locks else {**row, "status": "correct"}
                 for row in points]
    proposed = timing_adjustments(slides, source, effective)
    approved = []
    existing = set(check_cases(slides, cases, source, partial=True))
    for slide in slides:
        moves = [row for row in proposed if row["slide"] == slide["first"]]
        if moves and not set(check_cases(apply_adjustments(slides, source, approved + moves),
                                         cases, source, partial=True)) - existing:
            approved.extend(moves)
    return {"model": model, "points": points, "adjustments": approved, "owner_locks": list(locks.values())}


def verified_plan(record, source):
    plan = record.get(PLAN_KEY)
    if not isinstance(plan, dict) or not plan.get("model"):
        raise ValueError("Prepare and freeze point timing before semantic Eval")
    if (plan != make_plan(record, source, plan["points"], plan["model"])
            or record.get("point_timing_adjustments") != plan["adjustments"]):
        raise ValueError("Frozen point timing does not match its recorded evidence")
    return plan


def prepare_record(slide_path, selection_path, artifacts):
    """Set the playback timing once, before sending the candidate for review."""
    from backend.pipeline import chapter_slides, claim_flash_common, evidence_refresh

    original = slide_path.read_bytes()
    record = json.loads(original)
    selection = selection_path.read_bytes()
    selection_sha = hashlib.sha256(selection).hexdigest()
    if record["decisions_sha256"] != selection_sha:
        raise ValueError("Selection does not match this slide candidate")
    if (record.get("claim_context") or {}).get("scope") != "subunit":
        raise ValueError("Point timing preparation requires a subunit")
    source = chapter_slides.sentences(chapter_slides.kept_pieces(json.loads(selection)))
    if PLAN_KEY in record:
        return verified_plan(record, source)
    points = evaluate_points({"source": source, "slides": record["slides"]},
        lambda state, questions: claim_flash_common.jev(artifacts, evidence_refresh.JevPort(), state, questions),
        model=evidence_refresh.MODEL, max_bytes=claim_flash_common.JEV_BYTES, locks=owner_locks(record, source))
    plan = make_plan(record, source, points, evidence_refresh.MODEL)
    if slide_path.read_bytes() != original or selection_path.read_bytes() != selection:
        raise ValueError("Slides or source changed during point timing")
    record.update({PLAN_KEY: plan, "point_timing_adjustments": plan["adjustments"]})
    temp = slide_path.with_suffix(".point-timing.tmp")
    temp.write_text(json.dumps(record, ensure_ascii=False, indent=1))
    temp.replace(slide_path)
    return plan


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Prepare subunit playback timing before Eval")
    parser.add_argument("slides", type=Path)
    parser.add_argument("selection", type=Path)
    parser.add_argument("--artifacts", required=True, type=Path)
    args = parser.parse_args()
    prepare_record(args.slides, args.selection, args.artifacts)
