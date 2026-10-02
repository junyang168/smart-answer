import copy
import hashlib
import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import (
    CvpProductionBlocked, validate_scoped_audit_admission,
)
from backend.pipeline.viewpoint_grouping_repository import SharedGroupingRepository
from backend.pipeline.viewpoint_scoped_audit_disposition import scope_audit_disposition
from backend.tests.test_viewpoint_reviewed_grouping import inputs
from backend.tests.test_viewpoint_scoped_audit_disposition import fixture


def admission(tmp_path):
    args = inputs(tmp_path)
    repo = SharedGroupingRepository(tmp_path / "shared")
    path = repo.publish(bindings=args["bindings"], expected_manifest_sha256=args["expected_manifest_sha256"])
    audit = fixture()["audit"]
    audit_sha = hashlib.sha256(json.dumps(audit).encode()).hexdigest()
    disposition = scope_audit_disposition(audit=audit, manifest=args["manifest"], audit_file_sha256=audit_sha, authorized_by="user authorized isolation")
    return dict(audit=audit, disposition=disposition, reference=json.loads(path.read_text()), audit_file_sha256=audit_sha, selected_claim_ids=["C1"])


def test_actual_findings_are_recomputed_not_hidden(tmp_path):
    args = admission(tmp_path)
    assert validate_scoped_audit_admission(**args)["manifest"]["claim_packets"][0]["claim_id"] == "C1"
    assert args["disposition"]["full_corpus_audit_passed"] is False
    assert args["disposition"]["isolated_finding_count"] == 4


@pytest.mark.parametrize("mutation", ["omit_finding", "audit_sha", "grouping_sha", "denominator", "scope_failure", "registry_failure", "authority"])
def test_false_pass_cannot_authorize_production(tmp_path, mutation):
    args = admission(tmp_path)
    if mutation == "omit_finding":
        args["disposition"]["isolated_findings"].pop()
    elif mutation == "audit_sha":
        args["audit_file_sha256"] = "other-audit"
    elif mutation == "grouping_sha":
        args["disposition"]["grouping_manifest_sha256"] = "other-grouping"
    elif mutation == "denominator":
        args["selected_claim_ids"] = []
    elif mutation == "scope_failure":
        args["audit"]["layers"]["1"]["findings"][0]["fragment_id"] = "F1"
    elif mutation == "registry_failure":
        args["audit"]["layers"]["2"]["component_locator_findings"] = [{"object_id": "other-book-cvp"}]
    else:
        args["disposition"]["authorized_by"] = ""
    unsigned = copy.deepcopy(args["disposition"])
    unsigned.pop("artifact_sha256")
    args["disposition"]["artifact_sha256"] = sha256_json(unsigned)
    with pytest.raises(CvpProductionBlocked):
        validate_scoped_audit_admission(**args)


def test_drifted_svg_blocks_admission(tmp_path):
    args = admission(tmp_path)
    (tmp_path / "diagram.svg").write_text("changed")
    with pytest.raises(CvpProductionBlocked, match="SHA drift"):
        validate_scoped_audit_admission(**args)
