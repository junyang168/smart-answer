"""Program-built list of the professor's explicit Scripture citations, and the
source-layer check that every held citation is visible through a fragment.

Only explicit citations count (owner, 2026-11-14): he names book and chapter,
reads the verse text, or gives the words a person in the verse says as that
person's words. A retold story, an explanation or a resemblance is not one.

The list is built once per source version from the whole sermon, so a book or
chapter he announced earlier carries into later paragraphs. Jev is asked only
which verse an introduced quotation is; it never decides a reference alone.
Layers above the source see the source only through SourceFragments, so a
lower record holding a cited verse must have a fragment containing the citing
sentence. Missing ones become literal `claim_flash_scripture` jobs: the program
locates the sentence and attaches the fragment, with no model call and no new
reference.
"""
from collections import Counter, defaultdict
from functools import lru_cache
import json
from pathlib import Path
import re

from backend.api import scripture as S
from backend.pipeline import claim_flash_common as f, evidence_refresh as e
from backend.pipeline.claim_flash_scripture import references
from backend.pipeline.transcript_evidence_migration import script_layout

CONTRACT = 'claim_flash_citation_list_v1'
CUV_CHAPTERS = 1189
QUOTE_CHARS = 8          # a verbatim quote: this many consecutive characters of one verse
COMMON_VERSES = 4        # a run found in more verses is a formula, not a quote
CLEAR_MARGIN = 3         # a parallel quote matching this many more characters of one verse
RECENT = 3               # an announced chapter stays current for this many paragraphs
BARE_REACH = S.BARE_VERSE_REACH * 4
INTRO_CHARS = 60
NEIGHBOURS = 2           # paragraphs either side: where a fragment can show a citation, and where a repair may go
READING_PAIRS = 6        # two-character words of the verse a fragment must share to show his reading

SPEAKER = (r'(?:主耶穌|耶穌|主|保羅|彼得|約翰|雅各|摩西|大衛|以賽亞|耶利米|先知|使徒|天使|神|上帝|祂|'
           r'聖經|經上|經文|律法|舊約|新約)')
INTRODUCTION = re.compile(SPEAKER + r'(?:就|也|又|卻|再|還|這樣|這麼|那樣|明明|清楚)?(?:對\S{0,6}?)?'
                          r'(?:明講|明說|說|講|告訴\S{0,4}?|記載|寫著|吩咐|回答)[：:，,「『\s]*')
# 「兩三節聖經」「只有三節」「33減掉12節」「三節的聖經」 count verses; they cite none.
COUNTED_VERSES = re.compile(r'(?:兩|幾|只有|用|多少|減掉|減|加上|加)\s*$')
COUNT_NOUN = re.compile(r'^\s*(?:的)?(?:聖經|經文)')


def norm(text):
    return re.sub(r'[^\w]', '', script_layout(text)[0])


def sentences(text):
    """Literal sentence spans of the paragraph, in order."""
    return [m.group() for m in re.finditer(r'[^。！？!?\n]+[。！？!?]*', text) if norm(m.group())]


@lru_cache(maxsize=1)
def bible(root=None):
    """The cached 和合本 by (OSIS book, chapter, verse). An incomplete cache would
    silently miss quotes, so it is refused."""
    if root is None:
        from backend.bible_study.paths import cuv_dir
        root = cuv_dir()
    files = sorted(Path(root).glob('*/*.json'))
    if len(files) != CUV_CHAPTERS:
        raise ValueError(f'和合本 cache has {len(files)} of {CUV_CHAPTERS} chapters; run backend.bible_study.cuv first')
    verses = {}
    for path in files:
        book = S.BOOK_TO_OSIS[path.parent.name]
        for verse, text in json.loads(path.read_text()).items():
            verses[(book, int(path.stem), int(verse))] = text
    return verses


class Bible:
    def __init__(self, verses):
        self.verses = verses
        self.chapters = {(b, c) for b, c, _ in verses}
        self.index = defaultdict(set)
        for key, text in verses.items():
            t = norm(text)
            for i in range(len(t) - QUOTE_CHARS + 1):
                self.index[t[i:i + QUOTE_CHARS]].add(key)
        self.bigrams = defaultdict(set)
        for key, text in verses.items():
            t = norm(text)
            for i in range(len(t) - 1):
                self.bigrams[t[i:i + 2]].add(key)


@lru_cache(maxsize=1)
def default_bible():
    return Bible(bible())


def osis_book(slug):
    return S.BOOK_TO_OSIS.get(slug.upper()) if slug else None


def resolve_book(token):
    """Existing aliases first, then the same token with 書／記 added (「瑪拉基」)."""
    if not token:
        return None
    for i in range(len(token)):
        for suffix in ('', '書', '記'):
            slug = S._spoken_book(token[i:] + suffix)
            if slug:
                return osis_book(slug)
    return None


