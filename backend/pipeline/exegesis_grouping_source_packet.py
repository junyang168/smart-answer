"""Canonical source packet for #411: physical originals, read once, verbatim.

A source enters a model packet in exactly one shape:

* sermon transcript JSON -> every ``script`` row with all of its physical
  fields (``index``/``start_time``/``end_time``/``type``/``user_id``/``text``)
  plus explicit physical locators; ``~~…~~`` soft deletions, subtitle rows and
  headings are kept verbatim, never stripped or filtered;
* notes manuscript Markdown -> the whole file as consecutive blocks whose
  concatenation reproduces the file byte-for-byte, with block ordinals;
* linked visual originals (SVG/XML) -> SHA-verified on disk and kept in the
  audit layer as path/file SHA/size references only. By user decision no
  SVG/XML source text enters any model input: not through ``linked_files``,
  not through a frozen ``verbatim_excerpt``, not inline in a transcript row or
  Markdown block, and not smuggled into any other field.

Inline SVG inside professor text is excluded *exactly*, never by dropping the
row. ``svg_regions`` bounds every SVG/XML document in a text (nested and
self-closing ``svg`` elements, comments and CDATA inside them, a preceding XML
declaration/DOCTYPE that belongs to it) and fails closed on anything it cannot
bound: an unclosed element, a stray closing tag, a declaration without an svg
root. The model projection then replaces ``text``/``verbatim_excerpt`` with
ordered ``*_parts``: each contiguous non-SVG run verbatim with its char offset,
each excluded region as a content-free marker (offset, length, SHA-256), plus a
``*_excluded`` summary marker. Chars before and after a region (including CR/LF)
stay exactly where they were; nothing is joined across a marker, so a quote
can never pretend the speech on both sides of a diagram was continuous. The
audit layer keeps the complete original text and the physical file SHA.

No reader that changes text is used. ``paragraphs`` produced by the
preparation readers (soft deletions removed, editorial rows dropped, positions
unverified) are never model text; ``original_text`` pre-inlined by a caller is
rejected because the physical file must be read here. ``project_for_model``
removes only explicitly listed provenance keys at known positions (never a
global key strip, so nested locators or relations that happen to use a key
such as ``path`` or ``revision`` survive). ``assert_projection_complete``
proves: every non-SVG character is preserved verbatim and in order, every SVG
exclusion matches the audit text exactly, and nothing was added.

This module deliberately uses only the standard library so that the
independent reviewer can carry a parallel implementation without importing
``backend``.
"""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

SOURCE_PACKET_FORMAT = 'wang_exegesis_canonical_source_v2'
PROJECTION_FORMAT = 'wang_exegesis_model_projection_v3'
SVG_EXCLUDED = 'svg_xml_source_excluded_by_user'
TEXT_PART, EXCLUDED_PART = 'text', 'svg_xml_excluded'
TRANSCRIPT = 'sermon_transcript'
NOTES = 'notes_manuscript'
HEADING = re.compile(r'^#{1,6}\s+.+$')
#: Any SVG/XML token, a stray closing tag included; one anywhere in model input fails closed.
SVG_MARKUP = re.compile(r'<\?xml\b|<!DOCTYPE\s+svg\b|<svg\b|</svg\s*>', re.IGNORECASE)
SVG_OPEN = re.compile(r'<svg\b', re.IGNORECASE)
SVG_DOCTYPE = re.compile(r'<!DOCTYPE\s+svg\b', re.IGNORECASE)
SVG_ELEMENT_TOKEN = re.compile(r'<svg\b|</svg\s*>|<!--|<!\[CDATA\[', re.IGNORECASE)
PROLOGUE_GAP = re.compile(r'(?:\s|<!--.*?-->)*', re.DOTALL)
LOCATOR_FIELDS = ('physical_row', 'body_row', 'editorial_row')
#: Explicit non-semantic provenance removed from the model projection, by position.
CLAIM_PROVENANCE = frozenset({'claim_revision', 'claim_content_sha256', 'source_revision', 'source_content_sha256',
                              'source_file_sha256', 'frozen_file_sha256', 'source_body_sha256'})
