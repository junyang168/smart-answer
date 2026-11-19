"""Source-bound argument requirements for a listening subunit."""
from __future__ import annotations

import json
import re


PLAN_KEY = "required_inferences"
BOUNDARY = (
    "每張的標題和要點只可寫本張 first 到 last 的原文實際說到的意思。"
    "本張稍後說出的內容可由標題概括；只在另一張才說出的內容不可搬到本張。"
    "跨張時，理由留在有理由的張，結論留在有結論的張，遣詞讓聽眾看得出承接。"
    "論證層只供核對，不授權加入本子單元原文沒有說到的內容。"
)

CHAIN_RULE = (
    "若原文的理由經過一個中間判斷才推出結論，承重的中間判斷也要寫成要點，"
    "不能只並列起點與結論，或用「留意背景」「所以會誤解」省掉為何如此。"
    "原文有對照時，交代是哪兩種情況、差異如何改變結論；只需保留會改變推論的部分。"
    "可以合寫或跨相鄰兩張。若所需原文在相隔一張以上的位置，不能把遠處的要點算作這裡已經接上。"
    "本段原文若重述了理由或對照，就用這裡的原話重接；本段沒有重述，就不要把別段的話補到這一張。"
)


PLAN_SYSTEM = """你只做一件事：從這個聆聽子單元的逐字稿找出若省略就會改變教授論證的推論。
claims 用來辨認這些關係；以 source 為準。找出教授怎樣用一個理由、例子或對照回答某問題或支持某結論。
也留意論證層只記了例子、但教授接著說出用途的情形。重述只留一次，例子的非必要細節不用列。
觀眾的話可作他所回應的問題或立場，不可當成教授自己的主張。
每條寫清 reason、inference（理由如何支持結論）及 conclusion，並列出最少的原文句號。
conclusion_sentence 是實際支持結論的原文句，不是提問或引介句；
source_sentences 可包括理由、關係和結論所在的多句。缺乏本子單元原文支持就省略。
不要寫投影片、定時間、加神學推論，或把每條 Claim 都列為必出。沒有必要推論才回空清單。"""


_ROUTE_FIELDS = ("reason", "inference", "conclusion", "conclusion_sentence", "source_sentences")
PLAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": [PLAN_KEY],
    "properties": {PLAN_KEY: {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": list(_ROUTE_FIELDS),
        "properties": {
            **{key: {"type": "string"} for key in _ROUTE_FIELDS[:-1]},
            "source_sentences": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        },
    }}},
}


def _content_lines(source: list[dict]) -> list[dict]:
    return [line for line in source if re.sub(r"^(?:[問答]\s*[:：]\s*)+", "", line["text"].strip())]


def validate(routes: list[dict], source: list[dict]) -> list[dict]:
    source_ids = {line["id"] for line in _content_lines(source)}
    if not isinstance(routes, list):
        raise ValueError("Argument plan must contain a list of inferences")
    validated = []
    seen = set()
    for index, route in enumerate(routes, 1):
        if not isinstance(route, dict) or set(route) - {"id"} != set(_ROUTE_FIELDS):
            raise ValueError("Invalid argument plan fields")
        if any(not isinstance(route[key], str) or not route[key].strip() for key in _ROUTE_FIELDS[:-1]):
            raise ValueError("Argument plan requires a reason, relation and conclusion")
        anchors = route["source_sentences"]
        if (not isinstance(anchors, list) or not all(isinstance(sid, str) for sid in anchors)
                or len(anchors) != len(set(anchors)) or not set(anchors) <= source_ids
                or route["conclusion_sentence"] not in anchors):
            raise ValueError("Argument plan cites a sentence outside its source or lacks its conclusion anchor")
        signature = (route["conclusion_sentence"], route["inference"].strip())
        if signature in seen:
            raise ValueError("Duplicate planned inference")
        seen.add(signature)
        route_id = f"argument-{index}"
        if route.get("id", route_id) != route_id:
            raise ValueError("Argument plan IDs changed")
        validated.append({**route, "id": route_id})
    return validated


def plan(context: dict, source: list[dict], client) -> list[dict]:
    claims = [{key: value for key, value in claim.items() if key != "diagrams"}
              for claim in context["claims"]]
    prompt = json.dumps({"source": [{"id": line["id"], "text": line["text"]} for line in _content_lines(source)],
                         "claims": claims}, ensure_ascii=False)
    answer = client.generate_json(PLAN_SYSTEM, prompt, PLAN_SCHEMA)
    return validate(answer[PLAN_KEY], source)


