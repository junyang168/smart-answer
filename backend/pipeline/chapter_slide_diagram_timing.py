"""Locate the start of a diagram's explanation within its own slide."""

import json
from pathlib import Path
from xml.etree import ElementTree

# Distinguish the subject of the image from prerequisites and isolated mentions.
_FOCUS = {
    "宗主國與附庸國的約": "宗主國與附庸國之約如何運作：先施恩再提出要求。泛談新舊約或只報比喻名稱不算。",
    "兩律法的重疊": "為何摩西律法與基督律法會重疊。直接用來解釋重疊的理由已算開始，不必等說出結論。",
    "登山寶訓的文學結構": "登山寶訓的整體分段。只講一個局部的首尾呼應（比如禱告段）不算開始講整體。",
    "登山寶訓（論彌賽亞／門徒的使命）": "登山寶訓如何分為兩大段。開始說整體分段就算，不用等第一段的細節。",
}
_EDITORIAL_OVERVIEW = "登山寶訓的文學結構"


def _questions(diagram, source):
    svg = ElementTree.fromstring(diagram["svg"])
    labels = [" ".join("".join(node.itertext()).split()) for node in svg.iter()
              if node.tag.rsplit("}", 1)[-1] in {"text", "title", "desc"}]
    state = {"圖名": diagram["title"], "圖中文字": [label for label in labels if label],
             "這一張投影片的依序原文": source}
    focus = _FOCUS.get(diagram["title"], "圖整體要說明的關係或結構。")
    questions = {"scope": {
        "type": "choice",
        "instructions": f"只看這一張投影片的原文。他是否開始實質講解「{focus}」？不要求同一張講完。",
        "criteria": {"whole": "這張開始實質講圖的主題。",
                     "part": "只講圖的一個局部，沒有開始講圖要呈現的主題。",
                     "other": "只有題名、提及或無關內容。",
                     "uncertain": "無法確定。"},
    }}
    for sid in source:
        questions[sid] = {
            "type": "choice",
            "instructions": f"只看這一張投影片。{sid} 這句是否已實質講解以下內容？{focus}"
                            "直接用來解釋圖的理由算。報名、預告、泛問、介紹上一步和普通過渡不算。",
            "criteria": {"explains": "已在講解這張圖的實質內容。",
                         "other": "這句尚未講解圖的實質內容。",
                         "uncertain": "無法確定。"},
        }
    return state, questions


def resolve(record, lines, ask):
    """Return timings plus issues. Each request sees exactly one slide's source."""
    slides = record["slides"]
    context = record.get("claim_context") or {}
    diagrams = {item["sha256"]: item for claim in context.get("claims", [])
                for item in claim.get("diagrams", []) if "svg" in item}
    timings = {}
    problems = []
    for slide in slides:
        if not (ref := slide.get("diagram_ref")):
            continue
        first, last = slide["first"], slide["last"]
        source = {line["id"]: line["text"] for line in lines if first <= line["id"] <= last}
        if ref not in diagrams or not source:
            problems.append(f"{first}: 論證圖或本張原文缺失")
            continue
        diagram = diagrams[ref]
        state, questions = _questions(diagram, source)
        verdicts = ask(state, questions)
        sentence = next((sid for sid in source if verdicts[sid] == "explains"), None)
        scope = verdicts["scope"]
        if scope == "whole" and sentence:
            timings[first] = {"sha256": ref, "sentence": sentence}
        elif scope in {"part", "other"} and diagram["title"] == _EDITORIAL_OVERVIEW:
            # The owner approved the full editorial chart only for an overall
            # exposition. A local example may be relevant but cannot show it.
            timings[first] = {"sha256": ref, "sentence": None}
        else:
            problems.append(f"{first}: 無法確認「{diagram['title']}」在本張的講解起點（{scope}）")
    return timings, problems


