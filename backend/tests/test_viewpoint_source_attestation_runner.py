import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline import viewpoint_source_attestation_runner as runner
from backend.api.canonical_repository.knowledge_models import ClaimRecord


class EmptyStore:
    def __init__(self, database_url=None):
        pass

    def list_records(self, collection):
        return []


def test_review_input_comparison_allows_only_review_metadata_changes():
    prior = ClaimRecord(claim_id="C1", statement="原主張", claim_type="exegetical", evidence_step_ids=["E1"], revision=1)
    current = prior.model_copy(update={"review_status": "ai_consensus_reviewed", "reviewed_by": "reviewer", "revision": 2})
    assert runner._review_input_meaning(prior) == runner._review_input_meaning(current)
    assert runner.semantic_record_sha(prior) != runner.semantic_record_sha(current)


@pytest.mark.parametrize("update", [{"statement": "改寫主張"}, {"evidence_step_ids": ["E2"]}, {"scripture_refs": ["Matt.2.1"]}, {"claim_type": "theological"}, {"occurrences": []}])
def test_review_input_comparison_rejects_content_changes(update):
    prior = ClaimRecord(claim_id="C1", statement="原主張", claim_type="exegetical", evidence_step_ids=["E1"], revision=1)
    if update == {"occurrences": []}:
        update = {"occurrences": [{"source_id": "S1", "anchors": []}]}
    changed = prior.model_copy(update=update)
    assert runner._review_input_meaning(prior) != runner._review_input_meaning(changed)


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