STEP_PROVENANCE = frozenset({'revision', 'content_sha256'})
FRAGMENT_PROVENANCE = frozenset({'revision', 'content_sha256', 'match', 'source_body_sha256'})
OWNERSHIP_PROVENANCE = frozenset({'claim_revision', 'claim_content_sha256', 'source_revision', 'source_content_sha256',
                                  'review_artifact_sha256'})
SOURCE_PROVENANCE = frozenset({'path', 'file_sha256', 'byte_length', 'source_content_sha256', 'source_revision',
                               'source_file_sha256', 'discarded_input_fields', 'source_type_basis', 'source_packet_format'})
LINKED_FILE_PROVENANCE = frozenset({'path', 'file_sha256'})
OMITTED_PROVENANCE = dict(claim=sorted(CLAIM_PROVENANCE), evidence_step=sorted(STEP_PROVENANCE),
                          fragment=sorted(FRAGMENT_PROVENANCE), ownership=sorted(OWNERSHIP_PROVENANCE),
                          source=sorted(SOURCE_PROVENANCE), linked_file=sorted(LINKED_FILE_PROVENANCE))
PROJECTION_ONLY_KEYS = frozenset({'projection_format', 'omitted_provenance_fields', 'visual_evidence_policy'})
TEXT_PART_KEYS = frozenset({'part', 'kind', 'char_start', 'char_length', 'text'})
EXCLUDED_PART_KEYS = frozenset({'part', 'kind', 'char_start', 'char_length', 'excluded', 'sha256'})
LOCATOR_NOTE = (
    '逐字稿：physical_row 是 script 列表中的 1 起始物理行号；body_row 只计非编辑行（subtitle/comment/标题行除外）。'
    '冻结 fragment 的 paragraph_key（如 S0016）来自抽取时的定位空间，可能对应 body_row 或 physical_row；'
    '本 packet 不改写原始锚点，也不宣称已修正，请以 verbatim_excerpt 逐字定位。'
    '~~…~~ 是校对者划掉的内容，原样保留以供核对，不作为教授现在所说的话。'
    'Markdown：physical_block 是文件中按空行切分的块序号（文件开头的空行自成一块），block_ordinal 为非空块序号（含标题块），'
    'body_block 不含标题块；块文本逐字相接即原文件。S0002/V01 指该块内第 1 个视觉来源；'
    '其 SVG 原文按用户决定不进入模型输入，linked_files 只保留引用，正文中保留原图片链接。'
    '正文行／块内嵌 SVG 时，该行／块的 text 由 text_parts 代替：按原顺序列出逐字文字片段与 SVG 排除标记，'
    'char_start 是该行／块原文中的 0 起始字符偏移；排除标记两侧的文字之间原有 SVG，不是连续原话。'
)
VISUAL_EVIDENCE_POLICY = (
    '用户决定：SVG／XML 视觉原件不进入模型输入。linked_files 只保留引用（file_name、byte_length、svg_excluded_by_user）。'
    '正文行／块或冻结 fragment 的文字若内嵌 SVG，只排除 SVG 区域本身：text／verbatim_excerpt 由 text_parts／verbatim_excerpt_parts 代替，'
    '按原顺序列出各连续逐字文字片段（kind=text，含 char_start／char_length）与排除标记（kind=svg_xml_excluded，含 char_start／char_length／sha256），'
    '并附 text_excluded／verbatim_excerpt_excluded 总标记（char_length、sha256、svg_regions、excluded_char_length）。'
    '排除标记两侧的文字片段之间原有 SVG，不是连续原话；引文只能取自单一文字片段，不得跨越排除标记拼接。'
    '不要推测、总结或补写被排除的视觉内容。若某段落边界或成员归属没有该视觉证据就不能判断，必须明确写出缺少视觉证据这一限制'
    '（rationale／reason 中注明，复核时给 needs_resolution），不得伪称已完整核对。'
    '审计层保留原件 path 与 file SHA 及完整原文，独立复核可凭 SHA 核验原件。'
)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_json(value) -> str:
    return sha256_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode('utf-8'))


