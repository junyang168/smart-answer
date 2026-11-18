"""One lecture's argument, for a church co-worker to read, hear and check.

Design: docs/wang-knowledge-platform/10-extraction/lecture_argument_reader_design.md.
The page reads the knowledge instance only: each Claim of the lecture with its
reasons in the order he spoke them, each reason with his own words and the second
they start in the recording, and his whole transcript with every sentence marked
by the reasons that use it. Words no record uses stay visible, so a reader can
hear what was left out.

Nothing is shown that the instance does not hold, except his own section headings
(from the transcript) and the core-point candidates Claude chose, which the page
labels as candidates for the owner to rule on.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.pipeline import original_audio_index as audio
from backend.pipeline.chapter_slide_claims import instance_records
from backend.pipeline.source_projection import project_script

CORE_POINTS = Path(__file__).parent / 'argument_golden' / 'core-points.json'
RULINGS = Path('argument_layer') / 'core-point-rulings.json'
VERDICTS = {'right', 'wrong', 'no_summary'}
SENTENCE_END = re.compile(r'(?<=[。？！?!])')

BOOKS = {'馬太福音': '太', '馬可福音': '可', '路加福音': '路', '約翰福音': '約', '使徒行傳': '徒',
         '羅馬書': '羅', '哥林多前書': '林前', '哥林多後書': '林後', '加拉太書': '加', '以弗所書': '弗',
         '腓立比書': '腓', '歌羅西書': '西', '帖撒羅尼迦前書': '帖前', '帖撒羅尼迦後書': '帖後',
         '提摩太前書': '提前', '提摩太後書': '提後', '提多書': '多', '希伯來書': '來', '雅各書': '雅',
         '彼得前書': '彼前', '彼得後書': '彼後', '約翰一書': '約壹', '啟示錄': '啟', '創世記': '創',
         '出埃及記': '出', '利未記': '利', '民數記': '民', '申命記': '申', '撒母耳記上': '撒上',
         '撒母耳記下': '撒下', '列王紀上': '王上', '列王紀下': '王下', '歷代志上': '代上',
         '歷代志下': '代下', '詩篇': '詩', '箴言': '箴', '以賽亞書': '賽', '耶利米書': '耶',
         '以西結書': '結', '但以理書': '但', '何西阿書': '何', '瑪拉基書': '瑪'}
REF = re.compile(r'^(?P<book>[一-鿿]+?)\s*(?P<c1>\d+):(?P<v1>\d+)'
                 r'(?:\s*[-–—]\s*(?:(?P<c2>\d+):)?(?P<v2>\d+))?$')


def verse(ref: str) -> dict | None:
    """'馬太福音5:3–7:29' -> {'label': '太 5:3–7:29', 'book': '太', 'from': [5, 3], 'to': [7, 29]}."""
    m = REF.match(ref.strip())
    if not m:
        return None
    book = BOOKS.get(m['book'], m['book'])
    c1, v1 = int(m['c1']), int(m['v1'])
    c2 = int(m['c2']) if m['c2'] else c1
    v2 = int(m['v2']) if m['v2'] else v1
    tail = '' if not m['v2'] else (f"–{c2}:{v2}" if m['c2'] else f"–{v2}")
    return {'label': f"{book} {c1}:{v1}{tail}", 'book': book, 'from': [c1, v1], 'to': [c2, v2]}


def lectures(conn) -> list[dict]:
    """Every lecture written to the instance, by its source document's title."""
    out = []
    for (cs,) in conn.execute("SELECT change_set_id FROM wang_knowledge.change_sets "
                              "WHERE change_set_id LIKE 'argument-layer-%'").fetchall():
        row = conn.execute("""SELECT o.payload->>'title' FROM wang_knowledge.object_versions v
                              JOIN wang_knowledge.objects o USING (collection, object_id)
                              WHERE v.change_set_id=%s AND v.collection='source_documents'""", (cs,)).fetchone()
        if row:
            out.append({'id': cs, 'title': row[0]})
    return sorted(out, key=lambda x: x['title'])


