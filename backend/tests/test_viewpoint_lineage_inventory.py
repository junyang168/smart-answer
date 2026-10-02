import json

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.pipeline.viewpoint_lineage_inventory import build_lineage_inventory


def test_inventory_distinguishes_coordinate_changes_from_review_metadata(tmp_path):
    prior = {"claim_id": "C1", "statement": "原主張", "claim_type": "teaching",
             "occurrences": [{"anchors": [{"paragraph_key": "S0001"}]}]}
    (tmp_path / "one.reviewed-candidate.json").write_text(json.dumps({"claims": [prior]}))
    live = dict(prior, revision=2, review_status="ai_consensus_reviewed")

    class Store:
        def list_records(self, collection):
            assert collection == "claims"
            return [live]

    manifest = {"claim_packets": [{"claim_id": "C1"}]}
    manifest["artifact_sha256"] = sha256_json(manifest)
    inventory = build_lineage_inventory(manifest=manifest, reviewed_root=tmp_path, store=Store())
    assert inventory["claims"][0]["disposition"] == "historical_current_substance_match_requires_proof_adapter"
    assert inventory["eligibility_attestation_created"] is False
    assert inventory["master_data_mutations"] == 0
    live["occurrences"] = [{"anchors": [{"paragraph_key": "S0002"}]}]
    inventory = build_lineage_inventory(manifest=manifest, reviewed_root=tmp_path, store=Store())
    assert inventory["claims"][0]["disposition"] == "occurrence_change_requires_applied_repair_proof"
    assert inventory["claims"][0]["variants"][0]["changed_substantive_fields"] == ["occurrences"]
