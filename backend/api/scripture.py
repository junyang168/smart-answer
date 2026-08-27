from __future__ import annotations

import os
import re
from typing import Dict, Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, HTTPException
from opencc import OpenCC

BOOK_TO_OSIS: Dict[str, str] = {
    "GEN": "Gen",
    "EXO": "Exod",
    "LEV": "Lev",
    "NUM": "Num",
    "DEU": "Deut",
    "JOS": "Josh",
    "JDG": "Judg",
    "RUT": "Ruth",
    "1SA": "1Sam",
    "2SA": "2Sam",
    "1KI": "1Kgs",
    "2KI": "2Kgs",
    "1CH": "1Chr",
    "2CH": "2Chr",
    "EZR": "Ezra",
    "NEH": "Neh",
    "EST": "Esth",
    "JOB": "Job",
    "PSA": "Ps",
    "PRO": "Prov",
    "ECC": "Eccl",
    "SNG": "Song",
    "ISA": "Isa",
    "JER": "Jer",
    "LAM": "Lam",
    "EZK": "Ezek",
    "DAN": "Dan",
    "HOS": "Hos",
    "JOL": "Joel",
    "AMO": "Amos",
    "OBA": "Obad",
    "JON": "Jonah",
    "MIC": "Mic",
    "NAM": "Nah",
    "HAB": "Hab",
    "ZEP": "Zeph",
    "HAG": "Hag",
    "ZEC": "Zech",
    "MAL": "Mal",
    "MAT": "Matt",
    "MRK": "Mark",
    "LUK": "Luke",
    "JHN": "John",
    "ACT": "Acts",
    "ROM": "Rom",
    "1CO": "1Cor",
    "2CO": "2Cor",
    "GAL": "Gal",
    "EPH": "Eph",
    "PHP": "Phil",
    "COL": "Col",
    "1TH": "1Thess",
    "2TH": "2Thess",
    "1TI": "1Tim",
    "2TI": "2Tim",
    "TIT": "Titus",
    "PHM": "Phlm",
    "HEB": "Heb",
    "JAS": "Jas",
    "1PE": "1Pet",
    "2PE": "2Pet",
    "1JN": "1John",
    "2JN": "2John",
    "3JN": "3John",
    "JUD": "Jude",
    "REV": "Rev",
}