def contains_svg_markup(text) -> bool:
    return isinstance(text, str) and SVG_MARKUP.search(text) is not None


# ---------------------------------------------------------------------------
# Exact SVG region bounding; fail closed, never widen.
# ---------------------------------------------------------------------------

def _markup_end(text: str, start: int) -> int:
    """Index just past the ``>`` closing the markup opened at ``start``; quotes and ``[...]`` honoured."""
    quote, depth = None, 0
    for i in range(start, len(text)):
        ch = text[i]
        if quote:
            if ch == quote:
                quote = None
        elif ch in '"\'':
            quote = ch
        elif ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth < 0:
                break
        elif ch == '>' and depth == 0:
            return i + 1
    raise ValueError(f'SVG/XML markup at char {start} has no closing ">"; boundary uncertain')


def _svg_element_end(text: str, start: int) -> int:
    """End offset of the svg element opened at ``start``: nesting, self-closing, comments and CDATA honoured."""
    depth, cursor = 0, start
    while True:
        match = SVG_ELEMENT_TOKEN.search(text, cursor)
        if match is None:
            raise ValueError(f'svg element opened at char {start} is never closed; boundary uncertain')
        token = match.group().lower()
        if token in {'<!--', '<![cdata['}:
            terminator = '-->' if token == '<!--' else ']]>'
            end = text.find(terminator, match.end())
            if end < 0:
                raise ValueError(f'unterminated {token} inside svg at char {match.start()}; boundary uncertain')
            cursor = end + len(terminator)
        elif token.startswith('</'):
            depth -= 1
            cursor = match.end()
            if depth == 0:
                return cursor
        else:
            cursor = _markup_end(text, match.start())
            if text[cursor - 2:cursor] == '/>':
                if depth == 0:
                    return cursor
            else:
                depth += 1


def svg_regions(text: str) -> list[tuple[int, int]]:
    """Exact ``[start, end)`` offsets of every SVG/XML document in ``text``; fail closed when uncertain.

    A region is one svg element (nested svg elements, comments and CDATA inside
    it are part of it; a self-closing ``<svg …/>`` is a region of its own),
    together with an XML declaration and/or ``<!DOCTYPE svg …>`` that directly
    precedes it (whitespace and comments only in between). Anything SVG-shaped
    that cannot be bounded exactly raises instead of widening: an unclosed svg
    element, a stray closing tag, a declaration/DOCTYPE without an svg root.
    Nothing outside a region is touched, so chars before and after it
    (including CR/LF) stay exactly where they are.
    """
    regions, cursor = [], 0
    while True:
        match = SVG_MARKUP.search(text, cursor)
        if match is None:
            return regions
        start, token = match.start(), match.group().lower()
        if token.startswith('</'):
            raise ValueError(f'closing svg tag at char {start} without an open svg element; boundary uncertain')
        element = start
        if token.startswith('<?xml'):
            end = text.find('?>', match.end())
            if end < 0:
                raise ValueError(f'XML declaration at char {start} is unterminated; boundary uncertain')
            element = PROLOGUE_GAP.match(text, end + 2).end()
        if SVG_DOCTYPE.match(text, element):
            element = PROLOGUE_GAP.match(text, _markup_end(text, element)).end()
        if not SVG_OPEN.match(text, element):
            raise ValueError(f'XML declaration/DOCTYPE at char {start} is not followed by an svg root; boundary uncertain')
        cursor = _svg_element_end(text, element)
        regions.append((start, cursor))


def svg_text_parts(text: str) -> list[dict]:
    """Ordered parts tiling ``text``: verbatim non-SVG runs and content-free markers for SVG regions."""
    parts, cursor = [], 0

    def add(kind, start, end):
        part = dict(part=len(parts) + 1, kind=kind, char_start=start, char_length=end - start)
        if kind == TEXT_PART:
            part['text'] = text[start:end]
        else:
            part.update(excluded=SVG_EXCLUDED, sha256=sha256_text(text[start:end]))
        parts.append(part)

    for start, end in svg_regions(text):
        if start > cursor:
            add(TEXT_PART, cursor, start)
        add(EXCLUDED_PART, start, end)
        cursor = end
    if cursor < len(text):
        add(TEXT_PART, cursor, len(text))
    return parts


