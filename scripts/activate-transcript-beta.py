#!/usr/bin/env python3
"""Install an existing reviewed AI draft for the private beta without replacing legacy data."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.api.sc_api.transcript_review import ReviewStore, atomic, file_hash, read


def copy_file(source, target):
    # Independent files: later local edits must not mutate production beta history.
    return shutil.copy2(source, target)


def activate(source_root: Path, production_root: Path, item: str, owner: str, target_root: Path | None = None, *, append=False):
    target = target_root or production_root / "transcript-review-beta"
    if target.exists() and not append:
        raise ValueError("Beta data already exists; refusing to overwrite it")
    source = ReviewStore(source_root)
    source.folder(item)  # Validate the item before using it in paths.
    if source_root.resolve() == production_root.resolve():
        raise ValueError("Source and production must be different roots")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".transcript-beta-", dir=target.parent))
    try:
        with source.lock(item):
            _, source_context = source.load(item)
            if list((source.folder(item) / "operations").glob("*.prepared.json")):
                raise ValueError("Unrecovered source operations")
            shutil.copytree(source.folder(item), staging / "transcript_review" / item, copy_function=copy_file)
            for folder in ("script", "script_patched", "script_review", "script_published"):
                path = source_root / folder / (item + ".json")
                if path.exists():
                    (staging / folder).mkdir(exist_ok=True)
                    copy_file(path, staging / folder / path.name)
            _, metadata = source.meta(item)
            original_metadata = dict(metadata)
            metadata.update(assigned_to=None, assigned_to_name=None, assigned_to_date=None, status="ready", assignment_epoch=uuid.uuid4().hex)
            atomic(staging / "config/sermon.json", [metadata])
            atomic(staging / "config/config.json", read(production_root / "config/config.json"))
            manifest = {
                "schema_version": "transcript-beta-activation.v1",
                "item": item,
                "owner": owner,
                "action": "owner_requested_new_ai_transcript_beta",
                "source_root": str(source_root.resolve()),
                "source_metadata": original_metadata,
                "source_context_sha256": file_hash(source.folder(item) / "context.json"),
                "source_script_sha256": file_hash(source_root / "script_review" / (item + ".json")),
                "copied_files": {
                    str(p.relative_to(staging)): file_hash(p)
                    for p in staging.rglob("*") if p.is_file()
                },
                "content_review_decisions_added": 0,
                "legacy_manuscripts_modified": False,
            }
        beta = ReviewStore(staging, public_api_base="/api/sc_api/beta/review_public")
        if file_hash(beta.folder(item) / "source.mp4") != source_context["source_video_sha256"]:
            raise ValueError("Copied source video SHA does not match the original")
        document = beta.document(owner, item)
        for asset_id in document["context"]["visual_assets"]:
            beta.asset_path(item, asset_id, actor=owner)
        beta.claim(owner, item, {"version": document["version"], "operation_id": uuid.uuid4().hex, "action": "claim", "reason": "Owner-requested production beta activation; no content approval"})
        document = beta.document(owner, item)
        manifest["activated_revision"] = document["context"]["revision"]
        atomic(staging / "activation.json", manifest)
        if target.exists():
            if not append:
                raise ValueError("Beta data appeared during preparation; refusing to overwrite it")
            append_staged(staging, target, item, permission_root=production_root)
        else:
            os.rename(staging, target)
        return {"root": str(target), "item": item, "paragraphs": len(document["script"]), "groups": len(document["context"]["groups"]), "owner": owner}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def append_staged(staging: Path, target: Path, item: str, *, permission_root: Path | None = None):
    """Add a new item, retaining existing manuscripts, claims and history.

    Hold every current item's workflow lock while updating the shared catalog,
    plus an activation lock to serialize concurrent additions. Catalog insertion
    is last: a partial failed install remains inaccessible and refuses overwrite.
    """
    import fcntl
    with (target / '.activation.lock').open('a+b') as activation_lock:
        fcntl.flock(activation_lock, fcntl.LOCK_EX)
        store = ReviewStore(target)
        catalog = read(target / 'config/sermon.json', [])
        with ExitStack() as locks:
            for existing in sorted(m['item'] for m in catalog):
                # Serialize the catalog snapshot without opening an archived
                # manuscript for edits. ReviewStore.lock correctly rejects a
                # promoted manuscript; that must not prevent adding a different
                # lecture to the still-private Beta catalog.
                folder=store.folder(existing)
                folder.mkdir(parents=True,exist_ok=True)
                handle=locks.enter_context((folder/'workflow.lock').open('a+b'))
                fcntl.flock(handle,fcntl.LOCK_EX)
                if not (folder/'production-link.json').exists():
                    store.recover(existing)
            catalog = read(target / 'config/sermon.json', [])
            if any(m['item'] == item for m in catalog) or store.folder(item).exists():
                raise ValueError('Beta item already exists; refusing to overwrite it')
            if read(staging / 'config/config.json') != read((permission_root or target) / 'config/config.json'):
                raise ValueError('Beta permissions differ; refusing implicit permission changes')
            files = [(staging / folder / (item + '.json'), target / folder / (item + '.json'))
                     for folder in ('script', 'script_patched', 'script_review', 'script_published')]
            if any(dest.exists() for _, dest in files):
                raise ValueError('Beta manuscript exists; refusing to overwrite it')
            atomic(staging / 'transcript_review' / item / 'activation.json', read(staging / 'activation.json'))
            for src, dest in files:
                if src.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    copy_file(src, dest)
            os.rename(staging / 'transcript_review' / item, store.folder(item))
            atomic(target / 'config/sermon.json', catalog + read(staging / 'config/sermon.json'))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--target-root", type=Path, required=True, help="Private persistent directory outside the public web data tree")
    parser.add_argument("--append", action="store_true", help="Add a new item to an existing beta root; never overwrite an item")
    args = parser.parse_args()
    print(json.dumps(activate(args.source_root, args.production_root, args.item, "junyang168@gmail.com", args.target_root, append=args.append), ensure_ascii=False))
