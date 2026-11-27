"""Generate unit-wide argument candidates from source-bound groups and their native Claims."""

from collections import defaultdict
import fcntl
import json
from pathlib import Path

from backend.pipeline import claim_creation_flash as creation
from backend.pipeline import claim_unit_plan as unit
from backend.pipeline import evidence_refresh as artifacts


def load_plan(folder):
    folder = Path(folder)
    manifest = unit.UnitManifest.model_validate_json((folder / "unit-manifest.json").read_bytes())
    first = manifest.sources[0]
    return unit.UnitContext.load(folder, first.source_id, folder / first.file)


def group_passages(context):
    """Keep discontinuous teaching together, but never join a gap in a quotation."""
    groups = defaultdict(list)
    sentences = context.reading.sentences
    runs = context.plan["runs"]
    for index, run in enumerate(runs):
        end = runs[index + 1]["first"] - 1 if index + 1 < len(runs) else len(sentences)
        passages = groups[run["group"]]
        for pos in range(run["first"] - 1, end):
            row = sentences[pos]
            if (not passages or pos != passages[-1]["positions"][-1]
                    or row["paragraph"] != passages[-1]["paragraph"]
                    or row["source_id"] != passages[-1]["source_id"]
                    or row["modality"] != passages[-1]["modality"]
                    or row["start"] != passages[-1]["end"]):
                passages.append(dict(key=f"R{pos + 1:04d}", source_id=row["source_id"],
                    paragraph=row["paragraph"], modality=row["modality"], text="", positions=[]))
            passage = passages[-1]
            passage["text"] += row["text"]
            passage["end"] = row["end"]
            passage["positions"].append(pos + 1)
    return dict(groups)


def extract_claims(root, context, group, passages, ports):
    paragraphs = {p["key"]: p["text"] for p in passages}
    by_key = {p["key"]: p for p in passages}
    prompt = creation.CLAIMS + (
        " 本次是单元内的一个论证组。每个R编号是一段连续原话，可能跨讲回到同一问题。"
        "related_ranges的first/last和conclusion_anchor的segment_index只能选owned_paragraphs的R编号。"
        "仅在结论确有讲出的地方锚定。单纯场务或留待解答的问题可没有Claim。"
        "涉及原文词义、文法或译本辨析时，完整保留教授点名的关键语法特征与所评译本用词，勿泛化省略。"
        "不得替教授给语法术语加教科书定义，不得带入讲员未亲口说出的神学或历史百科专名与头衔，也不得自行补写未说过的排他、对比、目的或阐释性半句（如「从而……」「以便……」「表示……」「并非……」）。"
        "讲员用反问表达强烈否定时须改写为明确否定句（勿用「不就是」），全称否定勿擅自加「整个」「全部」缩小范围。"
    )
    content = {"group": group, "owned_paragraphs": paragraphs, "existing_claims": {},
               "sources": {p["key"]: [p["source_id"], p["paragraph"], p["modality"]] for p in passages}}
    reply = creation.call(root, ports, "creation_claims", content, creation.schemas()["claims"],
                          prompt, context.reading.verify)
    candidates = reply["candidates"]
    repairs = creation.calibrate(candidates, paragraphs)
    keys = list(paragraphs)
    for c in candidates:
        anchor = c["conclusion_anchor"]
        key = anchor["segment_index"]
        quote = anchor["verbatim_excerpt"]
        if (key not in by_key or not quote.strip() or quote not in paragraphs[key]
                or not c["statement"].strip() or c["existing_claim_id"] is not None):
            raise ValueError("Claim conclusion is not bound to its supplied passage")
        related = creation.ranges(c["related_ranges"], keys)
        if key not in related:
            raise ValueError("Claim ranges omit the conclusion")
        p = by_key[key]
        if p["modality"] != "spoken":
            raise ValueError("Claim conclusion must cite his own words")
        start = paragraphs[key].index(quote) + context.reading.sentences[p["positions"][0] - 1]["start"]
        c["at"] = [i for i in p["positions"] if context.reading.sentences[i-1]["end"] > start
                   and context.reading.sentences[i-1]["start"] < start + len(quote)]
        c["group"] = group
        c["source_id"] = p["source_id"]
        c["paragraph"] = p["paragraph"]
    artifacts.save(root / "quote-calibration.json", repairs)
    return candidates


