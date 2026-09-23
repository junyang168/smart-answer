"""Page records for one printed volume.

Layout under `<root>/<volume_id>/`:

    pages.json                  one record per printed page
    images/p0427-<sha12>.jpg    display image; every scan of a page is kept
    originals/<sha12>.<ext>     the file as it arrived (HEIC or PDF)
    text/p0427-<sha12>.md       every text version, content-addressed

A page is keyed by its printed page number, never by photo order: photos get
retaken and skipped, printed pages do not change. Nothing is overwritten. A
rescan becomes the current image; its OCR text becomes current only while the
page is still unproofread, so a new OCR pass never silently undoes a proofread.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any


STATUS_OCR = "ocr"
STATUS_PROOFREAD = "proofread"

GAP_MARK = "[…]"

_ROMAN = re.compile(r"^[ivxlcdm]+$", re.IGNORECASE)
# Section headings carry the verse range: `#### 20:20–28` from Gemini, or a
# bold line `**18:3-4**` in the July transcription. A bare `20:3` line is not
# a heading, so one of the two markers is required.
_RANGE = r"(\d{1,2}):(\d{1,3})(?:\s*[–-]\s*(?:(\d{1,2}):)?(\d{1,3}))?"
_SECTION_RANGE = re.compile(
    rf"^(?:#{{1,6}}\s*(?:\*\*)?{_RANGE}(?:\*\*)?|\*\*{_RANGE}\*\*)\s*$",
    re.MULTILINE,
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_page(value: str | int) -> str:
    text = str(value).strip()
    if text.isdigit():
        return str(int(text))
    if _ROMAN.match(text):
        return text.lower()
    raise ValueError(f"not a printed page number: {value!r}")


def page_sort_key(page: str) -> tuple[int, int, str]:
    # Front matter (roman) sorts before the body.
    return (1, int(page), "") if page.isdigit() else (0, 0, page)


def page_slug(page: str) -> str:
    return f"p{int(page):04d}" if page.isdigit() else f"p-{page}"


def find_gaps(text: str) -> list[dict[str, int]]:
    """Every `[…]` the OCR left for text it could not see, by line."""

    gaps = []
    for number, line in enumerate(text.splitlines(), start=1):
        for match in re.finditer(re.escape(GAP_MARK), line):
            gaps.append({"line": number, "column": match.start() + 1})
    return gaps


def find_passages(text: str) -> list[str]:
    """Verse ranges named by the section headings on this page, e.g. `20:20-28`."""

    passages = []
    for match in _SECTION_RANGE.findall(text):
        chapter, verse, end_chapter, end_verse = match[:4] if match[0] else match[4:]
        ref = f"{int(chapter)}:{int(verse)}"
        if end_verse:
            ref += f"-{int(end_chapter)}:{int(end_verse)}" if end_chapter else f"-{int(end_verse)}"
        if ref not in passages:
            passages.append(ref)
    return passages


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _write_once(path: Path, data: bytes) -> None:
    if not path.exists():
        _atomic_write(path, data)


@dataclass(frozen=True)
class IngestResult:
    volume_id: str
    printed_page: str
    action: str  # "created" | "updated" | "rescan_kept_proofread" | "unchanged"


class VolumeStore:
    def __init__(self, directory: Path, volume_id: str):
        self.directory = directory
        self.volume_id = volume_id
        self.pages_file = directory / "pages.json"

    # -- reading -----------------------------------------------------------

    def load(self) -> dict[str, Any]:
        if not self.pages_file.exists():
            return {"volume_id": self.volume_id, "pages": {}}
        return json.loads(self.pages_file.read_text(encoding="utf-8"))

    def pages(self) -> list[dict[str, Any]]:
        data = self.load()["pages"]
        return [data[key] for key in sorted(data, key=page_sort_key)]

    def page(self, printed_page: str | int) -> dict[str, Any] | None:
        return self.load()["pages"].get(normalize_page(printed_page))

    def read_text(self, record: dict[str, Any]) -> str:
        return (self.directory / record["text"]["file"]).read_text(encoding="utf-8")

    # -- writing -----------------------------------------------------------

    def _save(self, data: dict[str, Any]) -> None:
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _atomic_write(self.pages_file, payload.encode("utf-8"))

    def _store_text(self, page: str, text: str) -> dict[str, str]:
        body = text.strip() + "\n"
        digest = sha256_bytes(body.encode("utf-8"))
        name = f"text/{page_slug(page)}-{digest[:12]}.md"
        _write_once(self.directory / name, body.encode("utf-8"))
        return {"file": name, "sha256": digest}

    def ingest(
        self,
        *,
        printed_page: str | int,
        image: bytes,
        text: str,
        source: dict[str, Any],
        original: bytes | None = None,
        original_suffix: str = "",
        chapter: int | None = None,
    ) -> IngestResult:
        """Record one OCR'd page scan. `image` is the JPEG shown in the browser."""

        page = normalize_page(printed_page)
        image_sha = sha256_bytes(image)
        image_ref = {
            "file": f"images/{page_slug(page)}-{image_sha[:12]}.jpg",
            "sha256": image_sha,
            "added_at": now_iso(),
            "source": dict(source),
        }
        _write_once(self.directory / image_ref["file"], image)
        if original is not None:
            original_sha = sha256_bytes(original)
            name = f"originals/{original_sha[:12]}{original_suffix.lower()}"
            _write_once(self.directory / name, original)
            image_ref["source"]["original"] = name
            image_ref["source"]["original_sha256"] = original_sha

        text_ref = self._store_text(page, text)
        data = self.load()
        pages = data["pages"]
        record = pages.get(page)
        at = now_iso()

        if record is None:
            pages[page] = {
                "printed_page": page,
                "chapters": [chapter] if chapter else [],
                "passages": find_passages(text),
                "image": image_ref,
                "image_history": [],
                "text": text_ref,
                "status": STATUS_OCR,
                "gaps": find_gaps(text),
                "revisions": [{"at": at, "from": None, "to": text_ref["sha256"], "source": "ocr"}],
                "pending_ocr": None,
            }
            self._save(data)
            return IngestResult(self.volume_id, page, "created")

        if chapter and chapter not in record["chapters"]:
            record["chapters"] = sorted({*record["chapters"], chapter})

        same_image = record["image"]["sha256"] == image_sha
        if not same_image:
            record["image_history"].append(record["image"])
            record["image"] = image_ref

        if record["text"]["sha256"] == text_ref["sha256"]:
            self._save(data)
            return IngestResult(self.volume_id, page, "unchanged" if same_image else "updated")

        if record["status"] == STATUS_PROOFREAD:
            record["pending_ocr"] = {"at": at, **text_ref}
            self._save(data)
            return IngestResult(self.volume_id, page, "rescan_kept_proofread")

        record["revisions"].append(
            {"at": at, "from": record["text"]["sha256"], "to": text_ref["sha256"], "source": "ocr"}
        )
        record["text"] = text_ref
        record["passages"] = find_passages(text)
        record["gaps"] = find_gaps(text)
        self._save(data)
        return IngestResult(self.volume_id, page, "updated")

    def refresh_passages(self) -> int:
        """Recompute every page's verse ranges from its current text."""

        data = self.load()
        changed = 0
        for record in data["pages"].values():
            passages = find_passages((self.directory / record["text"]["file"]).read_text(encoding="utf-8"))
            if passages != record.get("passages"):
                record["passages"] = passages
                changed += 1
        if changed:
            self._save(data)
        return changed

    def proofread(self, printed_page: str | int, text: str, *, expected_sha256: str) -> dict[str, Any]:
        """Save a proofread text as a new version. Refuses a stale edit."""

        page = normalize_page(printed_page)
        data = self.load()
        record = data["pages"].get(page)
        if record is None:
            raise KeyError(page)
        if record["text"]["sha256"] != expected_sha256:
            raise ValueError("page text changed since it was opened")
        text_ref = self._store_text(page, text)
        if text_ref["sha256"] != record["text"]["sha256"]:
            record["revisions"].append(
                {"at": now_iso(), "from": record["text"]["sha256"], "to": text_ref["sha256"], "source": "proofread"}
            )
            record["text"] = text_ref
            record["passages"] = find_passages(text)
            record["gaps"] = find_gaps(text)
        record["status"] = STATUS_PROOFREAD
        record["pending_ocr"] = None
        self._save(data)
        return record
