"""Study page API for reference commentaries (WKP-F11.03).

Reads the page store the scan inbox writes (`backend.reference_commentary`),
and takes proofreading edits back into it as new versions. Admin-only through
the web middleware like every `/admin/*` router here; the backend itself binds
to 127.0.0.1.

No model is called from here. Carson's text is served to the owner's study
page only: never to a public route, never into the Wang knowledge store.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.config.reference_commentary_paths import ReferenceCommentaryPaths, reference_commentary_paths
from backend.reference_commentary.store import VolumeStore, normalize_page, page_slug, page_sort_key
from backend.reference_commentary.volumes import VOLUMES, Volume, volume_by_id

router = APIRouter(prefix="/admin/reference-commentary", tags=["reference-commentary-admin"])

_SAFE_STEM = re.compile(r"^[0-9a-f]{12}-\d{3}$")


def paths() -> ReferenceCommentaryPaths:
    return reference_commentary_paths()


def _volume(volume_id: str) -> Volume:
    try:
        return volume_by_id(volume_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown volume {volume_id}") from None


def _store(volume: Volume) -> VolumeStore:
    return VolumeStore(paths().volume(volume.volume_id), volume.volume_id)


def _page_key(printed_page: str) -> str:
    try:
        return normalize_page(printed_page)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"not a page number: {printed_page}") from None


def chapters_of(record: dict[str, Any]) -> list[int]:
    """The folder it was scanned under, plus every chapter its headings name."""

    chapters = set(record.get("chapters") or [])
    for passage in record.get("passages") or []:
        chapters.add(int(passage.split(":")[0]))
    return sorted(chapters)


def _summary(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "printed_page": record["printed_page"],
        "chapters": chapters_of(record),
        "passages": record.get("passages") or [],
        "status": record["status"],
        "gaps": len(record.get("gaps") or []),
        "pending_ocr": bool(record.get("pending_ocr")),
    }


def missing_pages(records: list[dict[str, Any]]) -> list[int]:
    numbers = sorted(int(r["printed_page"]) for r in records if r["printed_page"].isdigit())
    missing: list[int] = []
    for before, after in zip(numbers, numbers[1:]):
        missing.extend(range(before + 1, after))
    return missing


def _unassigned(volume: Volume) -> list[dict[str, str]]:
    folder = paths().volume(volume.volume_id) / "unassigned"
    if not folder.is_dir():
        return []
    return [{"id": image.stem} for image in sorted(folder.glob("*.jpg"))]


@router.get("")
def shelf() -> dict[str, Any]:
    """Every volume with its pages summarised: the shelf and by-passage views."""

    volumes = []
    for volume in VOLUMES:
        records = _store(volume).pages()
        volumes.append(
            {
                "volume_id": volume.volume_id,
                "title": volume.title,
                "chapters": [volume.chapters.start, volume.chapters.stop - 1],
                "isbn": volume.isbn,
                "confirmed": volume.confirmed,
                "pages": [_summary(r) for r in records],
                "proofread": sum(r["status"] == "proofread" for r in records),
                "missing_pages": missing_pages(records),
                "unassigned": _unassigned(volume),
            }
        )
    return {"volumes": volumes}


@router.get("/chapters/{chapter}")
def chapter_text(chapter: int) -> dict[str, Any]:
    """One chapter for continuous reading, pages in printed order."""

    pages = []
    for volume in VOLUMES:
        store = _store(volume)
        for record in store.pages():
            if chapter in chapters_of(record):
                pages.append(
                    {
                        "volume_id": volume.volume_id,
                        **_summary(record),
                        "text": store.read_text(record),
                    }
                )
    if not pages:
        raise HTTPException(status_code=404, detail=f"chapter {chapter} has no pages yet")
    pages.sort(key=lambda p: page_sort_key(p["printed_page"]))
    return {"chapter": chapter, "pages": pages, "missing_pages": missing_pages(pages)}


@router.get("/volumes/{volume_id}/pages/{printed_page}")
def page_detail(volume_id: str, printed_page: str) -> dict[str, Any]:
    volume = _volume(volume_id)
    store = _store(volume)
    record = store.page(_page_key(printed_page))
    if record is None:
        raise HTTPException(status_code=404, detail=f"p. {printed_page} is not in {volume_id}")
    neighbours = [r["printed_page"] for r in store.pages()]
    index = neighbours.index(record["printed_page"])
    pending = record.get("pending_ocr")
    return {
        "volume_id": volume_id,
        "title": volume.title,
        **_summary(record),
        "text": store.read_text(record),
        "text_sha256": record["text"]["sha256"],
        "gaps": record.get("gaps") or [],
        "revisions": record["revisions"],
        "image_history": [image["sha256"] for image in record.get("image_history") or []],
        "pending_ocr_text": (
            (store.directory / pending["file"]).read_text(encoding="utf-8") if pending else None
        ),
        "previous_page": neighbours[index - 1] if index > 0 else None,
        "next_page": neighbours[index + 1] if index + 1 < len(neighbours) else None,
    }


@router.get("/volumes/{volume_id}/pages/{printed_page}/image")
def page_image(volume_id: str, printed_page: str, sha: str | None = None) -> FileResponse:
    volume = _volume(volume_id)
    store = _store(volume)
    record = store.page(_page_key(printed_page))
    if record is None:
        raise HTTPException(status_code=404, detail="no such page")
    images = [record["image"], *(record.get("image_history") or [])]
    chosen = next((image for image in images if sha is None or image["sha256"] == sha), None)
    if chosen is None:
        raise HTTPException(status_code=404, detail="no such image version")
    return FileResponse(store.directory / chosen["file"], media_type="image/jpeg")


@router.get("/volumes/{volume_id}/pages/{printed_page}/versions/{sha}")
def page_version(volume_id: str, printed_page: str, sha: str) -> dict[str, str]:
    """Any earlier text of this page, by the SHA its revision entry names."""

    store = _store(_volume(volume_id))
    if not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise HTTPException(status_code=404, detail="no such version")
    version = store.directory / "text" / f"{page_slug(_page_key(printed_page))}-{sha[:12]}.md"
    if not version.exists():
        raise HTTPException(status_code=404, detail="no such version")
    return {"sha256": sha, "text": version.read_text(encoding="utf-8")}


class ProofreadRequest(BaseModel):
    text: str
    expected_sha256: str


@router.post("/volumes/{volume_id}/pages/{printed_page}/proofread")
def proofread(volume_id: str, printed_page: str, body: ProofreadRequest) -> dict[str, Any]:
    """Save the owner's corrections as a new version and mark the page proofread."""

    if not body.text.strip():
        raise HTTPException(status_code=400, detail="文字不能是空的")
    store = _store(_volume(volume_id))
    try:
        store.proofread(_page_key(printed_page), body.text, expected_sha256=body.expected_sha256)
    except KeyError:
        raise HTTPException(status_code=404, detail="no such page") from None
    except ValueError:
        raise HTTPException(status_code=409, detail="這一頁在你打開之後被改過了，請重新載入") from None
    return page_detail(volume_id, printed_page)


