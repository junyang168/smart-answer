"""Rehearse transcript storage migration from an independently verified snapshot."""

import argparse
import hashlib
import re
import shutil
import subprocess
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import unquote

from backend.api.sc_api.script_delta import ScriptDelta
from backend.api.sc_api.transcript_review import ReviewError, ReviewStore, atomic, file_hash, read

CLIP_ID = "S 190512-GH010035"
FULL_ID = "2019-05-12 圣灵充满"
LEGACY_MEDIA_ALIASES = {"2019-09-29 可4章10-12 耶穌恐怕人悔改嗎": "2019-09-29 可4章10-12 耶穌恐怕人悔改嗎\uf025.mp4"}


def checksum(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_snapshot(snapshot):
    manifest = read(snapshot / "manifest.json")
    for scope in ("production", "beta"):
        for entry in manifest["scopes"][scope]:
            relative = Path(entry["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid snapshot path: {relative}")
            source = snapshot / scope / relative
            if source.is_symlink() or checksum(source) != entry["sha256"]:
                raise ValueError(f"Snapshot checksum changed: {source}")
    return manifest


def select_drafts(production, beta):
    production_ids = {path.stem for path in (production / "script_review").glob("*.json")}
    beta_ids = {path.stem for path in (beta / "script_review").glob("*.json")}
    for item in sorted(production_ids | beta_ids):
        production_folder = ReviewStore(production).folder(item)
        beta_folder = ReviewStore(beta).folder(item)
        if item in beta_ids:
            link = read(beta_folder / "production-link.json")
            if link:
                receipt = production_folder / "beta-promotion.json"
                if link.get("item") != item or file_hash(receipt) != link.get("receipt_sha256"):
                    raise ValueError(f"Unverifiable Beta link: {item}")
                yield item, "linked_beta"
            else:
                yield item, "beta"
        else:
            yield item, "production_review" if (production_folder / "context.json").is_file() else "legacy_conversion"


def copy_beta_item(beta, target, item, catalog):
    source_store = ReviewStore(beta)
    rows, _ = source_store.load(item)
    _, header = source_store.meta(item)
    existing = next((row for row in catalog if row["item"] == item), None)
    if existing is None:
        catalog.append(header)
    else:
        if (target / "script_review" / f"{item}.json").is_file():
            raise ValueError("Beta and production both have draft files")
        for key in ("assigned_to", "assigned_to_name", "assigned_to_date", "assignment_epoch"):
            if key in header:
                existing[key] = header[key]
        if existing.get("status") != "published":
            existing["status"] = header.get("status", existing.get("status"))
    for folder in ("script", "script_patched", "script_review", "transcript_candidates"):
        relative = Path(folder) / f"{item}.json"
        if (beta / relative).is_file():
            (target / folder).mkdir(exist_ok=True)
            shutil.copy2(beta / relative, target / relative)
    shutil.copytree(source_store.folder(item), ReviewStore(target).folder(item))
    if ReviewStore(target).load(item)[0] != rows:
        raise ValueError("Copied Beta script does not match its source")


def convert_legacy(store, item, *, media_root=None):
    root = store.root
    folder = store.folder(item)
    _, meta = store.meta(item)
    source = root / "script_review" / f"{item}.json"
    raw = read(source)
    timeline_path = root / "script" / f"{item}.json"
    timeline = read(timeline_path)
    patched = read(root / "script_patched" / f"{item}.json")
    rows = []
    bindings = {}
    timing_exceptions = {}
    row_ids = {str(row["index"]) for row in raw}
    used_ids = set()
    entries = timeline["entries"]
    timeline_indexes = {str(row["index"]): i for i, row in enumerate(entries)}
    previous_end = -1
    for position, row in enumerate(raw, 1):
        legacy_id = str(row["index"])
        row_id = legacy_id
        if row_id in used_ids:
            row_id = f"legacy-{position}-{legacy_id}"
            while row_id in row_ids:
                row_id = "legacy-" + row_id
        used_ids.add(row_id)
        rows.append({**row, "index": row_id})
        if row.get("type") in ("comment", "subtitle") or legacy_id.startswith("subtitle-"):
            continue
        end_id = str(row.get("end_index") or legacy_id)
        try:
            end_position = timeline_indexes[end_id]
            start_position = timeline_indexes[legacy_id] if row.get("end_index") else previous_end + 1
            if not 0 <= start_position <= end_position:
                raise ValueError("Legacy timeline ranges overlap or are inverted")
            first, last = entries[start_position], entries[end_position]
            start = ScriptDelta.calcuateTime(None, first["index"], first["start"])
            end = ScriptDelta.calcuateTime(None, last["index"], last["end"])
            if not 0 <= start < end:
                raise ValueError("Legacy start and end are equal or inverted")
            bindings[row_id] = {"source_paragraph_id": legacy_id, "start_ms": start * 1000, "end_ms": end * 1000}
        except (KeyError, ValueError) as exc:
            timing_exceptions[row_id] = str(exc)
            bindings[row_id] = {"source_paragraph_id": legacy_id, "start_ms": None, "end_ms": None}
        previous_end = timeline_indexes.get(legacy_id, previous_end)
    timing_quality = None
    if timing_exceptions:
        timing_quality = {
            "status": "withheld",
            "paragraph_states": {key: {"status": "withheld" if key in timing_exceptions else "qualified"} for key in bindings},
        }
    context = {
        "schema_version": "transcript-review-context.v1",
        "script_sha256": file_hash(source),
        "revision": 0,
        "legacy_timeline_sha256": file_hash(timeline_path),
        "media_mime_type": "audio/mpeg" if meta.get("type") == "audio" else "video/mp4",
        "paragraph_bindings": bindings,
        "timing_quality": timing_quality,
        "visual_assets": {},
        "occurrences": {},
        "groups": [],
        "reviews": {},
        "editorial_metadata": {key: meta.get(key) or "" for key in ("title", "summary", "keypoints")},
        "source_metadata": {key: meta.get(key) for key in ("item", "author", "author_name", "type", "deliver_date", "theme", "source", "series_id")},
        "visibility": {"spans": []},
    }
    extra = {}
    if media_root:
        extension = "mp3" if meta.get("type") == "audio" else "mp4"
        filename = LEGACY_MEDIA_ALIASES.get(item, f"{item}.{extension}")
        media = media_root / filename
        if not media.is_file():
            raise ValueError(f"Legacy playback file is missing: {filename}")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(media)],
            check=True, capture_output=True, text=True,
        )
        duration_ms = round(float(probe.stdout) * 1000)
        context["legacy_media_filename"] = filename
        if item in LEGACY_MEDIA_ALIASES:
            last = max(v["end_ms"] or 0 for v in bindings.values())
            if abs(last - duration_ms) > 1000:
                raise ValueError("Legacy media alias duration does not match the timeline")
        extra[folder / "source-record.json"] = {
            "duration_ms": duration_ms,
            "legacy_media_filename": filename,
            "legacy_media_size": media.stat().st_size,
        }
    with store.lock(item):
        atomic(folder / "legacy-baseline.json", {"script": raw, "patched": patched, "timeline": timeline})
        store.commit(item, "operator-migration", uuid.uuid4().hex, {}, rows, context, "legacy_migration", extra=extra)
    saved_rows, saved_context = store.load(item)
    if saved_rows != rows or saved_context["paragraph_bindings"] != bindings:
        raise ValueError("Converted script or timing failed read-back")
    return len(rows), len(bindings) - len(timing_exceptions), timing_exceptions


