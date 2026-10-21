import copy

import pytest

from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition


def fixture():
    manifest = {"artifact_sha256": "manifest", "claim_packets": [{"claim_id": "C1", "source_id": "S1",
                "evidence_steps": [{"fragments": [{"fragment_id": "F1"}]}]}]}
    audit = {"meta": {"scope": "all"}, "layers": {
        "1": {"findings": [{"fragment_id": "F2", "source_id": "S2", "verdict": "absent"}],
              "counts": {"pass": 1, "absent": 1}, "version_binding": {"stale": 2, "stale_by_source": {"S2": 2}}},
        "2": {"source_document_findings": [{"source_id": "S2", "verdict": "sha_mismatch"}],
              "claim_occurrence_anchors": {"checked": 2, "resolved": 1, "findings": [{"claim_id": "C2"}]},
              "claim_evidence_reciprocity": {"error_count": 0}, "references_dangling": 0, "dangling": []},
    }}
    return dict(audit=audit, manifest=manifest, audit_file_sha256="audit-file-sha", authorized_by="explicit user approval")


def test_isolation_preserves_findings_and_does_not_claim_global_pass():
    args = fixture()
    original = copy.deepcopy(args["audit"])
    report = scope_audit_disposition(**args)
    assert report["status"] == "passed"
    assert report["isolated_finding_count"] == 4
    assert report["scope_findings"] == []
    assert report["full_corpus_audit_passed"] is False
    assert args["audit"] == original
    assert report["master_data_mutations"] == 0


@pytest.mark.parametrize("kind", ["fragment", "source", "claim", "registry"])
def test_in_scope_problem_cannot_be_quarantined(kind):
    args = fixture()
    layers = args["audit"]["layers"]
    if kind == "fragment":
        layers["1"]["findings"][0]["fragment_id"] = "F1"
    elif kind == "source":
        layers["2"]["source_document_findings"][0]["source_id"] = "S1"
    elif kind == "claim":
        layers["2"]["claim_occurrence_anchors"]["findings"][0]["claim_id"] = "C1"
    else:
        layers["2"]["component_locator_findings"] = [{"object_id": "CV-other-book"}]
    assert scope_audit_disposition(**args)["status"] == "blocked"


@pytest.mark.parametrize("kind", ["truncated", "stale", "unknown", "authorization", "scope"])
def test_incomplete_audit_or_authority_fails(kind):
    args = fixture()
    if kind == "truncated":
        args["audit"]["layers"]["2"]["claim_occurrence_anchors"]["resolved"] = 0
    elif kind == "stale":
        args["audit"]["layers"]["1"]["version_binding"]["stale"] = 3
    elif kind == "unknown":
        args["audit"]["layers"]["2"]["new_problem_findings"] = [{"error": "unknown"}]
    elif kind == "authorization":
        args["authorized_by"] = ""
    else:
        args["audit"]["meta"]["scope"] = "current-run"
    with pytest.raises(CvpProductionBlocked):
        scope_audit_disposition(**args)
