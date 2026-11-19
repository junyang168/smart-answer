"""Read a diagram's words and structure from its SVG (#812).

His diagrams become SourceFragments whose verbatim is the SVG. The faithfulness
check compares words; a statement about structure ("5:13-16 sits inside 門徒的使命")
needs the structure too, so this module reads both, with no model call:

- ``texts``: every text element, whitespace normalised, in reading order;
- ``relations``: what the drawing says about those texts --
  ``inside`` (a text, or the region a text names, lies in the region another names),
  ``above`` / ``left_of`` (two regions in the same parent, the first higher, or level and further left),
  ``arrow`` (a line with an arrowhead from one text to another),
  ``column`` (a heading and the texts stacked under it at the same x),
  ``overlap`` (two circles intersect) and ``inside_both`` (a text in both circles).

A region is a rectangle, a cell of a rectangle cut by a straight line across it,
or a circle. A region is named by its topmost own text; a rectangle with no text
of its own is named by a title sitting just above its top edge, if there is one.
Only ``translate`` transforms are understood; anything else is refused, because a
silently misplaced text would yield a wrong structure.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

NS = '{http://www.w3.org/2000/svg}'
TITLE_GAP = 40  # how far above a box's top edge a title may sit
COLUMN_TOLERANCE = 2


def norm(text: str) -> str:
    return re.sub(r'\s+', ' ', (text or '').replace(' ', ' ').replace('　', ' ')).strip()


def _num(value, default=0.0) -> float:
    if value is None:
        return default
    match = re.match(r'-?[\d.]+', str(value))
    return float(match.group()) if match else default


def _translate(transform: str | None) -> tuple[float, float]:
    if not transform:
        return 0.0, 0.0
    match = re.fullmatch(r'\s*translate\(\s*(-?[\d.]+)[\s,]+(-?[\d.]+)\s*\)\s*', transform)
    if not match:
        raise ValueError(f'unsupported transform: {transform!r}')
    return float(match.group(1)), float(match.group(2))


@dataclass
class Text:
    text: str
    x: float
    y: float
    size: float

    @property
    def center(self) -> tuple[float, float]:
        return self.x, self.y - self.size / 2


@dataclass
class Region:
    kind: str  # 'rect' | 'cell' | 'circle'
    box: tuple[float, float, float, float] = (0, 0, 0, 0)  # x0, y0, x1, y1 for rect and cell
    circle: tuple[float, float, float] = (0, 0, 0)  # cx, cy, r
    parent: 'Region | None' = None
    name: str | None = None
    own: list = field(default_factory=list)

    def holds(self, point) -> bool:
        x, y = point
        if self.kind == 'circle':
            cx, cy, r = self.circle
            return math.hypot(x - cx, y - cy) <= r
        x0, y0, x1, y1 = self.box
        return x0 <= x <= x1 and y0 <= y <= y1

    def area(self) -> float:
        if self.kind == 'circle':
            return math.pi * self.circle[2] ** 2
        x0, y0, x1, y1 = self.box
        return (x1 - x0) * (y1 - y0)

    def inside(self, other: 'Region') -> bool:
        if self is other or other.kind == 'circle' or self.kind == 'circle':
            return False
        a, b = self.box, other.box
        return b[0] <= a[0] and b[1] <= a[1] and a[2] <= b[2] and a[3] <= b[3] and self.area() < other.area()


def _style_size(element, classes: dict[str, float]) -> float:
    style = element.get('style') or ''
    match = re.search(r'font-size:\s*([\d.]+)', style)
    if match:
        return float(match.group(1))
    if element.get('font-size'):
        return _num(element.get('font-size'))
    for name in (element.get('class') or '').split():
        if name in classes:
            return classes[name]
    return 16.0


def parse(svg: str) -> dict:
    root = ET.fromstring(svg)
    classes = {}
    for style in root.iter(NS + 'style'):
        for name, body in re.findall(r'\.([\w-]+)\s*\{([^}]*)\}', style.text or ''):
            match = re.search(r'font-size:\s*([\d.]+)', body)
            if match:
                classes[name] = float(match.group(1))
    texts, rects, lines, circles = [], [], [], []

    def walk(node, dx, dy, inherited):
        tx, ty = _translate(node.get('transform'))
        dx, dy = dx + tx, dy + ty
        size = _num(node.get('font-size'), inherited) if node.get('font-size') else inherited
        tag = node.tag.replace(NS, '')
        if tag == 'text':
            words = norm(''.join(node.itertext()))
            if words:
                own = _style_size(node, classes) if (node.get('style') or node.get('font-size') or node.get('class')) else size
                texts.append(Text(words, _num(node.get('x')) + dx, _num(node.get('y')) + dy, own))
            return
        if tag == 'rect':
            x, y = _num(node.get('x')) + dx, _num(node.get('y')) + dy
            rects.append((x, y, x + _num(node.get('width')), y + _num(node.get('height'))))
        elif tag == 'line':
            lines.append(((_num(node.get('x1')) + dx, _num(node.get('y1')) + dy),
                          (_num(node.get('x2')) + dx, _num(node.get('y2')) + dy), bool(node.get('marker-end'))))
        elif tag == 'circle':
            circles.append((_num(node.get('cx')) + dx, _num(node.get('cy')) + dy, _num(node.get('r'))))
        for child in node:
            walk(child, dx, dy, size)

    walk(root, 0.0, 0.0, 16.0)
    return {'texts': texts, 'rects': rects, 'lines': lines, 'circles': circles}


def _regions(shapes) -> list[Region]:
    regions = []
    for box in shapes['rects']:
        rect = Region('rect', box=box)
        regions.append(rect)
        # A plain line drawn edge to edge across a rectangle cuts it into cells.
        cuts = sorted(y1 for (x1, y1), (x2, y2), arrow in shapes['lines']
                      if not arrow and abs(y1 - y2) < 1 and abs(x1 - box[0]) < 2 and abs(x2 - box[2]) < 2
                      and box[1] < y1 < box[3])
        edges = [box[1]] + cuts + [box[3]]
        if cuts:
            regions += [Region('cell', box=(box[0], top, box[2], bottom)) for top, bottom in zip(edges, edges[1:])]
    regions += [Region('circle', circle=c) for c in shapes['circles']]
    for region in regions:
        holders = [r for r in regions if region.inside(r) or (region.kind == 'cell' and r.kind == 'rect'
                   and r.box[0] == region.box[0] and r.box[2] == region.box[2] and r.box[1] <= region.box[1] and region.box[3] <= r.box[3]
                   and r is not region)]
        region.parent = min(holders, key=Region.area) if holders else None
    return regions


def read(svg: str) -> dict:
    shapes = parse(svg)
    texts = shapes['texts']
    regions = _regions(shapes)
    boxes = [r for r in regions if r.kind != 'circle']

    def home(text: Text):
        held = [r for r in boxes if r.holds(text.center)]
        return min(held, key=Region.area) if held else None

    for text in texts:
        region = home(text)
        if region:
            region.own.append(text)
    for region in boxes:
        region.own.sort(key=lambda t: t.y)
        if region.own:
            region.name = region.own[0].text
    for region in boxes:
        if region.name is None and region.kind == 'rect':
            titles = [t for t in texts if home(t) is None and region.box[0] - 5 <= t.x <= region.box[2]
                      and 0 <= region.box[1] - t.y <= TITLE_GAP]
            if titles:
                region.name = titles[0].text

    def named_ancestors(region):
        while region is not None:
            if region.name:
                yield region
            region = region.parent

    relations = set()
    for text in texts:
        region = home(text)
        start = region
        if region is not None and region.name == text.text:
            start = region.parent  # the text names this region; it lies inside its ancestors
        for outer in named_ancestors(start):
            if outer.name != text.text:
                relations.add(('inside', text.text, outer.name))
    # Siblings: named regions, or a region's own texts, top to bottom in one parent.
    by_parent = {}
    for region in boxes:
        if region.name:
            parent = next(named_ancestors(region.parent), None) if region.parent else None
            by_parent.setdefault(id(parent), []).append((region.box[1], region.box[0], region.name))
    for group in by_parent.values():
        group.sort()
        for (ya, xa, a), (yb, xb, b) in zip(group, group[1:]):
            if a != b:
                # Boxes whose tops line up stand side by side, not one above the other.
                relations.add(('left_of', a, b) if abs(ya - yb) < 2 else ('above', a, b))
    for (x1, y1), (x2, y2), arrow in shapes['lines']:
        if arrow and texts:
            start = min(texts, key=lambda t: math.dist(t.center, (x1, y1)))
            end = min(texts, key=lambda t: math.dist(t.center, (x2, y2)))
            relations.add(('arrow', start.text, end.text))
    for head in texts:
        under = sorted((t for t in texts if t is not head and abs(t.x - head.x) <= COLUMN_TOLERANCE and t.y > head.y
                        and home(t) is home(head)), key=lambda t: t.y)
        if len(under) >= 2 and not any(abs(t.x - head.x) <= COLUMN_TOLERANCE and t.y < head.y and home(t) is home(head) for t in texts):
            relations.add(('column', head.text, tuple(t.text for t in under)))
    circles = [r for r in regions if r.kind == 'circle']
    if circles:
        labels = {}
        for text in texts:
            if not any(c.holds(text.center) for c in circles):
                nearest = min(circles, key=lambda c: abs(c.circle[0] - text.x))
                below = text.y > nearest.circle[1] + nearest.circle[2]
                if below and id(nearest) not in labels:
                    labels[id(nearest)] = text.text
        named = [(c, labels.get(id(c))) for c in circles]
        for i, (a, na) in enumerate(named):
            for b, nb in named[i + 1:]:
                if na and nb and math.dist(a.circle[:2], b.circle[:2]) < a.circle[2] + b.circle[2]:
                    relations.add(('overlap',) + tuple(sorted((na, nb))))
        for text in texts:
            within = [n for c, n in named if n and c.holds(text.center)]
            if len(within) >= 2:
                relations.add(('inside_both', text.text, tuple(sorted(within))))
    return {'texts': [t.text for t in sorted(texts, key=lambda t: (t.y, t.x))], 'relations': sorted(relations, key=str)}


def _key_relation(relation: list) -> tuple:
    kind, *rest = relation
    if kind == 'overlap':
        return ('overlap',) + tuple(sorted(rest))
    if kind in ('column', 'inside_both'):
        values = tuple(rest[1]) if kind == 'column' else tuple(sorted(rest[1]))
        return (kind, rest[0], values)
    return (kind, *rest)


def grade(key: dict, svg: str) -> dict:
    """E3: every text of the key exactly, and every relation of the key present."""
    got = read(svg)
    want_texts = sorted(norm(t) for t in key['texts'])
    have_texts = sorted(got['texts'])
    have = set(got['relations'])
    want = [_key_relation(r) for r in key['relations']]
    missing = [r for r in want if r not in have]
    return {'key': key['key'], 'texts_ok': want_texts == have_texts,
            'texts_missing': sorted(set(want_texts) - set(have_texts)), 'texts_extra': sorted(set(have_texts) - set(want_texts)),
            'relations_missing': missing, 'passed': want_texts == have_texts and not missing}


KEY = Path(__file__).parent / 'argument_golden' / 'slides' / 'diagram-structure-key.json'


def main():
    p = argparse.ArgumentParser(description="Grade the SVG reader against the diagram structure key (E3)")
    p.add_argument('--data-base-dir', type=Path, required=True)
    a = p.parse_args()
    keys = json.loads(KEY.read_text(encoding='utf-8'))
    results = [grade(k, (a.data_base_dir / k['svg']).read_text(encoding='utf-8')) for k in keys['diagrams']]
    print(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