def exclusion_marker(text: str) -> dict:
    """Explicit, content-free summary of one text whose SVG regions are excluded."""
    regions = svg_regions(text)
    return dict(excluded=SVG_EXCLUDED, sha256=sha256_text(text), char_length=len(text),
                svg_regions=len(regions), excluded_char_length=sum(end - start for start, end in regions))


def exclude_svg(item: dict, field: str) -> dict:
    """Copy of ``item``; when ``field`` holds SVG it becomes ``<field>_excluded`` + ordered ``<field>_parts``."""
    text = item.get(field)
    if not contains_svg_markup(text):
        return dict(item)
    image = {k: v for k, v in item.items() if k != field}
    image[field + '_excluded'] = exclusion_marker(text)
    image[field + '_parts'] = svg_text_parts(text)
    return image


def editorial_row(row: dict) -> bool:
    """Classification only; the row and its text are kept verbatim either way."""
    return (str(row.get('type') or '').strip().lower() in {'subtitle', 'comment'}
            or str(row.get('index') or '').strip().startswith('subtitle-')
            or HEADING.match(str(row.get('text') or '').strip()) is not None)


def transcript_rows(text: str):
    """Parse a physical transcript JSON; fail closed on any uncertain structure."""
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'transcript source is not JSON: {exc}') from None
    if isinstance(value, dict):
        script, document = value.get('script'), {k: v for k, v in value.items() if k != 'script'}
    elif isinstance(value, list):
        script, document = value, {}
    else:
        raise ValueError('transcript JSON must be an object with script or a row list')
    if not isinstance(script, list) or not script:
        raise ValueError('transcript JSON has no non-empty script list; source schema uncertain')
    rows, body = [], 0
    for ordinal, row in enumerate(script, 1):
        if not isinstance(row, dict) or not isinstance(row.get('text'), str):
            raise ValueError(f'script row {ordinal} is not an object with string text; source schema uncertain')
        if any(k in row for k in LOCATOR_FIELDS):
            raise ValueError(f'script row {ordinal} already carries locator fields')
        editorial = editorial_row(row)
        if not editorial:
            body += 1
        rows.append({'physical_row': ordinal, 'body_row': None if editorial else body, 'editorial_row': editorial, **row})
    return document, rows


def markdown_blocks(text: str) -> list[dict]:
    """Split into blocks keeping every byte; concatenation equals the file.

    A block is one run of content lines together with the blank lines that
    follow it. Blank lines before the first content line form their own block
    (``block_ordinal`` None) so that every byte has exactly one physical home.
    """
    if not text:
        raise ValueError('empty manuscript')
    segments, current, has_content = [], [], False
    for line in text.splitlines(keepends=True):
        blank = not line.strip()
        if not blank and current and (not has_content or not current[-1].strip()):
            segments.append(''.join(current))
            current, has_content = [], False
        current.append(line)
        has_content = has_content or not blank
    segments.append(''.join(current))
    blocks, ordinal, body = [], 0, 0
    for segment in segments:
        content = segment.strip()
        heading = bool(content) and HEADING.match(content) is not None
        if content:
            ordinal += 1
            if not heading:
                body += 1
        blocks.append(dict(physical_block=len(blocks) + 1, block_ordinal=ordinal if content else None,
                           body_block=body if content and not heading else None, heading=heading, text=segment))
    if ''.join(b['text'] for b in blocks) != text:
        raise ValueError('markdown block split is not lossless')
    return blocks


def read_verified(path: Path, expected_sha: str) -> tuple[bytes, str]:
    raw = Path(path).read_bytes()
    if sha256_bytes(raw) != expected_sha:
        raise ValueError(f'physical source drift: {path}')
    try:
        return raw, raw.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError(f'physical source is not UTF-8: {path}') from exc


def detect_format(text: str, path: Path) -> str:
    try:
        json.loads(text)
    except json.JSONDecodeError:
        if Path(path).suffix.lower() in {'.md', '.markdown'}:
            return NOTES
        raise ValueError(f'source format uncertain (not JSON, not .md): {path}')
    return TRANSCRIPT