def reconcile_legacy_images(target, rows):
    """Restore links to extracted images whose saved path includes frames/."""
    results = []
    for row in rows:
        for link in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", row["text"]):
            if link.startswith("/web/data/"):
                relative = Path(unquote(link.removeprefix("/web/data/")))
            elif link.startswith("data%2F"):
                relative = Path(unquote(link).removeprefix("data/"))
            else:
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Invalid legacy image path: {relative}")
            destination = target / relative
            alternate = destination.parent / "frames" / destination.name
            restored = False
            if not destination.exists() and relative.parts[0] == "slides" and alternate.is_file():
                shutil.copy2(alternate, destination)
                if checksum(destination) != checksum(alternate):
                    raise ValueError(f"Legacy slide alias changed: {relative}")
                restored = True
            results.append({
                "row": row["index"], "path": str(relative),
                "sha256": checksum(destination) if destination.is_file() else None,
                "restored": restored,
            })
    return results


def archive_clip(root):
    destination = root / "transcript_review" / FULL_ID / "related-sources"
    destination.mkdir(parents=True, exist_ok=True)
    files = []
    for kind in ("script", "script_patched", "script_review"):
        source = root / kind / f"{CLIP_ID}.json"
        saved = destination / CLIP_ID / kind / source.name
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, saved)
        checksum_value = checksum(source)
        if checksum(saved) != checksum_value:
            raise ValueError(f"Archived clip checksum changed: {source}")
        files.append({"path": str(saved.relative_to(root)), "sha256": checksum_value})
    atomic(destination / "manifest.json", {"item": FULL_ID, "source_item": CLIP_ID, "files": files})
    return files