def review_requirements(context: dict, source: list[dict], slides: list[dict]) -> list[dict]:
    rows = []
    content_ids = {line["id"] for line in _content_lines(source)}
    evidence = {}
    for claim in context.get("claims", []):
        for step in claim.get("evidence_steps", []):
            if step_id := step.get("evidence_step_id"):
                evidence.setdefault(step_id, []).extend(
                    set(anchor["sentences"]) & content_ids
                    for anchor in step.get("anchors", []) if anchor.get("fragment_id"))
    indices = {slide["first"]: i for i, slide in enumerate(slides)}
    for route in validate(context.get(PLAN_KEY, []), source):
        locations = {}
        for sid in route["source_sentences"]:
            containing = [slide["first"] for slide in slides if slide["first"] <= sid <= slide["last"]]
            if len(containing) != 1:
                raise ValueError(f"{route['id']} has no unique slide for {sid}")
            locations[sid] = containing[0]
        conclusion = locations[route["conclusion_sentence"]]
        planned = set(locations)
        repeated_points = []
        for slide in slides:
            at = slide["first"]
            if abs(indices[at] - indices[conclusion]) != 1 or at in locations.values():
                continue
            for point in slide.get("points", []):
                sid = point["sentence"]
                anchors = evidence.get(point.get("source"), [])
                if (slide["first"] <= sid <= slide["last"]
                        and any(sid in anchor for anchor in anchors)
                        and any(sid not in anchor and planned & anchor for anchor in anchors)):
                    locations[sid] = at
                    repeated_points.append(sid)
        rows.append({**route, "conclusion_slide": conclusion, "source_slides": locations,
                     **({"repeated_evidence_points": repeated_points} if repeated_points else {})})
    return rows


BINDINGS_KEY = "argument_bindings"
RAW_BINDINGS_KEY = "raw_argument_bindings"


def binding_schema(routes: list[dict]) -> dict:
    return {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["id", "point_sentences"],
        "properties": {
            "id": {"type": "string", "enum": [route["id"] for route in routes]},
            "point_sentences": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        },
    }}


def bind_points(requirements: list[dict], slides: list[dict], bindings: list[dict], *, recover_repeats=False) -> list[dict]:
    """Check that every inference maps to existing points in its source slides."""
    points = {point["sentence"]: slide["first"] for slide in slides for point in slide["points"]}
    order = {slide["first"]: i for i, slide in enumerate(slides)}
    expected = {route["id"]: route for route in requirements}
    if not isinstance(bindings, list):
        raise ValueError("Missing generated argument bindings")
    found = {}
    for row in bindings:
        if not isinstance(row, dict) or set(row) != {"id", "point_sentences"}:
            raise ValueError("Invalid generated argument binding")
        route_id, anchors = row["id"], row["point_sentences"]
        if not isinstance(route_id, str) or route_id not in expected or route_id in found:
            raise ValueError("Duplicate or unknown generated argument binding")
        route = expected[route_id]
        allowed = set(route["source_slides"].values())
        if (not isinstance(anchors, list) or not anchors
                or not all(isinstance(sid, str) and points.get(sid) in allowed for sid in anchors)
                or len(anchors) != len(set(anchors))
                or not any(points[sid] == route["conclusion_slide"] for sid in anchors)):
            raise ValueError(f"{route_id} must cite existing points in its source slides, including its conclusion slide")
        bound_order = [order[points[sid]] for sid in anchors]
        if max(bound_order) - min(bound_order) > 1:
            local_repeats = {points[sid] for sid in anchors if sid in route.get("repeated_evidence_points", [])}
            if not recover_repeats or len(local_repeats) != 1:
                raise ValueError(f"{route_id} must be readable on one slide or two adjacent slides")
            # Keep the conclusion and its independently anchored neighboring repeat.
            # Semantic Eval still checks whether these points express the entire inference.
            nearby = local_repeats | {route["conclusion_slide"]}
            anchors = [sid for sid in anchors if points[sid] in nearby]
        found[route_id] = anchors
    if expected.keys() != found.keys():
        missing = ", ".join(sorted(expected.keys() - found.keys()))
        raise ValueError(f"Missing generated argument bindings: {missing}")
    return [{**route, "writer_points": found[route["id"]]} for route in requirements]
