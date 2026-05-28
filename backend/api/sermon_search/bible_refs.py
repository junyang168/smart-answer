from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Tuple

from .models import CanonicalRef


BOOKS: List[Tuple[str, str, List[str]]] = [
    ("Gen", "創世記", ["創", "创", "創世記", "创世记", "Genesis", "Gen"]),
    ("Exod", "出埃及記", ["出", "出埃及記", "出埃及记", "Exodus", "Exod", "Ex"]),
    ("Lev", "利未記", ["利", "利未記", "利未记", "Leviticus", "Lev"]),
    ("Num", "民數記", ["民", "民數記", "民数记", "Numbers", "Num"]),
    ("Deut", "申命記", ["申", "申命記", "申命记", "Deuteronomy", "Deut"]),
    ("Josh", "約書亞記", ["書", "书", "約書亞記", "约书亚记", "Joshua", "Josh"]),
    ("Judg", "士師記", ["士", "士師記", "士师记", "Judges", "Judg"]),
    ("Ruth", "路得記", ["得", "路得記", "路得记", "Ruth"]),
    ("1Sam", "撒母耳記上", ["撒上", "撒母耳記上", "撒母耳记上", "1 Samuel", "1Sam", "1 Sam"]),
    ("2Sam", "撒母耳記下", ["撒下", "撒母耳記下", "撒母耳记下", "2 Samuel", "2Sam", "2 Sam"]),
    ("1Kgs", "列王紀上", ["王上", "列王紀上", "列王纪上", "1 Kings", "1Kgs", "1 Kgs"]),
    ("2Kgs", "列王紀下", ["王下", "列王紀下", "列王纪下", "2 Kings", "2Kgs", "2 Kgs"]),
    ("1Chr", "歷代志上", ["代上", "歷代志上", "历代志上", "1 Chronicles", "1Chr", "1 Chr"]),
    ("2Chr", "歷代志下", ["代下", "歷代志下", "历代志下", "2 Chronicles", "2Chr", "2 Chr"]),
    ("Ezra", "以斯拉記", ["拉", "以斯拉記", "以斯拉记", "Ezra"]),
    ("Neh", "尼希米記", ["尼", "尼希米記", "尼希米记", "Nehemiah", "Neh"]),
    ("Esth", "以斯帖記", ["斯", "以斯帖記", "以斯帖记", "Esther", "Esth"]),
    ("Job", "約伯記", ["伯", "約伯記", "约伯记", "Job"]),
    ("Ps", "詩篇", ["詩", "诗", "詩篇", "诗篇", "Psalm", "Psalms", "Ps"]),
    ("Prov", "箴言", ["箴", "箴言", "Proverbs", "Prov"]),
    ("Eccl", "傳道書", ["傳", "传", "傳道書", "传道书", "Ecclesiastes", "Eccl"]),
    ("Song", "雅歌", ["歌", "雅歌", "Song of Songs", "Song"]),
    ("Isa", "以賽亞書", ["賽", "赛", "以賽亞書", "以赛亚书", "Isaiah", "Isa"]),
    ("Jer", "耶利米書", ["耶", "耶利米書", "耶利米书", "Jeremiah", "Jer"]),
    ("Lam", "耶利米哀歌", ["哀", "耶利米哀歌", "Lamentations", "Lam"]),
    ("Ezek", "以西結書", ["結", "结", "以西結書", "以西结书", "Ezekiel", "Ezek"]),
    ("Dan", "但以理書", ["但", "但以理書", "但以理书", "Daniel", "Dan"]),
    ("Hos", "何西阿書", ["何", "何西阿書", "何西阿书", "Hosea", "Hos"]),
    ("Joel", "約珥書", ["珥", "約珥書", "约珥书", "Joel"]),
    ("Amos", "阿摩司書", ["摩", "阿摩司書", "阿摩司书", "Amos"]),
    ("Obad", "俄巴底亞書", ["俄", "俄巴底亞書", "俄巴底亚书", "Obadiah", "Obad"]),
    ("Jonah", "約拿書", ["拿", "約拿書", "约拿书", "Jonah"]),
    ("Mic", "彌迦書", ["彌", "弥", "彌迦書", "弥迦书", "Micah", "Mic"]),
    ("Nah", "那鴻書", ["鴻", "鸿", "那鴻書", "那鸿书", "Nahum", "Nah"]),
    ("Hab", "哈巴谷書", ["哈", "哈巴谷書", "Habakkuk", "Hab"]),
    ("Zeph", "西番雅書", ["番", "西番雅書", "西番雅书", "Zephaniah", "Zeph"]),
    ("Hag", "哈該書", ["該", "该", "哈該書", "哈该书", "Haggai", "Hag"]),
    ("Zech", "撒迦利亞書", ["亞", "亚", "撒迦利亞書", "撒迦利亚书", "Zechariah", "Zech"]),
    ("Mal", "瑪拉基書", ["瑪", "玛", "瑪拉基書", "玛拉基书", "Malachi", "Mal"]),
    ("Matt", "馬太福音", ["太", "馬太", "马太", "馬太福音", "马太福音", "Matthew", "Matt", "Mt"]),
    ("Mark", "馬可福音", ["可", "馬可", "马可", "馬可福音", "马可福音", "Mark", "Mk"]),
    ("Luke", "路加福音", ["路", "路加", "路加福音", "Luke", "Lk"]),
    ("John", "約翰福音", ["約", "约", "約翰", "约翰", "約翰福音", "约翰福音", "John", "Jn"]),
    ("Acts", "使徒行傳", ["徒", "使徒行傳", "使徒行传", "Acts"]),
    ("Rom", "羅馬書", ["羅", "罗", "羅馬書", "罗马书", "Romans", "Rom"]),
    ("1Cor", "哥林多前書", ["林前", "哥林多前書", "哥林多前书", "1 Corinthians", "1Cor", "1 Cor"]),
    ("2Cor", "哥林多後書", ["林後", "林后", "哥林多後書", "哥林多后书", "2 Corinthians", "2Cor", "2 Cor"]),
    ("Gal", "加拉太書", ["加", "加拉太書", "加拉太书", "Galatians", "Gal"]),
    ("Eph", "以弗所書", ["弗", "以弗所書", "以弗所书", "Ephesians", "Eph"]),
    ("Phil", "腓立比書", ["腓", "腓立比書", "腓立比书", "Philippians", "Phil"]),
    ("Col", "歌羅西書", ["西", "歌羅西書", "歌罗西书", "Colossians", "Col"]),
    ("1Thess", "帖撒羅尼迦前書", ["帖前", "帖撒羅尼迦前書", "帖撒罗尼迦前书", "1 Thessalonians", "1Thess", "1 Thess"]),
    ("2Thess", "帖撒羅尼迦後書", ["帖後", "帖后", "帖撒羅尼迦後書", "帖撒罗尼迦后书", "2 Thessalonians", "2Thess", "2 Thess"]),
    ("1Tim", "提摩太前書", ["提前", "提摩太前書", "提摩太前书", "1 Timothy", "1Tim", "1 Tim"]),
    ("2Tim", "提摩太後書", ["提後", "提后", "提摩太後書", "提摩太后书", "2 Timothy", "2Tim", "2 Tim"]),
    ("Titus", "提多書", ["多", "提多書", "提多书", "Titus"]),
    ("Phlm", "腓利門書", ["門", "门", "腓利門書", "腓利门书", "Philemon", "Phlm"]),
    ("Heb", "希伯來書", ["來", "来", "希伯來書", "希伯来书", "Hebrews", "Heb"]),
    ("Jas", "雅各書", ["雅", "雅各書", "雅各书", "James", "Jas"]),
    ("1Pet", "彼得前書", ["彼前", "彼得前書", "彼得前书", "1 Peter", "1Pet", "1 Pet"]),
    ("2Pet", "彼得後書", ["彼後", "彼后", "彼得後書", "彼得后书", "2 Peter", "2Pet", "2 Pet"]),
    ("1John", "約翰一書", ["約一", "约一", "約翰一書", "约翰一书", "1 John", "1John"]),
    ("2John", "約翰二書", ["約二", "约二", "約翰二書", "约翰二书", "2 John", "2John"]),
    ("3John", "約翰三書", ["約三", "约三", "約翰三書", "约翰三书", "3 John", "3John"]),
    ("Jude", "猶大書", ["猶", "犹", "猶大書", "犹大书", "Jude"]),
    ("Rev", "啟示錄", ["啟", "启", "啟示錄", "启示录", "Revelation", "Rev"]),
]

