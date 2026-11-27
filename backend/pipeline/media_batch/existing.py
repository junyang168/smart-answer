"""Filename lookup proves an existing manuscript, not acoustic recording identity."""

import hashlib
import json
import re
import unicodedata
from pathlib import Path


def identity(name):
    text = Path(name).name
    if Path(text).suffix.lower() in (
        ".wav",
        ".mp3",
        ".mp4",
        ".aac",
        ".m4a",
        ".flac",
        ".mov",
        ".mts",
        ".m2ts",
        ".m4v",
    ):
        text = text[: text.rfind(".")]
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text).casefold())


def find(root, paths, *, require_catalog=False):
    if root is None:
        return {}
    root = Path(root)
    catalog = root / "config/sermon.json"
    if not catalog.is_file():
        if require_catalog:
            raise ValueError("Configured existing catalog is unavailable")
        return {}
    wanted = {identity(path) for path in paths}
    candidates = {}
    for row in json.loads(catalog.read_text()):
        key = identity(row["item"])
        if key in wanted:
            candidates.setdefault(key, []).append(row)
    results = {}
    for key, rows in candidates.items():
        if len(rows) != 1:
            continue
        row = rows[0]
        item = row["item"]
        if "/" in item or "\\" in item or item in (".", ".."):
            continue
        for kind in ("script_published", "script_review", "script_patched", "script"):
            path = root / kind / (item + ".json")
            if not path.is_file():
                continue
            raw = path.read_bytes()
            v = json.loads(raw)
            paragraphs = (
                v if isinstance(v, list) else v.get("script", v.get("entries", []))
            )
            if not any(
                isinstance(p, dict) and str(p.get("text") or "").strip()
                for p in paragraphs
            ):
                continue
            results[key] = dict(
                item=item,
                title=row.get("title"),
                published=kind == "script_published",
                manuscript=kind,
                script_sha256=hashlib.sha256(raw).hexdigest(),
                match_basis="normalized_original_filename",
            )
            break
    return {
        path: results[identity(path)] for path in paths if identity(path) in results
    }