def run(plan_dir, root, ports):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "unit-argument.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        context = load_plan(plan_dir)
        groups = group_passages(context)
        artifacts.save(root / "intent.json", dict(unit_plan_sha256=artifacts.digest(context.plan),
                       sources=context.reading.sources, model=ports.identity["gpt"]))
        claims = {}
        for i, (name, passages) in enumerate(groups.items(), 1):
            for c in extract_claims(root / f"group-{i:02d}", context, name, passages, ports):
                claims[f"C{len(claims) + 1:03d}"] = c
        artifacts.save(root / "claims.json", claims)
        return connect(root, context, claims, ports)


TEXT = {"type": "string"}
POSITIONS = creation.array({"type": "integer"})
EDGE_TYPES = ["supports", "answers", "refutes", "qualifies", "illustrates", "contextualizes"]
GRAPH = creation.schema("unit_argument", {
    "new_nodes": creation.array(creation.object_schema({
        "id": TEXT, "kind": {"type": "string", "enum": ["evidence", "observation", "example", "position", "question", "claim"]},
        "statement": TEXT, "at": POSITIONS})),
    "edges": creation.array(creation.object_schema({
        "from_id": TEXT, "to_id": TEXT, "relation": {"type": "string", "enum": EDGE_TYPES},
        "reason": TEXT, "at": POSITIONS,
        "attribution": {"type": "string", "enum": ["explicit", "direct_inference", "editorial"]}})),
    "question_states": creation.array(creation.object_schema({
        "question_id": TEXT, "state": {"type": "string", "enum": ["answered", "partly_answered", "open"]},
        "remaining": TEXT})),
    "gaps": creation.array(creation.object_schema({"description": TEXT, "at": POSITIONS})),
})
GRAPH_PROMPT = (
    "你是讲课记录的论证整理者。输入为同一阅读单元按录音顺序逐句编号的真实原文、各组已生成的Claim、"
    "原先留题的节点。还原教授怎样从问题走到答案。先从开题追到后来真正作答的地方，再从最终判断倒查承重前提。"
    "解释每条承接究竟证明目标中的哪一部分，以及为什么。若一段推理先提出困难，后面有解释消除了它，"
    "把困难保留为question，解释用answers指向它，并将该解释连到它支持的下一步；明示两端如何相容。"
    "按本题实际讲法还原每一支理由所支持的判断、反驳的看法或限定的结果。若几支共同回答一个问题，"
    "保留各自通向该问题的路线；仅有共同主题不足以互相证明。教授直接作判断时可只有一个节点。"
    "若教授先明确提出解经方法、阅读原则或平行经文互查等桥接前提，再据以引入后续经文分析或跨卷书论证，"
    "除必要的illustrates外，须用contextualizes将该方法前提连向它所引入的论证，明示过渡理由，勿只留成独立旁支。"
    "现有C和Q编号直接引用，不重复生成。new_nodes用于缺的证据、关键中间步骤、独立插问、例证、"
    "对方看法与来源确实讲出但各组遗漏的主张。给每个新节点唯一编号N1、N2等。"
    "设想读者只见节点陈述与关系说明，看不到原句：保留支撑判断的具体依据、限定和插问自己的答案。"
    "涉及原文词义、文法或译本辨析时，完整保留教授点名的关键语法特征及所评各译本用词，勿泛化改写或省略。"
    "不得替教授给语法术语加教科书定义，不得带入讲员未亲口说出的神学或历史百科专名与头衔，也不得自行补写未说过的排他、对比、目的或阐释性半句（如「从而……」「以便……」「表示……」「并非……」「不能只凭……」）。"
    "讲员用反问表达强烈否定时须写成明确否定句（勿用「不就是」），全称否定勿擅自加「整个」「全部」缩小范围。"
    "若有例证，说清故事说明哪一点；教授亲自把它用到什么对象、处境或行动，或排除了什么推论，就具体保留。"
    "讲道中顺口的谐音笑话、纯粹幽默插曲或场务过渡不得接入主论点作supports或illustrates；"
    "宏观总括句（「所以从这里可以看到……」等）应指向它所总括的核心论点或多条前提，勿窄化错接成仅由紧邻上一句单点支撑；"
    "同一对节点之间不得互指双向supports/illustrates。"
    "顺带提及的生活应用除非与本单元核心论证强关联，否则只需在例证或说明里简提，不单独升格为claim。"
    "故事中人物的行为与教授的教导分开。若支持跨讲，分别指明原来的起点与后来的作答，"
    "不能用后来重述抹掉原来的留题。只引用输入句号的整数位置，at可列多句。"
    "证据、例子等指向受支持或被说明的节点，回答指向问题节点。重要推理应能沿这些边直接走通。"
    "reason应写成可核对的中文判断，讲清如何推出，每条边也列支持该关系的原文句号。"
    "边理由（reason）只说明当前两端节点间的实际推论，不得弱化原断言强度、不得夹带其他段落的后续推论，也不得把描述性成因改写成规范性要求；教授用归谬或反问反驳某看法时，应建节点并用refutes指向该反方立场；归谬论证中条件推论与现实反例共同指向被反驳的前提或读法，勿把现实反例连成反驳条件推论本身。"
    "每个新节点若为claim，其at只引作出这个判断的那一讲；跨讲的支持另建边。"
    "explicit表示教授实际说明此关系，direct_inference表示其所述步骤可直接推出，编辑联系应标editorial。"
    "所有question都填状态，未答的保留未答，不补答案。gaps只列读过原文后仍无法落实的必要承接。"
    "不要为把图连满而强加关系，不用任何外部资料。"
)


