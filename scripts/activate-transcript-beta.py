#!/usr/bin/env python3
"""Install an existing reviewed AI draft for the private beta without replacing legacy data."""
import argparse
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


def activate(source_root: Path, production_root: Path, item: str, owner: str, target_root: Path | None = None):
    target = target_root or production_root / "transcript-review-beta"
    if target.exists():
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
            raise ValueError("Beta data appeared during preparation; refusing to overwrite it")
        os.rename(staging, target)
        return {"root": str(target), "item": item, "paragraphs": len(document["script"]), "groups": len(document["context"]["groups"]), "owner": owner}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--production-root", type=Path, required=True)
    parser.add_argument("--item", required=True)
    parser.add_argument("--target-root", type=Path, required=True, help="Private persistent directory outside the public web data tree")
    args = parser.parse_args()
    print(json.dumps(activate(args.source_root, args.production_root, args.item, "junyang168@gmail.com", args.target_root), ensure_ascii=False))
