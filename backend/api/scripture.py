from __future__ import annotations

import os
from typing import Dict, Optional
from urllib.parse import quote_plus

import httpx
from fastapi import APIRouter, HTTPException

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

BOOK_NAME_TO_SLUG: Dict[str, str] = {}
BOOK_SLUG_TO_NAME: Dict[str, str] = {}

for slug, name in CHINESE_BOOKS:
    BOOK_SLUG_TO_NAME[slug] = name
    BOOK_NAME_TO_SLUG[name] = slug
    BOOK_NAME_TO_SLUG[name.lower()] = slug
    BOOK_NAME_TO_SLUG[slug] = slug
    BOOK_NAME_TO_SLUG[slug.upper()] = slug
    alias = SLUG_ALIASES.get(slug)
    if alias:
        english = ALIAS_TO_API_BOOK.get(alias)
        if english:
            BOOK_NAME_TO_SLUG[english] = slug
            BOOK_NAME_TO_SLUG[english.lower()] = slug

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
        "chapter": chapter_number,
        "start": verse_start,
        "end": verse_end or verse_start,
    }


def build_params() -> Dict[str, str]:
    return {
        "content-type": "text",
        "include-notes": "false",
        "include-titles": "false",
        "include-chapter-numbers": "false",
        "include-verse-numbers": "true",
        "include-verse-spans": "false",
    }


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
                verse_part = f"{book_slug}.{chapter}.{start}-{book_slug}.{chapter}.{end}" if end != start else f"{chapter}:{start}"
                range_ref = quote_plus(verse_part)
                passages[lang] = await fetch_passage(client, bible_id, range_ref)
            except httpx.HTTPError as exc:
                passages[lang] = ""
                print(f"Failed to fetch passage for {lang}: {exc}")
    return {"reference": info["display"], "passages": passages}
