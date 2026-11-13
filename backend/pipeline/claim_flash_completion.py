"""Small display receipts for completed canonical Flash runs."""
import hashlib
import json
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile

logger = logging.getLogger(__name__)


def receipt_path(data_root, kind, item):
    key = hashlib.sha256(item.encode()).hexdigest()
    return Path(data_root) / "wang-knowledge-platform/admin/flash-completion" / kind / f"{key}.json"


def save_completion(data_root, document, result):
    if result["mode"] != "canonical_refresh" or result["status"] != "completed" or result["pending"]:
        return
    kind = document["source_type"]
    item_field = {"sermon_transcript": "transcript_id", "notes_manuscript": "project_id"}.get(kind)
    item = document.get(item_field) if item_field else None
    if not item:
        return
    # Reuse the final source binding. The full audit stays in the run directory.
    receipt = {"source_file": result["source_file"], "body_sha256": result.get("source_body_sha256")}
    path = receipt_path(data_root, kind, item)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
            temp = Path(handle.name)
            try:
                json.dump(receipt, handle)
                handle.flush()
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not record Flash completion for %s", result["source_id"])


def sermon_completion_status(data_root, item):
    from backend.pipeline.transcript_source import resolve_transcript_path

    return completion_status(data_root, "sermon_transcript", item, lambda: resolve_transcript_path(
        item, [Path(data_root) / "script_published", Path(data_root) / "script_review"],
    ))


def completion_status(data_root, kind, item, current_source):
    if not item:
        return None
    path = receipt_path(data_root, kind, item)
    try:
        receipt = json.loads(path.read_bytes())
        binding = receipt["source_file"]
        current_path = current_source()
        if not current_path or not current_path.is_file():
            return "stale"
        raw = current_path.read_bytes()
        if hashlib.sha256(raw).hexdigest() == binding["file_sha256"]:
            return "completed"
        if receipt.get("body_sha256") and kind == "sermon_transcript":
            from backend.pipeline.source_projection import project_script
            from backend.pipeline.transcript_publication_projection import spoken_document

            document, _ = spoken_document(json.loads(raw))
            rows = document["script"] if isinstance(document, dict) else document
            if project_script(rows).body_sha256 == receipt["body_sha256"]:
                return "completed"
        return "stale"
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
