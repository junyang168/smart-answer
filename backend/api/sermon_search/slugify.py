from __future__ import annotations

import re

# Keep ASCII alphanumerics plus CJK, Greek and Hebrew letters; everything else
# (punctuation, whitespace, ASCII symbols) becomes a single hyphen.
#
# IMPORTANT: this rule must stay byte-for-byte equivalent to the TypeScript
# `slugifyHeadingAnchor` in
#   web/src/app/components/full-article/heading-anchor.ts
# because the manuscript renderer derives heading ids with the TS version while
# the topic API precomputes the same ids with this one. If you change one, change
# both.
_KEEP = (
    r"a-z0-9"
    r"一-鿿"   # CJK Unified Ideographs
    r"Ͱ-Ͽ"   # Greek
    r"ἀ-῿"   # Greek Extended
    r"֐-׿"   # Hebrew
)
_NON_KEEP_RE = re.compile(rf"[^{_KEEP}]+")
_TRIM_RE = re.compile(r"^-+|-+$")


def slugify_heading(value: str) -> str:
    """Stable anchor slug for a manuscript heading (CJK-safe)."""
    text = (value or "").strip().lower()
    text = _NON_KEEP_RE.sub("-", text)
    text = _TRIM_RE.sub("", text)
    return text