SLUG_ALIASES: Dict[str, str] = {
    "gen": "GEN",
    "exo": "EXO",
    "lev": "LEV",
    "num": "NUM",
    "deu": "DEU",
    "jos": "JOS",
    "jdg": "JDG",
    "rut": "RUT",
    "1sa": "1SA",
    "2sa": "2SA",
    "1ki": "1KI",
    "2ki": "2KI",
    "1ch": "1CH",
    "2ch": "2CH",
    "ezr": "EZR",
    "neh": "NEH",
    "est": "EST",
    "job": "JOB",
    "psa": "PSA",
    "pro": "PRO",
    "ecc": "ECC",
    "sng": "SNG",
    "isa": "ISA",
    "jer": "JER",
    "lam": "LAM",
    "ezk": "EZK",
    "dan": "DAN",
    "hos": "HOS",
    "jol": "JOL",
    "amo": "AMO",
    "oba": "OBA",
    "jon": "JON",
    "mic": "MIC",
    "nah": "NAM",
    "hab": "HAB",
    "zep": "ZEP",
    "hag": "HAG",
    "zec": "ZEC",
    "mal": "MAL",
    "mat": "MAT",
    "mrk": "MRK",
    "luk": "LUK",
    "jhn": "JHN",
    "act": "ACT",
    "rom": "ROM",
    "1co": "1CO",
    "2co": "2CO",
    "gal": "GAL",
    "eph": "EPH",
    "php": "PHP",
    "col": "COL",
    "1th": "1TH",
    "2th": "2TH",
    "1ti": "1TI",
    "2ti": "2TI",
    "tit": "TIT",
    "phm": "PHM",
    "heb": "HEB",
    "jas": "JAS",
    "1pe": "1PE",
    "2pe": "2PE",
    "1jn": "1JN",
    "2jn": "2JN",
    "3jn": "3JN",
    "jud": "JUD",
    "rev": "REV",
    "創世記": "GEN",
    "出埃及記": "EXO",
    "利未記": "LEV",
    "民數記": "NUM",
    "申命記": "DEU",
    "約書亞記": "JOS",
    "士師記": "JDG",
    "路得記": "RUT",
    "撒母耳記上": "1SA",
    "撒母耳記下": "2SA",
    "列王記上": "1KI",
    "列王記下": "2KI",
    "歷代志上": "1CH",
    "歷代志下": "2CH",
    "以斯拉記": "EZR",
    "尼希米記": "NEH",
    "以斯帖記": "EST",
    "約伯記": "JOB",
    "詩篇": "PSA",
    "箴言": "PRO",
    "傳道書": "ECC",
    "雅歌": "SNG",
    "以賽亞書": "ISA",
    "耶利米書": "JER",
    "耶利米哀歌": "LAM",
    "以西結書": "EZK",
    "但以理書": "DAN",
    "何西阿書": "HOS",
    "約珥書": "JOL",
    "阿摩司書": "AMO",
    "俄巴底亞書": "OBA",
    "約拿書": "JON",
    "彌迦書": "MIC",
    "那鴻書": "NAM",
    "哈巴谷書": "HAB",
    "西番雅書": "ZEP",
    "哈該書": "HAG",
    "撒迦利亞書": "ZEC",
    "瑪拉基書": "MAL",
    "馬太福音": "MAT",
    "馬可福音": "MRK",
    "路加福音": "LUK",
    "約翰福音": "JHN",
    "使徒行傳": "ACT",
    "羅馬書": "ROM",
    "哥林多前書": "1CO",
    "哥林多後書": "2CO",
    "加拉太書": "GAL",
    "以弗所書": "EPH",
    "腓立比書": "PHP",
    "歌羅西書": "COL",
    "帖撒羅尼迦前書": "1TH",
    "帖撒羅尼迦後書": "2TH",
    "提摩太前書": "1TI",
    "提摩太後書": "2TI",
    "提多書": "TIT",
    "腓利門書": "PHM",
    "希伯來書": "HEB",
    "雅各書": "JAS",
    "彼得前書": "1PE",
    "彼得後書": "2PE",
    "約翰一書": "1JN",
    "約翰二書": "2JN",
    "約翰三書": "3JN",
    "猶大書": "JUD",
    "啟示錄": "REV",
}

ALIAS_TO_API_BOOK: Dict[str, str] = {
    "GEN": "Genesis",
    "EXO": "Exodus",
    "LEV": "Leviticus",
    "NUM": "Numbers",
    "DEU": "Deuteronomy",
    "JOS": "Joshua",
    "JDG": "Judges",
    "RUT": "Ruth",
    "1SA": "1 Samuel",
    "2SA": "2 Samuel",
    "1KI": "1 Kings",
    "2KI": "2 Kings",
    "1CH": "1 Chronicles",
    "2CH": "2 Chronicles",
    "EZR": "Ezra",
    "NEH": "Nehemiah",
    "EST": "Esther",
    "JOB": "Job",
    "PSA": "Psalms",
    "PRO": "Proverbs",
    "ECC": "Ecclesiastes",
    "SNG": "Song of Solomon",
    "ISA": "Isaiah",
    "JER": "Jeremiah",
    "LAM": "Lamentations",
    "EZK": "Ezekiel",
    "DAN": "Daniel",
    "HOS": "Hosea",
    "JOL": "Joel",
    "AMO": "Amos",
    "OBA": "Obadiah",
    "JON": "Jonah",
    "MIC": "Micah",
    "NAM": "Nahum",
    "HAB": "Habakkuk",
    "ZEP": "Zephaniah",
    "HAG": "Haggai",
    "ZEC": "Zechariah",
    "MAL": "Malachi",
    "MAT": "Matthew",
    "MRK": "Mark",
    "LUK": "Luke",
    "JHN": "John",
    "ACT": "Acts",
    "ROM": "Romans",
    "1CO": "1 Corinthians",
    "2CO": "2 Corinthians",
    "GAL": "Galatians",
    "EPH": "Ephesians",
    "PHP": "Philippians",
    "COL": "Colossians",
    "1TH": "1 Thessalonians",
    "2TH": "2 Thessalonians",
    "1TI": "1 Timothy",
    "2TI": "2 Timothy",
    "TIT": "Titus",
    "PHM": "Philemon",
    "HEB": "Hebrews",
    "JAS": "James",
    "1PE": "1 Peter",
    "2PE": "2 Peter",
    "1JN": "1 John",
    "2JN": "2 John",
    "3JN": "3 John",
    "JUD": "Jude",
    "REV": "Revelation",
}

