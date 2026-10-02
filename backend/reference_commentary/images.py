"""Turn an arriving scan into one display JPEG per page, with macOS tools only.

HEIC/JPEG/PNG photos go through `sips`; a Files-app scan arrives as one
multi-page PDF and is split with PDFKit (`split_pdf.swift`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
import tempfile


PHOTO_SUFFIXES = {".heic", ".heif", ".jpg", ".jpeg", ".png"}
PDF_SUFFIXES = {".pdf"}
SCAN_SUFFIXES = PHOTO_SUFFIXES | PDF_SUFFIXES
MAX_EDGE = 3000

_SPLIT_PDF = Path(__file__).with_name("split_pdf.swift")


@dataclass(frozen=True)
class PageImage:
    jpeg: bytes
    pdf_page: int | None  # 1-based page within the PDF; None for a photo


def _run(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def photo_to_jpeg(path: Path) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "page.jpg"
        _run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", "-Z", str(MAX_EDGE), str(path), "--out", str(out)])
        return out.read_bytes()


def split_pdf(path: Path) -> list[bytes]:
    with tempfile.TemporaryDirectory() as tmp:
        _run(["swift", str(_SPLIT_PDF), str(path), tmp])
        return [page.read_bytes() for page in sorted(Path(tmp).glob("page-*.jpg"))]


def scan_pages(path: Path) -> list[PageImage]:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return [PageImage(jpeg, index) for index, jpeg in enumerate(split_pdf(path), start=1)]
    if suffix in PHOTO_SUFFIXES:
        return [PageImage(photo_to_jpeg(path), None)]
    raise ValueError(f"not a scan: {path.name}")