class AssignRequest(BaseModel):
    printed_page: str


@router.get("/volumes/{volume_id}/unassigned/{item}/image")
def unassigned_image(volume_id: str, item: str) -> FileResponse:
    volume = _volume(volume_id)
    if not _SAFE_STEM.match(item):
        raise HTTPException(status_code=404, detail="no such item")
    image = paths().volume(volume.volume_id) / "unassigned" / f"{item}.jpg"
    if not image.exists():
        raise HTTPException(status_code=404, detail="no such item")
    return FileResponse(image, media_type="image/jpeg")


@router.post("/volumes/{volume_id}/unassigned/{item}/assign")
def assign_page(volume_id: str, item: str, body: AssignRequest) -> dict[str, Any]:
    """The owner names the printed page of a scan whose number OCR could not read."""

    volume = _volume(volume_id)
    if not _SAFE_STEM.match(item):
        raise HTTPException(status_code=404, detail="no such item")
    folder = paths().volume(volume.volume_id) / "unassigned"
    image, text = folder / f"{item}.jpg", folder / f"{item}.md"
    if not image.exists() or not text.exists():
        raise HTTPException(status_code=404, detail="no such item")
    try:
        page = normalize_page(body.printed_page)
    except ValueError:
        raise HTTPException(status_code=400, detail="頁碼要是數字或羅馬數字") from None
    result = _store(volume).ingest(
        printed_page=page,
        image=image.read_bytes(),
        text=text.read_text(encoding="utf-8"),
        source={"unassigned": item, "page_assigned": "by owner on the study page", "pdf_page": None},
    )
    done = folder / "assigned"
    done.mkdir(exist_ok=True)
    shutil.move(str(image), done / image.name)
    shutil.move(str(text), done / text.name)
    return {"volume_id": volume_id, "printed_page": page, "action": result.action}


def fold(text: str) -> str:
    """Case- and accent-insensitive form, so `tapeinosei` style queries and
    unaccented Greek both find accented text."""

    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


@router.get("/search")
def search(q: str, limit: int = 50) -> dict[str, Any]:
    """Pages containing the words, each with the first line that matched."""

    needle = fold(q.strip())
    if len(needle) < 2:
        raise HTTPException(status_code=400, detail="至少輸入兩個字")
    hits = []
    for volume in VOLUMES:
        store = _store(volume)
        for record in store.pages():
            line = next((l for l in store.read_text(record).splitlines() if needle in fold(l)), None)
            if line is None:
                continue
            hits.append(
                {
                    "volume_id": volume.volume_id,
                    "printed_page": record["printed_page"],
                    "chapters": chapters_of(record),
                    "snippet": line.strip()[:240],
                }
            )
            if len(hits) >= limit:
                return {"query": q, "hits": hits, "truncated": True}
    return {"query": q, "hits": hits, "truncated": False}