CHINESE_BOOKS: tuple[tuple[str, str], ...] = (
    ("gen", "創世記"),
    ("exo", "出埃及記"),
    ("lev", "利未記"),
    ("num", "民數記"),
    ("deu", "申命記"),
    ("jos", "約書亞記"),
    ("jdg", "士師記"),
    ("rut", "路得記"),
    ("1sa", "撒母耳記上"),
    ("2sa", "撒母耳記下"),
    ("1ki", "列王記上"),
    ("2ki", "列王記下"),
    ("1ch", "歷代志上"),
    ("2ch", "歷代志下"),
    ("ezr", "以斯拉記"),
    ("neh", "尼希米記"),
    ("est", "以斯帖記"),
    ("job", "約伯記"),
    ("psa", "詩篇"),
    ("pro", "箴言"),
    ("ecc", "傳道書"),
    ("sng", "雅歌"),
    ("isa", "以賽亞書"),
    ("jer", "耶利米書"),
    ("lam", "耶利米哀歌"),
    ("ezk", "以西結書"),
    ("dan", "但以理書"),
    ("hos", "何西阿書"),
    ("jol", "約珥書"),
    ("amo", "阿摩司書"),
    ("oba", "俄巴底亞書"),
    ("jon", "約拿書"),
    ("mic", "彌迦書"),
    ("nam", "那鴻書"),
    ("hab", "哈巴谷書"),
    ("zep", "西番雅書"),
    ("hag", "哈該書"),
    ("zec", "撒迦利亞書"),
    ("mal", "瑪拉基書"),
    ("mat", "馬太福音"),
    ("mrk", "馬可福音"),
    ("luk", "路加福音"),
    ("jhn", "約翰福音"),
    ("act", "使徒行傳"),
    ("rom", "羅馬書"),
    ("1co", "哥林多前書"),
    ("2co", "哥林多後書"),
    ("gal", "加拉太書"),
    ("eph", "以弗所書"),
    ("php", "腓立比書"),
    ("col", "歌羅西書"),
    ("1th", "帖撒羅尼迦前書"),
    ("2th", "帖撒羅尼迦後書"),
    ("1ti", "提摩太前書"),
    ("2ti", "提摩太後書"),
    ("tit", "提多書"),
    ("phm", "腓利門書"),
    ("heb", "希伯來書"),
    ("jas", "雅各書"),
    ("1pe", "彼得前書"),
    ("2pe", "彼得後書"),
    ("1jn", "約翰一書"),
    ("2jn", "約翰二書"),
    ("3jn", "約翰三書"),
    ("jud", "猶大書"),
    ("rev", "啟示錄"),
)

CHINESE_BOOK_ABBREVIATIONS: Dict[str, str] = {
    # Old Testament
    "創": "gen",
    "创": "gen",
    "出": "exo",
    "利": "lev",
    "民": "num",
    "申": "deu",
    "書": "jos",
    "书": "jos",
    "約書": "jos",
    "约书": "jos",
    "約書亞": "jos",
    "约书亚": "jos",
    "士": "jdg",
    "得": "rut",
    "撒上": "1sa",
    "撒下": "2sa",
    "王上": "1ki",
    "王下": "2ki",
    "代上": "1ch",
    "代下": "2ch",
    "拉": "ezr",
    "尼": "neh",
    "斯": "est",
    "伯": "job",
    "詩": "psa",
    "诗": "psa",
    "箴": "pro",
    "傳": "ecc",
    "传": "ecc",
    "歌": "sng",
    "賽": "isa",
    "赛": "isa",
    "耶": "jer",
    "哀": "lam",
    "結": "ezk",
    "结": "ezk",
    "但": "dan",
    "何": "hos",
    "珥": "jol",
    "摩": "amo",
    "俄": "oba",
    "拿": "jon",
    "彌": "mic",
    "弥": "mic",
    "鴻": "nam",
    "鸿": "nam",
    "哈": "hab",
    "番": "zep",
    "該": "hag",
    "该": "hag",
    "亞": "zec",
    "亚": "zec",
    "瑪": "mal",
    "玛": "mal",
    # New Testament
    "太": "mat",
    "可": "mrk",
    "路": "luk",
    "約": "jhn",
    "约": "jhn",
    "徒": "act",
    "羅": "rom",
    "罗": "rom",
    "林前": "1co",
    "哥前": "1co",
    "林後": "2co",
    "林后": "2co",
    "哥後": "2co",
    "哥后": "2co",
    "加": "gal",
    "弗": "eph",
    "腓": "php",
    "西": "col",
    "帖前": "1th",
    "帖後": "2th",
    "帖后": "2th",
    "提前": "1ti",
    "提後": "2ti",
    "提后": "2ti",
    "多": "tit",
    "門": "phm",
    "门": "phm",
    "來": "heb",
    "来": "heb",
    "雅": "jas",
    "彼前": "1pe",
    "彼後": "2pe",
    "彼后": "2pe",
    "約一": "1jn",
    "约一": "1jn",
    "約二": "2jn",
    "约二": "2jn",
    "約三": "3jn",
    "约三": "3jn",
    "猶": "jud",
    "犹": "jud",
    "啟": "rev",
    "启": "rev",
}

