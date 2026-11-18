"""Compile source-local Claim hints for a chapter-slide experiment; never change knowledge."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from backend.pipeline import chapter_exposition, chapter_slides


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def validate_context(context, passage, lecture, decisions, decisions_sha):
    if (context.get("passage"), context.get("lecture"), context.get("decisions_sha256")) != (passage, lecture, decisions_sha):
        raise ValueError("Claim context does not match these decisions/lecture/passage")
    if context.get("transcript_sha256") != decisions.get("transcript_sha256"):
        raise ValueError("Claim context transcript changed")



def claim_source_ranges(context: dict, decisions: dict) -> list[dict]:
    """Group exact evidence sentences by role and continuous retained source."""
    piece_order = {piece["id"]: index for index, piece in enumerate(decisions["pieces"])}
    positions = {}
    previous_piece = None
    block = 0
    kept = chapter_slides.kept_pieces(decisions)
    for index, line in enumerate(chapter_slides.sentences(kept)):
        piece = piece_order[line["piece"]]
        if previous_piece is not None and piece > previous_piece + 1:
            block += 1
        positions[line["id"]] = (block, index)
        previous_piece = piece

    result = []
    for claim in context["claims"]:
        roles_by_sentence = {}
        for step in claim["evidence_steps"]:
            role = step.get("step_type") or "unspecified"
            for anchor in step["anchors"]:
                for sentence_id in anchor["sentences"]:
                    if sentence_id not in positions:
                        raise ValueError(f"Evidence sentence outside retained transcript: {sentence_id}")
                    roles_by_sentence.setdefault(sentence_id, set()).add(role)

        ranges = []
        previous_position = None
        for sentence_id in sorted(roles_by_sentence, key=positions.__getitem__):
            position = positions[sentence_id]
            roles = sorted(roles_by_sentence[sentence_id])
            if (
                previous_position is not None
                and position == (previous_position[0], previous_position[1] + 1)
                and roles == ranges[-1]["evidence_roles"]
            ):
                ranges[-1]["last"] = sentence_id
            else:
                ranges.append({"first": sentence_id, "last": sentence_id, "evidence_roles": roles})
            previous_position = position
        if ranges:
            result.append({"claim_id": claim["claim_id"], "ranges": ranges})
    return result

def retained_spans(decisions: dict) -> tuple[str, list[tuple[int, int, str, str]]]:
    """The lecture's whole cut text (compacted) and where each retained sentence sits in it.

    Excerpts are located in the actual, byte-verified transcript, including excluded
    pieces; only sentences overlapping an excerpt can be cited as hints."""
    text = ""
    piece_offsets = {}
    for piece in decisions["pieces"]:
        piece_offsets[piece["id"]] = len(text)
        text += compact(piece["text"])
    sentence_spans = []
    for line in chapter_slides.sentences(chapter_slides.kept_pieces(decisions)):
        piece = next(p for p in decisions["pieces"] if p["id"] == line["piece"])
        part = compact(piece["text"])
        # Repeated identical sentences are tracked in order within each piece.
        previous = [end - piece_offsets[line["piece"]] for _, end, _, pid in sentence_spans if pid == line["piece"]]
        start = part.find(compact(line["text"]), previous[-1] if previous else 0)
        if start < 0:
            raise ValueError("Cannot locate a retained sentence")
        low = piece_offsets[line["piece"]] + start
        sentence_spans.append((low, low + len(compact(line["text"])), line["id"], line["piece"]))
    return text, sentence_spans


def locate(text: str, spans: list, quote: str) -> list[str] | None:
    """Retained sentences an excerpt overlaps; None when it is missing or not unique."""
    needle = compact(quote)
    if not needle or text.count(needle) != 1:
        return None
    low = text.index(needle)
    return [sid for a, b, sid, _ in spans if a < low + len(needle) and b > low]


def compile_context(passage: str, lecture: str, *, data_base_dir: Path, store: Any, allow_unusable_evidence: bool = False) -> dict:
    from backend.pipeline.original_audio_index import Sermons
    path = chapter_exposition.record_path(data_base_dir, passage, lecture)
    decisions = json.loads(path.read_text())
    documents = [d for d in store.list_records("source_documents") if d.get("transcript_id") == lecture]
    if len(documents) != 1:
        raise ValueError("Expected one source document")
    document = documents[0]
    sermons = Sermons(data_base_dir)
    source_path = sermons.transcript_path(document)
    if not source_path or chapter_exposition.file_sha256(source_path) != decisions["transcript_sha256"]:
        raise ValueError("Actual transcript differs from saved keep decisions")
    if chapter_exposition.cut(sermons.load(document["source_id"], document)) != decisions["pieces"]:
        raise ValueError("Actual transcript pieces differ from saved keep decisions")
    text, sentence_spans = retained_spans(decisions)
    steps = {e["evidence_step_id"]: e for e in store.list_records("evidence_steps")}
    fragments = {f["fragment_id"]: f for f in store.list_records("source_fragments")}
    included, excluded, unusable = [], [], []
    for claim in sorted(store.list_records("claims"), key=lambda c: c["claim_id"]):
        if not any(o.get("transcript_id") == lecture for o in claim.get("occurrences", [])):
            continue
        cid = claim["claim_id"]
        if claim.get("review_status") in {"superseded", "retired", "rejected"}:
            excluded.append({"claim_id": cid, "reason": claim.get("review_status")})
            continue
        evidence = []
        for eid in claim.get("evidence_step_ids", []):
            step = steps.get(eid)
            if step is None:
                if not allow_unusable_evidence:
                    raise ValueError(f"Missing evidence step: {eid}")
                unusable.append({"claim_id": cid, "object_id": eid, "reason": "missing evidence step"})
                continue
            anchors = []
            fids = list(step.get("source_fragment_ids") or [])
            if step.get("source_fragment_id"):
                fids.append(step["source_fragment_id"])
            for fid in dict.fromkeys(fids):
                fragment = fragments.get(fid)
                if fragment is None:
                    if not allow_unusable_evidence:
                        raise ValueError(f"Missing fragment: {fid}")
                    unusable.append({"claim_id": cid, "object_id": fid, "reason": "missing or retired fragment"})
                    continue
                if fragment.get("source_id") != document["source_id"]:
                    if not allow_unusable_evidence:
                        raise ValueError(f"Cross-sermon fragment in {cid}: {fid}")
                    unusable.append({"claim_id": cid, "object_id": fid, "reason": "cross-sermon fragment"})
                    continue
                quote = fragment.get("verbatim_excerpt") or ""
                ids = locate(text, sentence_spans, quote)
                if ids is None:
                    if not allow_unusable_evidence:
                        raise ValueError(f"Excerpt missing/ambiguous in actual transcript: {fid}")
                    unusable.append({"claim_id": cid, "object_id": fid, "reason": "excerpt missing or ambiguous"})
                    continue
                if ids:
                    anchors.append({"fragment_id": fid, "fragment_sha256": digest(fragment),
                                    "quote": quote, "sentences": ids})
            if anchors:
                evidence.append({"evidence_step_id": eid, "sha256": digest(step),
                                 "statement": step.get("statement"), "step_type": step.get("step_type"),
                                 "review_status": step.get("review_status"), "anchors": anchors})
        if evidence:
            included.append({"claim_id": cid, "sha256": digest(claim), "statement": claim["statement"],
                             "review_status": claim.get("review_status"), "attribution": claim.get("attribution"),
                             "scripture_refs": claim.get("scripture_refs"), "evidence_steps": evidence})
        else:
            excluded.append({"claim_id": cid, "sha256": digest(claim), "reason": "no evidence in retained sentences"})
    context = {"schema_version": "chapter-slide-claim-context.v1", "passage": passage, "lecture": lecture,
               "decisions_sha256": chapter_exposition.file_sha256(path),
               "transcript_sha256": decisions["transcript_sha256"], "source_id": document["source_id"],
               "claims": included, "excluded": excluded}
    if unusable:
        context["unusable_evidence"] = unusable
    context["claim_source_ranges"] = claim_source_ranges(context, decisions)
    return context



OUTLINE_SCHEMA = "chapter-slide-outline-context.v1"
_GROUP = re.compile(r"^## (G\d+) (.+)$")
_CLAIM = re.compile(r"^(\s*)- (\S+) (.+?)  〔(.*?)；(.*?)；講出 (?:(L\d+-S\d{4})「(.*)」|未定位)〕$")


def parse_outline(text: str) -> list[dict]:
    """Read a lecture-outline Claim structure (one cover per argument group, points indented)."""
    groups, stack = [], []
    for line in text.splitlines():
        if match := _GROUP.match(line):
            groups.append({"group_id": match[1], "title": match[2], "claims": []})
            stack = []
            continue
        match = _CLAIM.match(line)
        if not match or not groups:
            continue
        depth = len(match[1]) // 2
        stack = stack[:depth]
        parent = stack[-1] if stack else None
        groups[-1]["claims"].append({
            "claim_id": match[2], "statement": match[3], "parent": parent, "depth": depth,
            "ranges": [r for r in match[5].split(",") if r],
            "stated_paragraph": match[6], "stated_quote": match[7]})
        stack.append(match[2])
    return groups


def compile_outline_context(passage: str, lecture: str, lecture_number: int, *,
                            data_base_dir: Path, structure_path: Path) -> dict:
    """Place outline Claims on the retained sentences of one lecture; reads no knowledge store."""
    path = chapter_exposition.record_path(data_base_dir, passage, lecture)
    decisions = json.loads(path.read_text())
    lines = chapter_slides.sentences(chapter_slides.kept_pieces(decisions))
    joined, spans = "", []
    for line in lines:
        piece = compact(line["text"])
        spans.append((len(joined), len(joined) + len(piece), line["id"]))
        joined += piece
    prefix = f"L{lecture_number}-"
    claims, excluded, groups = [], [], []
    for group in parse_outline(structure_path.read_text(encoding="utf-8")):
        if not any(r.startswith(prefix) for c in group["claims"] for r in c["ranges"]):
            continue
        groups.append({"group_id": group["group_id"], "title": group["title"]})
        for claim in group["claims"]:
            entry = {"claim_id": claim["claim_id"], "group_id": group["group_id"],
                     "statement": claim["statement"], "parent": claim["parent"], "evidence_steps": []}
            paragraph = claim["stated_paragraph"] or ""
            if not paragraph.startswith(prefix):
                entry["stated"] = f"其他講（{paragraph or '未定位'}）"
                claims.append(entry)
                continue
            needle = compact(claim["stated_quote"])
            if joined.count(needle) != 1:
                excluded.append({"claim_id": claim["claim_id"], "reason": "stated sentence not in retained transcript"})
                continue
            low = joined.index(needle)
            ids = [sid for a, b, sid in spans if a < low + len(needle) and b > low]
            entry["stated"] = ids[0]
            entry["evidence_steps"] = [{"step_type": "stated", "anchors": [{"quote": claim["stated_quote"], "sentences": ids}]}]
            claims.append(entry)
    context = {"schema_version": OUTLINE_SCHEMA, "passage": passage, "lecture": lecture,
               "decisions_sha256": chapter_exposition.file_sha256(path),
               "transcript_sha256": decisions.get("transcript_sha256"),
               "structure_sha256": hashlib.sha256(structure_path.read_bytes()).hexdigest(),
               "groups": groups, "claims": claims, "excluded": excluded}
    context["claim_source_ranges"] = claim_source_ranges(context, decisions)
    return context


#: 他報段落結構時說的跨章範圍（「五章十三節到七章二十九節」）：是結構標記，不是要放全文的經文。
_CROSS_CHAPTER = re.compile(r"\d+\s*[:：]\s*\d+\s*[-–—~～]\s*\d+\s*[:：]\s*\d+")
def _bigrams(text: str) -> set[str]:
    han = "".join(re.findall(r"[\u3400-\u9fff]", text))
    return {han[i:i + 2] for i in range(len(han) - 1)}


def _chapter_verses(cuv_root: Path, book: str, chapter: int) -> dict:
    try:
        return json.loads((cuv_root / book.upper() / f"{chapter}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def read_aloud(slug: str, lines: list[str], cuv_root: Path | None = None, passage: str | None = None) -> list[str]:
    """這段經文裡他在本堂念出來的各節（單節 slug）：有一句逐字稿，一半以上的相鄰兩字出現在那一節。

    與聆聽頁判斷「他此刻念到哪一節」用同一個準則。只報出處、說段落範圍不算。
    他念的是本章經文（passage）時，一句短經文會碰巧與別卷的一節共用一半的相鄰兩字
    （太 5:9「因為他們必稱為神的兒子」對 賽 61:6「人必稱你們為我們神的僕役」）：
    那一句與本章某節更相符，就算他念本章，不算念這一節。"""
    from backend.api.scripture import parse_reference

    if cuv_root is None:
        from backend.bible_study.paths import cuv_dir
        cuv_root = cuv_dir()
    ref = parse_reference(slug)
    chapter = _chapter_verses(cuv_root, ref["slug_book"], ref["chapter"])
    if not chapter:
        return []
    said = [pairs for pairs in map(_bigrams, lines) if len(pairs) >= 5]
    own = []
    if passage:
        home = parse_reference(passage)
        if (home["slug_book"], home["chapter"]) != (ref["slug_book"], ref["chapter"]):
            own = [_bigrams(text) for text in _chapter_verses(cuv_root, home["slug_book"], home["chapter"]).values()]

    def own_better(pairs: set, share: float) -> bool:
        return any(v and len(pairs & v) / len(pairs) > share for v in own)

    read = []
    for number in range(ref["start"], ref["end"] + 1):
        verse = _bigrams(chapter.get(str(number), ""))
        if verse and any(len(pairs & verse) / len(pairs) >= 0.5 and not own_better(pairs, len(pairs & verse) / len(pairs))
                         for pairs in said):
            read.append(f"{slug.split('-')[0]}-{ref['chapter']}-{number}")
    return read


def evidence_scriptures(refs: list[str], lines: list[str], cuv_root: Path | None = None,
                        passage: str | None = None) -> tuple[list[str], list[str]]:
    """Evidence 引的經文中，他在本堂念出來的 → 投影片必須顯示（負責人 2026-11-16）。另回讀不出的出處。

    跨章範圍（「五章十三節到七章二十九節」）是他在講段落範圍，不是念經文。"""
    from backend.api.scripture import reference_slugs

    shown, unread = [], []
    for ref in refs:
        if _CROSS_CHAPTER.search(ref):
            continue
        slugs = reference_slugs(ref)
        if len(slugs) != 1:
            unread.append(ref)
            continue
        shown += [verse for verse in read_aloud(slugs[0], lines, cuv_root, passage) if verse not in shown]
    return shown, unread


def evidence_scripture_gaps(context: dict | None, slides: list[dict], *, source_lines=None, passage=None) -> list[str]:
    """負責人 2026-11-16：Evidence 用到的經文都要出現在投影片上。回沒出現的。"""
    from backend.api.scripture import parse_reference

    if not context:
        return []
    shown = [parse_reference(item["slug"]) for slide in slides for item in slide.get("scriptures", [])]
    gaps = []
    for claim in context.get("claims", []):
        for step in claim.get("evidence_steps", []):
            candidates = step.get("scriptures", [])
            if source_lines is not None:
                candidates = [read for slug in candidates for read in read_aloud(slug, source_lines, passage=passage)]
            for slug in candidates:
                wanted = parse_reference(slug)
                covered = any(item["slug_book"] == wanted["slug_book"] and item["chapter"] == wanted["chapter"]
                              and item["start"] <= wanted["start"] and wanted["end"] <= item["end"] for item in shown)
                note = f"{claim['claim_id']} 的 Evidence 引用 {slug}，投影片沒有顯示"
                if not covered and note not in gaps:
                    gaps.append(note)
    return gaps


def argument_index(context: dict) -> dict[str, dict]:
    """Claim 與每一步論證在本堂的位置：id → 他開始講它的那一句、它屬於哪些 Claim、說的是什麼。"""
    index: dict[str, dict] = {}
    for claim in context.get("claims", []):
        cid = claim["claim_id"]
        said = [s for s in [claim.get("stated", "")] if s.startswith("S")]
        said += [s for passage in claim.get("own_passages", []) for s in passage["sentences"]]
        if said:
            index.setdefault(cid, {"first": min(said), "last": max(said), "claims": set(),
                                   "statement": claim["statement"]})["claims"].add(cid)
        for step in claim.get("evidence_steps", []):
            sentences = sorted(s for anchor in step.get("anchors", []) for s in anchor.get("sentences", []))
            if not step.get("evidence_step_id") or not sentences:
                continue  # 結構檔提綱的步驟沒有 id：只按 Claim 對齊
            entry = index.setdefault(step["evidence_step_id"], {"first": sentences[0], "last": sentences[-1],
                                                                "claims": set(), "statement": step["statement"]})
            entry["claims"].add(cid)
    # 一個 Claim 的範圍：從它最早一步到它最晚一步或 stated 句。
    for cid in {c for e in index.values() for c in e["claims"]}:
        if cid in index:
            spans = [e for e in index.values() if cid in e["claims"]]
            index[cid]["first"] = min(e["first"] for e in spans)
            index[cid]["last"] = max(e["last"] for e in spans)
    return index


def retime(raw_slides: list[dict], context: dict) -> list[dict]:
    """要點照出現的句子排序（模型綁的是他講出這條要點的那一句）。"""
    return [{**slide, "points": sorted(slide.get("points") or [],
                                       key=lambda p: str(p.get("sentence") or "") if isinstance(p, dict) else "")}
            for slide in raw_slides]


def point_spans(context: dict, slides: list[dict]) -> list[str]:
    """有 source 的要點，綁的句子要落在那一步（或那個 Claim）在逐字稿的範圍裡。

    負責人 2026-11-16：他講要點 A、投影片卻亮著 B 不行。一步論證的 SourceFragment 常
    從引子開始，要點講的多是後面的結論，所以時間不能取這一步的第一句；由模型指出
    他講出這條要點的那一句，程序只核對它沒有跑出這一步。"""
    index = argument_index(context)
    problems = []
    for slide in slides:
        for point in slide["points"]:
            entry = index.get(point.get("source") or "")
            if entry and not entry["first"] <= point["sentence"] <= entry["last"]:
                problems.append(f"{slide['title']}: 要點「{point['text']}」綁在 {point['sentence']}，"
                                f"但它的 source {point['source']} 在 {entry['first']}–{entry['last']}")
    return problems


def argument_alignment(context: dict, slides: list[dict]) -> list[str]:
    """他講某一步論證時，畫面上那一張要有一條要點屬於同一個 Claim（負責人 2026-11-16：
    教授論證的是 X，投影片顯示不相關的，不行）。另報不認得的 source。"""
    index = argument_index(context)
    problems = []
    for slide in slides:
        for point in slide["points"]:
            if point.get("source") and point["source"] not in index:
                problems.append(f"{slide['title']}: 要點「{point['text']}」的 source {point['source']} 不是本堂的 Claim 或論證步驟")
    steps_of: dict[str, set] = {}
    for rid, entry in index.items():
        for cid in entry["claims"]:
            steps_of.setdefault(cid, set()).add(rid)
    reported = set()
    for rid, entry in sorted(index.items(), key=lambda kv: (kv[1]["first"], kv[0])):
        slide = next((s for s in slides if s["first"] <= entry["first"] <= s["last"]), None)
        if slide is None or entry["first"] in reported:
            continue
        related = {rid} | entry["claims"] | {r for cid in entry["claims"] for r in steps_of.get(cid, ())}
        if not any(point.get("source") in related for point in slide["points"]):
            problem = (f"{slide['title']}（{entry['first']}）: 他在講「{entry['statement'][:40]}」（{rid}），"
                       f"這一張沒有要點屬於它的 Claim（{'、'.join(sorted(entry['claims']))}）")
            problems.append(problem)
            reported.add(entry["first"])
    return problems


#: 他已講進另一步論證，畫面上的要點還停在別的 Claim，這麼多句以上才報（一兩句的過門不算）。
STALE_SENTENCES = 3


def stale_points(context: dict, slides: list[dict], lines: list[dict]) -> list[str]:
    """他講進一步新的論證，目前那條要點卻還是別處的（負責人 2026-11-16：他講 A，畫面亮著 B）。

    逐句看：這一句落在哪些步驟的 SourceFragment 裡；畫面此刻最新的要點，source 要是其中
    一步或它們的 Claim。沒有 source 的要點（故事、插話）和不屬任何一步的句子不查。
    另查同一張兩條要點綁同一句：那等於兩條一起亮。"""
    cover: dict[str, set] = {}
    for claim in context.get("claims", []):
        for passage in claim.get("own_passages", []):
            for sentence in passage["sentences"]:
                cover.setdefault(sentence, set()).add(claim["claim_id"])
        for step in claim.get("evidence_steps", []):
            if not step.get("evidence_step_id"):
                continue
            for anchor in step.get("anchors", []):
                for sentence in anchor.get("sentences", []):
                    cover.setdefault(sentence, set()).update({step["evidence_step_id"], claim["claim_id"]})
    problems, run = [], None

    def close():
        if run and run["count"] >= STALE_SENTENCES:
            problems.append(f"{run['slide']}（{run['first']}–{run['last']}）: 他已講到另一步（{'、'.join(sorted(run['steps']))}），"
                            f"畫面還停在要點「{run['text']}」；那一步的第一條要點要在 {run['first']} 或更早出現")

    for line in lines:
        sid = line["id"]
        slide = next((s for s in reversed(slides) if s["first"] <= sid), None)
        shown = [p for p in (slide or {}).get("points", []) if p["sentence"] <= sid]
        source = shown[-1].get("source") if shown else ""
        if not source or sid not in cover or source in cover[sid]:
            close(); run = None
            continue
        if run and run["text"] == shown[-1]["text"]:
            run.update(last=sid, count=run["count"] + 1)
        else:
            close()
            run = {"slide": slide["title"], "first": sid, "last": sid, "count": 1, "text": shown[-1]["text"], "steps": cover[sid]}
    close()
    for slide in slides:
        bound = [p["sentence"] for p in slide["points"]]
        for sentence in sorted({s for s in bound if bound.count(s) > 1}):
            problems.append(f"{slide['title']}: 有兩條要點都綁在 {sentence}，會一起亮；每條綁他講出它的那一句")
    return problems


def compile_package_context(passage: str, lecture: str, *, data_base_dir: Path, runs: list[Path]) -> dict:
    """Outline context from Claim-creation runs (files): each run is one argument group.

    The run's first Claim is the group's cover and the rest sit under it. Each Claim
    carries the records that support it, directly or through other records, and the
    views it refutes; their SourceFragments are placed on this lecture's retained
    sentences. Reads files only."""
    path = chapter_exposition.record_path(data_base_dir, passage, lecture)
    decisions = json.loads(path.read_text())
    text, spans = retained_spans(decisions)
    spoken = [line["text"] for line in chapter_slides.sentences(chapter_slides.kept_pieces(decisions))]
    groups, claims, excluded, sources = [], [], [], []
    for number, run in enumerate(runs, 1):
        run = Path(run)
        package = json.loads((run / "candidate.json").read_text())
        if not package.get("complete"):
            raise ValueError(f"{run}: creation candidate is not complete")
        candidates = json.loads((run / "claim-candidates.json").read_text())
        title = json.loads((run / "groups.json").read_text())[0]["title"]
        sources.append({"run": str(run), "candidate_sha256": digest(package)})
        gid = f"G{number:02d}"
        groups.append({"group_id": gid, "title": title})
        fragments = {f["fragment_id"]: f for f in package["source_fragments"]}
        records = {}
        for step in package["evidence_steps"]:
            records[step["evidence_step_id"]] = (step["statement"], step["step_type"], step["source_fragment_ids"],
                                                 step.get("scripture_refs") or [])
        for row in package["observations"]:
            records[row["observation_id"]] = (row["statement"], row["observation_type"], row["source_fragment_ids"],
                                              row.get("scripture_refs") or [])
        for row in package["position_nodes"]:
            records[row["position_id"]] = (row["title"], "opposed_position", row["source_fragment_ids"], [])
        support = {}
        for rel in package["knowledge_relations"]:
            if rel["relation_type"] == "supports":
                support.setdefault(rel["to_id"], []).append(rel["from_id"])
        cover = None
        for claim in package["claims"]:
            short = claim["claim_id"].rsplit("-", 1)[1]
            cid = f"{gid}-{short}"
            cover = cover or cid
            behind, queue = [], list(claim["evidence_step_ids"]) + support.get(claim["claim_id"], [])
            while queue:
                rid = queue.pop(0)
                if rid in records and rid not in behind:
                    behind.append(rid)
                    queue += support.get(rid, [])
            evidence = []
            for rid in behind + claim["opposed_position_ids"]:
                statement, kind, fids, refs = records[rid]
                anchors = []
                for fid in fids:
                    quote = fragments[fid]["verbatim_excerpt"]
                    ids = locate(text, spans, quote)
                    if ids is None:
                        excluded.append({"claim_id": cid, "object_id": fid, "reason": "excerpt missing or ambiguous"})
                    elif ids:
                        anchors.append({"fragment_id": fid, "quote": quote, "sentences": ids})
                if anchors:
                    entry = {"evidence_step_id": rid, "statement": statement, "step_type": kind, "anchors": anchors}
                    scriptures, unread = evidence_scriptures(refs, spoken, passage=passage)
                    if scriptures:
                        entry["scriptures"] = scriptures
                    excluded += [{"claim_id": cid, "object_id": rid, "reason": f"scripture not checkable: {ref}"}
                                 for ref in unread]
                    evidence.append(entry)
            stated_quote = candidates[short]["conclusion_anchor"]["verbatim_excerpt"]
            stated = locate(text, spans, stated_quote) or []
            # 他直接講這條 Claim 的地方：Claim 自己的 SourceFragment，不屬於它任何一筆 Evidence。
            # 他第一次定義謙卑（S0195–S0199）就只在這裡，沒有它畫面那幾句無從顯示。
            borrowed = {fid for rid in behind + claim["opposed_position_ids"] for fid in records[rid][2]}
            own = []
            for anchor in (a for o in claim.get("occurrences", []) for a in o["anchors"]):
                fid = anchor["source_fragment_id"]
                if fid in borrowed or fid not in fragments or any(p["fragment_id"] == fid for p in own):
                    continue
                ids = locate(text, spans, fragments[fid]["verbatim_excerpt"])
                if ids:
                    own.append({"fragment_id": fid, "quote": fragments[fid]["verbatim_excerpt"], "sentences": ids})
            claims.append({"claim_id": cid, "group_id": gid, "statement": claim["title"],
                           "parent": None if cid == cover else cover,
                           "stated": stated[0] if stated else "本堂未保留",
                           "own_passages": sorted(own, key=lambda p: p["sentences"][0]), "evidence_steps": evidence})
    context = {"schema_version": OUTLINE_SCHEMA, "passage": passage, "lecture": lecture,
               "decisions_sha256": chapter_exposition.file_sha256(path),
               "transcript_sha256": decisions.get("transcript_sha256"),
               "creation_runs": sources, "groups": groups, "claims": claims, "excluded": excluded}
    context["claim_source_ranges"] = claim_source_ranges(context, decisions)
    return context


INSTANCE_DEAD = ("superseded", "retired", "rejected")


def instance_records(conn, change_set_id: str) -> tuple[dict, dict]:
    """The live records one argument-layer ChangeSet wrote, by collection, and its metadata.

    A record keeps belonging to the lecture that wrote it after a correction gives it a
    later revision: this returns its current payload. A retired record is not returned."""
    row = conn.execute("SELECT metadata FROM wang_knowledge.change_sets WHERE change_set_id=%s", (change_set_id,)).fetchone()
    if not row:
        raise ValueError(f"no ChangeSet {change_set_id} in the instance")
    records: dict = {}
    for collection, payload, status in conn.execute(
            """SELECT o.collection, o.payload, o.review_status FROM wang_knowledge.object_versions v
               JOIN wang_knowledge.objects o USING (collection, object_id)
               WHERE v.change_set_id=%s AND v.revision=1 AND o.retired_at IS NULL
               ORDER BY o.collection, o.object_id""", (change_set_id,)).fetchall():
        if status not in INSTANCE_DEAD:
            records.setdefault(collection, []).append(payload)
    return records, row[0]


def compile_instance_context(passage: str, lecture: str, *, data_base_dir: Path, conn, change_set_id: str) -> dict:
    """Outline context read from the new knowledge instance (rebuild design §六).

    Claims, their supporting records, SourceFragments, scripture and refuted views all
    come from the instance. Only the group order and titles come from the run the
    ChangeSet names: the instance has no record for an argument group. The run's
    candidate SHA must match what the ChangeSet recorded."""
    path = chapter_exposition.record_path(data_base_dir, passage, lecture)
    decisions = json.loads(path.read_text())
    text, spans = retained_spans(decisions)
    spoken = [line["text"] for line in chapter_slides.sentences(chapter_slides.kept_pieces(decisions))]
    records, meta = instance_records(conn, change_set_id)
    [document] = records["source_documents"]
    if document.get("title") != lecture:
        raise ValueError(f"ChangeSet {change_set_id} holds {document.get('title')}, not {lecture}")
    candidate = Path(meta["candidate_path"])
    if hashlib.sha256(candidate.read_bytes()).hexdigest() != meta["candidate_sha256"]:
        raise ValueError("the run's candidate changed since it was written to the instance")
    result = json.loads((candidate.parent / "creation-result.json").read_text())
    fragments = {f["fragment_id"]: f for f in records.get("source_fragments", [])}
    by_id = {}
    for step in records.get("evidence_steps", []):
        by_id[step["evidence_step_id"]] = (step["statement"], step["step_type"], step["source_fragment_ids"], step.get("scripture_refs") or [])
    for row in records.get("observations", []):
        by_id[row["observation_id"]] = (row["statement"], row["observation_type"], row["source_fragment_ids"], row.get("scripture_refs") or [])
    for row in records.get("position_nodes", []):
        by_id[row["position_id"]] = (row["title"], "opposed_position", row["source_fragment_ids"], [])
    claims_by_short = {c["claim_id"].rsplit("-", 1)[1]: c for c in records.get("claims", [])}
    support = {}
    for rel in records.get("knowledge_relations", []):
        if rel["relation_type"] == "supports":
            support.setdefault(rel["to_id"], []).append(rel["from_id"])
    from backend.pipeline.argument_diagram_check import diagrams_for
    diagrams = diagrams_for(change_set_id, Path(data_base_dir))
    groups, claims, excluded = [], [], []
    for number, (gid_run, shorts) in enumerate(sorted(result["group_claim_ids"].items()), 1):
        shorts = [s for s in shorts if s in claims_by_short]  # a Claim the owner ruled out is gone from the instance
        if not shorts:
            continue
        gid = f"G{len(groups) + 1:02d}"
        groups.append({"group_id": gid, "title": result["groups"][number - 1]["title"]})
        cover = None
        for short in shorts:
            claim = claims_by_short[short]
            cid = f"{gid}-{short}"
            cover = cover or cid
            behind, queue = [], list(claim["evidence_step_ids"]) + support.get(claim["claim_id"], [])
            while queue:
                rid = queue.pop(0)
                if rid in by_id and rid not in behind:
                    behind.append(rid)
                    queue += support.get(rid, [])
            evidence = []
            for rid in behind + list(claim.get("opposed_position_ids") or []):
                statement, kind, fids, refs = by_id[rid]
                anchors = []
                for fid in fids:
                    quote = fragments[fid]["verbatim_excerpt"]
                    ids = locate(text, spans, quote)
                    if ids is None:
                        excluded.append({"claim_id": cid, "object_id": fid, "reason": "excerpt missing or ambiguous"})
                    elif ids:
                        anchors.append({"fragment_id": fid, "quote": quote, "sentences": ids})
                if anchors:
                    entry = {"evidence_step_id": rid, "statement": statement, "step_type": kind, "anchors": anchors}
                    scriptures, unread = evidence_scriptures(refs, spoken, passage=passage)
                    if scriptures:
                        entry["scriptures"] = scriptures
                    excluded += [{"claim_id": cid, "object_id": rid, "reason": f"scripture not checkable: {ref}"} for ref in unread]
                    evidence.append(entry)
            # His own words for the Claim: an occurrence anchor that is not a supporting record.
            own = [a["source_fragment_id"] for o in claim.get("occurrences", []) for a in o.get("anchors", [])
                   if not a.get("evidence_id") and not a.get("observation_id") and a.get("source_fragment_id") in fragments]
            stated = next((ids[0] for fid in own for ids in [locate(text, spans, fragments[fid]["verbatim_excerpt"])] if ids), None)
            if not stated and not evidence:
                # Nothing of it is in this chapter's retained sentences (Acts 15, the Q&A): not this page's outline.
                excluded.append({"claim_id": cid, "object_id": claim["claim_id"], "reason": "not in this chapter's retained sentences"})
                continue
            entry = {"claim_id": cid, "group_id": gid, "statement": claim["statement"],
                     "parent": None if cid == cover else cover,
                     "stated": stated or "本堂未保留", "evidence_steps": evidence}
            if claim["claim_id"] in diagrams:
                # His own diagram of this argument, from another sermon (owner 2026-11-17).
                entry["diagrams"] = diagrams[claim["claim_id"]]
            claims.append(entry)
    used = {c["group_id"] for c in claims}
    groups = [g for g in groups if g["group_id"] in used]
    context = {"schema_version": OUTLINE_SCHEMA, "passage": passage, "lecture": lecture,
               "decisions_sha256": chapter_exposition.file_sha256(path),
               "transcript_sha256": decisions.get("transcript_sha256"),
               "instance": {"change_set_id": change_set_id, "candidate_sha256": meta["candidate_sha256"]},
               "groups": groups, "claims": claims, "excluded": excluded}
    context["claim_source_ranges"] = claim_source_ranges(context, decisions)
    return context


def review_contract(context: dict, slides: list[dict]) -> tuple[str, dict]:
    from copy import deepcopy
    schema = deepcopy(chapter_slides.REVIEW_SCHEMA)
    schema["properties"]["claim_checks"] = {"type": "array", "items": {
        "type": "object", "properties": {
            "claim_id": {"type": "string"},
            "status": {"type": "string", "enum": ["covered", "missing", "not_supported", "contradicts"]},
            "slides": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"}},
        "required": ["claim_id", "status", "slides", "reason"], "additionalProperties": False}}
    schema["required"].append("claim_checks")
    instruction = """\n另逐項回查提示包 claims 中的每一條（不含 excluded），回傳 claim_checks。