def linked_file_record(item: dict, ordinal: int) -> dict:
    """SHA-verify one linked visual original; keep a reference, never its text."""
    if 'original_text' in item:
        raise ValueError('pre-inlined original_text rejected for linked file; physical originals are read here')
    path = Path(item['path'])
    raw, text = read_verified(path, item['file_sha256'])
    if not (contains_svg_markup(text) or path.suffix.lower() in {'.svg', '.xml'}):
        raise ValueError(f'linked file format uncertain (not SVG/XML source): {path}')
    return dict(linked_file_ordinal=ordinal, file_name=path.name, format='svg_xml', path=str(path),
                file_sha256=item['file_sha256'], byte_length=len(raw), char_length=len(text))


def canonical_source(record: dict) -> dict:
    """Read one physical original into the canonical semantic shape (audit layer: text verbatim, SVG included)."""
    record = dict(record)
    if 'original_text' in record or any('original_text' in (f or {}) for f in record.get('linked_files') or []):
        raise ValueError(f"pre-inlined original_text rejected for {record.get('source_id')}; physical originals are read here")
    discarded = {}
    if 'paragraphs' in record:
        paragraphs = record.pop('paragraphs')
        discarded['paragraphs'] = dict(count=len(paragraphs), sha256=sha256_json(paragraphs),
            reason='preparation reader output (soft deletions removed, editorial rows dropped, positions unverified); never model text')
    path = Path(record['path'])
    raw, text = read_verified(path, record['file_sha256'])
    detected = detect_format(text, path)
    declared = record.get('source_type')
    if declared is not None and declared != detected:
        raise ValueError(f'declared source_type {declared!r} does not match physical structure {detected!r}: {path}')
    linked = [linked_file_record(item, ordinal) for ordinal, item in enumerate(record.get('linked_files') or [], 1)]
    source = {k: v for k, v in record.items() if k not in {'linked_files', 'source_type', 'path', 'file_sha256'}}
    source.update(source_packet_format=SOURCE_PACKET_FORMAT, source_type=detected,
        source_type_basis='declared_and_physically_verified' if declared is not None else 'detected_from_physical_structure',
        path=str(path), file_sha256=record['file_sha256'], byte_length=len(raw))
    if detected == TRANSCRIPT:
        document, rows = transcript_rows(text)
        source.update(document=document, rows=rows, text_row_count=len(rows),
                      body_row_count=sum(1 for r in rows if not r['editorial_row']))
    else:
        blocks = markdown_blocks(text)
        source.update(blocks=blocks, block_count=sum(1 for b in blocks if b['block_ordinal']),
                      body_block_count=sum(1 for b in blocks if b['body_block']))
    source.update(linked_files=linked, locator_note=LOCATOR_NOTE, discarded_input_fields=discarded)
    return source


def text_field(source: dict) -> str:
    return 'rows' if source.get('source_type') == TRANSCRIPT else 'blocks'


def physical_texts(source: dict) -> list[str]:
    """Audit-layer professor texts exactly as read from disk (inline SVG included); never a model input."""
    return [str(r['text']) for r in source.get(text_field(source)) or []]


def text_units(source: dict) -> list[str]:
    """Model-visible professor texts: each row/block, or each contiguous non-SVG part of a mixed one.

    Never metadata, paths, hashes or SVG. A quote must sit inside one unit, so
    nothing can be quoted across an excluded region as if it were continuous.
    """
    units = []
    for text in physical_texts(source):
        if contains_svg_markup(text):
            units.extend(part['text'] for part in svg_text_parts(text) if part['kind'] == TEXT_PART)
        else:
            units.append(text)
    return units


def linked_texts(source: dict) -> list[str]:
    """Audit-only: re-read linked visual originals from disk with SHA check. Never placed in a payload."""
    return [read_verified(Path(f['path']), f['file_sha256'])[1] for f in source.get('linked_files') or []]


def _project_linked(link: dict) -> dict:
    return {**{k: v for k, v in link.items() if k not in LINKED_FILE_PROVENANCE}, 'svg_excluded_by_user': True, 'excluded': SVG_EXCLUDED}