BOOK_NAME_TO_SLUG: Dict[str, str] = {}
BOOK_SLUG_TO_NAME: Dict[str, str] = {}

# 书名表是繁体的，而库里两种都有。
#
# 观点的 `scripture_scope` 里「馬太福音16:1」和「马太福音16:1」并存——命题正文
# 是简体写的，经文范围有时跟着写成简体。缩写表本来就两种都收（創／创），全名表
# 只收了繁体，于是简体全名一律查不到书卷。
#
# opencc 已经是本项目的依赖（backend/requirements.txt，sermon_to_video 在用）。
_TO_SIMPLIFIED = OpenCC("t2s")

for slug, name in CHINESE_BOOKS:
    BOOK_SLUG_TO_NAME[slug] = name
    BOOK_NAME_TO_SLUG[name] = slug
    BOOK_NAME_TO_SLUG[name.lower()] = slug
    simplified = _TO_SIMPLIFIED.convert(name)
    if simplified != name:
        BOOK_NAME_TO_SLUG.setdefault(simplified, slug)
    BOOK_NAME_TO_SLUG[slug] = slug
    BOOK_NAME_TO_SLUG[slug.upper()] = slug
    alias = SLUG_ALIASES.get(slug)
    if alias:
        english = ALIAS_TO_API_BOOK.get(alias)
        if english:
            BOOK_NAME_TO_SLUG[english] = slug
            BOOK_NAME_TO_SLUG[english.lower()] = slug

for alias, slug in CHINESE_BOOK_ABBREVIATIONS.items():
    BOOK_NAME_TO_SLUG[alias] = slug
    BOOK_NAME_TO_SLUG[alias.lower()] = slug

API_ENDPOINT = os.getenv("SCRIPTURE_API_ENDPOINT", "https://api.scripture.api.bible/v1/bibles")
SCRIPTURE_API_KEY = os.getenv("SCRIPTURE_API_KEY")

BIBLE_IDS: Dict[str, Optional[str]] = {
    "el": os.getenv("SCRIPTURE_BIBLE_ID_EL"),
    "he": os.getenv("SCRIPTURE_BIBLE_ID_HE"),
}

BIBLE_API_TRANSLATION_ZH = os.getenv("BIBLE_API_TRANSLATION_ZH", "cuv")
BIBLE_API_TRANSLATION_EN = os.getenv("BIBLE_API_TRANSLATION_EN", "kjv")

router = APIRouter(prefix="/scripture", tags=["scripture"])


@router.get("/books")
async def list_scripture_books() -> list[Dict[str, str]]:
    return [{"slug": slug, "name": name} for slug, name in CHINESE_BOOKS]


def parse_reference(slug: str) -> Dict[str, object]:
    cleaned = slug.replace("scripture-", "", 1)
    parts = cleaned.split("-")
    if len(parts) < 3:
        raise ValueError("Invalid scripture reference format")
    book, chapter, start_verse, *maybe_end = parts
    book_slug = BOOK_NAME_TO_SLUG.get(book) or BOOK_NAME_TO_SLUG.get(book.lower()) or book.lower()
    alias = SLUG_ALIASES.get(book_slug)
    if not alias:
        raise ValueError(f"Unknown book abbreviation: {book}")

    osis_book = BOOK_TO_OSIS[alias]
    try:
        chapter_number = int(chapter)
        verse_start = int(start_verse)
        verse_end = int(maybe_end[0]) if maybe_end else None
    except ValueError as exc:
        raise ValueError("Chapter and verse must be numeric") from exc

    start_ref = f"{osis_book}.{chapter_number}.{verse_start}"
    end_ref = f"{osis_book}.{chapter_number}.{verse_end}" if verse_end else None
    display = f"{osis_book} {chapter_number}:{verse_start}{f'-{verse_end}' if verse_end else ''}"
    return {
        "osis_start": start_ref,
        "osis_end": end_ref,
        "display": display,
        "slug": book_slug,
        "slug_book": alias,
        "osis_book": osis_book,
        "chapter": chapter_number,
        "start": verse_start,
        "end": verse_end or verse_start,
    }


