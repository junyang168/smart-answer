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

API_ENDPOINT = os.getenv("SCRIPTURE_API_ENDPOINT", "https://api.scripture.api.bible/v1/bibles")
SCRIPTURE_API_KEY = os.getenv("SCRIPTURE_API_KEY")

BIBLE_IDS: Dict[str, Optional[str]] = {
    "el": os.getenv("SCRIPTURE_BIBLE_ID_EL"),
    "he": os.getenv("SCRIPTURE_BIBLE_ID_HE"),
}

BIBLE_API_TRANSLATION_ZH = os.getenv("BIBLE_API_TRANSLATION_ZH", "cuv")
BIBLE_API_TRANSLATION_EN = os.getenv("BIBLE_API_TRANSLATION_EN", "kjv")

router = APIRouter(prefix="/scripture", tags=["scripture"])


def parse_reference(slug: str) -> Dict[str, object]:
    cleaned = slug.replace("scripture-", "", 1)
    parts = cleaned.split("-")
    if len(parts) < 3:
        raise ValueError("Invalid scripture reference format")
    book, chapter, start_verse, *maybe_end = parts
    alias = SLUG_ALIASES.get(book.lower())
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