def plan_spans(context, lines, ask):
    """Find complete diagram explanations in the retained argument before dividing slides."""
    from backend.pipeline.chapter_slide_cases import diagrams_for_lecture

    order = {line["id"]: i for i, line in enumerate(lines)}
    candidates = {}
    # An approved diagram choice is scoped to the spoken passage it describes.
    excluded = set()
    for case in diagrams_for_lecture(context.get("lecture", "")):
        if quote := case.get("source_text"):
            excluded.update(case["replace_refs"])
            if any(line["text"] == quote for line in lines):
                excluded.discard(case["sha256"])
    for claim in context.get("claims", []):
        ids = {claim.get("stated")}
        ids.update(sid for passage in claim.get("own_passages", []) for sid in passage["sentences"])
        ids.update(sid for step in claim.get("evidence_steps", []) for anchor in step.get("anchors", [])
                   for sid in anchor["sentences"])
        if ids.isdisjoint(order):
            continue
        for diagram in claim.get("diagrams", []):
            ref = diagram["sha256"]
            if "svg" not in diagram or ref in excluded:
                continue
            candidates.setdefault(ref, diagram)

    spans, problems = [], []
    source = {line["id"]: line["text"] for line in lines}
    for ref, diagram in candidates.items():
        # The argument selects the candidate. Read the retained subunit to find
        # the boundaries, since a claim anchor can be inside the explanation.
        state, base = _questions(diagram, source)
        state["相關論證的依序原文"] = state.pop("這一張投影片的依序原文")
        focus = _FOCUS.get(diagram["title"], "圖整體呈現的關係或結構")
        questions = {"scope": {
            **base["scope"],
            "instructions": f"這段原文有沒有連續展開「{focus}」？區分整體講解、只講圖的一部分和順帶提及。",
        }}
        questions.update({sid: {
            "type": "choice",
            "instructions": f"{sid} 在「{focus}」的連續講解中是開始、延續還是範圍外？"
                            "帶讀另一個圖內區塊、提出相關問題、解釋理由或收結圖的關係，都算同一段。"
                            "普通引言、只報圖名和轉到別的主題在範圍外。不要把只講局部當成整圖講解。",
            "criteria": {"start": "開始說明整體圖的內容。", "inside": "仍在這段連續講解，包括收結。",
                         "outside": "不在這段講解中。", "uncertain": "無法確定。"},
        } for sid in source})
        answers = ask(state, questions)
        scope = answers["scope"]
        if scope in {"part", "other"}:
            continue
        found = []
        first = last = None
        for sid in source:
            value = answers[sid]
            if first is not None and value in {"outside", "uncertain"}:
                found.append({"sha256": ref, "first": first, "last": last})
                first = last = None
            if value == "start" and first is None:
                first = last = sid
            elif value in {"start", "inside"} and first is not None:
                last = sid
        if first is not None:
            found.append({"sha256": ref, "first": first, "last": last})
        if scope != "whole" or not found:
            problems.append(f"無法確認「{diagram['title']}」的連續講解範圍")
        else:
            spans.extend(found)
    spans.sort(key=lambda span: order[span["first"]])
    if any(order[left["last"]] >= order[right["first"]] for left, right in zip(spans, spans[1:])):
        problems.append("兩張論證圖的講解範圍相交，須核對選圖")
    return spans, problems


def plan_record(context, lines, root):
    from backend.pipeline import claim_flash_common, evidence_refresh
    from backend.pipeline.chapter_slide_point_timing import _ask_batches

    return plan_spans(context, lines, lambda state, questions: _ask_batches(
        state, questions,
        lambda shared, batch: claim_flash_common.jev(root, evidence_refresh.JevPort(), shared, batch),
        evidence_refresh.MODEL, claim_flash_common.JEV_BYTES))


def project_plan(slides, spans):
    """Use the verified diagram once, and check that its continuous source stays on one slide."""
    timings, problems = {}, []
    for span in spans:
        slide = next((row for row in slides if row["first"] <= span["first"] <= row["last"]), None)
        if (slide is None or slide["last"] != span["last"] or slide.get("diagram_ref") != span["sha256"]
                or slide["first"] in timings):
            problems.append(f"{span['first']}–{span['last']}: 同一張圖的連續講解須完整放在一張投影片")
            continue
        timings[slide["first"]] = {"sha256": span["sha256"], "sentence": span["first"]}
    if {row["first"] for row in slides if row.get("diagram_ref")} != set(timings):
        problems.append("論證圖出現在已確認的講解範圍之外")
    return timings, problems


def check_record(record_path: Path, selection_path: Path, root: Path):
    from backend.pipeline import chapter_exposition, chapter_slides, claim_flash_common, evidence_refresh

    record = json.loads(record_path.read_text())
    context = record.get("claim_context") or {}
    if ("diagram_spans" not in context and "diagram_timing" not in record
            and not any(slide.get("diagram_ref") for slide in record["slides"])):
        return []
    if record["decisions_sha256"] != chapter_exposition.file_sha256(selection_path):
        raise ValueError("Diagram timing requires the matching slide selection")
    selection = json.loads(selection_path.read_text())
    lines = chapter_slides.sentences(chapter_slides.kept_pieces(selection))
    if "diagram_spans" in context:
        timings, problems = project_plan(record["slides"], context["diagram_spans"])
    else:
        timings, problems = resolve(record, lines, lambda state, questions: claim_flash_common.jev(
            root, evidence_refresh.JevPort(), state, questions))
    if not problems and record.get("diagram_timing") != timings:
        chapter_slides.timed_diagrams(record["slides"], lines, record.get("claim_context") or {}, timings)
        record["diagram_timing"] = timings
        stage = record_path.with_suffix(".diagram-timing.tmp")
        stage.write_text(json.dumps(record, ensure_ascii=False, indent=1))
        stage.replace(record_path)
    return problems
