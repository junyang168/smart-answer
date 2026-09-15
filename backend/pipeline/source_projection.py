"""Separate professor-spoken source from editor-authored transcript structure.

Transcript JSON deliberately stores both in one ``script`` list.  Storage
co-location is not authorship: subtitle and comment rows are editorial data,
so they must never acquire source locators, sentence ids, evidence anchors, or
the content identity used to decide whether a source fragment is current.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SOFT_DELETION = re.compile(r"~~([^~]+?)~~", re.S)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
SVG_BLOCK_PATTERN = re.compile(r"<svg\b[^>]*>.*?</svg\s*>", re.I | re.S)
SVG_OPEN_PATTERN = re.compile(r"<svg\b", re.I)
MARKDOWN_IMAGE_PATTERN = re.compile(
    r'!\[(?P<alt>[^\]]*)\]\((?P<url>[^)\s]+)\)'
)
SVG_ELEMENT_PATTERN = re.compile(
    r"</?(?:style|defs|marker|path|line|rect|ellipse|circle|text|tspan|polygon|"
    r"polyline|g)\b[^>]*>",
    re.I,
)
HTML_COMMENT_PATTERN = re.compile(r"<!--.*?-->", re.S)
HTML_COMMENT_OPEN_PATTERN = re.compile(r"<!--")
BLOCKQUOTE_LINE_PATTERN = re.compile(r"(?m)^[ \t]*>[^\n]*(?:\n|$)")
INLINE_HEADING_LINE_PATTERN = re.compile(
    r"(?m)^[ \t]*#{1,6}[ \t]+[^\n]*(?:\n|$)"
)
EDITORIAL_ROW_TYPES = frozenset({"subtitle", "comment"})
EDITORIAL_ONLY_FIELDS = frozenset({"type", "user_id"})
PROJECTION_ONLY_FIELDS = frozenset({"_visual_source_assets"})
LOCATOR_SPACE = "spoken_body_v1"
VISUAL_RENDERER_VERSION = "svg_literal_facts_v3_cjk_white"
_UNSAFE_SVG_TAGS = frozenset(
    {
        "script",
        "foreignobject",
        "image",
        "iframe",
        "object",
        "audio",
        "video",
        "animate",
        "animatemotion",
        "animatetransform",
        "set",
    }
)


class LocatorSpaceError(ValueError):
    """A source descriptor cannot prove how its locators address the script."""


class VisualSourceAttestationError(ValueError):
    """Inline SVG lacks an exact editorial attestation as source evidence."""


@dataclass(frozen=True)
class InlineMarkupSpan:
    """A non-prose or provenance-ambiguous span inside a source-bearing row."""

    start: int
    end: int
    kind: str


@dataclass(frozen=True)
class VisualSourceBlock:
    """One professor-displayed/drawn diagram embedded in a source row.

    The raw SVG is source evidence. ``facts`` are deterministic literal
    render facts (element, attributes, text and position), never an
    interpretation of what the diagram means.
    """

    locator: str
    segment_index: str
    source_segment_index: Any
    ordinal: int
    char_start: int
    char_end: int
    raw_svg: str
    raw_sha256: str
    canonical_sha256: str | None
    facts: tuple[dict[str, Any], ...]
    parse_error: str | None = None
    source_path: str | None = None
    source_file_sha256: str | None = None
    source_url: str | None = None
    binding_kind: str = "inline_svg"

    @property
    def readable(self) -> bool:
        return self.parse_error is None

    def descriptor(self) -> dict[str, Any]:
        result = {
            "locator": self.locator,
            "segment_index": self.segment_index,
            "source_segment_index": self.source_segment_index,
            "ordinal": self.ordinal,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "raw_sha256": self.raw_sha256,
            "canonical_sha256": self.canonical_sha256,
            "renderer_version": VISUAL_RENDERER_VERSION,
            "fact_count": len(self.facts),
            "literal_facts": [dict(row) for row in self.facts],
            "raw_svg": self.raw_svg,
            "parse_status": "readable" if self.readable else "invalid",
            "parse_error": self.parse_error,
            "binding_kind": self.binding_kind,
        }
        if self.source_path is not None:
            result["source_path"] = self.source_path
        if self.source_file_sha256 is not None:
            result["source_file_sha256"] = self.source_file_sha256
        if self.source_url is not None:
            result["source_url"] = self.source_url
        return result


def validate_visual_source_attestations(
    projection: "SourceProjection",
    attestations: Any,
) -> None:
    """Require an exact locator/SHA decision for every inline visual.

    Well-formed XML proves only that an SVG is readable.  It does not prove
    that the professor displayed it rather than an editor adding it later.
    That provenance judgement therefore stays outside the source file and is
    bound here to both the body-relative locator and the raw block SHA.
    """

    declared = visual_source_attestation_map(attestations)
    actual = {block.locator: block for block in projection.visual_blocks}
    findings: list[str] = []
    for locator in sorted(set(declared) - set(actual)):
        findings.append(f"{locator} (attestation does not resolve)")
    for locator, block in actual.items():
        expected = declared.get(locator)
        if expected is None:
            findings.append(f"{locator} (visual source is not attested)")
        elif not re.fullmatch(r"[0-9a-f]{64}", expected):
            findings.append(f"{locator} (attestation SHA is invalid)")
        elif expected != block.raw_sha256:
            findings.append(f"{locator} (attestation SHA does not match source)")
        if not block.readable:
            findings.append(f"{locator} ({block.parse_error})")
    if findings:
        raise VisualSourceAttestationError(", ".join(findings))


def visual_source_attestation_map(value: Any) -> dict[str, str]:
    """Normalize batch mappings and persisted SourceDocument attestation rows."""

    if value is None:
        return {}
    if isinstance(value, Mapping):
        rows = list(value.items())
    elif isinstance(value, list):
        rows = [
            (row.get("locator"), row.get("raw_sha256"))
            for row in value
            if isinstance(row, Mapping)
        ]
        if len(rows) != len(value):
            raise VisualSourceAttestationError(
                "visual source attestations must be objects"
            )
    else:
        raise VisualSourceAttestationError(
            "visual source attestations must be an object or list"
        )
    result: dict[str, str] = {}
    for raw_locator, raw_sha256 in rows:
        locator = str(raw_locator or "")
        if not locator or locator in result:
            raise VisualSourceAttestationError(
                f"duplicate or missing visual source locator: {locator!r}"
            )
        result[locator] = str(raw_sha256 or "")
    return result


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _svg_has_external_reference(root: ET.Element) -> str | None:
    for element in root.iter():
        inline_css = " ".join(
            value
            for value in (str(element.text or ""), str(element.tail or ""))
            if value
        )
        if re.search(r"@import\b", inline_css, flags=re.I):
            return "external CSS import is not allowed"
        for target in re.findall(r"url\(([^)]+)\)", inline_css, flags=re.I):
            cleaned = target.strip().strip("'\"")
            if cleaned and not cleaned.startswith("#"):
                return "external CSS url reference is not allowed"
        for raw_name, raw_value in element.attrib.items():
            name = _local_name(raw_name).lower()
            value = str(raw_value).strip()
            if name.startswith("on"):
                return f"event handler attribute {name} is not allowed"
            if name in {"href", "src"} and value and not value.startswith("#"):
                return f"external {name} reference is not allowed"
            for target in re.findall(r"url\(([^)]+)\)", value, flags=re.I):
                cleaned = target.strip().strip("'\"")
                if cleaned and not cleaned.startswith("#"):
                    return "external CSS url reference is not allowed"
    return None


def _svg_literal_facts(root: ET.Element) -> tuple[dict[str, Any], ...]:
    """Describe every SVG element without assigning theological semantics."""

    facts: list[dict[str, Any]] = []

    def visit(element: ET.Element, path: str) -> None:
        tag = _local_name(str(element.tag))
        direct_text = str(element.text or "").strip()
        tail_text = str(element.tail or "").strip()
        facts.append(
            {
                "fact_id": f"VF{len(facts) + 1:03d}",
                "element_path": path,
                "tag": tag,
                "attributes": {
                    _local_name(str(key)): str(value)
                    for key, value in sorted(element.attrib.items())
                },
                "text": direct_text or None,
                "tail": tail_text or None,
            }
        )
        counts: dict[str, int] = {}
        for child in list(element):
            child_tag = _local_name(str(child.tag))
            counts[child_tag] = counts.get(child_tag, 0) + 1
            visit(child, f"{path}/{child_tag}[{counts[child_tag]}]")

    root_tag = _local_name(str(root.tag))
    visit(root, f"/{root_tag}[1]")
    return tuple(facts)


def _parse_visual_block(
    *,
    raw_svg: str,
    locator: str,
    segment_index: str,
    source_segment_index: Any,
    ordinal: int,
    char_start: int,
    char_end: int,
    source_path: str | None = None,
    source_file_sha256: str | None = None,
    source_url: str | None = None,
    binding_kind: str = "inline_svg",
) -> VisualSourceBlock:
    raw_sha256 = hashlib.sha256(raw_svg.encode("utf-8")).hexdigest()
    try:
        root = ET.fromstring(raw_svg)
        if _local_name(str(root.tag)).lower() != "svg":
            raise ValueError("root element is not svg")
        unsafe = sorted(
            {
                _local_name(str(element.tag)).lower()
                for element in root.iter()
                if _local_name(str(element.tag)).lower() in _UNSAFE_SVG_TAGS
            }
        )
        if unsafe:
            raise ValueError("unsafe SVG element(s): " + ", ".join(unsafe))
        external = _svg_has_external_reference(root)
        if external:
            raise ValueError(external)
        canonical = ET.canonicalize(
            xml_data=raw_svg, with_comments=False, strip_text=False
        )
        canonical_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        facts = _svg_literal_facts(root)
        error = None
    except (ET.ParseError, ValueError) as exc:
        canonical_sha256 = None
        facts = ()
        error = f"{type(exc).__name__}: {exc}"
    return VisualSourceBlock(
        locator=locator,
        segment_index=segment_index,
        source_segment_index=source_segment_index,
        ordinal=ordinal,
        char_start=char_start,
        char_end=char_end,
        raw_svg=raw_svg,
        raw_sha256=raw_sha256,
        canonical_sha256=canonical_sha256,
        facts=facts,
        parse_error=error,
        source_path=source_path,
        source_file_sha256=source_file_sha256,
        source_url=source_url,
        binding_kind=binding_kind,
    )


def visual_source_blocks(
    text: str,
    *,
    segment_index: str,
    source_segment_index: Any = None,
) -> tuple[VisualSourceBlock, ...]:
    """Return complete visual blocks and fail-closed descriptors for open SVGs."""

    value = str(text or "")
    matches = list(SVG_BLOCK_PATTERN.finditer(value))
    covered = [(match.start(), match.end()) for match in matches]
    ranges = [(match.start(), match.end()) for match in matches]
    for opener in SVG_OPEN_PATTERN.finditer(value):
        if any(left <= opener.start() < right for left, right in covered):
            continue
        ranges.append((opener.start(), len(value)))
    rows: list[VisualSourceBlock] = []
    for ordinal, (start, end) in enumerate(sorted(set(ranges)), start=1):
        locator = f"{segment_index}/V{ordinal:02d}"
        rows.append(
            _parse_visual_block(
                raw_svg=value[start:end],
                locator=locator,
                segment_index=segment_index,
                source_segment_index=source_segment_index,
                ordinal=ordinal,
                char_start=start,
                char_end=end,
            )
        )
    return tuple(rows)


def bound_visual_source_blocks(
    row: Mapping[str, Any], *, segment_index: str
) -> tuple[VisualSourceBlock, ...]:
    """Resolve SVG assets explicitly bound to links in one source row.

    The Markdown link remains part of the original notes file and therefore of
    the body identity.  The SVG bytes come from their own SHA-bound file.  This
    keeps extraction's multimodal projection reproducible without pretending
    those bytes were embedded in the notes manuscript.
    """

    text = str(row.get("text") or "")
    values = row.get("_visual_source_assets") or []
    if not isinstance(values, list):
        raise ValueError("_visual_source_assets must be a list")
    blocks: list[VisualSourceBlock] = []
    for ordinal, value in enumerate(values, start=1):
        if not isinstance(value, Mapping):
            raise ValueError("_visual_source_assets rows must be objects")
        start = value.get("char_start")
        end = value.get("char_end")
        raw_svg = str(value.get("raw_svg") or "")
        source_path = str(value.get("source_path") or "")
        source_file_sha256 = str(value.get("source_file_sha256") or "")
        source_url = str(value.get("source_url") or "")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(text)
        ):
            raise ValueError("bound visual source has an invalid Markdown span")
        if not raw_svg or not source_path or not re.fullmatch(
            r"[0-9a-f]{64}", source_file_sha256
        ):
            raise ValueError("bound visual source lacks exact SVG file provenance")
        blocks.append(
            _parse_visual_block(
                raw_svg=raw_svg,
                locator=f"{segment_index}/V{ordinal:02d}",
                segment_index=segment_index,
                source_segment_index=row.get("index"),
                ordinal=ordinal,
                char_start=start,
                char_end=end,
                source_path=source_path,
                source_file_sha256=source_file_sha256,
                source_url=source_url or None,
                binding_kind="linked_svg_asset",
            )
        )
    return tuple(blocks)


def spoken_text_without_visuals(
    text: str, blocks: Iterable[VisualSourceBlock]
) -> str:
    """Remove visual spans while preserving a line boundary between speech."""

    value = str(text or "")
    for block in sorted(blocks, key=lambda row: row.char_start, reverse=True):
        value = value[:block.char_start] + "\n" + value[block.char_end:]
    return value


def visual_fragment_display_text(fragment: Mapping[str, Any]) -> str:
    """Give text-only consumers a safe label without calling SVG speech."""

    if str(fragment.get("source_modality") or "spoken") != "visual":
        return str(fragment.get("verbatim_excerpt") or "").strip()
    label_tags = {"text", "tspan", "title", "desc"}
    labels: list[str] = []
    for row in fragment.get("visual_facts") or []:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("tag") or "").lower() not in label_tags:
            continue
        for key in ("text", "tail"):
            value = str(row.get(key) or "").replace("\u200b", "").strip()
            if value and value not in labels:
                labels.append(value)
    locator = str(
        fragment.get("visual_locator") or fragment.get("paragraph_key") or ""
    )
    suffix = "；".join(labels)
    return f"视觉来源（非口述，{locator}）" + (f"：{suffix}" if suffix else "")


def validate_visual_fragment_against_block(
    fragment: Mapping[str, Any], block: VisualSourceBlock
) -> None:
    """Prove a persisted visual fragment is an exact subset of one SVG.

    A fragment may cite only the literal facts used by its owning record, but
    it may not rewrite those facts, substitute another SVG, or omit the
    renderer identity that defines their deterministic interpretation.
    """

    if str(fragment.get("source_modality") or "") != "visual":
        raise ValueError("visual fragment lacks source_modality=visual")
    if not block.readable:
        raise ValueError("visual fragment resolves to an unreadable SVG")
    if str(fragment.get("visual_locator") or "") != block.locator:
        raise ValueError("visual fragment locator does not match SVG block")
    if str(fragment.get("verbatim_excerpt") or "") != block.raw_svg:
        raise ValueError("visual fragment raw SVG does not match source")
    if str(fragment.get("visual_block_sha256") or "") != block.raw_sha256:
        raise ValueError("visual fragment raw SHA does not match source")
    if str(fragment.get("visual_canonical_sha256") or "") != str(
        block.canonical_sha256 or ""
    ):
        raise ValueError("visual fragment canonical SHA does not match source")
    if str(fragment.get("visual_renderer_version") or "") != VISUAL_RENDERER_VERSION:
        raise ValueError("visual fragment renderer version is unsupported")
    if block.binding_kind == "linked_svg_asset":
        if str(fragment.get("visual_source_path") or "") != str(
            block.source_path or ""
        ):
            raise ValueError("visual fragment does not point to the SVG source path")
        if str(fragment.get("visual_source_file_sha256") or "") != str(
            block.source_file_sha256 or ""
        ):
            raise ValueError("visual fragment does not bind the SVG file SHA")
        if str(fragment.get("visual_source_url") or "") != str(
            block.source_url or ""
        ):
            raise ValueError("visual fragment does not bind the Markdown image URL")
    rows = fragment.get("visual_facts")
    if not isinstance(rows, list) or not rows:
        raise ValueError("visual fragment must cite at least one literal fact")
    expected = {str(row["fact_id"]): row for row in block.facts}
    fact_ids = [
        str(row.get("fact_id") or "") if isinstance(row, Mapping) else ""
        for row in rows
    ]
    if not all(fact_ids) or len(fact_ids) != len(set(fact_ids)):
        raise ValueError("visual fragment fact IDs must be present and unique")
    for fact_id, row in zip(fact_ids, rows):
        if fact_id not in expected or dict(row) != expected[fact_id]:
            raise ValueError(
                f"visual fragment fact {fact_id!r} does not match the SVG block"
            )


def resolve_visual_source_descriptor(
    descriptor: Mapping[str, Any], *, paragraph_text: str
) -> VisualSourceBlock:
    """Reopen and verify one persisted visual source against its real origin."""

    locator = str(descriptor.get("locator") or "")
    segment_index = str(descriptor.get("segment_index") or locator.split("/", 1)[0])
    binding_kind = str(descriptor.get("binding_kind") or "inline_svg")
    if binding_kind == "linked_svg_asset":
        path_value = str(descriptor.get("source_path") or "")
        expected_file_sha = str(descriptor.get("source_file_sha256") or "")
        source_url = str(descriptor.get("source_url") or "")
        if not path_value or not re.fullmatch(r"[0-9a-f]{64}", expected_file_sha):
            raise ValueError("linked visual source lacks SVG path or physical SHA")
        path = Path(path_value)
        raw = path.read_bytes()
        actual_file_sha = hashlib.sha256(raw).hexdigest()
        if actual_file_sha != expected_file_sha:
            raise ValueError("linked visual source file SHA does not match")
        try:
            raw_svg = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("linked visual source is not UTF-8 SVG") from exc
        start = descriptor.get("char_start")
        end = descriptor.get("char_end")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(paragraph_text)
        ):
            raise ValueError("linked visual source has an invalid Markdown span")
        match = MARKDOWN_IMAGE_PATTERN.fullmatch(paragraph_text[start:end])
        if match is None or not source_url or match.group("url") != source_url:
            raise ValueError("linked visual source does not match the Markdown image")
        block = _parse_visual_block(
            raw_svg=raw_svg,
            locator=locator,
            segment_index=segment_index,
            source_segment_index=descriptor.get("source_segment_index"),
            ordinal=int(descriptor.get("ordinal") or 0),
            char_start=start,
            char_end=end,
            source_path=path_value,
            source_file_sha256=actual_file_sha,
            source_url=source_url,
            binding_kind=binding_kind,
        )
    elif binding_kind == "inline_svg":
        block = next(
            (
                value
                for value in visual_source_blocks(
                    paragraph_text,
                    segment_index=segment_index,
                    source_segment_index=descriptor.get("source_segment_index"),
                )
                if value.locator == locator
            ),
            None,
        )
        if block is None:
            raise ValueError("inline visual source locator does not resolve")
    else:
        raise ValueError(f"unsupported visual source binding kind {binding_kind!r}")

    persisted_raw = str(descriptor.get("raw_svg") or "")
    if persisted_raw != block.raw_svg:
        raise ValueError("visual source raw SVG does not match its origin")
    if str(descriptor.get("raw_sha256") or "") != block.raw_sha256:
        raise ValueError("visual source raw SHA does not match its origin")
    if str(descriptor.get("canonical_sha256") or "") != str(
        block.canonical_sha256 or ""
    ):
        raise ValueError("visual source canonical SHA does not match its origin")
    if str(descriptor.get("renderer_version") or "") != VISUAL_RENDERER_VERSION:
        raise ValueError("visual source renderer version is unsupported")
    if list(descriptor.get("literal_facts") or []) != [
        dict(row) for row in block.facts
    ]:
        raise ValueError("visual source literal facts do not match its origin")
    return block


def inline_markup_spans(text: str) -> tuple[InlineMarkupSpan, ...]:
    """Locate inline structure that cannot serve as professor-speech evidence.

    A transcript row may interleave speech with Markdown blockquotes or an
    editor-created SVG. The row itself remains in spoken locator space, but an
    anchor cannot turn that embedded structure into a professor quotation.
    Blockquotes are intentionally only evidence-ineligible here: they can hold
    spoken scripture or editorial slide text, and current storage has no
    provenance field that can safely decide which.
    """

    value = str(text or "")
    spans: list[InlineMarkupSpan] = []
    for kind, pattern in (
        ("svg", SVG_BLOCK_PATTERN),
        ("svg", SVG_ELEMENT_PATTERN),
        ("html_comment", HTML_COMMENT_PATTERN),
        ("blockquote", BLOCKQUOTE_LINE_PATTERN),
        ("inline_heading", INLINE_HEADING_LINE_PATTERN),
    ):
        spans.extend(
            InlineMarkupSpan(match.start(), match.end(), kind)
            for match in pattern.finditer(value)
        )

    # A missing closing marker is not permission to send editor payload to the
    # model. Extend unmatched openers to the end of the row and fail closed.
    for kind, opener, covered in (
        ("svg", SVG_OPEN_PATTERN, SVG_BLOCK_PATTERN),
        ("html_comment", HTML_COMMENT_OPEN_PATTERN, HTML_COMMENT_PATTERN),
    ):
        covered_ranges = [(m.start(), m.end()) for m in covered.finditer(value)]
        for match in opener.finditer(value):
            if any(left <= match.start() < right for left, right in covered_ranges):
                continue
            spans.append(InlineMarkupSpan(match.start(), len(value), kind))
    return tuple(sorted(spans, key=lambda row: (row.start, row.end, row.kind)))


def provably_nonspoken_inline_markup(text: str) -> tuple[InlineMarkupSpan, ...]:
    """Return editor payload whose syntax alone proves it is not speech."""

    rows = sorted(
        (
            span
            for span in inline_markup_spans(text)
            if span.kind in {"svg", "html_comment"}
        ),
        key=lambda span: (span.kind, span.start, span.end),
    )
    merged: list[InlineMarkupSpan] = []
    for span in rows:
        if (
            merged
            and merged[-1].kind == span.kind
            and span.start <= merged[-1].end
        ):
            prior = merged[-1]
            merged[-1] = InlineMarkupSpan(
                prior.start, max(prior.end, span.end), prior.kind
            )
        else:
            merged.append(span)
    return tuple(merged)


def excerpt_overlaps_inline_markup(
    text: str,
    excerpt: str,
    *,
    blocked_kinds: set[str] | frozenset[str] | None = None,
) -> bool:
    """Whether the deterministic first verbatim match lands in inline structure."""

    value = str(text or "")
    needle = str(excerpt or "")
    start = value.find(needle) if needle else -1
    if start < 0:
        return False
    end = start + len(needle)
    return any(
        (blocked_kinds is None or span.kind in blocked_kinds)
        and span.start < end
        and start < span.end
        for span in inline_markup_spans(value)
    )


def live_text(text: str) -> str:
    """Return one segment as the proofreader sees it after soft deletion."""

    return SOFT_DELETION.sub("\n", str(text or ""))


def live_script(script: Any) -> list[dict[str, Any]]:
    """Apply soft deletion without filtering or renumbering physical rows."""

    rows: list[dict[str, Any]] = []
    for segment in script or []:
        row = dict(segment) if isinstance(segment, Mapping) else {"text": str(segment or "")}
        original_text = str(row.get("text") or "")
        level = heading_level(original_text)
        if level is None:
            row["text"] = live_text(original_text)
        else:
            # Preserve the fact that an untyped legacy row was a standalone
            # heading before soft-deletion inserted a newline into its text.
            # Normalizing whitespace inside the editorial title is safe: this
            # row never enters the spoken source projection.
            row.setdefault("type", "subtitle")
            title = " ".join(live_text(heading_text(original_text)).split())
            row["text"] = f"{'#' * level} {title}"
        rows.append(row)
    return rows


def heading_level(text: str) -> int | None:
    """Return the level of a standalone Markdown heading row."""

    match = HEADING_PATTERN.match(str(text or "").strip())
    return len(match.group(1)) if match else None


def heading_text(text: str) -> str:
    """Return a standalone Markdown heading without its marker."""

    match = HEADING_PATTERN.match(str(text or "").strip())
    return match.group(2).strip() if match else str(text or "").strip()


def is_editorial_row(row: Mapping[str, Any]) -> bool:
    """Whether a co-located row is structure/commentary rather than speech."""

    return (
        str(row.get("type") or "").strip().lower() in EDITORIAL_ROW_TYPES
        # Older editor saves used ``type=content`` for heading rows. Their
        # dedicated index namespace remains an authorship signal even for the
        # one observed legacy heading whose text is now empty.
        or str(row.get("index") or "").strip().startswith("subtitle-")
        or heading_level(str(row.get("text") or "")) is not None
    )


@dataclass(frozen=True)
class EditorialHeading:
    """One editor-authored label at a boundary in spoken-body coordinates."""

    boundary: int
    level: int
    title: str


@dataclass(frozen=True)
class SourceProjection:
    """The spoken, visual, and editorial projections of one stored script."""

    body_rows: tuple[dict[str, Any], ...]
    spoken_rows: tuple[dict[str, Any], ...]
    headings: tuple[EditorialHeading, ...]
    visual_blocks: tuple[VisualSourceBlock, ...]
    spoken_text_sha256: str
    body_sha256: str
    visual_content_sha256: str | None
    editorial_structure_sha256: str
    editorial_topology_sha256: str


def _canonical_body_row(row: Mapping[str, Any]) -> dict[str, Any]:
    # ``type`` and ``user_id`` describe editing/UI state.  Every other field is
    # retained so a changed transcript index, timing, or future source-bearing
    # field invalidates the source identity instead of being silently ignored.
    return {
        str(key): value
        for key, value in row.items()
        if str(key) not in EDITORIAL_ONLY_FIELDS | PROJECTION_ONLY_FIELDS
    }


def _sha256_json(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def project_script(script: Any) -> SourceProjection:
    """Project a mixed script into spoken rows and editorial heading structure.

    Moving, adding, deleting, or renaming subtitle/comment rows cannot change
    the body rows or their coordinates.  Comments do not become model context;
    valid heading rows become labels at the next spoken row boundary.
    """

    body: list[dict[str, Any]] = []
    spoken: list[dict[str, Any]] = []
    headings: list[EditorialHeading] = []
    visuals: list[VisualSourceBlock] = []
    for row in live_script(script):
        level = heading_level(str(row.get("text") or ""))
        if is_editorial_row(row):
            # Comments are never model context. A reviewer may begin a note
            # with Markdown syntax, but that does not turn the note into the
            # document's editorial outline.
            if (
                level is not None
                and str(row.get("type") or "").strip().lower() != "comment"
            ):
                title = heading_text(str(row.get("text") or ""))
                headings.append(
                    EditorialHeading(
                        boundary=len(body), level=level, title=title
                    )
                )
            continue
        locator = f"S{len(body) + 1:04d}"
        inline_visuals = visual_source_blocks(
            str(row.get("text") or ""),
            segment_index=locator,
            source_segment_index=row.get("index"),
        )
        bound_visuals = bound_visual_source_blocks(row, segment_index=locator)
        if inline_visuals and bound_visuals:
            raise ValueError(
                f"{locator}: inline SVG and linked SVG assets cannot share one row"
            )
        row_visuals = inline_visuals or bound_visuals
        spoken_row = dict(row)
        spoken_row["text"] = spoken_text_without_visuals(
            str(row.get("text") or ""), row_visuals
        )
        visuals.extend(row_visuals)
        body.append(row)
        spoken.append(spoken_row)

    canonical_body = [_canonical_body_row(row) for row in body]
    canonical_spoken_text = [str(row.get("text") or "") for row in spoken]
    canonical_structure = [
        {"boundary": row.boundary, "level": row.level, "title": row.title}
        for row in headings
    ]
    canonical_topology = [
        {"boundary": row.boundary, "level": row.level}
        for row in headings
    ]
    return SourceProjection(
        body_rows=tuple(body),
        spoken_rows=tuple(spoken),
        headings=tuple(headings),
        visual_blocks=tuple(visuals),
        spoken_text_sha256=_sha256_json(canonical_spoken_text),
        body_sha256=_sha256_json(canonical_body),
        visual_content_sha256=(
            _sha256_json(
                [
                    {
                        "locator": block.locator,
                        "raw_sha256": block.raw_sha256,
                        "canonical_sha256": block.canonical_sha256,
                    }
                    for block in visuals
                ]
            )
            if visuals
            else None
        ),
        editorial_structure_sha256=_sha256_json(canonical_structure),
        editorial_topology_sha256=_sha256_json(canonical_topology),
    )


def source_uses_body_locator_space(source: Mapping[str, Any]) -> bool:
    """Return whether a descriptor explicitly uses professor-body coordinates.

    A body SHA without a locator-space declaration is not treated as an
    implicit migration. Doing so would silently reinterpret every stored
    ``S n`` from physical-row coordinates as body-row coordinates.
    """

    locator_space = str(source.get("locator_space") or "").strip()
    if locator_space and locator_space != LOCATOR_SPACE:
        raise LocatorSpaceError(f"unsupported source locator space {locator_space!r}")
    if locator_space == LOCATOR_SPACE:
        if not str(source.get("source_body_sha256") or "").strip():
            raise LocatorSpaceError(
                f"{LOCATOR_SPACE} source is missing source_body_sha256"
            )
        return True
    if str(source.get("source_body_sha256") or "").strip():
        raise LocatorSpaceError(
            "source_body_sha256 requires an explicit locator_space"
        )
    return False


def assert_locator_space_compatible(
    source: Mapping[str, Any], script: Any
) -> bool:
    """Fail closed when a legacy locator would be reinterpreted by projection.

    Legacy physical-row and ``spoken_body_v1`` coordinates coincide only when
    the script contains no editorial rows. A mixed legacy source must be
    re-extracted; readers may not guess which row an old locator meant.
    """

    uses_body_coordinates = source_uses_body_locator_space(source)
    if not uses_body_coordinates and any(
        is_editorial_row(row) for row in live_script(script)
    ):
        raise LocatorSpaceError(
            "legacy source has editorial rows but no locator_space; "
            "re-extraction required"
        )
    return uses_body_coordinates


def script_from_markdown_blocks(blocks: Iterable[str]) -> list[dict[str, Any]]:
    """Give Markdown blocks the same stable body/editorial index scheme.

    Heading rows use a separate string namespace.  Therefore inserting or
    moving a Markdown heading cannot renumber the professor-authored body rows
    or change their body identity.
    """

    body_index = 0
    heading_index = 0
    rows: list[dict[str, Any]] = []
    for block in blocks:
        text = str(block)
        if heading_level(text) is not None:
            heading_index += 1
            index: int | str = f"heading-{heading_index}"
        else:
            body_index += 1
            index = body_index
        rows.append(
            {
                "index": index,
                "start_time": None,
                "end_time": None,
                "text": text,
            }
        )
    return rows


def spoken_source_rows(script: Any) -> list[dict[str, Any]]:
    """Convenience wrapper returning only professor-spoken source rows."""

    return [dict(row) for row in project_script(script).body_rows]


def source_body_sha256(script: Any) -> str:
    """The source identity used by anchors and staleness checks."""

    return project_script(script).body_sha256


def editorial_structure_sha256(script: Any) -> str:
    """The audit/display identity of editorial boundaries and labels."""

    return project_script(script).editorial_structure_sha256