def check_positions(values, length):
    if (not values or len(set(values)) != len(values)
            or any(type(i) is not int or not 1 <= i <= length for i in values)):
        raise ValueError(f"invalid unit evidence positions: {values}")


def bind_graph(reading, claims, questions, answer):
    sentences = reading.sentences
    nodes = {cid: {"kind": "claim", "statement": c["statement"], "at": c["at"]} for cid, c in claims.items()}
    nodes.update(questions)
    for item in answer["new_nodes"]:
        key = item["id"]
        if not key.strip() or key in nodes or not item["statement"].strip():
            raise ValueError(f"invalid or duplicate unit node {key}")
        nodes[key] = {k: v for k, v in item.items() if k != "id"}
    for key, node in nodes.items():
        check_positions(node["at"], len(sentences))
        if node["kind"] == "claim" and len({sentences[i-1]["source_id"] for i in node["at"]}) != 1:
            raise ValueError(f"Claim {key} spans multiple lecture identities")
    edges = set()
    support_pairs = set()
    answered = set()
    for link in answer["edges"]:
        a, b, rel = link["from_id"], link["to_id"], link["relation"]
        signature = (a, b, rel, link["reason"].strip(), tuple(sorted(link["at"])))
        if a not in nodes or b not in nodes or a == b or signature in edges or not link["reason"].strip():
            raise ValueError(f"invalid unit edge {a} {rel} {b}")
        if rel in {"supports", "illustrates"}:
            if (b, a) in support_pairs:
                raise ValueError(f"bidirectional conflicting support/illustrate edge between {a} and {b}")
            support_pairs.add((a, b))
        check_positions(link["at"], len(sentences))
        edges.add(signature)
        if rel == "answers":
            if nodes[b]["kind"] != "question":
                raise ValueError("an answer must point to a question")
            if link["attribution"] != "editorial":
                answered.add(b)
    states = answer["question_states"]
    wanted = {key for key, node in nodes.items() if node["kind"] == "question"}
    if len(states) != len(wanted) or {r["question_id"] for r in states} != wanted:
        raise ValueError("every unit question needs one disposition")
    for row in states:
        if (row["state"] != "open") != (row["question_id"] in answered):
            raise ValueError("question disposition disagrees with source-backed answer edges")
        if row["state"] != "answered" and not row["remaining"].strip():
            raise ValueError("open question needs its unresolved part")
    for gap in answer["gaps"]:
        check_positions(gap["at"], len(sentences))
        if not gap["description"].strip():
            raise ValueError("unit gap needs a description")
    return nodes