def spoken(paragraphs, holy, start_book=None):
    """Book/chapter/verse he says, carried across paragraphs. A chapter the
    carried book does not have falls back to the sermon's main book; a verse
    the chapter does not have is dropped."""
    book, chapter, at, offset = start_book, None, -10**9, 0
    out, current = defaultdict(list), {}
    for pk, text in paragraphs:
        marks = []
        for m in S._SPOKEN_REFERENCE_PATTERN.finditer(text):
            marks.append((m.start(), m.end(), resolve_book(m.group('book')), S.chinese_number(m.group('chapter')),
                          S.chinese_number(m.group('verse')) if m.group('verse') else None))
        for m in S._BARE_VERSE_PATTERN.finditer(text):
            if any(s <= m.start() < s + 14 for s, _, _, _, v in marks if v):
                continue
            if COUNTED_VERSES.search(text[max(0, m.start() - 4):m.start()]) or COUNT_NOUN.match(text[m.end():]):
                continue
            marks.append((m.start(), m.end(), None, None, S.chinese_number(m.group('verse'))))
        for pos, end, b, c, v in sorted(marks):
            if b:
                book = b
            if c:
                if book and (book, c) not in holy.chapters and start_book and (start_book, c) in holy.chapters:
                    book = start_book
                chapter, at = c, offset + pos
            if not book or not chapter or (book, chapter) not in holy.chapters:
                continue
            if v is None:
                if c:
                    out[pk].append(((book, chapter, None), pos, end))
                continue
            if not c and offset + pos - at > BARE_REACH:
                continue
            if (book, chapter, v) in holy.verses:
                out[pk].append(((book, chapter, v), pos, end))
        current[pk] = (book, chapter) if book and chapter else None
        offset += len(text)
    return out, current


def verbatim(text, holy, announced, primary, held):
    """Verses whose text he reads: QUOTE_CHARS consecutive characters of one verse.
    One quoted span matching several passages keeps the clearly longer match,
    then the passage a record holds, the chapter just announced, the sermon's
    main chapter. Anything still ambiguous is returned unresolved."""
    t = norm(text)
    hits = defaultdict(set)
    for i in range(len(t) - QUOTE_CHARS + 1):
        found = holy.index.get(t[i:i + QUOTE_CHARS], ())
        if len(found) > COMMON_VERSES:
            continue
        for key in found:
            hits[key].update(range(i, i + QUOTE_CHARS))
    keys = sorted(hits)
    parent = {k: k for k in keys}

    def root(k):
        while parent[k] != k:
            k = parent[k]
        return k
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if hits[a] & hits[b]:
                parent[root(a)] = root(b)
    clusters = defaultdict(list)
    for k in keys:
        clusters[root(k)].append(k)
    resolved, unresolved = [], []
    for members in clusters.values():
        size = {k: len(hits[k]) for k in members}
        best = max(size.values())
        longest = [k for k in members if size[k] == best]
        if len(longest) == 1 and all(best - size[k] >= CLEAR_MARGIN for k in members if k != longest[0]):
            resolved += longest
            continue
        # Verses of one chapter that do not overlap each other are one reading.
        overlapping = any(hits[a] & hits[b] for i, a in enumerate(members) for b in members[i + 1:])
        if len({k[:2] for k in members}) == 1 and not overlapping:
            resolved += members
            continue
        for prefer in (held, announced, set(primary)):
            pick = [k for k in members if k in prefer or k[:2] in prefer]
            if pick and len({k[:2] for k in pick}) == 1:
                resolved += pick
                break
        else:
            unresolved.append(sorted(members))
    spans = {k: sorted(hits[k]) for k in resolved}
    return resolved, unresolved, spans


def introduction_candidates(segment, holy, near, *, k=5, local=3):
    """Verses sharing two-character words with the introduced words: the chapters
    he is reading first, then the whole Bible; ties in verse order."""
    t = norm(segment)
    grams = {t[i:i + 2] for i in range(len(t) - 1)}
    score = Counter()
    for g in sorted(grams):
        hit = holy.bigrams.get(g, ())
        if 0 < len(hit) <= 1600:
            for key in hit:
                if key[:2] in near or len(hit) <= 400:
                    score[key] += 1
    ranked = sorted((key for key, s in score.items() if s >= 3), key=lambda key: (-score[key], key))
    first = [key for key in ranked if key[:2] in near][:local]
    return (first + [key for key in ranked if key not in first])[:k]


def label(key):
    return f'{key[0]} {key[1]}:{key[2]}' if key[2] else f'{key[0]} {key[1]}'


