"""Read pre-migration data through the original editor, without writing snapshots."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from .script_delta import ScriptDelta
from .transcript_review import ReviewError, ReviewStore, read


def legacy_history(root: Path, item: str):
    """Read the production audit entries and the last saved legacy manuscript."""
    path = root / "logs/sermon_meta_audit.log"
    events = []
    if path.exists():
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if entry.get("item") == item:
                    events.append({
                        "time": entry.get("timestamp"),
                        "actor": entry.get("actor_name") or entry.get("actor_id"),
                        "action": entry.get("context"),
                    })
    snapshot = legacy_editor_document(root, item)
    return {
        "events": sorted(events, key=lambda entry: entry["time"] or "", reverse=True),
        "snapshot_available": bool(snapshot["script"]),
    }


def legacy_editor_document(root: Path, item: str, *, changes=False):
    store = ReviewStore(root)
    folder = store.folder(item)  # Validate the item before constructing paths.
    receipt = read(folder / 'beta-promotion.json')
    _, header = store.meta(item)
    if receipt is not None:
        if (receipt.get('schema_version') != 'transcript-production-promotion.v1'
                or receipt.get('item') != item
                or receipt.get('production_root') != str(root.resolve())):
            raise ReviewError(409, '舊稿備份綁定不一致')
        backup = Path(receipt['backup_root'])
        raw = (backup / 'backup.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != receipt['backup_sha256']:
            raise ReviewError(409, '舊稿備份校驗失敗')
        manifest = json.loads(raw)
        if (manifest.get('schema_version') != 'transcript-promotion-backup.v1'
                or manifest['intent']['item'] != item
                or manifest['intent']['production_root'] != str(root.resolve())):
            raise ReviewError(409, '舊稿備份來源不一致')
        def saved(relative):
            raw = (backup / 'production' / relative).read_bytes()
            if hashlib.sha256(raw).hexdigest() != manifest['files']['production'][relative]['sha256']:
                raise ReviewError(409, '舊稿備份檔案校驗失敗')
            return json.loads(raw)
        rows = saved(f'script_review/{item}.json')
        patched = saved(f'script_patched/{item}.json')
        timeline = saved(f'script/{item}.json')
        header = next(row for row in saved('config/sermon.json') if row['item'] == item)
    elif store.enabled(item):
        baseline = read(folder / 'legacy-baseline.json')
        if not baseline:
            raise ReviewError(404, '找不到搬遷前舊稿；不會以新版稿件代替')
        rows, patched, timeline = baseline['script'], baseline['patched'], baseline['timeline']
    else:
        # Not migrated: the production legacy files are still authoritative.
        rows = read(root / 'script_review' / f'{item}.json')
        patched = read(root / 'script_patched' / f'{item}.json')
        timeline = read(root / 'script' / f'{item}.json')
    if not isinstance(rows, list) or not isinstance(patched, list) or not isinstance(timeline, dict):
        raise ReviewError(404, '舊稿資料不完整')

    class SnapshotDelta(ScriptDelta):
        def __init__(self):
            entries = deepcopy(timeline['entries'])
            for i, entry in enumerate(entries):
                entry['next_item'] = entries[i + 1]['index'] if i + 1 < len(entries) else None
            self.timelineDictionary = {entry['index']: entry for entry in entries}
        def loadPatchedScript(self):
            self.patched = deepcopy(patched)
            return [row.get('text') for row in self.patched]
        def loadReviewScript(self):
            self.review = deepcopy(rows)
            return [row.get('text') for row in self.review]

    delta = SnapshotDelta()
    if changes:
        script = delta.getChanges()
    else:
        script = deepcopy(rows)
        delta.add_timeline(script)
    return {'header': header, 'script': script, 'script_sha256': None, 'read_only': True}