def _project_fragment(fragment: dict) -> dict:
    return exclude_svg({k: v for k, v in fragment.items() if k not in FRAGMENT_PROVENANCE}, 'verbatim_excerpt')


def _project_step(step: dict) -> dict:
    image = {k: v for k, v in step.items() if k not in STEP_PROVENANCE}
    if 'fragments' in step:
        image['fragments'] = [_project_fragment(f) for f in step['fragments'] or []]
    return image


def _project_claim(claim: dict) -> dict:
    image = {k: v for k, v in claim.items() if k not in CLAIM_PROVENANCE}
    if 'evidence_steps' in claim:
        image['evidence_steps'] = [_project_step(s) for s in claim['evidence_steps'] or []]
    if claim.get('ownership') is not None:
        image['ownership'] = {k: v for k, v in claim['ownership'].items() if k not in OWNERSHIP_PROVENANCE}
    return image


def _project_source(source: dict) -> dict:
    image = {k: v for k, v in source.items() if k not in SOURCE_PROVENANCE}
    key = text_field(source)
    if key in source:
        image[key] = [exclude_svg(item, 'text') for item in source[key]]
    image['linked_files'] = [_project_linked(f) for f in source.get('linked_files') or []]
    return image


def project_for_model(audit: dict) -> dict:
    """Structured projection: listed provenance keys removed at known positions only.

    Everything else (statements, evidence steps, verbatim excerpts, locators,
    nested visual_locator objects, secondary/cross-unit relations, context such
    as reviewed_unit/scope_label) is copied verbatim. SVG/XML regions inside a
    row, block or excerpt are replaced by explicit exclusion markers between
    the verbatim text on either side, never by a summary and never by dropping
    the row; SVG anywhere else fails closed.
    """
    projection = {}
    for key, value in audit.items():
        if key == 'claims':
            projection[key] = [_project_claim(c) for c in value]
        elif key == 'sources':
            projection[key] = [_project_source(s) for s in value]
        else:
            projection[key] = value
    projection['projection_format'] = PROJECTION_FORMAT
    projection['omitted_provenance_fields'] = OMITTED_PROVENANCE
    projection['visual_evidence_policy'] = VISUAL_EVIDENCE_POLICY
    assert_no_svg_source(projection)
    return projection


def assert_no_svg_source(value, location='projection'):
    """Fail closed if any string anywhere in a model-bound value carries SVG/XML markup."""
    if isinstance(value, str):
        if contains_svg_markup(value):
            raise ValueError(f'SVG/XML source text in model input at {location}; excluded by user')
    elif isinstance(value, list):
        for i, item in enumerate(value):
            assert_no_svg_source(item, f'{location}[{i}]')
    elif isinstance(value, dict):
        for k, v in value.items():
            assert_no_svg_source(v, f'{location}.{k}')


def _same(label, left, right):
    if left != right:
        raise ValueError(f'model projection lost {label}')


def _no_extra(label, image: dict, allowed: set):
    extra = sorted(set(image) - set(allowed))
    if extra:
        raise ValueError(f'model projection added fields at {label}: {extra}')