def _encode_sentence_lines(rows, start_idx, end_idx):
    lines = []
    previous = None
    for i in range(start_idx, end_idx):
        row = rows[i]
        location = (row["source_id"], row["paragraph"], row["modality"])
        if location != previous:
            lines.append(list(location))
            previous = location
        lines.append([i + 1, row["text"]])
    return lines


def _request_bytes(ports, content):
    req = dict(identity=ports.identity["gpt"], system=GRAPH_PROMPT,
               input={"task": "creation_unit_connections", **content}, schema=GRAPH)
    return len(json.dumps(req, ensure_ascii=False).encode())


def connect(root, context, claims, ports):
    rows = context.reading.sentences
    questions = {f"Q{i:03d}": dict(kind="question", statement=q["question"], at=[q["at"]])
                 for i, q in enumerate(context.plan["questions"], 1)}
    compact = {cid: {"statement": c["statement"], "group": c["group"], "at": c["at"],
                     "question": c["answers_question"], "support_hint": c["support_hint"]}
               for cid, c in claims.items()}
    full_lines = _encode_sentence_lines(rows, 0, len(rows))
    full_content = {"sentences": full_lines, "claims": compact, "questions": questions}
    if _request_bytes(ports, full_content) <= creation.f.GPT_BYTES:
        answer = creation.call(root / "connections", ports, "creation_unit_connections",
                               full_content, GRAPH, GRAPH_PROMPT, context.reading.verify)
        try:
            nodes = bind_graph(context.reading, claims, questions, answer)
        except ValueError as exc:
            retry_content = {**full_content, "previous_validation_error": str(exc)}
            if _request_bytes(ports, retry_content) > creation.f.GPT_BYTES:
                raise
            answer = creation.call(root / "connections-retry-01", ports, "creation_unit_connections",
                                   retry_content, GRAPH, GRAPH_PROMPT, context.reading.verify)
            nodes = bind_graph(context.reading, claims, questions, answer)
    else:
        merged_nodes = {}
        merged_edges = []
        edge_sigs = set()
        merged_states = {}
        merged_gaps = []
        start = 0
        batch = 0
        while start < len(rows):
            batch += 1

            def batch_content(end):
                content = {
                    "sentences": _encode_sentence_lines(rows, start, end),
                    "claims": compact,
                    "questions": questions,
                }
                if merged_nodes or merged_edges:
                    content["earlier_nodes"] = list(merged_nodes.values())
                    content["earlier_edges"] = merged_edges
                return content

            low, high = start + 1, len(rows)
            if _request_bytes(ports, batch_content(low)) > creation.f.GPT_BYTES:
                raise ValueError("unit connections request cannot fit one sentence with its argument context")
            while low < high:
                mid = (low + high + 1) // 2
                if _request_bytes(ports, batch_content(mid)) <= creation.f.GPT_BYTES:
                    low = mid
                else:
                    high = mid - 1
            end = low
            part = creation.call(root / f"connections-{batch:02d}", ports, "creation_unit_connections",
                                 batch_content(end), GRAPH, GRAPH_PROMPT, context.reading.verify)
            for node in part["new_nodes"]:
                nid = node["id"]
                if nid in claims or nid in questions:
                    raise ValueError(f"unit connections batch {batch} collided with fixed node {nid}")
                if nid in merged_nodes:
                    prev = merged_nodes[nid]
                    if (prev.get("kind"), prev.get("statement"), sorted(prev.get("at") or ())) != (
                        node.get("kind"), node.get("statement"), sorted(node.get("at") or ())
                    ):
                        raise ValueError(f"unit connections batch {batch} redefined existing node {nid}")
                else:
                    merged_nodes[nid] = node
            for link in part["edges"]:
                sig = (link["from_id"], link["to_id"], link["relation"],
                       link["reason"].strip(), tuple(sorted(link["at"])))
                if sig not in edge_sigs:
                    edge_sigs.add(sig)
                    merged_edges.append(link)
            for row in part["question_states"]:
                merged_states[row["question_id"]] = row
            merged_gaps.extend(part["gaps"])
            start = end
        answer = {
            "new_nodes": list(merged_nodes.values()),
            "edges": merged_edges,
            "question_states": list(merged_states.values()),
            "gaps": merged_gaps,
        }
        nodes = bind_graph(context.reading, claims, questions, answer)
    result = dict(version="unit_argument_candidate_v1", status="candidate_with_gaps" if answer["gaps"] else "candidate",
                  unit_plan_sha256=artifacts.digest(context.plan), sources=context.reading.sources,
                  sentences=rows, group_runs=context.plan["runs"], claims=claims, nodes=nodes, edges=answer["edges"],
                  question_states=answer["question_states"], gaps=answer["gaps"], production_writes=0)
    context.reading.verify()
    artifacts.save(root / "unit-argument.json", result)
    artifacts.save(root / "unit-argument-reading.json", blind_reading(result))
    receipt = {"candidate_sha256": artifacts.digest(result), "nodes": len(nodes), "edges": len(answer["edges"]),
               "claims": sum(n["kind"] == "claim" for n in nodes.values()), "sentences_read": len(rows),
               "groups_read": len({r["group"] for r in context.plan["runs"]}), "status": result["status"]}
    artifacts.save(root / "unit-argument-receipt.json", receipt)
    (root / "unit-argument.md").write_text(render(result, receipt), encoding="utf-8")
    return receipt