def format_chinese_reference(slug: str) -> str:
    try:
        info = parse_reference(slug)
    except ValueError:
        return slug

    book_slug = str(info.get("slug", "")).lower()
    book_name = BOOK_SLUG_TO_NAME.get(book_slug, book_slug.upper())
    chapter = info.get("chapter")
    start = info.get("start")
    end = info.get("end")

    if not isinstance(chapter, int) or not isinstance(start, int) or not isinstance(end, int):
        return f"{book_name} {info.get('display', slug)}"

    verse_part = f"{start}-{end}" if end != start else f"{start}"
    return f"{book_name} {chapter}:{verse_part}"


_PAREN_REFERENCE_PATTERN = re.compile(r"（(?P<content>[^（）]+)）")
# 书名里的数字只能在开头。
#
# 原来书名是 `[A-Za-z0-9\u4e00-\u9fff…]+`，数字也算书名的一部分，于是
# 「馬太福音16:19」里贪婪的书名吃掉了 `1`，剩下 `6` 当章号，`馬太福音1` 查不到
# 书卷，整条引用就不转链接了。单位数章号不受影响——「馬太福音8:22」一直是好
# 的——所以这个 bug 藏了很久：读者看到的是「（馬太福音8:22）」有链接、
# 「（馬太福音16:19）」没有。太16 是本项目的正题，整章都在这个坑里。
#
# 数字仍要允许在开头，「彼得後書」「1jn」「2pe」靠它。
_SINGLE_REFERENCE_PATTERN = re.compile(
    r"(?P<prefix>\s*)(?<!\[)(?P<book>[A-Za-z0-9]?[A-Za-z\u4e00-\u9fff一二三上下前後后]+)"
    r"\s*(?P<chapter>\d+)\s*(?:[:：])\s*(?P<start>\d+)"
    # 结尾的数字后面不能再跟冒号，否则「16:28-17:2」这种跨章范围会被读成
    # 「16:28-17」——第 17 节根本不是它的意思。挡掉之后退回单节，链接指向
    # 16:28，剩下的「-17:2」留作原文。跨章范围本来就不在这条正则的能力内。
    r"(?:\s*(?:[-–—~～至]\s*(?P<end>\d+)(?!\d)(?!\s*[:：])))?"
)
_REFERENCE_SEPARATOR_CHARS = set(" ，,、；;/／&＆和與与及跟或")


def _resolve_book_slug(token: str) -> Optional[str]:
    normalized = token.strip()
    if not normalized:
        return None
    slug = BOOK_NAME_TO_SLUG.get(normalized)
    if slug:
        return slug
    compact = normalized.replace(" ", "")
    slug = BOOK_NAME_TO_SLUG.get(compact)
    if slug:
        return slug
    slug = BOOK_NAME_TO_SLUG.get(compact.lower())
    if slug:
        return slug
    return None


def reference_slugs(text: str) -> list[str]:
    """从一段文字里认出所有经文引用，返回 `mat-16-18-19` 这样的 slug。

    书名表、缩写表、繁简别名都在本模块，所以认经文这件事归这里做，别处不另起一
    套。`parse_reference` 吃的就是这个 slug 格式。

    认不出书卷的跳过——「聖經」「詩篇」「使徒行傳15章」这类没有节号的，返回空
    列表，由调用方决定怎么办。
    """

    found: list[str] = []
    for match in _SINGLE_REFERENCE_PATTERN.finditer(text or ""):
        slug = _resolve_book_slug(match.group("book"))
        if not slug:
            continue
        end = match.group("end")
        tail = f"-{end}" if end else ""
        candidate = f"{slug}-{match.group('chapter')}-{match.group('start')}{tail}"
        if candidate not in found:
            found.append(candidate)
    return found