class _Text:
    """His transcript as the page shows it: body paragraphs, headings, and seconds by character."""

    def __init__(self, document: dict, data_base_dir: Path):
        sermons = audio.Sermons(data_base_dir)
        # The lecture's recording and the speech recognizer's timed output are filed under
        # its title (`2011 NYSC 專題：…6.mp3`), not under the run's transcript ID.
        if audio.media_file(Path(data_base_dir), document.get('title') or ''):
            document = {**document, 'transcript_id': document['title']}
        self.sermon = sermons.load(document['source_id'], document)
        if self.sermon is None:
            raise ValueError(f"no transcript for {document.get('title')}")
        raw = json.loads(sermons.transcript_path(document).read_text(encoding='utf-8'))
        projection = project_script(raw['script'] if isinstance(raw, dict) else raw)
        self.headings = [(h.boundary, h.title) for h in projection.headings if h.level <= 2]
        self.paragraphs = [audio.STRIKETHROUGH.sub('', str(s.get('text') or '')) for s in self.sermon['segments']]
        self.base = []
        total = 0
        for text in self.paragraphs:
            self.base.append(total)
            total += len(text)
        self._coarse = audio._timeline(self.sermon)
        self._retime = audio.retimer(self.sermon, data_base_dir)

    def seconds(self, position: int) -> float | None:
        coarse = audio._at(self._coarse, position)
        return None if coarse is None else round(max(self._retime(coarse), 0.0), 1)

    def locate(self, fragment: dict) -> tuple[int, int, int] | None:
        """(paragraph index, start, end) of his words inside that paragraph, or None."""
        m = re.match(r'^S(\d+)$', str(fragment.get('paragraph_key') or ''))
        excerpt = str(fragment.get('verbatim_excerpt') or '')
        if not m or not excerpt or not 1 <= int(m[1]) <= len(self.paragraphs):
            return None
        index = int(m[1]) - 1
        start = self.paragraphs[index].find(excerpt)
        return None if start < 0 else (index, start, start + len(excerpt))

    def section(self, paragraph: int) -> int:
        """Index into headings of the section holding this paragraph; -1 before the first heading."""
        found = -1
        for i, (boundary, _) in enumerate(self.headings):
            if boundary <= paragraph:
                found = i
        return found


def _reasons(claim: dict, records: dict) -> list[dict]:
    """A Claim's reasons, each with the fragments that hold his words for it."""
    steps = {s['evidence_step_id']: s for s in records.get('evidence_steps', [])}
    observations = {o['observation_id']: o for o in records.get('observations', [])}
    positions = {p['position_id']: p for p in records.get('position_nodes', [])}
    cid = claim['claim_id']
    ids = list(claim.get('evidence_step_ids') or [])
    own = []
    for occurrence in claim.get('occurrences', []):
        for anchor in occurrence.get('anchors', []):
            rid = anchor.get('evidence_id') or anchor.get('observation_id')
            if rid:
                ids.append(rid)
            elif anchor.get('source_fragment_id'):
                own.append(anchor['source_fragment_id'])
    ids += [s for s, row in steps.items() if cid in (row.get('produced_claim_ids') or [])]
    ids += [r['from_id'] for r in records.get('knowledge_relations', [])
            if r['to_id'] == cid and r['relation_type'] == 'supports']
    reasons, seen = [], set()
    for rid in ids:
        if rid in seen:
            continue
        seen.add(rid)
        if rid in steps:
            reasons.append({'kind': '理由', 'text': steps[rid]['statement'], 'fragments': steps[rid]['source_fragment_ids']})
        elif rid in observations:
            row = observations[rid]
            kind = '經文' if row.get('observation_type') == 'scripture_text' else '他指出'
            reasons.append({'kind': kind, 'text': row['statement'], 'fragments': row['source_fragment_ids']})
    if own:
        reasons.append({'kind': '他的原話', 'text': '', 'fragments': list(dict.fromkeys(own))})
    for pid in claim.get('opposed_position_ids') or []:
        if pid in positions:
            reasons.append({'kind': '他反對的看法', 'text': positions[pid]['title'],
                            'fragments': positions[pid].get('source_fragment_ids') or []})
    return reasons


def _rulings(data_base_dir: Path) -> list[dict]:
    path = Path(data_base_dir) / RULINGS
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else []


def record_ruling(data_base_dir: Path, change_set_id: str, verdict: str, claim_id: str | None = None,
                  note: str = '') -> dict:
    """Append one owner ruling on a core-point candidate. Rulings are never rewritten."""
    if verdict not in VERDICTS:
        raise ValueError(f'unknown verdict {verdict}')
    if verdict != 'no_summary' and not claim_id:
        raise ValueError('a ruling on a candidate names its Claim')
    candidates = json.loads(CORE_POINTS.read_text(encoding='utf-8')).get(change_set_id, {}).get('candidates', [])
    if claim_id and claim_id not in {c['claim_id'] for c in candidates}:
        raise ValueError(f'{claim_id} is not a core-point candidate of this lecture')
    path = Path(data_base_dir) / RULINGS
    rows = _rulings(data_base_dir)
    row = {'change_set_id': change_set_id, 'claim_id': claim_id, 'verdict': verdict, 'note': note.strip(),
           'at': datetime.now(timezone.utc).isoformat(timespec='seconds')}
    rows.append(row)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding='utf-8')
    tmp.replace(path)
    return row