def blind_reading(candidate):
    """Expose argument content without transcript fields, anchors, or retrieval paths."""
    return {
        "nodes": {key: {"kind": node["kind"], "statement": node["statement"]}
                  for key, node in candidate["nodes"].items()},
        "edges": {f"E{i}": {key: edge[key] for key in
                  ("from_id", "to_id", "relation", "reason", "attribution")}
                  for i, edge in enumerate(candidate["edges"], 1)},
        "question_states": [{key: row[key] for key in ("question_id", "state", "remaining")}
                            for row in candidate["question_states"]],
        "gaps": [gap["description"] for gap in candidate["gaps"]],
    }


def render(candidate, receipt):
    """A readable projection of the frozen candidate; no editorial rewriting."""
    rows = candidate["sentences"]

    def sources(positions):
        return "、".join(dict.fromkeys(
            f'{rows[i-1]["source_id"]} {rows[i-1]["paragraph"]} {rows[i-1]["time"]}' for i in positions))

    def quotes(positions):
        return [f'> {rows[i-1]["text"]}（{rows[i-1]["source_id"]} {rows[i-1]["sentence_id"]}）' for i in positions]

    lines = ["# 单元论证候选", "", f'候选摘要值 `{receipt["candidate_sha256"]}`', "", "## 全部 Claim", ""]
    for key, node in candidate["nodes"].items():
        if node["kind"] == "claim":
            lines.extend([f'- **{key}** {node["statement"]}（{sources(node["at"])}）'])
    lines.extend(["", "## 问题、依据和例证", ""])
    for key, node in candidate["nodes"].items():
        if node["kind"] != "claim":
            lines.append(f'- **{key}** ({node["kind"]}) {node["statement"]}（{sources(node["at"])}）')
    lines.extend(["", "## 逐条论证关系与原句", ""])
    for edge in candidate["edges"]:
        lines.extend([f'### {edge["from_id"]} → {edge["to_id"]} · {edge["relation"]}', "",
            candidate["nodes"][edge["from_id"]]["statement"], "", candidate["nodes"][edge["to_id"]]["statement"],
            "", edge["reason"], "", *quotes(edge["at"]), ""])
    lines.extend(["## 待解问题", ""])
    for row in candidate["question_states"]:
        lines.append(f'- {row["question_id"]} · {row["state"]} {row["remaining"]}')
    for gap in candidate["gaps"]:
        lines.append(f'- {gap["description"]}（{sources(gap["at"])}）')
    return "\n".join(lines) + "\n"
