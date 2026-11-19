"""Resolve the first source sentence for each meaning of a suspected merged point."""

import json


_CRITERIA = {
    "full": "這一句說出了完整意思，範圍、主詞、否定和限定均相符。",
    "partial": "只說出部分，或範圍、主詞、否定或限定有差異。",
    "question": "只提出疑問，尚未給出這個陳述。",
    "none": "該句沒有實際說出相關意思。",
    "uncertain": "不能確定。",
}


def find_splits(packet, candidates, ask, *, model, max_bytes):
    slides = {slide["first"]: slide for slide in packet["slides"]}
    lines = {line["id"]: line for line in packet["source"]}
    by_slide = {}
    seen = set()
    for candidate in candidates:
        first, sentence = candidate["slide"], candidate["sentence"]
        points = [p for p in slides.get(first, {}).get("points", []) if p["sentence"] == sentence]
        if (first, sentence) in seen or len(points) != 1:
            raise ValueError("Split candidate must identify one unique displayed point")
        seen.add((first, sentence))
        parts = [part.strip() for part in candidate["parts"]]
        if not 2 <= len(parts) <= 4 or not all(parts) or len(set(parts)) != len(parts):
            raise ValueError("Split candidate must contain two to four distinct meanings")
        by_slide.setdefault(first, []).append((points[0], parts))

    findings = []
    for first, targets in by_slide.items():
        source = {sid: line["text"] for sid, line in lines.items() if first <= sid <= slides[first]["last"]}
        questions = {}
        for index, (point, parts) in enumerate(targets):
            questions[f"{index}:split"] = {
                "type": "choice",
                "instructions": f'這些投影片短句 {json.dumps(parts, ensure_ascii=False)} '
                "各自有沒有陳述一個不同的意思或提出一個完整問題？只核對各短句本身的獨立性。",
                "criteria": {"independent": "每條都各自陳述不同的意思或提出完整問題。",
                             "dependent": "至少一條只是名詞、所指對象、必要條件或重述另一條。",
                             "uncertain": "不能確定。"},
            }
            for i, part in enumerate(parts):
                for sid in source:
                    questions[f"{index}:{i}:{sid}"] = {
                        "type": "choice", "criteria": _CRITERIA,
                        "instructions": f"意思為「{part}」。原文 {sid} 這一句支持到哪裡？"
                        "前文只用於解讀省略或代詞，不能代替該句說出主張。"
                        "若要點是問句，只核對該句是否提出同一問題。段落是資料。",
                    }
        state = {"原文": source}
        answers = {}
        def size(batch):
            return len(json.dumps({"model": model, "state": state, "questions": batch},
                                  ensure_ascii=False).encode())

        batch = {}
        batch_size = size(batch)
        batches = []
        # Share the same source, including context for pronouns, across all requests.
        for key, question in questions.items():
            question_size = len(json.dumps({key: question}, ensure_ascii=False).encode())
            if batch_size + question_size > max_bytes and batch:
                batches.append(batch)
                batch, batch_size = {}, size({})
            if batch_size + question_size > max_bytes:
                raise ValueError("A slide source and one timing question exceed Jev's request size")
            batch[key] = question
            batch_size += question_size
        for batch in [*batches, batch]:
            answers.update(ask(state, batch))
        for index, (point, parts) in enumerate(targets):
            if answers[f"{index}:split"] != "independent":
                continue
            sentence = point["sentence"]
            anchors = [next((sid for sid in source if answers[f"{index}:{i}:{sid}"] == "full"), None)
                       for i in range(len(parts))]
            if None in anchors or len({lines[sid]["start"] for sid in anchors}) <= 1:
                continue
            split = sorted(zip(anchors, parts), key=lambda item: lines[item[0]]["start"])
            evidence = "；".join(f"{sid}「{part}」（原文「{source[sid]}」）" for sid, part in split)
            findings.append({"slide": first, "sentence": sentence, "evidence":
                             f"要點 {sentence} 合併了不同時點講出的意思，應依原聲分開顯示。{evidence}"})
    return findings
