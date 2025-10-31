"""Utilities for crawling hymn lyrics and writing them to JSON."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md_convert

def fetch_lyrics_text(
    url: str,
    session: requests.Session = None,
    referer: Optional[str] = None,
) -> str:
    session = session or requests.Session()
    _configure_session(session)

    try:
        headers = {
            "Referer": referer,
            "Origin": "https://fcccc.net",
        } if referer else None
        response = session.get(url, timeout=30, headers=headers)
        response.raise_for_status()
    except requests.RequestException:
        return ""
    _ensure_response_encoding(response)

    soup = BeautifulSoup(
        response.content,
        "lxml",
        from_encoding=response.encoding or response.apparent_encoding or "utf-8",
    )
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    _flatten_tables(soup)

#    candidates = _iter_lyrics_candidates(soup)
#    for candidate in candidates:
#        text = _element_to_markdown(candidate)
#        if text:
#            return text

    body = soup.body or soup
    return _element_to_markdown(body)


def _iter_lyrics_candidates(soup: BeautifulSoup) -> Iterable:
    selectors = [
        "div#lyrics",
        "div.lyrics",
        "div#content",
        "div.content",
        "article",
        "div.article",
        "div.post",
        "div.entry-content",
        "main",
        "div#main",
        "section",
        "pre",
        "td",
    ]

    for selector in selectors:
        element = soup.select_one(selector)
        if element and element.get_text(strip=True):
            yield element

    body = soup.body or soup
    blocks = sorted(
        (
            element
            for element in body.find_all(["div", "section", "article", "td", "pre"], recursive=True)
            if element.get_text(strip=True)
        ),
        key=lambda el: len(el.get_text(" ", strip=True)),
        reverse=True,
    )
    for block in blocks:
        yield block


def _element_to_markdown(element: Tag | BeautifulSoup) -> str:
    html = str(element)
    markdown = md_convert(
        html,
        heading_style="ATX",
        bullets="*",
        strip=["script", "style", "noscript"],
    )
    return _normalize_whitespace(markdown)


def _flatten_tables(root: Tag | BeautifulSoup) -> None:
    for table in list(root.find_all("table")):
        rows: list[str] = []
        for tr in table.find_all("tr"):
            cells = [
                cell.get_text(" ", strip=True)
                for cell in tr.find_all(["th", "td"])
            ]
            row_text = " ".join(filter(None, cells)).strip()
            if row_text:
                rows.append(row_text)
        replacement_text = "\n".join(rows).strip()
        if replacement_text:
            table.replace_with(replacement_text)
        else:
            table.decompose()


def _ensure_response_encoding(response: requests.Response) -> None:
    if not response.encoding or response.encoding.lower() == "iso-8859-1":
        apparent = response.apparent_encoding
        if apparent:
            response.encoding = apparent
        else:
            response.encoding = "utf-8"


def _configure_session(session: requests.Session) -> None:
    _apply_default_headers(session)
    _attach_retry_adapter(session)
    _maybe_apply_cookie_header(session)


def _apply_default_headers(session: requests.Session) -> None:
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_1) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9," \
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Sec-CH-UA": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-CH-UA-Mobile": "?0",
            "Sec-CH-UA-Platform": '"macOS"',
            "DNT": "1",
        }
    )


def _attach_retry_adapter(session: requests.Session) -> None:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    retry = Retry(
        total=3,
        read=3,
        connect=3,
        status=3,
        backoff_factor=0.5,
        status_forcelist=(403, 408, 429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)


def _maybe_apply_cookie_header(session: requests.Session) -> None:
    cookie_header = os.getenv("FCCCC_HYMNS_COOKIE")
    if not cookie_header:
        return

    cookies = {}
    for part in cookie_header.split(";"):
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        cookies[name.strip()] = value.strip()
    session.cookies.update(cookies)


def _parse_hymn_index(value: str) -> Optional[int]:
    match = re.search(r"\d+", value)
    if match:
        try:
            return int(match.group())
        except ValueError:
            return None
    return None


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _write_sunday_songs(output_path: str, songs: List[dict]) -> None:
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as fp:
        json.dump(songs, fp, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    lyrics_html = fetch_lyrics_text('https://fcccc.net/hymns/Hymns_HymnsForChurch/459.htm')
    print(lyrics_html)
