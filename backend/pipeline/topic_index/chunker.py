from __future__ import annotations

import re
from typing import List, Tuple

# Roughly 20 000 characters (~6 000–7 000 CJK tokens) before we split.
# A single ## section is never split mid-way.
_CHAR_LIMIT = 20_000

_H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def _extract_h2_label(heading_text: str) -> str:
    """Return a clean section label from a ## heading line."""
    text = heading_text.strip()
    # Strip leading ordinal like "一、" or "1." so we get the bare label
    text = re.sub(r"^[一二三四五六七八九十百\d]+[、.．]\s*", "", text)
    return text.strip() or heading_text.strip()


def split_into_groups(markdown: str) -> List[Tuple[List[str], str]]:
    """
    Split a manuscript into (section_labels, text) groups that each stay
    under _CHAR_LIMIT characters.  Splitting only happens at ## boundaries.

    Returns a single group when the whole manuscript is short enough.
    Each group carries the ## heading labels it contains.
    """
    if len(markdown) <= _CHAR_LIMIT:
        labels = [_extract_h2_label(m.group(1)) for m in _H2_RE.finditer(markdown)]
        return [(labels or ["全文"], markdown)]

    # Collect (heading_label, start_offset) for every ## heading.
    boundaries: List[Tuple[str, int]] = []
    for match in _H2_RE.finditer(markdown):
        boundaries.append((_extract_h2_label(match.group(1)), match.start()))

    if not boundaries:
        # No ## headings — can't split sensibly, return as one chunk.
        return [(["全文"], markdown)]

    # Build section slices: text between consecutive ## headings.
    sections: List[Tuple[str, str]] = []
    for idx, (label, start) in enumerate(boundaries):
        end = boundaries[idx + 1][1] if idx + 1 < len(boundaries) else len(markdown)
        sections.append((label, markdown[start:end]))

    # Greedily pack sections into groups under the char limit.
    groups: List[Tuple[List[str], str]] = []
    current_labels: List[str] = []
    current_parts: List[str] = []
    current_size = 0

    for label, text in sections:
        if current_parts and current_size + len(text) > _CHAR_LIMIT:
            groups.append((current_labels, "".join(current_parts)))
            current_labels = []
            current_parts = []
            current_size = 0
        current_labels.append(label)
        current_parts.append(text)
        current_size += len(text)

    if current_parts:
        groups.append((current_labels, "".join(current_parts)))

    return groups