def verify_text_parts(label: str, text: str, marker, parts) -> tuple[int, int]:
    """Prove ``parts`` tile the audit ``text`` exactly; return (excluded chars, excluded regions).

    Independent of ``svg_text_parts``: the parts must be contiguous from char 0
    to the end, every text part must equal its slice of the audit text verbatim
    (and hold no SVG token), every excluded part must carry the SHA-256 of its
    slice, and the excluded intervals must be exactly the SVG regions of the
    audit text. Dropping the speech before or after a diagram, reordering,
    altering a word, widening an exclusion or joining both sides into one
    "continuous" quote all fail here.
    """
    regions = svg_regions(text)
    _same(f'{label} svg exclusion marker', exclusion_marker(text), marker)
    if not isinstance(parts, list) or not parts:
        raise ValueError(f'model projection lost {label} text parts')
    cursor, excluded, previous = 0, [], None
    for number, part in enumerate(parts, 1):
        if not isinstance(part, dict) or part.get('part') != number or part.get('char_start') != cursor:
            raise ValueError(f'model projection reordered or broke {label} text parts at part {number}')
        length = part.get('char_length')
        if type(length) is not int or length <= 0 or cursor + length > len(text):
            raise ValueError(f'model projection altered {label} text part {number} length')
        segment, kind = text[cursor:cursor + length], part.get('kind')
        if kind == TEXT_PART:
            if previous == TEXT_PART:
                raise ValueError(f'{label} text parts are split without an exclusion between them')
            _no_extra(f'{label} text part {number}', part, TEXT_PART_KEYS)
            if part.get('text') != segment or contains_svg_markup(segment):
                raise ValueError(f'model projection altered {label} text part {number}')
        elif kind == EXCLUDED_PART:
            _no_extra(f'{label} svg exclusion part {number}', part, EXCLUDED_PART_KEYS)
            if part.get('excluded') != SVG_EXCLUDED or part.get('sha256') != sha256_text(segment):
                raise ValueError(f'{label} svg exclusion part {number} does not match the audit text')
            excluded.append((cursor, cursor + length))
        else:
            raise ValueError(f'{label} text part {number} has an unknown kind')
        previous, cursor = kind, cursor + length
    if cursor != len(text):
        raise ValueError(f'model projection lost the tail of {label}')
    if excluded != regions:
        raise ValueError(f'{label} excluded regions do not match the SVG regions of the audit text')
    return sum(end - start for start, end in excluded), len(excluded)


def _verify_text_field(label: str, original: dict, image, field: str, provenance=frozenset()) -> tuple[int, int]:
    """One row/block/fragment: non-provenance fields identical; ``field`` verbatim or exactly partitioned."""
    if not isinstance(image, dict):
        raise ValueError(f'model projection lost {label}')
    text = original.get(field)
    excluded = contains_svg_markup(text)
    allowed = set(original) - provenance
    if excluded:
        if field in image:
            raise ValueError(f'{label} carries SVG {field} into the model input')
        allowed = (allowed - {field}) | {field + '_excluded', field + '_parts'}
    _no_extra(label, image, allowed)
    for key, value in original.items():
        if key not in provenance and key != field:
            _same(f'{label} {key}', value, image.get(key))
    if not excluded:
        _same(f'{label} {field}', text, image.get(field))
        return 0, 0
    return verify_text_parts(label, text, image.get(field + '_excluded'), image.get(field + '_parts'))


