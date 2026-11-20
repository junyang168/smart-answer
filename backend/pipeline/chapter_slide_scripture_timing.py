"""Locate Scripture readings after the sub-unit writer has selected references."""

import re

from backend.api.scripture import parse_reference, slide_reference_slugs, spoken_references
from backend.pipeline.chapter_slide_eval import Bible, _bigrams, reads_scripture
from backend.pipeline.chapter_slides import scripture_covers

_SPOKEN_FILLER = re.compile(r"[啊呀呢][，,]\s*(?=第?[一二三四五六七八九十百零兩两0-9]+章)")
_QUOTED_WORDS = re.compile(r"[「『]([^」』]+)[」』]?")


def _related(left, right):
    a, b = parse_reference(left), parse_reference(right)
    return scripture_covers(a, b) or scripture_covers(b, a)


def _readings_for_slide(slide, source, wanted, confirmed, bible, ask):
    known = wanted | {slug for _, slug in confirmed}
    placements = set(confirmed)
    found = {slug for _, slug in confirmed}
    texts = {slug: _scripture_text(slug, bible) for slug in known}
    matches = {}
    explicit = {}
    previous = set()
    for sid, text in source.items():
        said = [slug for _, slug in spoken_references(_SPOKEN_FILLER.sub("", text))]
        explicit[sid] = {slug for slug in known if any(_related(slug, ref) for ref in said)}
        quoted = _QUOTED_WORDS.findall(text)
        matches[sid] = {slug for slug in known if reads_scripture(text, slug, bible)
                        or any(reads_scripture(quote, slug, bible) for quote in quoted)}
        if explicit[sid]:
            matches[sid] &= explicit[sid]
        elif len(matches[sid]) > 1:
            scores = {slug: _overlap(text, texts[slug]) for slug in matches[sid]}
            best = {slug for slug, score in scores.items() if score == max(scores.values())}
            matches[sid] = best if len(best) == 1 else set()
        for slug in matches[sid] - previous:
            placements.add((sid, slug))
            found.add(slug)
        previous = matches[sid]

    resolved = [parse_reference(slug) for slug in found]
    pending = {slug for slug in wanted if not any(
        scripture_covers(ref, parse_reference(slug)) for ref in resolved)}
    questions = {
        f"{slug}:{sid}": {"type": "choice",
            "instructions": f"只看本張上下文。{sid} 是否是這一次開始讀出或近乎逐字引述 {slug} 的位置？"
                            "用經文原句辨別相似的平行經文。直接引述一節的部分也算，"
                            "包括為解釋或糾正譯法而先念出該節可辨認的詞句。"
                            "若跨句引用，可以讀相鄰句。只報章節、一般轉述、繼續同一處或誦讀另一節均不算開始。",
            "criteria": {"start": "開始讀這一處經文", "other": "沒有開始讀這一處", "uncertain": "不能確定"}}
        for slug in sorted(pending) for sid in source
        if (not explicit[sid] or slug in explicit[sid]) and
           (not matches[sid] or any(_related(slug, ref) for ref in matches[sid]))
    }
    answers = {}
    if questions:
        from backend.pipeline.chapter_slide_point_timing import _ask_batches
        from backend.pipeline.claim_flash_common import JEV_BYTES
        from backend.pipeline.evidence_refresh import MODEL

        state = {"原文": source, "候選經文": {slug: texts[slug] for slug in sorted(pending)}}
        answers = _ask_batches(state, questions, ask, MODEL, JEV_BYTES)
    problems = []
    for slug in sorted(pending):
        candidates = [sid for sid in source if answers.get(f"{slug}:{sid}") == "start"]
        if not candidates:
            problems.append(f"{slide['title']}：無法定位 {slug} 的實際朗讀")
            continue
        # One candidate is enough to place the first reading. Playback's
        # existing re-read detection handles repeats of that verse.
        sid = max(candidates, key=lambda item: (slug in explicit[item], _overlap(source[item], texts[slug])))
        placements.add((sid, slug))
    return placements, problems


def locate_readings(raw, lines, verified, ask, *, bible=None):
    """Keep recorded readings and find distinct occurrences inside each slide."""
    bible = bible or Bible()
    positions = {line["id"]: i for i, line in enumerate(lines)}
    starts = [slide.get("first") for slide in raw]
    if any(sid not in positions for sid in starts) or starts != sorted(set(starts)):
        return [], ["Invalid slide boundaries for Scripture timing"]
    placements = {(item["sentence"], item["slug"]) for item in verified}
    problems = []
    for i, slide in enumerate(raw):
        first = positions[starts[i]]
        last = positions[starts[i+1]] if i+1 < len(starts) else len(lines)
        source = {line["id"]: line["text"] for line in lines[first:last]}
        wanted = set()
        for item in slide.get("scriptures", []):
            refs = slide_reference_slugs(item.get("ref", ""))
            if not refs:
                problems.append(f"{slide['title']}：經文出處讀不出來（{item.get('ref')}）")
            wanted.update(refs)
        confirmed = {(sid, slug) for sid, slug in placements if sid in source}
        readings, unresolved = _readings_for_slide(slide, source, wanted, confirmed, bible, ask)
        placements.update(readings)
        problems.extend(unresolved)
    return [{"sentence": sid, "slug": slug} for sid, slug in sorted(placements)], problems


def _overlap(text, verse):
    verse_pairs = _bigrams(verse)
    quotes = _QUOTED_WORDS.findall(text)
    return max(len(_bigrams(part) & verse_pairs) for part in (text, *quotes))


def _scripture_text(slug, bible):
    ref = parse_reference(slug)
    chapter = bible.chapter(ref["slug_book"], ref["chapter"])
    return " ".join(chapter.get(str(n), "") for n in range(ref["start"], ref["end"] + 1))