ALIAS_TO_BOOK: Dict[str, Tuple[str, str]] = {}
BOOK_TO_ZH: Dict[str, str] = {}
for osis_book, zh, aliases in BOOKS:
    BOOK_TO_ZH[osis_book] = zh
    for alias in aliases:
        ALIAS_TO_BOOK[alias.lower()] = (osis_book, zh)

_BOOK_PATTERN = "|".join(
    re.escape(alias) for alias in sorted(ALIAS_TO_BOOK, key=len, reverse=True)
)
_REF_RE = re.compile(
    rf"(?P<book>{_BOOK_PATTERN})\s*"
    r"(?P<chapter>\d{1,3})"
    r"(?:\s*[:：]\s*(?P<verse>\d{1,3})"
    r"(?:\s*[-–—~至]\s*(?:(?P<end_chapter>\d{1,3})\s*[:：]\s*)?(?P<end_verse>\d{1,3}))?)?",
    re.IGNORECASE,
)
_OSIS_RE = re.compile(
    r"^(?P<book>[1-3]?[A-Za-z]+)\.(?P<chapter>\d{1,3})"
    r"(?:\.(?P<verse>\d{1,3}))?"
    r"(?:-(?P<end_book>[1-3]?[A-Za-z]+)\.(?P<end_chapter>\d{1,3})(?:\.(?P<end_verse>\d{1,3}))?)?$"
)


