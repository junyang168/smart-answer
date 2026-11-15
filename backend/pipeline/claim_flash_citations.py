"""Program-built list of the professor's explicit Scripture citations, and the
source-layer check that every held citation is visible through a fragment.

Only explicit citations count (owner, 2026-11-14): he names book and chapter,
reads the verse text, or gives the words a person in the verse says as that
person's words. A retold story, an explanation or a resemblance is not one.

The list is built once per source version from the whole sermon, so a book or
chapter he announced earlier carries into later paragraphs. Jev is asked only
which verse an introduced quotation is; it never decides a reference alone.
Layers above the source see the transcript only through SourceFragments, so
every explicit citation must sit inside one. Uncovered citation sentences are saved as literal SourceFragments by the
source fill, with no model call; linking them to Evidence is a later layer.
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

CONTRACT = 'claim_flash_citation_list_v3'
CUV_CHAPTERS = 1189
QUOTE_CHARS = 8          # a verbatim quote: this many consecutive characters of one verse
COMMON_VERSES = 4        # a run found in more verses is a formula, not a quote
CLEAR_MARGIN = 3         # a parallel quote matching this many more characters of one verse
RECENT = 3               # an announced chapter stays current for this many paragraphs
BARE_REACH = S.BARE_VERSE_REACH * 4
INTRO_CHARS = 60
NEIGHBOURS = 2           # paragraphs either side where a SourceFragment can show a citation
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


JOHN_LETTER = re.compile(r'[約约]翰([一二三壹貳參])書\W{0,2}(?:第)?\s*$')
JOHN_LETTERS = {'一': '1John', '壹': '1John', '二': '2John', '貳': '2John', '三': '3John', '參': '3John'}


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


def names_book(mark):
    m = S._SPOKEN_REFERENCE_PATTERN.search(mark)
    return bool(m and m.group('book'))


def names_chapter(mark):
    m = S._SPOKEN_REFERENCE_PATTERN.search(mark)
    return bool(m and m.group('chapter'))


def spoken(paragraphs, holy, start_book=None):
    """Book/chapter/verse he says, carried across paragraphs. A chapter the
    carried book does not have falls back to the sermon's main book; one the
    book he just named does not have (「提摩太後書第六章」, a slip) and a verse
    the chapter does not have are dropped."""
    book, chapter, at, offset = start_book, None, -10**9, 0
    out, current = defaultdict(list), {}
    for pk, text in paragraphs:
        marks = []
        for m in S._SPOKEN_REFERENCE_PATTERN.finditer(text):
            # The book pattern skips numerals, so 「約翰一書」 reaches it as 「書」.
            numbered = JOHN_LETTER.search(text[:m.start('chapter')])
            start = numbered.start() if numbered else m.start()
            named = JOHN_LETTERS[numbered.group(1)] if numbered else resolve_book(m.group('book'))
            marks.append((start, m.end(), named, S.chinese_number(m.group('chapter')),
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
                if not b and book and (book, c) not in holy.chapters and start_book and (start_book, c) in holy.chapters:
                    book = start_book
                chapter, at = c, offset + pos
            if not book or not chapter or (book, chapter) not in holy.chapters:
                continue
            if v is None:
                # A bare 「第一章」 with a carried book is too often not Scripture
                # (「我第一章給他們」 is his thesis); a chapter counts when he names the book.
                if c and b:
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


AMEN = re.compile(r'(阿們|阿门|amen)', re.I)


def prayers(root, ports, paragraphs, matched):
    """{paragraph: length of his prayer in normalized characters}, as Jev judges,
    among paragraphs with a word-for-word match: the first two, the last, and
    any with 阿們／Amen (a prayer between sessions). A prayer often runs into
    the teaching in one paragraph (「…奉主耶穌基督的聖名求，Amen。我們昨天看到…」),
    so Jev is asked about the text up to the first Amen. Without Jev, none."""
    if ports is None or not paragraphs:
        return {}
    keys = [pk for pk, _ in paragraphs]
    asked = [pk for pk in dict.fromkeys(keys[:2] + keys[-1:] + [pk for pk, t in paragraphs if AMEN.search(t)])
             if pk in matched]
    text_of = dict(paragraphs)
    found = {}
    for pk in asked:
        amen = AMEN.search(text_of[pk])
        part = text_of[pk][:amen.end()] if amen else text_of[pk]
        answer = f.jev(Path(root) / 'prayers', ports, {'paragraph': part}, {'Q0': {'type': 'choice',
            'criteria': {'prayer': '這一段是禱告：向神說話，例如「我們這樣禱告，奉主耶穌基督的名，阿們」。',
                         'not_prayer': '這一段不是禱告：是講課、念經文、問答或其他內容。'},
            'instructions': '`paragraph` 這一段整段是不是禱告？'}})
        if answer['Q0'] == 'prayer':
            found[pk] = len(norm(part))
    return found


CHECK_INSTRUCTION = (
    '你核對講道逐字稿中的一處經文引用。每個工作給出教授講的一段（paragraph），前後各一段，'
    '該處的引出語與其後的話（mark、sentence），以及另一個模型認為他在引的經文（ref、verse_text，和合本）。\n'
    '負責人的規則：明確引用是念出經文原句，或經文中某人說的話被當作那人的話說出來'
    '（措辭不必與和合本相同，不論是教授、聽眾還是轉述者在說）。\n'
    '只敘述發生了什麼事而沒有任何人的話、解釋、評論、應用、提問（如「彼得說什麼？」之後若沒有說出那句話），'
    '或只是字詞相似、呼應某節，都不算引用。\n'
    '只看 mark 所在這一處：\n'
    '- quote：這一處教授確實說出了 ref 這一節中某人的話或經文原句。\n'
    '- other_verse：這一處是在引經文的話，但不是 ref 這一節（例如是相鄰的一節或平行經文）。\n'
    '- not_quote：這一處不是在引經文。\n'
    '用一句話說明依據。不要猜測其他節次。')
CHECK_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['items'], 'properties': {'items': {
    'type': 'array', 'items': {'type': 'object', 'additionalProperties': False,
        'required': ['job_id', 'decision', 'reason'], 'properties': {
            'job_id': {'type': 'string'}, 'decision': {'type': 'string', 'enum': ['quote', 'other_verse', 'not_quote']},
            'reason': {'type': 'string'}}}}}}


def confirm_introduced(root, ports, citations, keys, text_of, holy):
    """GPT checks each Jev-picked introduced quote. Without GPT none stays."""
    picked = [c for c in citations if c['basis'] == 'introduced']
    if not picked:
        return citations, []
    decisions = {}
    if ports is not None and 'gpt' in getattr(ports, 'identity', {}):
        index = {pk: i for i, pk in enumerate(keys)}
        jobs = []
        for c in picked:
            i = index[c['paragraph_key']]
            jobs.append({'job_id': f"{c['paragraph_key']}#{c['ref']}#{c['mark']}",
                         'previous': text_of[keys[i - 1]] if i else '', 'paragraph': text_of[c['paragraph_key']],
                         'next': text_of[keys[i + 1]] if i + 1 < len(keys) else '',
                         'mark': c['mark'], 'sentence': c['quote'], 'ref': c['ref'],
                         'verse_text': holy.verses[tuple(c['key'])]})
        for batch, reply in f.gpt(Path(root) / 'introduced-check', ports, 'introduced_quote_check', jobs,
                                  CHECK_SCHEMA, CHECK_INSTRUCTION, semantic_review=False):
            asked = {j['job_id'] for j in batch}
            for item in reply['items']:
                if item['job_id'] in asked:
                    decisions[item['job_id']] = item
            if not asked <= set(decisions):
                raise ValueError('introduced quote check left a job unanswered')
    kept, rejected = [], []
    for c in citations:
        if c['basis'] != 'introduced':
            kept.append(c)
            continue
        answer = decisions.get(f"{c['paragraph_key']}#{c['ref']}#{c['mark']}")
        if answer and answer['decision'] == 'quote':
            kept.append({**c, 'check': answer['reason']})
        else:
            rejected.append({'paragraph_key': c['paragraph_key'], 'reason': 'introduced_' + (answer['decision'] if answer else 'unchecked'),
                             'ref': c['ref'], 'quote': c['quote'], **({'check': answer['reason']} if answer else {})})
    return kept, rejected


def build(records, sources, sid, root, ports, *, holy=None):
    """The citation list for this source version, saved and reused."""
    holy = holy or default_bible()
    paragraphs = sorted((pk, p['text']) for pk, p in sources[sid]['paragraphs'].items())
    # Without Jev (ports=None) introduced quotes are not judged; that list is
    # saved under a different pin so a full build never reuses it.
    pin = e.digest({'contract': CONTRACT, 'paragraphs': paragraphs, **({'introduced': False} if ports is None else {})})
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
    found = {}
    for i, (pk, text) in enumerate(paragraphs):
        announced = {current[k] for k in keys[max(0, i - RECENT):i + 1] if current.get(k)}
        announced |= {r[0][:2] for r in marks.get(pk, [])}
        found[pk] = announced, verbatim(text, holy, announced, primary, held)
    # His opening or closing prayer shares words with verses
    # (「奉我們的主耶穌基督的聖名」); it is not a reading.
    praying = prayers(root, ports, paragraphs, {pk for pk, (_, (q, a, _)) in found.items() if q or a})
    for i, (pk, text) in enumerate(paragraphs):
        announced, (quoted, ambiguous, spans) = found[pk]
        if pk in praying:
            # Only matches inside the prayer are dropped; a reading after it stays.
            cut = praying[pk]
            quoted = [k for k in quoted if max(spans[k]) >= cut]
            ambiguous = ambiguous if cut < len(norm(text)) else []
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
    for pk, items in sorted(by_paragraph.items()) if ports is not None else ():
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
    # Jev alone picks wrongly about a third of the time (the lead-in itself, a
    # question, the next verse); GPT checks every pick and only a confirmed
    # one stays.
    citations, rejected = confirm_introduced(root, ports, citations, keys, text_of, holy)
    unresolved.extend(rejected)
    # 「五章十九節」 after an earlier book, then Matthew 5:19 read aloud; or a
    # misspoken 「第二章」 and 「第十三節」, then Daniel 7:13 read aloud: what he
    # reads decides. A verse number without a book loses to a reading of the
    # same verse number elsewhere in the paragraph (same chapter, if he said
    # one), unless he also reads that very verse there. Decided before
    # duplicates go, so a dropped entry never takes its reading with it.
    read = defaultdict(set)
    for c in citations:
        if c['basis'] == 'verbatim' and c['key'][2] is not None:
            read[c['paragraph_key'], c['key'][2]].add(tuple(c['key']))
    def contradicted(c):
        if c['basis'] != 'spoken' or c['key'][2] is None or names_book(c.get('mark', '')):
            return False
        readings = read[c['paragraph_key'], c['key'][2]]
        said_chapter = names_chapter(c.get('mark', ''))
        return tuple(c['key']) not in readings and any(
            not said_chapter or k[1] == c['key'][1] for k in readings)
    citations = [c for c in citations if not contradicted(c)]
    # One entry per verse and paragraph; the one he says aloud wins, since
    # its words mark the exact place (「又說第二章一節：『我小子們。』」).
    best = {}
    for c in citations:
        token = (c['paragraph_key'], tuple(c['key']))
        if token not in best or (c['basis'] == 'spoken' and best[token]['basis'] == 'verbatim'):
            best[token] = c
    unique = [c for c in citations if best[(c['paragraph_key'], tuple(c['key']))] is c]
    # A chapter-only mark adds nothing where the same paragraph cites a verse of it.
    unique = [c for c in unique if c['key'][2] is not None or not any(
        d['paragraph_key'] == c['paragraph_key'] and d['key'][:2] == c['key'][:2] and d['key'][2] for d in unique)]
    result = {'version': CONTRACT, 'source_id': sid, 'input_sha256': pin, 'main_book': main_book,
              'citations': unique, 'unresolved': unresolved, 'prayers': sorted(praying),
              **({'introduced': 'not_judged', 'introduced_questions': len(questions)} if ports is None else {})}
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


def fragment_check(records, sources, sid, listing, *, holy=None, valid=None):
    """Source layer, text -> fragment: is every explicit citation inside some
    SourceFragment of this source? Uncovered citation sentences become literal
    SourceFragments; which Evidence or Observation uses them is the Evidence
    layer's question, not this one."""
    holy = holy or default_bible()
    order = sorted(sources[sid]['paragraphs'])
    index = {pk: i for i, pk in enumerate(order)}
    # Only SourceFragments whose text is literally in the current transcript;
    # a fragment left from an earlier transcript version covers nothing.
    if valid is None:
        from backend.pipeline.claim_flash_source_fill import valid_fragments
        valid = valid_fragments(records, sources, sid)
    fragments = defaultdict(list)
    for row in valid.values():
        fragments[row['paragraph_key']].append(row.get('verbatim_excerpt') or '')
    spans, visible_count = [], 0
    for c in listing['citations']:
        # A named chapter (「馬太福音第五章」) is a citation too and must be visible.
        pk = c['paragraph_key']
        near = order[max(0, index[pk] - NEIGHBOURS):index[pk] + NEIGHBOURS + 1]
        if visible(c, [text for p in near for text in fragments.get(p, [])], holy):
            visible_count += 1
            continue
        if c['key'][2] is None:
            # Only the chapter («我們今天開始來看馬太福音第十六章»): an opening, not needed.
            continue
        span = {'reference': c['ref'], 'paragraph_key': pk, 'quote': c['quote']}
        if span not in spans:
            spans.append(span)
    return {'version': CONTRACT, 'spans': spans, 'visible': visible_count}
