"""Check whether a displayed point follows the discussion on its slide."""

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
                "instructions": f"原文 {sid} 是否正在提出同一個問題「{meaning}」？"
                                "用本張相鄰原文理解分幾句提出的問句。明確開始發問後，"
                                "若緊接的同一輪發言就是該問題，開頭可算。忽略回答和後續講解。",
                "criteria": {"full": "這句提出或明確開始同一個問題，可用相鄰句補足指代及分段問句。",
                             "none": "這句沒有提出同一個問題。過場、另一個問題、回答和回顧均不算。",
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


def evaluate_points(packet, ask, *, model, max_bytes):
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