def _osis(
    book: str,
    chapter_start: int,
    verse_start: Optional[int],
    chapter_end: Optional[int],
    verse_end: Optional[int],
) -> str:
    start = f"{book}.{chapter_start}"
    if verse_start is not None:
        start = f"{start}.{verse_start}"
    if chapter_end is None and verse_end is None:
        return start
    end_chapter = chapter_end or chapter_start
    end = f"{book}.{end_chapter}"
    if verse_end is not None:
        end = f"{end}.{verse_end}"
    return f"{start}-{end}"


def normalize_ref(raw: str) -> Optional[CanonicalRef]:
    stripped = raw.strip()
    osis_match = _OSIS_RE.match(stripped)
    if osis_match:
        book = osis_match.group("book")
        if book not in BOOK_TO_ZH:
            return None
        chapter_start = int(osis_match.group("chapter"))
        verse_start = int(osis_match.group("verse")) if osis_match.group("verse") else None
        chapter_end = int(osis_match.group("end_chapter")) if osis_match.group("end_chapter") else None
        verse_end = int(osis_match.group("end_verse")) if osis_match.group("end_verse") else None
        return CanonicalRef(
            raw=stripped,
            book=book,
            book_zh=BOOK_TO_ZH[book],
            chapter_start=chapter_start,
            verse_start=verse_start,
            chapter_end=chapter_end,
            verse_end=verse_end,
            osis=_osis(book, chapter_start, verse_start, chapter_end, verse_end),
        )

    match = _REF_RE.search(stripped)
    if not match:
        return None
    alias = match.group("book").lower()
    book, book_zh = ALIAS_TO_BOOK[alias]
    chapter_start = int(match.group("chapter"))
    verse_start = int(match.group("verse")) if match.group("verse") else None
    chapter_end = int(match.group("end_chapter")) if match.group("end_chapter") else None
    verse_end = int(match.group("end_verse")) if match.group("end_verse") else None
    return CanonicalRef(
        raw=match.group(0),
        book=book,
        book_zh=book_zh,
        chapter_start=chapter_start,
        verse_start=verse_start,
        chapter_end=chapter_end,
        verse_end=verse_end,
        osis=_osis(book, chapter_start, verse_start, chapter_end, verse_end),
    )


def extract_refs(text: str) -> List[CanonicalRef]:
    refs: List[CanonicalRef] = []
    seen: set[str] = set()
    for match in _REF_RE.finditer(text or ""):
        ref = normalize_ref(match.group(0))
        if ref and ref.osis not in seen:
            refs.append(ref)
            seen.add(ref.osis)
    return refs


def dedupe_refs(refs: Iterable[CanonicalRef]) -> List[CanonicalRef]:
    out: List[CanonicalRef] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.osis not in seen:
            out.append(ref)
            seen.add(ref.osis)
    return out


def refs_overlap(a: CanonicalRef, b: CanonicalRef) -> bool:
    if a.book != b.book:
        return False
    a_start, a_end = _ref_interval(a)
    b_start, b_end = _ref_interval(b)
    return a_start <= b_end and b_start <= a_end


def _ref_interval(ref: CanonicalRef) -> tuple[int, int]:
    chapter_end = ref.chapter_end or ref.chapter_start
    start_verse = ref.verse_start if ref.verse_start is not None else 0
    if ref.verse_end is not None:
        end_verse = ref.verse_end
    elif ref.verse_start is not None:
        end_verse = ref.verse_start
    else:
        end_verse = 999
    return ref.chapter_start * 1000 + start_verse, chapter_end * 1000 + end_verse
