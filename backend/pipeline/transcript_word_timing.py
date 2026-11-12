"""Locate Editor paragraph edges in saved Gemini word timestamps."""

from bisect import bisect_right
from collections import defaultdict
from itertools import accumulate
import unicodedata

from opencc import OpenCC


def locate_paragraph_edges(paragraphs, turns, words):
    converter = OpenCC("s2t")

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
