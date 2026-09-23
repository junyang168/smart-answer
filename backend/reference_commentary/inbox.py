"""Process the iCloud scan inbox: new scans -> OCR -> page records.

The owner scans a chapter with the iPhone Files app into
`<inbox>/Matthew/ch21/`. iCloud syncs it to this Mac, and launchd runs this
every few minutes. The inbox only receives scans: transcribed text is read on
the study page, not here. The one file written back is `_problems.md`, so the
owner sees on the phone when a page number could not be read or a page is
missing.

A scan is processed once, recognised by its SHA-256 in the ledger. A scan whose
OCR could not finish (Vertex rate limit) stays out of the ledger and is retried
on the next run.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Callable

from backend.config.reference_commentary_paths import ReferenceCommentaryPaths, reference_commentary_paths
from backend.reference_commentary.images import SCAN_SUFFIXES, PageImage, scan_pages
from backend.reference_commentary.ocr import OcrPage, OcrUnavailable, ocr_page
from backend.reference_commentary.store import (
    VolumeStore,
    _atomic_write,
    now_iso,
    sha256_bytes,
)
from backend.reference_commentary.volumes import resolve_folder


PROBLEMS_FILE = "_problems.md"
# iCloud writes a file in pieces; leave anything touched this recently alone.
SETTLE_SECONDS = 60


def log(message: str) -> None:
    print(f"{now_iso()} {message}", flush=True)


def load_ledger(paths: ReferenceCommentaryPaths) -> dict[str, Any]:
    if not paths.inbox_ledger.exists():
        return {"scans": {}}
    return json.loads(paths.inbox_ledger.read_text(encoding="utf-8"))


def save_ledger(paths: ReferenceCommentaryPaths, ledger: dict[str, Any]) -> None:
    payload = json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(paths.inbox_ledger, payload.encode("utf-8"))


def request_download(placeholder: Path) -> None:
    """`.name.pdf.icloud` is an evicted file; ask iCloud to bring it down."""

    real = placeholder.with_name(placeholder.name[1 : -len(".icloud")])
    subprocess.run(["brctl", "download", str(real)], capture_output=True, timeout=60)


def _visible(path: Path) -> bool:
    return not path.name.startswith(".") and not path.name.startswith("_")


def find_scans(inbox: Path) -> tuple[list[tuple[Path, str, str]], list[Path], list[str]]:
    """(scans as (path, book, chapter_folder), evicted placeholders, misplaced notes)."""

    scans: list[tuple[Path, str, str]] = []
    placeholders: list[Path] = []
    misplaced: list[str] = []
    if not inbox.is_dir():
        return scans, placeholders, misplaced
    for book in sorted(p for p in inbox.iterdir() if p.is_dir() and _visible(p)):
        for chapter in sorted(p for p in book.iterdir() if p.is_dir() and _visible(p)):
            for item in sorted(chapter.iterdir()):
                if item.name.startswith(".") and item.name.endswith(".icloud"):
                    placeholders.append(item)
                elif item.is_file() and _visible(item) and item.suffix.lower() in SCAN_SUFFIXES:
                    scans.append((item, book.name, chapter.name))
        for item in book.iterdir():
            if item.is_file() and _visible(item) and item.suffix.lower() in SCAN_SUFFIXES:
                misplaced.append(f"{book.name}/{item.name}")
    for item in inbox.iterdir():
        if item.is_file() and _visible(item) and item.suffix.lower() in SCAN_SUFFIXES:
            misplaced.append(item.name)
    return scans, placeholders, misplaced


def process_scan(
    paths: ReferenceCommentaryPaths,
    path: Path,
    book: str,
    chapter_folder: str,
    data: bytes,
    *,
    scan: Callable[[Path], list[PageImage]],
    ocr: Callable[[bytes], OcrPage],
) -> dict[str, Any]:
    volume, chapter = resolve_folder(book, chapter_folder)
    store = VolumeStore(paths.volume(volume.volume_id), volume.volume_id)
    digest = sha256_bytes(data)
    entry: dict[str, Any] = {
        "file": str(path.relative_to(paths.inbox)),
        "folder": f"{book}/{chapter_folder}",
        "volume_id": volume.volume_id,
        "chapter": chapter,
        "sha256": digest,
        "pages": [],
        "unassigned": [],
    }
    for index, image in enumerate(scan(path), start=1):
        result = ocr(image.jpeg)
        source = {"inbox_file": entry["file"], "inbox_sha256": digest, "pdf_page": image.pdf_page}
        if result.printed_page is None:
            # Kept aside, not guessed: the owner confirms the page number.
            stem = f"{digest[:12]}-{index:03d}"
            unassigned = paths.volume(volume.volume_id) / "unassigned"
            _atomic_write(unassigned / f"{stem}.jpg", image.jpeg)
            _atomic_write(unassigned / f"{stem}.md", (result.text.strip() + "\n").encode("utf-8"))
            entry["unassigned"].append({"image": f"unassigned/{stem}.jpg", "pdf_page": image.pdf_page})
            continue
        outcome = store.ingest(
            printed_page=result.printed_page,
            image=image.jpeg,
            text=result.text,
            source={**source, "ocr_model": result.model},
            original=data,
            original_suffix=path.suffix,
            chapter=chapter,
        )
        entry["pages"].append({"printed_page": outcome.printed_page, "action": outcome.action, "pdf_page": image.pdf_page})
    entry["processed_at"] = now_iso()
    return entry


def problems_for_folder(entries: list[dict[str, Any]], errors: list[str]) -> list[str]:
    lines: list[str] = list(errors)
    numbers: set[int] = set()
    for entry in entries:
        for item in entry["unassigned"]:
            where = f"PDF 第 {item['pdf_page']} 页" if item["pdf_page"] else "这张照片"
            lines.append(f"`{entry['file']}` {where}：读不出印刷页码，已放在 `{item['image']}` 等确认")
        for page in entry["pages"]:
            if page["printed_page"].isdigit():
                numbers.add(int(page["printed_page"]))
            if page["action"] == "rescan_kept_proofread":
                lines.append(f"第 {page['printed_page']} 页重扫了，但已校对过：保留校对文字，新转写待比较")
    if numbers:
        missing = [n for n in range(min(numbers), max(numbers) + 1) if n not in numbers]
        if missing:
            lines.append("缺页：" + "、".join(str(n) for n in missing))
    return lines


def write_problems(folder: Path, lines: list[str]) -> None:
    target = folder / PROBLEMS_FILE
    if not lines:
        target.unlink(missing_ok=True)
        return
    body = "# 需要处理\n\n" + "\n".join(f"- {line}" for line in lines) + f"\n\n更新于 {now_iso()}\n"
    target.write_text(body, encoding="utf-8")


def run_once(
    paths: ReferenceCommentaryPaths,
    *,
    scan: Callable[[Path], list[PageImage]] = scan_pages,
    ocr: Callable[[bytes], OcrPage] = ocr_page,
    now: Callable[[], float] = time.time,
    download: Callable[[Path], None] = request_download,
) -> dict[str, int]:
    paths.root.mkdir(parents=True, exist_ok=True)
    with open(paths.lock, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log("another run holds the lock; skipping")
            return {"processed": 0, "skipped_locked": 1}

        ledger = load_ledger(paths)
        scans, placeholders, misplaced = find_scans(paths.inbox)
        for placeholder in placeholders:
            download(placeholder)
        if misplaced:
            log("scans outside a <book>/<chapter> folder: " + ", ".join(misplaced))

        errors: dict[str, list[str]] = defaultdict(list)
        touched: set[str] = set()
        processed = 0
        for path, book, chapter_folder in scans:
            folder_key = f"{book}/{chapter_folder}"
            if now() - path.stat().st_mtime < SETTLE_SECONDS:
                continue
            data = path.read_bytes()
            digest = sha256_bytes(data)
            if digest in ledger["scans"]:
                continue
            touched.add(folder_key)
            try:
                entry = process_scan(paths, path, book, chapter_folder, data, scan=scan, ocr=ocr)
            except ValueError as exc:
                errors[folder_key].append(f"`{path.name}`：{exc}")
                continue
            except OcrUnavailable as exc:
                log(f"{path.name}: OCR unavailable, will retry next run ({exc})")
                continue
            ledger["scans"][digest] = entry
            save_ledger(paths, ledger)
            processed += 1
            log(f"{folder_key}/{path.name}: pages {[p['printed_page'] for p in entry['pages']]}, unassigned {len(entry['unassigned'])}")

        for folder_key in touched:
            entries = [e for e in ledger["scans"].values() if e["folder"] == folder_key]
            write_problems(paths.inbox / folder_key, problems_for_folder(entries, errors[folder_key]))

        return {"processed": processed, "pending_download": len(placeholders)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inbox", help="override REFERENCE_COMMENTARY_INBOX")
    args = parser.parse_args(argv)
    paths = reference_commentary_paths(inbox=args.inbox)
    if not paths.inbox.is_dir():
        log(f"inbox not found: {paths.inbox}")
        return 1
    result = run_once(paths)
    if result.get("processed") or result.get("pending_download"):
        log(f"done: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