covered：保留原文支持且投影片已表達重要結論、理由和限定，slides 列投影片 first。
missing：保留原文支持但漏了重要內容，必須同時列一條「完整」finding 並標 blocking。
not_supported：這條提示不受保留原文支持，不得補進幻燈；reason 說明原文依據。
contradicts：投影片的論證與這條 Claim 的 evidence_steps 相違——例如把 opposed_position（他反駁的看法）寫成他的主張、把理由和結論倒過來、或寫出與某步證據相反的推論；必須同時列一條「忠實」finding 並標 blocking，reason 指出是哪一步。
判 covered 之前先確認沒有這種相違。
每條恰好一次，不因 Claim 已經審核就信它。不要為逐條覆蓋重複要點、硬加標題或改講課次序。
"""
    return instruction, schema


def check_review(answer: dict, context: dict, slides: list[dict]) -> None:
    expected = {c["claim_id"] for c in context["claims"]}
    checks = answer.get("claim_checks", [])
    ids = [c.get("claim_id") for c in checks]
    if len(ids) != len(expected) or set(ids) != expected:
        raise ValueError("Review must check every supplied Claim exactly once")
    firsts = {s["first"] for s in slides}
    for check in checks:
        if check.get("status") not in {"covered", "missing", "not_supported", "contradicts"} or not check.get("reason"):
            raise ValueError("Invalid Claim disposition")
        if not set(check.get("slides", [])) <= firsts:
            raise ValueError("Claim check references unknown slide")
        if check["status"] == "covered" and not check.get("slides"):
            raise ValueError("Covered Claim needs a slide")
        if check["status"] == "missing" and (not answer.get("blocking") or not any(
            f.get("kind") == "完整" for f in answer.get("findings", [])
        )):
            raise ValueError("Missing Claim requires a blocking completeness finding")
        if check["status"] == "contradicts" and (not answer.get("blocking") or not any(
            f.get("kind") == "忠實" for f in answer.get("findings", [])
        )):
            raise ValueError("Contradicted Evidence requires a blocking fidelity finding")


def saved_context(record: dict, decisions: dict, decisions_sha: str) -> dict | None:
    context = record.get("claim_context")
    if context is not None:
        if digest(context) != record.get("claim_context_sha256"):
            raise ValueError("Saved Claim context changed")
        validate_context(context, record["passage"], record["lecture"], decisions, decisions_sha)
    return context

def main():
    from backend.api.config import DATA_BASE_PATH
    from backend.api.canonical_repository.postgres_store import PostgresKnowledgeStore
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("passage")
    parser.add_argument("--lecture", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-unusable-evidence", action="store_true",
                        help="Exclude and record unverifiable evidence instead of aborting")
    parser.add_argument("--structure", type=Path, help="講課提綱 Claim 結構檔；用它代替知識庫")
    parser.add_argument("--lecture-number", type=int, help="結構檔段落編號 Ln- 的 n")
    parser.add_argument("--data-dir", type=Path, help="讀選材紀錄的資料目錄")
    parser.add_argument("--creation-run", type=Path, action="append", default=[],
                        help="Claim 建立的一組輸出資料夾（含 candidate.json）；按論證次序給，每組一次")
    parser.add_argument("--instance-change-set", help="從新知識庫實例讀這一講的論證層（ARGUMENT_DATABASE_URL）")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; reuse the frozen context")
    if args.instance_change_set:
        import psycopg
        from dotenv import load_dotenv
        from backend.pipeline.argument_review import database_url
        load_dotenv(".env")
        with psycopg.connect(database_url()) as conn:
            context = compile_instance_context(args.passage, args.lecture, data_base_dir=args.data_dir or Path(DATA_BASE_PATH),
                                               conn=conn, change_set_id=args.instance_change_set)
    elif args.creation_run:
        context = compile_package_context(args.passage, args.lecture, data_base_dir=args.data_dir or Path(DATA_BASE_PATH),
                                          runs=args.creation_run)
    elif args.structure:
        if not args.lecture_number:
            parser.error("--structure needs --lecture-number")
        context = compile_outline_context(args.passage, args.lecture, args.lecture_number,
                                          data_base_dir=args.data_dir or Path(DATA_BASE_PATH),
                                          structure_path=args.structure)
    else:
        context = compile_context(args.passage, args.lecture, data_base_dir=Path(DATA_BASE_PATH),
                                  store=PostgresKnowledgeStore(), allow_unusable_evidence=args.allow_unusable_evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(context, ensure_ascii=False, indent=2))
    print(json.dumps({"claims": len(context["claims"]), "excluded": len(context["excluded"]),
                      "sha256": digest(context), "file": str(args.output)}, ensure_ascii=False))


class RecordedClient:
    """Save exact requests and raw CLI replies; replay never starts an unknown call."""

    def __init__(self, client, directory: Path):
        self.client, self.directory = client, directory
        self.model = client.model

    def generate_json(self, system, prompt, schema):
        request = {"system": system, "user": prompt, "schema": schema, "model": self.model}
        root = self.directory / digest(request)
        root.mkdir(parents=True, exist_ok=True)
        target = root / "request.json"
        if target.exists():
            if json.loads(target.read_text()) != request:
                raise ValueError("Saved request changed")
        else:
            target.write_text(json.dumps(request, ensure_ascii=False, indent=2))
        self.client.call_artifacts = root
        answer = self.client.generate_json(system, prompt, schema)
        (root / "answer.json").write_text(json.dumps(answer, ensure_ascii=False, indent=2))
        return answer


if __name__ == "__main__":
    main()