def assert_projection_complete(audit: dict, projection: dict) -> dict:
    """Prove: all non-SVG semantics preserved, all SVG exclusions explicitly accounted.

    Detects deleted Claims (including a dropped tail), deleted relations,
    deleted or altered original text (including the speech before or after an
    excluded diagram), SVG put back, and any value added that is not in the
    audit payload.
    """
    if projection.get('projection_format') != PROJECTION_FORMAT:
        raise ValueError('not a model projection')
    counts = dict(claims=0, evidence_steps=0, fragments=0, sources=0, source_texts=0, source_text_chars=0, linked_files=0,
                  svg_excluded_linked_files=0, svg_excluded_fragment_excerpts=0, svg_excluded_source_texts=0,
                  svg_excluded_text_regions=0, svg_excluded_chars=0)
    claims, projected = audit['claims'], projection['claims']
    _same('claim count', len(claims), len(projected))
    _same('claim order/ids', [c['claim_id'] for c in claims], [c['claim_id'] for c in projected])
    for claim, image in zip(claims, projected):
        counts['claims'] += 1
        cid = claim['claim_id']
        _no_extra(cid, image, set(claim) - CLAIM_PROVENANCE)
        for field, value in claim.items():
            if field in CLAIM_PROVENANCE or field in {'evidence_steps', 'ownership'}:
                continue
            _same(f'{cid} {field}', value, image.get(field))
        steps, image_steps = claim.get('evidence_steps') or [], image.get('evidence_steps') or []
        _same(f'{cid} evidence step count', len(steps), len(image_steps))
        for step, image_step in zip(steps, image_steps):
            counts['evidence_steps'] += 1
            _no_extra(f'{cid} evidence step', image_step, set(step) - STEP_PROVENANCE)
            for field, value in step.items():
                if field in STEP_PROVENANCE or field == 'fragments':
                    continue
                _same(f'{cid} evidence {field}', value, image_step.get(field))
            fragments, image_fragments = step.get('fragments') or [], image_step.get('fragments') or []
            _same(f'{cid} fragment count', len(fragments), len(image_fragments))
            for fragment, image_fragment in zip(fragments, image_fragments):
                counts['fragments'] += 1
                chars, regions = _verify_text_field(f'{cid} fragment', fragment, image_fragment, 'verbatim_excerpt', FRAGMENT_PROVENANCE)
                if regions:
                    counts['svg_excluded_fragment_excerpts'] += 1
                    counts['svg_excluded_chars'] += chars
        ownership, image_ownership = claim.get('ownership'), image.get('ownership')
        if ownership is not None:
            if not isinstance(image_ownership, dict):
                raise ValueError(f'model projection lost {cid} ownership')
            _no_extra(f'{cid} ownership', image_ownership, set(ownership) - OWNERSHIP_PROVENANCE)
            for field, value in ownership.items():
                if field not in OWNERSHIP_PROVENANCE:
                    _same(f'{cid} ownership {field}', value, image_ownership.get(field))
    sources, image_sources = audit['sources'], projection['sources']
    _same('source count', len(sources), len(image_sources))
    _same('source order/ids', [s['source_id'] for s in sources], [s['source_id'] for s in image_sources])
    for source, image in zip(sources, image_sources):
        counts['sources'] += 1
        sid, key = source['source_id'], text_field(source)
        _no_extra(sid, image, (set(source) - SOURCE_PROVENANCE) | {'linked_files'})
        for field, value in source.items():
            if field in SOURCE_PROVENANCE or field in {'linked_files', key}:
                continue
            _same(f'{sid} {field}', value, image.get(field))
        items, image_items = source[key], image.get(key) or []
        _same(f'{sid} {key} count', len(items), len(image_items))
        for ordinal, (item, image_item) in enumerate(zip(items, image_items), 1):
            counts['source_texts'] += 1
            counts['source_text_chars'] += len(item['text'])
            chars, regions = _verify_text_field(f'{sid} {key}[{ordinal}]', item, image_item, 'text')
            if regions:
                counts['svg_excluded_source_texts'] += 1
                counts['svg_excluded_text_regions'] += regions
                counts['svg_excluded_chars'] += chars
        links, image_links = source.get('linked_files') or [], image.get('linked_files') or []
        _same(f'{sid} linked file count', len(links), len(image_links))
        for link, image_link in zip(links, image_links):
            counts['linked_files'] += 1
            _same(f'{sid} linked file reference', _project_linked(link), image_link)
            counts['svg_excluded_linked_files'] += 1
            counts['svg_excluded_chars'] += link['char_length']
    for key, value in audit.items():
        if key not in {'claims', 'sources'}:
            _same(f'context {key}', value, projection.get(key))
    _no_extra('projection', projection, set(audit) | PROJECTION_ONLY_KEYS)
    _same('visual evidence policy', VISUAL_EVIDENCE_POLICY, projection.get('visual_evidence_policy'))
    _same('omitted provenance list', OMITTED_PROVENANCE, projection.get('omitted_provenance_fields'))
    assert_no_svg_source(projection)
    return counts


def text_occurrences(serialized: str, text: str) -> int:
    """Test/diagnostic helper: how many JSON string values in a serialized wire equal ``text`` exactly.

    Counts whole string tokens after parsing, never substrings, so a one-line
    text such as a lone newline is not confused with newlines inside other
    texts. In an interned packet the string table entry counts once and the
    ``$text`` references are not strings. Never used to change professor text.
    """
    def count(value):
        if isinstance(value, str):
            return int(value == text)
        if isinstance(value, list):
            return sum(count(v) for v in value)
        if isinstance(value, dict):
            return sum(count(v) for v in value.values())
        return 0
    return count(json.loads(serialized))