#: 教授口里的书名简称。
#:
#: 他讲课说「馬太十六章十八節」「約翰二十章二十三節」，不说全名。书名表收的是
#: 「馬太福音」「約翰福音」，所以这些简称查不到。这里只给口语识别用，不进
#: `BOOK_NAME_TO_SLUG`——那张表还管文章页的引用转链接，不该跟着放宽。
SPOKEN_BOOK_ALIASES: Dict[str, str] = {
    "馬太": "mat", "马太": "mat",
    "馬可": "mrk", "马可": "mrk",
    "路加": "luk",
    "約翰": "jhn", "约翰": "jhn",
    "使徒": "act",
    "羅馬": "rom", "罗马": "rom",
    "希伯來": "heb", "希伯来": "heb",
    "啟示錄": "rev", "启示录": "rev",
}

_CHINESE_DIGITS: Dict[str, int] = {c: i for i, c in enumerate("零一二三四五六七八九")}

#: 书名里不能出现数字字符，也不能包含「第」。
#:
#: 不挡的话贪婪的书名会把章号的第一个字吃掉：「馬太十六章十八節」里书名吞了
#: 「十」，剩下「六」当章号，认成了马太 6:18。「第」同理，「以弗所書第四章」的
#: 书名会带上「第」而查不到书卷。
_SPOKEN_BOOK_CHAR = r"(?:(?![零一二三四五六七八九十百第章節节])[\u4e00-\u9fff])"

#: 他念经文的三种说法。
#:
#: 一、书名 + 章 + 节：「以弗所書第四章第十一節」「馬太十六章十八節」。
#: 二、只报章：「因為耶穌在這個地方，馬太十六章，耶穌帶了門徒到該撒利亞……」。
#: 三、只报节：「十六節說，西門彼得回答說……」「我特別來看十九節那一段」。
#:
#: 第三种最要紧也最容易漏。他讲一章经文时章号是不重复的，一路只说「十八節」
#: 「十九節」「二十一節」——五篇讲道里这样的说法有 21 处，而幻灯要跟着他走，靠
#: 的正是这些。原来只认第一种，于是（五）1 开头两分钟里他从十六节念到十九节，
#: 幻灯一直停在十六节。
_SPOKEN_REFERENCE_PATTERN = re.compile(
    rf"(?P<book>{_SPOKEN_BOOK_CHAR}{{1,7}})?\s*[》〉」』〕】）]?\s*(?:第)?"
    # 诗篇用「篇」不用「章」：「詩篇三十四篇一節到三節」。不认的话「到三節」会
    # 带上前面别处的章号，成了另一卷书的第三节。
    rf"(?P<chapter>[零一二三四五六七八九十百\d]+)\s*[章篇]"
    rf"(?:\s*(?:第)?(?P<verse>[零一二三四五六七八九十百\d]+)\s*[節节])?"
)

#: 只报节：「十六節說」「我特別來看十九節那一段」「第十八節：我實在告訴你們」。
#:
#: 「這一節聖經很多人一直是很有困擾」「把一節聖經拿來亂扯」不是引用——这些
#: 「一節」说的是「这段经文」，不是第一节。「詩篇三十四篇一節」的「篇」同理，
#: 那是诗篇的章。
_BARE_VERSE_PATTERN = re.compile(
    r"(?<![這这那每上下同本一把幾几某整半篇章])(?:第)?"
    r"(?P<verse>[零一二三四五六七八九十百\d]+)\s*[節节]"
)

#: 只报节的说法，最多离上一次报章多远还算数。
#:
#: 他换书卷时会重报（「使徒行傳第十五章十三節開始」），可换之前那几句里的「第
#: 十九節」说的还是使徒行传。字数窗口挡住的是隔了半篇之后的孤立「第三節」。
BARE_VERSE_REACH = 1200


def chinese_number(text: str) -> Optional[int]:
    """「二十三」→ 23。阿拉伯数字原样返回。"""

    text = text.strip()
    if text.isdigit():
        return int(text)
    if not text or any(c not in "零一二三四五六七八九十百" for c in text):
        return None
    total = 0
    digit = 0
    for char in text:
        if char in _CHINESE_DIGITS:
            digit = _CHINESE_DIGITS[char]
        elif char == "十":
            total += (digit or 1) * 10
            digit = 0
        elif char == "百":
            total += (digit or 1) * 100
            digit = 0
    return total + digit


