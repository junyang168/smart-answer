"""Project the series argument reader's unit progress onto its original lectures."""
import json
from pathlib import Path

from backend.pipeline import series_unit_reader as reader


def lecture_progress(data_root):
    root = Path(data_root) / reader.SERIES_DIR_NAME
    if not root.is_dir():
        return {}
    loaded = reader._loaded_unit_keys()
    result = {}
    for folder in sorted(root.iterdir()):
        segmentation = folder / "segmentation.json"
        if not segmentation.is_file():
            continue
        seg = json.loads(segmentation.read_text(encoding="utf-8"))
        rulings = reader._latest_rulings(Path(data_root), folder.name)
        for source in seg.get("sources", []):
            lecture = source["lecture"]
            units = [u for u in seg.get("units", [])
                     if any(r.split("-")[0] == lecture for r in u.get("ranges", []))]
            if not units:
                continue
            progress = dict(series_id=folder.name, total=len(units), generated=0,
                            loaded=0, approved=0, needs_revision=0)
            for unit in units:
                uid = unit["unit_id"]
                in_db = (folder.name, uid) in loaded
                progress["loaded"] += int(in_db)
                progress["generated"] += int(in_db or (folder / "units" / uid / "unit-argument.json").is_file())
                verdict = rulings.get(uid, {}).get("verdict")
                progress["approved"] += int(verdict == "approved")
                progress["needs_revision"] += int(verdict == "needs_revision")
            result[source["source_id"].removesuffix(".json")] = progress
    return result
