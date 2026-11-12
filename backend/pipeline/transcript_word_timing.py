"""Locate Editor paragraph edges in saved Gemini word timestamps."""

from bisect import bisect_right
from collections import defaultdict
from itertools import accumulate
import unicodedata

from opencc import OpenCC


def locate_paragraph_edges(paragraphs, turns, words):
    converter = OpenCC("t2s")

    def normalized(text):
        return "".join(c for c in unicodedata.normalize("NFKC", converter.convert(text)).casefold()
                       if c.isalnum())

    by_speaker = defaultdict(list)
    for word in words:
        by_speaker[word["speaker"]].append((word, normalized(word["text"])))
    offsets = [0, *accumulate(len(turn["text"]) for turn in turns)]

    def locate(offset, opening):
        index = bisect_right(offsets, offset if opening else offset - 1) - 1
        turn = turns[index]
        split = offset - offsets[index]
        raw = turn["text"]
        before, after = normalized(raw[:split]), normalized(raw[split:])
        target = before + after
        if not (after if opening else before):
            return None
        pool = [(word, value) for word, value in by_speaker[turn["speaker"]] if value
                and turn["start"] - 1 <= word["start"] <= turn["end"] + 1]
        tokens = "".join(value for _, value in pool)
        word_offsets = [0, *accumulate(len(value) for _, value in pool)]
        endpoints = {
            pos: word for pos, (word, _) in zip(word_offsets if opening else word_offsets[1:], pool)
        }

        def find(anchor, delta):
            candidates = []
            pos = tokens.find(anchor)
            while pos >= 0:
                word = endpoints.get(pos + delta)
                if word is not None:
                    valid = not word.get("timing_review") and 0 <= word["start"] <= word["end"]
                    candidates.append(word["start" if opening else "end"] if valid else None)
                pos = tokens.find(anchor, pos + 1)
            return candidates

        # The whole turn is preferred. If Flash changed it, require an exact
        # local phrase around the chosen edge, still within the original turn.
        whole = find(target, len(before))
        if len(whole) == 1:
            return whole[0]
        for width in (10, 6, 4):
            anchor = after[:width] if opening else before[-width:]
            if len(anchor) < 4:
                break
            matches = find(anchor, 0 if opening else len(anchor))
            if len(matches) == 1:
                return matches[0]
        return None

    return [
        {"start": locate(p["start"], True), "end": locate(p["end"], False)}
        for p in paragraphs
    ]


def locate_starts_from_base(paragraphs, turns, words, base_turns):
    """Project a refined paragraph start through its original Flash input turn."""
    from difflib import SequenceMatcher

    converter = OpenCC("t2s")

    def norm(text):
        return "".join(c for c in unicodedata.normalize("NFKC", converter.convert(text)).casefold()
                       if c.isalnum())

    by_speaker = defaultdict(list)
    for word in words:
        text = norm(word["text"])
        if text:
            by_speaker[word["speaker"]].append((word, text))
    base = {turn["id"]: turn for turn in base_turns}
    offsets = [0, *accumulate(len(turn["text"]) for turn in turns)]
    results = []
    for paragraph in paragraphs:
        index = bisect_right(offsets, paragraph["start"]) - 1
        refined = turns[index]
        original = base.get(refined["id"])
        split = paragraph["start"] - offsets[index]
        before = norm(refined["text"][:split])
        after = norm(refined["text"][split:])
        source = norm(original["text"]) if original else ""
        if not source or not after:
            results.append(None)
            continue
        pool = [(word, text) for word, text in by_speaker[original["speaker"]]
                if original["start"] - .15 <= word["start"] <= original["end"] + .15]
        joined = "".join(text for _, text in pool)
        position = joined.find(source)
        if position == -1 or joined.find(source, position + 1) != -1:
            results.append(None)
            continue

        offset = len(before)
        mapped = None
        method = None
        matcher = SequenceMatcher(None, source, before + after, autojunk=False)
        for tag, a, b, c, d in matcher.get_opcodes():
            if not c <= offset < d:
                continue
            if tag == "equal":
                mapped, method = a + offset - c, "original_word"
            elif tag == "replace" and b - a == d - c and d - c <= 4 and matcher.find_longest_match().size >= 2:
                mapped, method = a + offset - c, "same_length_correction"
            break
        if mapped is None:
            results.append(None)
            continue
        token_starts = [0, *accumulate(len(text) for _, text in pool)]
        word = pool[bisect_right(token_starts, position + mapped) - 1][0]
        if word.get("timing_review") or not 0 <= word["start"] <= word["end"]:
            results.append(None)
            continue
        results.append({"start": word["start"], "method": method})
    return results
