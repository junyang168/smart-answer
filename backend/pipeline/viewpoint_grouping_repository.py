"""Shared, immutable references to accepted grouping artifacts.

Grouping production publishes once; CVP consumers resolve an explicit manifest
digest, never a mutable 'latest' alias. Original accepted artifacts remain the
authority. Publishing here does not qualify Claims or authorize a CVP write.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Mapping

from backend.api.canonical_repository.viewpoint_foundation import canonical_json, sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CvpProductionBlocked, validate_content_addressed, validate_reviewed_grouping_bindings,
)
from backend.pipeline.viewpoint_reviewed_grouping import (
    admit_reviewed_grouping, validate_accepted_grouping,
)

VERSION = "wang_shared_grouping_reference_v1"
ROLES = ("manifest", "validation", "acceptance", "physical_review")


class SharedGroupingRepository:
    def __init__(self, root: Path):
        if not root.is_absolute():
            raise CvpProductionBlocked(["shared grouping repository requires an absolute root"])
        self.root = root.resolve()

    def entry_path(self, manifest_sha256: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise CvpProductionBlocked(["invalid grouping manifest SHA"])
        return self.root / manifest_sha256 / "reference.json"

    def publish(self, *, bindings: Mapping[str, Mapping[str, str]], expected_manifest_sha256: str) -> Path:
        path = self.entry_path(expected_manifest_sha256)
        artifacts = validate_reviewed_grouping_bindings(bindings, manifest_sha256=expected_manifest_sha256)
        validate_accepted_grouping(**artifacts, bindings=bindings, expected_manifest_sha256=expected_manifest_sha256)
        body = {
            "schema_version": VERSION, "manifest_sha256": expected_manifest_sha256,
            "bindings": dict(bindings), "claim_count": len(artifacts["manifest"]["claim_packets"]),
            "group_count": len(artifacts["manifest"]["groups"]),
        }
        entry = body | {"artifact_sha256": sha256_json(body)}
        path.parent.mkdir(parents=True, exist_ok=True)
        # Link a fully flushed temporary file atomically: concurrent publishers
        # either win or compare with the winner. Never overwrite an existing ID.
        fd, temporary = tempfile.mkstemp(prefix=".publish-", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(canonical_json(entry) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
            try:
                os.link(temporary, path)
            except FileExistsError:
                existing = self._read_entry(expected_manifest_sha256)
                if existing != entry:
                    raise CvpProductionBlocked(["shared grouping ID already has different bindings"])
        finally:
            os.unlink(temporary)
        self.load(expected_manifest_sha256)
        return path

    def _read_entry(self, manifest_sha256: str) -> dict[str, Any]:
        path = self.entry_path(manifest_sha256)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entry, dict):
                raise CvpProductionBlocked(["shared grouping reference must be an object"])
            validate_content_addressed(entry, "artifact_sha256")
            if entry.get("schema_version") != VERSION or entry.get("manifest_sha256") != manifest_sha256:
                raise CvpProductionBlocked(["shared grouping reference identity mismatch"])
            return entry
        except (OSError, ValueError, TypeError) as exc:
            raise CvpProductionBlocked(["unreadable shared grouping reference"]) from exc

    def load(self, manifest_sha256: str, *, source_epoch_binding=None) -> dict[str, Any]:
        entry = self._read_entry(manifest_sha256)
        artifacts = validate_reviewed_grouping_bindings(entry.get("bindings"), manifest_sha256=manifest_sha256,
                                                       source_epoch_binding=source_epoch_binding)
        validate_accepted_grouping(**artifacts, bindings=entry["bindings"], expected_manifest_sha256=manifest_sha256,
                                   source_epoch_binding=source_epoch_binding)
        if entry.get("claim_count") != len(artifacts["manifest"]["claim_packets"]) or entry.get("group_count") != len(artifacts["manifest"]["groups"]):
            raise CvpProductionBlocked(["shared grouping denominator mismatch"])
        return {"reference": entry, **artifacts}

    def admit(self, manifest_sha256: str, *, scope_packet: Mapping[str, Any], freeze: Mapping[str, Any]) -> dict[str, Any]:
        from backend.pipeline.viewpoint_g041_source_epoch import GROUPING_EPOCH_KEY
        loaded = self.load(manifest_sha256, source_epoch_binding=freeze.get(GROUPING_EPOCH_KEY))
        reference = loaded.pop("reference")
        return admit_reviewed_grouping(
            **loaded, bindings=reference["bindings"], expected_manifest_sha256=manifest_sha256,
            scope_packet=scope_packet, freeze=freeze,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("publish", "verify"))
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    for role in ROLES:
        parser.add_argument("--" + role.replace("_", "-"), type=Path)
    args = parser.parse_args()
    repository = SharedGroupingRepository(args.repository_root)
    if args.action == "publish":
        bindings = {}
        for role in ROLES:
            supplied = getattr(args, role)
            if supplied is None:
                parser.error(f"publish requires --{role.replace('_', '-')}")
            path = supplied.resolve()
            raw = path.read_bytes()
            artifact = json.loads(raw)
            bindings[role] = {"path": str(path), "file_sha256": hashlib.sha256(raw).hexdigest(),
                              "artifact_sha256": artifact["artifact_sha256"]}
        repository.publish(bindings=bindings, expected_manifest_sha256=args.manifest_sha256)
    loaded = repository.load(args.manifest_sha256)
    print(json.dumps({"status": "verified", "reference_path": str(repository.entry_path(args.manifest_sha256)),
                      "manifest_sha256": args.manifest_sha256, "claims": loaded["reference"]["claim_count"],
                      "groups": loaded["reference"]["group_count"], "production_authorized": False}))


if __name__ == "__main__":
    main()
