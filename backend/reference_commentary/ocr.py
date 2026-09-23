"""OCR one page image with Vertex Gemini.

Why Gemini and not the Claude session: Claude's output filter blocks verbatim
reproduction of a copyrighted book page, and once the photos sit in a session
every later turn is blocked too. Gemini's text goes straight to disk. This is
an owner-ruled exception to "model calls go through subscription CLIs"
(design §4). The model is configured on its own so notes-to-sermon's
`OCR_MODEL` is untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
import time
from typing import Callable

from backend.reference_commentary.store import GAP_MARK, normalize_page


OCR_MODEL = os.getenv("REFERENCE_COMMENTARY_OCR_MODEL", "gemini-3.8-flash")
VERTEX_PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "gen-lang-client-0011233318")
VERTEX_LOCATION = os.getenv("REFERENCE_COMMENTARY_OCR_LOCATION", "global")

# The transcription rules of design §4, as the model sees them.
PROMPT = f"""Transcribe this scanned page of a printed Bible commentary to Markdown.

First line: the printed page number as an HTML comment, e.g. <!-- Page 427 --> or <!-- Page xii -->.
If no page number is printed or readable, write <!-- Page ? -->.

Rules:
1. Word for word. Do not correct anything: keep the author's spelling, punctuation, and abbreviations, and any printing error.
2. Leave out running heads and footers (book title, chapter title, page number); the page number goes only in the first-line comment.
3. Keep the structure: verse-range section headings (e.g. "20:20-28") as Markdown headings, paragraphs, "Notes" sections, and the boxed NIV Bible text.
4. Greek and Hebrew in their own alphabets with every accent and breathing mark; keep italics on transliterations.
5. Rejoin words hyphenated across a line break (recon- + ciliation -> reconciliation); keep real hyphens.
6. Where text is hidden by a finger, cut off, or too blurred to read, write {GAP_MARK} and do not guess.
7. Two-column pages: read the left column, then the right.

Output only the transcription, with no commentary before or after."""

# Asked only when the transcription came back without its page-number line.
PAGE_NUMBER_PROMPT = """What is the printed page number on this book page?
Answer with the number alone (e.g. 428 or xii). If none is printed or readable, answer ?."""

_PAGE_MARK = re.compile(r"^\s*<!--\s*Page\s+([^\s>]+)\s*-->\s*\n?", re.IGNORECASE)


@dataclass(frozen=True)
class OcrPage:
    printed_page: str | None  # None when the model could not read one
    text: str
    model: str


def parse_output(raw: str, model: str = OCR_MODEL) -> OcrPage:
    text = raw.strip()
    # Some responses wrap the whole page in a ```markdown fence.
    fenced = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    match = _PAGE_MARK.match(text)
    page = None
    if match:
        text = text[match.end():].strip()
        try:
            page = normalize_page(match.group(1))
        except ValueError:
            page = None
    return OcrPage(page, text, model)


class OcrUnavailable(RuntimeError):
    """Vertex kept refusing (rate limit or outage). Retry on the next run."""


def _vertex_call(jpeg: bytes, prompt: str) -> str:
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=VERTEX_PROJECT, location=VERTEX_LOCATION)
    response = client.models.generate_content(
        model=OCR_MODEL,
        config=types.GenerateContentConfig(
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
        ),
        contents=[
            types.Content(
                role="user",
                parts=[
                    types.Part.from_text(text=prompt),
                    types.Part.from_bytes(data=jpeg, mime_type="image/jpeg"),
                ],
            )
        ],
    )
    return response.text or ""


def _call_with_retry(
    call: Callable[[bytes, str], str],
    jpeg: bytes,
    prompt: str,
    attempts: int,
    sleep: Callable[[float], None],
) -> str:
    """Retry 429/5xx with backoff; raise OcrUnavailable when Vertex keeps refusing."""

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            raw = call(jpeg, prompt)
        except Exception as exc:  # google-genai raises its own error types
            message = str(exc)
            if not re.search(r"\b(429|RESOURCE_EXHAUSTED|500|502|503|504|UNAVAILABLE)\b", message):
                raise
            last = exc
            sleep(30 * (attempt + 1))
            continue
        if not raw.strip():
            last = RuntimeError("empty OCR response")
            sleep(10)
            continue
        return raw
    raise OcrUnavailable(str(last))


def ocr_page(
    jpeg: bytes,
    *,
    call: Callable[[bytes, str], str] = _vertex_call,
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> OcrPage:
    page = parse_output(_call_with_retry(call, jpeg, PROMPT, attempts, sleep))
    if page.printed_page is not None:
        return page
    # The model sometimes drops the first-line comment; ask for the number alone.
    answer = _call_with_retry(call, jpeg, PAGE_NUMBER_PROMPT, attempts, sleep).strip().strip(".")
    try:
        number = normalize_page(answer)
    except ValueError:
        return page
    return OcrPage(number, page.text, page.model)