def build(records: dict, text: _Text, core: dict, rulings: list[dict], change_set_id: str) -> dict:
    fragments = {f['fragment_id']: f for f in records.get('source_fragments', [])}
    located = {fid: text.locate(f) for fid, f in fragments.items()}

    def quote(fid: str) -> dict | None:
        f = fragments.get(fid)
        if not f:
            return None
        where = located[fid]
        if where:
            p, s, _ = where
            start = text.seconds(text.base[p] + s)
        else:
            p, start = None, f.get('media_time')
        return {'text': f['verbatim_excerpt'], 'start': start, 'paragraph': p, 'fragment': fid,
                'position': text.base[p] + where[1] if where else None}

    points = []
    for claim in records.get('claims', []):
        reasons = []
        for r in _reasons(claim, records):
            quotes = [q for q in (quote(f) for f in r['fragments']) if q]
            quotes.sort(key=lambda q: (q['position'] is None, q['position'] or 0))
            if quotes:
                reasons.append({**{k: r[k] for k in ('kind', 'text')}, 'quotes': quotes})
        reasons.sort(key=lambda r: min((q['position'] for q in r['quotes'] if q['position'] is not None), default=10**9))
        first = min((q['position'] for r in reasons for q in r['quotes'] if q['position'] is not None), default=None)
        refs = [v for v in (verse(r) for r in claim.get('scripture_refs') or []) if v]
        points.append({'claim_id': claim['claim_id'], 'statement': claim['statement'],
                       'application': claim.get('claim_type') == 'application',
                       'verses': refs, 'reasons': reasons, 'position': first,
                       'start': next((q['start'] for r in reasons for q in r['quotes'] if q['position'] == first), None)})
    points.sort(key=lambda p: (p['position'] is None, p['position'] or 0))
    number = {}
    for no, point in enumerate(points, 1):
        point['no'] = no
        number[point['claim_id']] = no
    for q in records.get('questions', []):
        for cid in q.get('answer_claim_ids') or []:
            if cid in number:
                points[number[cid] - 1].setdefault('questions', []).append(q['text'])

    uses: dict[str, list[list[int]]] = {}
    for point in points:
        for rno, reason in enumerate(point['reasons'], 1):
            for q in reason['quotes']:
                uses.setdefault(q['fragment'], []).append([point['no'], rno])
                q.pop('position')
        first_paragraph = next((q['paragraph'] for r in point['reasons'] for q in r['quotes']
                                if q['paragraph'] is not None), 0)
        point['section'] = text.section(first_paragraph)
        point.pop('position')

    spans: dict[int, list[tuple[int, int, list]]] = {}
    for fid, where in located.items():
        if where and fid in uses:
            spans.setdefault(where[0], []).append((where[1], where[2], uses[fid]))
    transcript = []
    for p, para in enumerate(text.paragraphs):
        pieces, cursor = [], 0
        cuts = sorted(spans.get(p, []))

        def plain(a: int, b: int):
            for piece in SENTENCE_END.split(para[a:b]):
                if piece:
                    pieces.append({'text': piece, 'start': text.seconds(text.base[p] + a), 'uses': []})
                    a += len(piece)
        i = 0
        while i < len(cuts):
            s, e, u = cuts[i]
            merged = [list(x) for x in u]
            while i + 1 < len(cuts) and cuts[i + 1][0] < e:
                i += 1
                e = max(e, cuts[i][1])
                merged += [list(x) for x in cuts[i][2] if list(x) not in merged]
            if s >= cursor:
                plain(cursor, s)
                pieces.append({'text': para[s:e], 'start': text.seconds(text.base[p] + s), 'uses': merged})
                cursor = e
            elif e > cursor:
                pieces.append({'text': para[cursor:e], 'start': text.seconds(text.base[p] + cursor), 'uses': merged})
                cursor = e
            i += 1
        plain(cursor, len(para))
        transcript.append({'paragraph': p, 'section': text.section(p),
                           'heading': next((t for b, t in text.headings if b == p), None), 'pieces': pieces})

    latest = {}
    for row in rulings:
        if row['change_set_id'] == change_set_id:
            latest[row['claim_id']] = row
    candidates = [{'no': number[c['claim_id']], 'claim_id': c['claim_id'], 'why': c.get('why', ''),
                   'covers': [number[x] for x in c.get('covers', []) if x in number],
                   'ruling': latest.get(c['claim_id'])}
                  for c in core.get('candidates', []) if c['claim_id'] in number]
    return {'sections': [t for _, t in text.headings], 'points': points, 'transcript': transcript,
            'core': {'candidates': candidates, 'note': core.get('note', ''), 'no_summary': latest.get(None)}}


def lecture(conn, change_set_id: str, data_base_dir: Path) -> dict:
    records, _ = instance_records(conn, change_set_id)
    [document] = records['source_documents']
    text = _Text(document, Path(data_base_dir))
    core = json.loads(CORE_POINTS.read_text(encoding='utf-8')).get(change_set_id, {})
    return {'id': change_set_id, 'title': document['title'], 'media_url': text.sermon.get('media_url'),
            'duration': text.sermon.get('media_duration'),
            **build(records, text, core, _rulings(data_base_dir), change_set_id)}
