"""Check every point against the first matching sentence on the same slide."""

import json
import re


_VERSE_QUOTE = re.compile(
    r"(?P<reference>(?:第?[一二三四五六七八九十百零兩两\d]+章[一二三四五六七八九十百零兩两\d]+[節节]|\d+[:：]\d+))"
    r"\s*(?:末尾|開頭|結尾)?的?\s*「(?P<quote>[^「」]+)」[。.]?"
)


_CRITERIA = {
    "full": "這一句實際表達投影片要點的完整意思，保留範圍、主詞、否定和限定。",
    "partial": "只說出部分，或範圍、主詞、否定或限定有差異。",
    "question": "只提出相關問題，尚未表達這個陳述。",
    "none": "該句沒有實際表達要點。",
    "uncertain": "不能確定。",
}


def _match_question(meaning, sid, sentence):
    if meaning.endswith(("？", "?")):
        return {"type": "choice",
                "instructions": f"原文 {sid} 是否提出同一個問題「{meaning}」？"
                                "只判這一句正在問什麼，忽略問題的答案和後面的講解。",
                "criteria": {"full": "這句提出同一個問題，可以用前文消解「那裡」「這段」等指代。",
                             "none": "這句沒有問同一個問題。只談相同主題、回答或回顧也不算。",
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
            "instructions": f"{sid}「{sentence}」是否說出「{meaning}」？"
                            "前文可補主詞、指代和已說明的章節範圍。摘要可省略附帶細節。提問不算回答。"
                            "只說有關，未表明是結論，不算完整。不可用後文補答案。"}


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


def evaluate_points(packet, candidates, ask, *, model, max_bytes):
    slides = {slide["first"]: slide for slide in packet["slides"]}
    lines = {line["id"]: line for line in packet["source"]}
    decompositions = {}
    for candidate in candidates:
        first, sentence = candidate["slide"], candidate["sentence"]
        points = [p for p in slides.get(first, {}).get("points", []) if p["sentence"] == sentence]
        if (first, sentence) in decompositions or len(points) != 1:
            raise ValueError("Split candidate must identify one unique displayed point")
        parts = [part.strip() for part in candidate["parts"]]
        if not 2 <= len(parts) <= 4 or not all(parts) or len(set(parts)) != len(parts):
            raise ValueError("Split candidate must contain two to four distinct meanings")
        decompositions[first, sentence] = parts

    results = []
    for first, slide in slides.items():
        if not slide["points"]:
            continue
        source = {sid: line["text"] for sid, line in lines.items() if first <= sid <= slide["last"]}
        if not source:
            raise ValueError("A displayed slide has no timing source")
        questions = {}
        exact = {}

        def match_sentences(key, meaning):
            target = meaning.strip().rstrip("。．.!！")
            for sid, sentence in source.items():
                name = f"{key}:{sid}"
                # Compare whole sentences. A quotation or substring can occur in
                # a denial or question. Only omit later matches once we have proof.
                if target and sentence.strip().rstrip("。．.!！") == target:
                    exact[name] = "full"
                    break
                questions[name] = _match_question(meaning, sid, sentence)

        for index, point in enumerate(slide["points"]):
            match_sentences(f"{index}:whole", point["text"])
            if parts := decompositions.get((first, point["sentence"])):
                questions[f"{index}:split"] = {
                    "type": "choice",
                    "instructions": f'這些投影片短句 {json.dumps(parts, ensure_ascii=False)} '
                                    "各自有沒有陳述一個不同的意思或提出一個完整問題？只核對各短句本身的獨立性。",
                    "criteria": {"independent": "每條都各自陳述不同的意思或提出完整問題。",
                                 "dependent": "至少一條只是名詞、所指對象、必要條件或重述另一條。",
                                 "uncertain": "不能確定。"},
                }
                for part_index, part in enumerate(parts):
                    match_sentences(f"{index}:{part_index}", part)

        answers = _ask_batches({"原文": source}, questions, ask, model, max_bytes) if questions else {}
        answers.update(exact)

        def earliest(index, part):
            return next((sid for sid in source if answers.get(f"{index}:{part}:{sid}") == "full"), None)

        for index, point in enumerate(slide["points"]):
            current, text = point["sentence"], point["text"]
            item = {"slide": first, "point": index, "sentence": current}
            parts = decompositions.get((first, current))
            if parts and answers[f"{index}:split"] == "independent":
                anchors = [earliest(index, number) for number in range(len(parts))]
                if all(anchors) and len({lines[sid]["start"] for sid in anchors}) > 1:
                    split = sorted(zip(anchors, parts), key=lambda entry: lines[entry[0]]["start"])
                    evidence = "；".join(f"{sid}「{part}」（原文「{source[sid]}」）" for sid, part in split)
                    results.append({**item, "status": "split", "evidence":
                                    f"要點 {current} 合併了不同時點講出的意思，應依原聲分開顯示。{evidence}"})
                    continue

            expected = earliest(index, "whole")
            if current not in source or expected is None:
                evidence = f"{current}「{text}」在本張找不到可確認的完整原句，須核對要點或分張。"
                results.append({**item, "status": "unresolved", "evidence": evidence})
                continue
            delta = round(lines[current]["start"] - lines[expected]["start"], 2)
            status = "correct" if current == expected or not delta else "early" if delta < 0 else "late"
            evidence = (f"{current}「{text}」首次完整對應 {expected}「{source[expected]}」。"
                        if status == "correct" else
                        f"{current}「{text}」出現{'太早' if delta < 0 else '太晚'} {abs(delta):g} 秒，"
                        f"應綁 {expected}「{source[expected]}」。")
            results.append({**item, "status": status, "expected_sentence": expected,
                            "delta_seconds": delta, "evidence": evidence})
    return results