def _spoken_book(token: Optional[str]) -> Optional[str]:
    """从「可是以弗所書」这样的一串字里认出书名。

    正则捕的是「章」前面的几个汉字，前面粘着上一句的尾巴是常事——量到的有
    「各位看以弗所書」「可是以弗所書」「的罪約翰福音」。所以从最长的后缀往短了
    试，第一个查得到的就是书名。
    """

    if not token:
        return None
    # 至少两个字。单字缩写（太、弗、書）在写出来的引用里是正经写法，在连着的
    # 讲话里不是：「帖撒羅尼迦前書五章二十三節」一路退到「書」就命中了
    # `BOOK_NAME_TO_SLUG["書"]`——约书亚记，跟原话差了三十多卷。
    for size in range(len(token), 1, -1):
        tail = token[-size:]
        slug = BOOK_NAME_TO_SLUG.get(tail) or SPOKEN_BOOK_ALIASES.get(tail)
        if slug:
            return slug
    return None


def spoken_references(text: str) -> list[tuple[int, str]]:
    """教授在这段话里念到的经文，按出现次序返回 `(第几个字, slug)`。

    观点的 `scripture_scope` 说不出「他此刻在念哪一节」——同一个观点的证据能横跨
    十分钟，中间他翻了三处经文。他自己是念出来的：「可是以弗所書二章二十節，
    『並且教會被建造在使徒和先知的根基上』」，那才是那一刻屏幕上该有的字。

    书名和章号都往下带。他讲一章经文时不重复报，一路只说「十八節」「十九節」；
    换书卷时会重报（「使徒行傳第十五章十三節開始」），带下去的就跟着换。
    """

    found: list[tuple[int, str]] = []
    carried_book: Optional[str] = None
    carried_chapter: Optional[int] = None
    chapter_at = -10**9

    marks: list[tuple[int, Optional[str], Optional[int], Optional[int]]] = []
    for match in _SPOKEN_REFERENCE_PATTERN.finditer(text or ""):
        marks.append((
            match.start(),
            _spoken_book(match.group("book")),
            chinese_number(match.group("chapter")),
            chinese_number(match.group("verse")) if match.group("verse") else None,
        ))
    for match in _BARE_VERSE_PATTERN.finditer(text or ""):
        # 「十六章十八節」里的「十八節」已经被上面认过了，别再算一次。
        if any(start <= match.start() < start + 14 for start, _, _, verse in marks if verse):
            continue
        marks.append((match.start(), None, None, chinese_number(match.group("verse"))))
    marks.sort()

    for position, book, chapter, verse in marks:
        if book:
            carried_book = book
        if chapter:
            carried_chapter = chapter
            chapter_at = position
        if verse is None:
            continue
        if not carried_book or not carried_chapter:
            continue
        if not chapter and position - chapter_at > BARE_VERSE_REACH:
            continue
        found.append((position, f"{carried_book}-{carried_chapter}-{verse}"))
    return found


def _build_reference_link(slug: str, chapter: str, start: str, end: Optional[str]) -> str:
    book_name = BOOK_SLUG_TO_NAME.get(slug, slug.upper())
    chapter_number = int(chapter)
    verse_start = int(start)
    verse_end = int(end) if end else None
    verse_part = f"{verse_start}-{verse_end}" if verse_end is not None else str(verse_start)
    anchor = f"#scripture-{slug}-{chapter_number}-{verse_start}"
    if verse_end is not None:
        anchor += f"-{verse_end}"
    return f"[{book_name} {chapter_number}:{verse_part}]({anchor})"


def _convert_references_in_segment(content: str) -> tuple[str, list[tuple[int, int]]]:
    result: list[str] = []
    replaced_spans: list[tuple[int, int]] = []
    last_index = 0
    for match in _SINGLE_REFERENCE_PATTERN.finditer(content):
        start, end = match.span()
        result.append(content[last_index:start])
        slug = _resolve_book_slug(match.group("book"))
        if not slug:
            result.append(content[start:end])
        else:
            prefix = match.group("prefix") or ""
            replacement = _build_reference_link(slug, match.group("chapter"), match.group("start"), match.group("end"))
            result.append(prefix + replacement)
            replaced_spans.append((start, end))
        last_index = end
    result.append(content[last_index:])
    return "".join(result), replaced_spans


