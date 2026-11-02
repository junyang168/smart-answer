#!/usr/bin/env python3
"""Copy dated historical PPTs into OneDrive; default is a read-only plan.

Templates, lock files and metadata stay in place. Originals remain as recovery
copies. Conflicting destination files stop the entire plan before any write.
"""
import argparse
from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from backend.sunday_presenter import OneDrivePpts


def plan(source, cloud):
    result = []
    for original in sorted(source.glob("*.pptx")):
        match = re.match(r"(?:聖道教會)?(\d{4}-\d{2}-\d{2})(?:_|主日)", original.name)
        if not match:
            continue
        day = date.fromisoformat(match[1])
        target = cloud.file(day, original.name)
        digest = sha256(original.read_bytes()).hexdigest()
        if target.exists() and sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"Destination differs; refusing to overwrite: {target}")
        result.append({"source": str(original), "target": str(target), "sha256": digest,
                       "status": "identical" if target.exists() else "copy"})
    return result


def apply(rows):
    for row in rows:
        if row["status"] == "identical":
            continue
        target, source = Path(row["target"]), Path(row["source"])
        target.parent.mkdir(exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".migration-", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as output, source.open("rb") as stream:
                shutil.copyfileobj(stream, output)
            if sha256(Path(tmp).read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError(f"Source changed during migration: {source}")
            # Exclusive link avoids replacing a file created after preflight.
            os.link(tmp, target)
            if sha256(target.read_bytes()).hexdigest() != row["sha256"]:
                raise ValueError(f"Readback failed: {target}")
        finally:
            Path(tmp).unlink(missing_ok=True)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(os.environ["DATA_BASE_DIR"]) / "sunday_worship")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    cloud = OneDrivePpts.configured()
    if cloud is None:
        parser.error("Configure OneDrive PPT storage first")
    rows = plan(args.source, cloud)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({"applied": False, "files": rows}, ensure_ascii=False, indent=2) + "\n")
    if args.apply:
        apply(rows)
        args.manifest.write_text(json.dumps({"applied": True, "files": rows}, ensure_ascii=False, indent=2) + "\n")
    print(f"{'Verified copies' if args.apply else 'Planned files'}: {len(rows)}; manifest: {args.manifest}")


if __name__ == "__main__":
    main()