def rehearse(snapshot, output, *, media_root=None):
    if output.exists():
        raise ValueError("Use a fresh rehearsal output to retain prior results")
    manifest = verify_snapshot(snapshot)
    production, beta = (snapshot / name for name in ("production", "beta"))
    staging = Path(tempfile.mkdtemp(prefix=".rehearsal-", dir=output.parent))
    target = staging / "data"
    shutil.copytree(production, target)
    store = ReviewStore(target)
    catalog_path = target / "config/sermon.json"
    catalog = read(catalog_path)
    archived = archive_clip(target)
    reports = []
    for item, mode in select_drafts(production, beta):
        outcome = {"item": item, "mode": mode}
        try:
            if item == CLIP_ID:
                outcome.update(status="related_source", linked_item=FULL_ID, files=archived)
            elif mode == "legacy_conversion":
                count, timed, exceptions = convert_legacy(store, item, media_root=media_root)
                header = next(row for row in catalog if row["item"] == item)
                if header.get("assigned_to") and not header.get("assignment_epoch"):
                    header["assignment_epoch"] = uuid.uuid5(uuid.NAMESPACE_URL, f"legacy-review:{item}:{header['assigned_to']}").hex
                rows, _ = store.load(item)
                outcome.update(
                    status="converted", rows=count, timed=timed, timing_exceptions=exceptions,
                    legacy_images=reconcile_legacy_images(target, rows),
                )
            else:
                if mode == "beta":
                    copy_beta_item(beta, target, item, catalog)
                rows, context = store.load(item)
                outcome.update(status="retained", rows=len(rows), revision=context["revision"])
        except (ReviewError, ValueError, KeyError, TypeError, OSError, subprocess.CalledProcessError) as exc:
            outcome.update(status="blocked", reason=str(exc))
        reports.append(outcome)
    atomic(catalog_path, catalog)
    converted = {row["item"] for row in reports if row["status"] == "converted"}
    verified_production_files = 0
    for entry in manifest["scopes"]["production"]:
        relative = entry["path"]
        if relative == "config/sermon.json":
            continue
        if relative.startswith("script_review/") and Path(relative).stem in converted:
            baseline = store.folder(Path(relative).stem) / "legacy-baseline.json"
            if read(baseline)["script"] != read(production / relative):
                raise ValueError(f"Legacy baseline changed: {relative}")
        elif checksum(target / relative) != entry["sha256"]:
            raise ValueError(f"Production file changed: {relative}")
        verified_production_files += 1
    copied_beta_items = {r["item"] for r in reports if r["mode"] == "beta" and r["status"] == "retained"}
    beta_files_verified = 0
    for entry in manifest["scopes"]["beta"]:
        relative = Path(entry["path"])
        item = relative.parts[1] if relative.parts[0] == "transcript_review" else relative.stem
        if item not in copied_beta_items or relative.parts[0] not in {
            "script", "script_patched", "script_review", "transcript_review", "transcript_candidates"
        }:
            continue
        if checksum(target / relative) != entry["sha256"]:
            raise ValueError(f"Beta copy changed: {relative}")
        beta_files_verified += 1
    report = {
        "status": "storage_rehearsal",
        "counts": dict(Counter(row["status"] for row in reports)),
        "production_files_verified": verified_production_files,
        "production_publication_files_verified": sum(
            entry["path"].startswith("script_published/") for entry in manifest["scopes"]["production"]
        ),
        "copied_beta_files_verified": beta_files_verified,
        "external_dependencies_pending": manifest.get("external_dependencies_pending", True),
        "items": reports,
    }
    atomic(staging / "report.json", report)
    staging.rename(output)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--legacy-media-root", type=Path)
    args = parser.parse_args()
    print(rehearse(args.snapshot.resolve(), args.output.resolve(), media_root=args.legacy_media_root)["counts"])


if __name__ == "__main__":
    main()