def _content_is_reference_only(content: str, spans: list[tuple[int, int]]) -> bool:
    if not spans:
        return False
    remaining_parts: list[str] = []
    last = 0
    for start, end in spans:
        remaining_parts.append(content[last:start])
        last = end
    remaining_parts.append(content[last:])
    residual = "".join(remaining_parts).strip()
    if not residual:
        return True
    for char in residual:
        if char.isspace():
            continue
        if char not in _REFERENCE_SEPARATOR_CHARS:
            return False
    return True


def convert_parenthetical_references(markdown: str) -> str:
    if not markdown:
        return markdown

    def _replace(match: re.Match) -> str:
        content = match.group("content") or ""
        converted, spans = _convert_references_in_segment(content)
        if not spans:
            return match.group(0)
        if _content_is_reference_only(content, spans):
            return converted.strip()
        return f"（{converted}）"

    return _PAREN_REFERENCE_PATTERN.sub(_replace, markdown)


def build_params() -> Dict[str, str]:
    return {
        "content-type": "text",
        "include-notes": "false",
        "include-titles": "false",
        "include-chapter-numbers": "false",
        "include-verse-numbers": "true",
        "include-verse-spans": "false",
    }


def passage_id(book_slug: str, chapter: int, start: int, end: int) -> str:
    """API.Bible 的 passage id，单节和范围都写成 `MAT.16.18` 这种形状。

    原来单节拼的是 `16:18`——那不是 passage id，接口回空字符串，于是任何单节的
    希腊文／希伯来文一律取不到，只有范围（`16:18-19`）才有。原声幻灯上太16:18
    单独一节时希腊原文是空的，就是这里；文章页的原文弹窗同样中招。
    """

    head = f"{book_slug}.{chapter}.{start}"
    return head if end == start else f"{head}-{book_slug}.{chapter}.{end}"


async def fetch_passage(client: httpx.AsyncClient, bible_id: str, reference: str) -> str:
    url = f"{API_ENDPOINT}/{bible_id}/passages/{reference}"
    response = await client.get(url, headers={"api-key": SCRIPTURE_API_KEY or ""}, params=build_params())
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", {}).get("content", "")


async def fetch_bible_api(
    client: httpx.AsyncClient,
    book_slug: str,
    chapter: int,
    start: int,
    end: int,
    translation: Optional[str],
) -> str:
    if not translation:
        return ""
    verse_part = f"{chapter}:{start}-{end}" if end != start else f"{chapter}:{start}"
    english_book = ALIAS_TO_API_BOOK.get(book_slug, book_slug)
    query = quote_plus(f"{book_slug} {verse_part}")
    url = f"https://bible-api.com/{query}?translation={translation}"
    response = await client.get(url)
    response.raise_for_status()
    data = response.json()
    text = (data.get("text") or "").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    html = "<br/>".join(lines)
    return f"<p>{html}</p>"


@router.get("/basic/{reference}")
async def get_scripture_basic(reference: str):
    info = parse_reference(reference)
    passages: Dict[str, str] = {"zh": "", "en": ""}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            passages["zh"] = await fetch_bible_api(
                client,
                info["slug_book"],
                info["chapter"],
                info["start"],
                info["end"],
                BIBLE_API_TRANSLATION_ZH,
            )
        except httpx.HTTPError as exc:
            print(f"Failed to fetch Chinese passage: {exc}")
        try:
            passages["en"] = await fetch_bible_api(
                client,
                info["slug_book"],
                info["chapter"],
                info["start"],
                info["end"],
                BIBLE_API_TRANSLATION_EN,
            )
        except httpx.HTTPError as exc:
            print(f"Failed to fetch English passage: {exc}")
    return {"reference": info["display"], "passages": passages}


@router.get("/original/{reference}")
async def get_scripture_original(reference: str):
    if not SCRIPTURE_API_KEY:
        raise HTTPException(status_code=500, detail="SCRIPTURE_API_KEY is not configured")
    try:
        info = parse_reference(reference)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    passages: Dict[str, str] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for lang in ("el", "he"):
            bible_id = BIBLE_IDS.get(lang)
            if not bible_id:
                continue
            try:
                chapter = info["chapter"]
                start = info["start"]
                end = info["end"]
                book_slug = info["slug_book"]
                range_ref = quote_plus(passage_id(book_slug, chapter, start, end))
                passages[lang] = await fetch_passage(client, bible_id, range_ref)
            except httpx.HTTPError as exc:
                passages[lang] = ""
                print(f"Failed to fetch passage for {lang}: {exc}")
    return {"reference": info["display"], "passages": passages}