def held_refs(records, sid):
    """(book, chapter, verse|None) held by active lower records of this source."""
    out = {}
    for key, row in records.items():
        if key.split('/')[0] not in {'evidence_steps', 'observations'} or row.get('retired'):
            continue
        payload = row['payload']
        fragments = [records.get('source_fragments/' + fid) for fid in e.evidence_fragment_ids(payload)]
        if not any(x and x['payload'].get('source_id') == sid for x in fragments):
            continue
        refs = set()
        for raw in payload.get('scripture_refs') or []:
            for r in references(raw) or []:
                if r.verse_start is None:
                    refs.add((r.book, r.chapter_start, None))
                    continue
                end = r.verse_end if r.verse_end and r.chapter_end in (None, r.chapter_start) else r.verse_start
                refs.update((r.book, r.chapter_start, v) for v in range(r.verse_start, end + 1))
        out[key] = refs
    return out


def holds(refs, cite):
    b, c, v = cite
    return any(x[0] == b and x[1] == c and (v is None or x[2] is None or x[2] == v) for x in refs)


def build(records, sources, sid, root, ports, *, holy=None):
    """The citation list for this source version, saved and reused."""
    holy = holy or default_bible()
    paragraphs = sorted((pk, p['text']) for pk, p in sources[sid]['paragraphs'].items())
    pin = e.digest({'contract': CONTRACT, 'paragraphs': paragraphs})
    path = Path(root) / 'citations.json'
    if path.exists():
        saved = json.loads(path.read_text())
        if saved['input_sha256'] != pin:
            raise ValueError('citation list source changed; explicit continuation required')
        return saved
    held = set().union(*held_refs(records, sid).values()) if records else set()
    first, _ = spoken(paragraphs, holy)
    books = Counter(r[0][0] for v in first.values() for r in v)
    chapters = Counter(r[0][:2] for v in first.values() for r in v)
    main_book = books.most_common(1)[0][0] if books else None
    marks, current = spoken(paragraphs, holy, main_book)
    primary = [c for c, _ in chapters.most_common(1)]
    keys = [pk for pk, _ in paragraphs]
    text_of = dict(paragraphs)
    citations, unresolved, questions = [], [], []
    for i, (pk, text) in enumerate(paragraphs):
        announced = {current[k] for k in keys[max(0, i - RECENT):i + 1] if current.get(k)}
        announced |= {r[0][:2] for r in marks.get(pk, [])}
        quoted, ambiguous, spans = verbatim(text, holy, announced, primary, held)
        for key in sorted(quoted):
            citations.append({'paragraph_key': pk, 'basis': 'verbatim', 'ref': label(key), 'key': list(key),
                              'quote': quote_sentence(text, verse_text=holy.verses[key])})
        for members in ambiguous:
            unresolved.append({'paragraph_key': pk, 'reason': 'parallel_passage', 'candidates': [label(k) for k in members]})
        for key, pos, end in marks.get(pk, []):
            citations.append({'paragraph_key': pk, 'basis': 'spoken', 'ref': label(key), 'key': list(key),
                              'quote': quote_sentence(text, position=pos), 'mark': text[pos:end]})
        near = announced | set(primary)
        for m in INTRODUCTION.finditer(text):
            segment = text[m.end():m.end() + INTRO_CHARS]
            ns = norm(segment)
            if len(ns) < 6 or any(0 < len(holy.index.get(ns[j:j + QUOTE_CHARS], ())) <= COMMON_VERSES
                                  for j in range(len(ns) - QUOTE_CHARS + 1)):
                continue
            cands = introduction_candidates(segment, holy, near)
            if cands:
                questions.append({'paragraph_key': pk, 'position': m.start(), 'marker': m.group(0),
                                  'mark': text[m.start():m.end() + QUOTE_CHARS],
                                  'segment': segment, 'candidates': cands})
    by_paragraph = defaultdict(list)
    for q in questions:
        by_paragraph[q['paragraph_key']].append(q)
    for pk, items in sorted(by_paragraph.items()):
        asked = {}
        for n, q in enumerate(items):
            criteria = {f'V{i}': f'{label(c)}：{holy.verses[c]}' for i, c in enumerate(q['candidates'])}
            criteria['other_verse'] = '是引用經文中的話，但不是以上任何一節。'
            criteria['not_quote'] = '不是引用經文的話：只是轉述大意、講故事、教授自己的話，或引述非經文人物。'
            asked[f'Q{n}'] = {'type': 'choice', 'criteria': criteria, 'instructions':
                f'`paragraph` 中「{q["marker"]}{q["segment"][:30]}」這一處，教授是否把後面的話當作經文中這位說話者的話來引用？'
                '措辭不必與和合本完全相同，但須是在引那節經文的話，不是轉述故事情節或自己的解釋。若是，是哪一節？'}
        answers = f.jev(Path(root) / 'introductions', ports, {'paragraph': text_of[pk]}, asked)
        for n, q in enumerate(items):
            pick = answers[f'Q{n}']
            if pick.startswith('V'):
                key = q['candidates'][int(pick[1:])]
                citations.append({'paragraph_key': pk, 'basis': 'introduced', 'ref': label(key), 'key': list(key),
                                  'quote': quote_sentence(text_of[pk], position=q['position']), 'mark': q['mark']})
            elif pick == 'other_verse':
                unresolved.append({'paragraph_key': pk, 'reason': 'introduced_other_verse',
                                   'quote': quote_sentence(text_of[pk], position=q['position'])})
    seen, unique = set(), []
    for c in citations:
        token = (c['paragraph_key'], tuple(c['key']))
        if token not in seen:
            seen.add(token)
            unique.append(c)
    # A chapter-only mark adds nothing where the same paragraph cites a verse of it.
    unique = [c for c in unique if c['key'][2] is not None or not any(
        d['paragraph_key'] == c['paragraph_key'] and d['key'][:2] == c['key'][:2] and d['key'][2] for d in unique)]
    result = {'version': CONTRACT, 'source_id': sid, 'input_sha256': pin, 'main_book': main_book,
              'citations': unique, 'unresolved': unresolved}
    e.save(path, result)
    return result


