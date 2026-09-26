import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import viewpoint_source_attestation_runner as runner


class EmptyStore:
    def __init__(self, database_url=None):
        pass

    def list_records(self, collection):
        return []


def test_source_attestation_requires_exact_content_addressed_lineage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(runner, "PostgresKnowledgeStore", EmptyStore)
    manifest = {
        "claims": [
            {
                "claim_id": "C1",
                "pinned_claim_revision": 1,
                "claim_revision_sha256": "claim-sha",
            }
        ]
    }
    manifest_path = tmp_path / "claim-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    lineage_body = {"claims": []}
    lineage = lineage_body | {"artifact_sha256": sha256_json(lineage_body)}
    lineage_path = tmp_path / "lineage.json"
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")

    with pytest.raises(ValueError, match="Claim set differs"):
        runner.build_attestations(
            claim_manifest_path=manifest_path,
            lineage_manifest_path=lineage_path,
            output_path=tmp_path / "attestation.json",
        )

    lineage["artifact_sha256"] = "tampered"
    lineage_path.write_text(json.dumps(lineage), encoding="utf-8")
    with pytest.raises(ValueError, match="lineage manifest SHA mismatch"):
        runner.build_attestations(
            claim_manifest_path=manifest_path,
            lineage_manifest_path=lineage_path,
            output_path=tmp_path / "attestation.json",
        )
