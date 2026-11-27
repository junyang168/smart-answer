"""Apply source-bound timing and recovered images to an isolated migration rehearsal."""

import shutil
import uuid
from collections import defaultdict
from pathlib import Path

from backend.api.sc_api.transcript_review import ReviewStore, digest, read
from backend.pipeline.transcript_editor_migration import checksum


def verified_file(root, path, expected):
    candidate = (root / path).resolve(strict=True)
    if not candidate.is_relative_to(root.resolve()) or checksum(candidate) != expected:
        raise ValueError(f"Evidence file does not match its manifest: {path}")
    return candidate


def apply_evidence(target, media_root, manifest_path, reports):
    manifest = read(manifest_path)
    store = ReviewStore(target)
    by_item = {row["item"]: row for row in reports}
    timing = defaultdict(list)
    for entry in manifest["timing"]:
        timing[entry["item"]].append(entry)
    for item, entries in timing.items():
        outcome = by_item[item]
        with store.lock(item):
            rows, context = store.load(item)
            by_id = {row["index"]: row for row in rows}
            source = read(store.folder(item) / "source-record.json")
            for entry in entries:
                pid = entry["row"]
                if pid not in outcome["timing_exceptions"] or digest(by_id[pid]["text"]) != entry["text_sha256"]:
                    raise ValueError(f"Timing evidence belongs to another paragraph: {item}/{pid}")
                verified_file(media_root, source["legacy_media_filename"], entry["media_sha256"])
                verified_file(manifest_path.parent, entry["audio_file"], entry["audio_sha256"])
                start, end = entry["start_ms"], entry["end_ms"]
                if not (type(start) is int and type(end) is int and 0 <= start < end <= source["duration_ms"]):
                    raise ValueError(f"Timing evidence exceeds the source: {item}/{pid}")
                if entry["decision"] not in ("qualified", "reference_only"):
                    raise ValueError("Invalid timing evidence decision")
                binding = context["paragraph_bindings"][pid]
                binding.update(start_ms=start, end_ms=end)
                state = context["timing_quality"]["paragraph_states"][pid]
                state["migration_evidence"] = entry
                if entry["decision"] == "qualified":
                    state["status"] = "qualified"
                    del outcome["timing_exceptions"][pid]
                    outcome["timed"] += 1
                else:
                    outcome["timing_exceptions"][pid] = entry["reason"]
            if not outcome["timing_exceptions"]:
                context["timing_quality"]["status"] = "qualified"
            store.commit(item, "operator-migration", uuid.uuid4().hex, {}, rows, context, "migration_timing")
        outcome["timing_evidence"] = entries
    for entry in manifest["images"]:
        item = entry["item"]
        outcome = by_item[item]
        matches = [
            image for image in outcome["legacy_images"]
            if image["row"] == entry["row"] and image["path"] == entry["target_path"] and image["sha256"] is None
        ]
        if len(matches) != 1:
            raise ValueError(f"Image evidence does not match a missing attachment: {entry['target_path']}")
        rows, context = store.load(item)
        row = next(row for row in rows if row["index"] == entry["row"])
        if digest(row["text"]) != entry["text_sha256"]:
            raise ValueError("Image evidence belongs to another transcript")
        interval = context["paragraph_bindings"][entry["row"]]
        if not interval["start_ms"] <= entry["capture_ms"] <= interval["end_ms"]:
            raise ValueError("Recovered image is outside its paragraph")
        verified_file(media_root, context["legacy_media_filename"], entry["media_sha256"])
        image = verified_file(manifest_path.parent, entry["image_file"], entry["image_sha256"])
        dest = target / Path(entry["target_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        with image.open("rb") as source, dest.open("xb") as output:
            shutil.copyfileobj(source, output)
        if checksum(dest) != entry["image_sha256"]:
            raise ValueError("Recovered attachment copy changed")
        matches[0].update(sha256=entry["image_sha256"], recovered_from_video=entry)