def quote_sentence(text, *, position=None, verse_text=None):
    """The literal sentence that carries the citation."""
    spans = [(m.start(), m.end(), m.group()) for m in re.finditer(r'[^。！？!?\n]+[。！？!?]*', text) if norm(m.group())]
    if position is not None:
        for start, end, sentence in spans:
            if start <= position < end:
                return sentence.strip()
    v = norm(verse_text or '')
    grams = {v[i:i + QUOTE_CHARS] for i in range(len(v) - QUOTE_CHARS + 1)}
    scored = [(sum(g in norm(s) for g in grams), -start, s) for start, _, s in spans]
    return max(scored)[2].strip() if scored else text.strip()


def visible(citation, fragments, holy):
    """A held citation is visible when a holder's fragment contains the citation
    itself: the words naming the verse, the introduced words, or a run of the
    verse text he reads. The rest of the sentence is not required."""
    quote = norm(citation.get('mark') or citation['quote'])
    key = tuple(citation['key'])
    v = norm(holy.verses.get(key, ''))
    pairs = {v[i:i + 2] for i in range(len(v) - 1)}
    # A loose reading (「聽了這話就憂愁走了」, 爲／為) shares many two-character words.
    enough = min(READING_PAIRS, max(1, len(pairs) // 2))
    for text in fragments:
        t = norm(text)
        if quote and quote in t:
            return True
        if pairs and len(pairs & {t[i:i + 2] for i in range(len(t) - 1)}) >= enough:
            return True
    return False


def fragment_check(records, sources, sid, listing, *, holy=None):
    """Literal attachment jobs for held citations no fragment shows, plus a report
    of citations no lower record holds."""
    holy = holy or default_bible()
    order = sorted(sources[sid]['paragraphs'])
    index = {pk: i for i, pk in enumerate(order)}
    holders = held_refs(records, sid)
    fragments_of = {}
    for key in holders:
        rows = [records.get('source_fragments/' + fid) for fid in e.evidence_fragment_ids(records[key]['payload'])]
        fragments_of[key] = [(r['payload']['paragraph_key'], r['payload'].get('verbatim_excerpt') or '')
                             for r in rows if r and not r.get('retired') and r['payload'].get('source_id') == sid]
    jobs, unheld, distant, visible_count = [], [], [], 0
    for c in listing['citations']:
        key = tuple(c['key'])
        if key[2] is None:
            continue
        pk = c['paragraph_key']
        owners = [k for k, refs in holders.items() if holds(refs, key)]
        if not owners:
            unheld.append(c)
            continue
        near = set(order[max(0, index[pk] - NEIGHBOURS):index[pk] + NEIGHBOURS + 1])
        shown = [text for k in owners for p, text in fragments_of[k] if p in near]
        if visible(c, shown, holy):
            visible_count += 1
            continue
        close = [k for k in owners if any(abs(index.get(p, -10**6) - index[pk]) <= NEIGHBOURS for p, _ in fragments_of[k])
                 and records[k]['payload'].get('review_status') not in f.PROTECTED]
        if not close:
            distant.append({**c, 'holders': sorted(owners)})
            continue
        jobs.append({'job_id': e.digest([CONTRACT, pk, c['ref'], c['quote']]), 'reference': c['ref'],
                     'quote': c['quote'], 'paragraph_key': pk, 'target_keys': sorted(close),
                     'program_citation': True})
    return {'version': CONTRACT, 'jobs': jobs, 'visible': visible_count, 'unheld': unheld, 'distant': distant}
