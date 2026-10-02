import argparse
from concurrent.futures import ThreadPoolExecutor
import copy
import json

import pytest

from backend.api.canonical_repository.viewpoint_foundation import sha256_json
from backend.api.canonical_repository.viewpoint_production_safety import CvpProductionBlocked
from backend.pipeline.viewpoint_batch_resolution_runner import load_production_grouping
from backend.pipeline.viewpoint_grouping_repository import SharedGroupingRepository
from backend.tests.test_viewpoint_reviewed_grouping import inputs


def setup_repository(tmp_path):
    args = inputs(tmp_path)
    repository = SharedGroupingRepository(tmp_path / "shared")
    digest = args["expected_manifest_sha256"]
    repository.publish(bindings=args["bindings"], expected_manifest_sha256=digest)
    return args, repository, digest


def test_publish_is_idempotent_and_lossless(tmp_path):
    args, repository, digest = setup_repository(tmp_path)
    path = repository.entry_path(digest)
    original = path.read_bytes()
    repository.publish(bindings=args["bindings"], expected_manifest_sha256=digest)
    assert path.read_bytes() == original
    assert repository.load(digest)["manifest"] == args["manifest"]
    assert not (repository.root / "latest").exists()
    assert not list(path.parent.glob(".publish-*"))


def test_concurrent_publishers_do_not_overwrite(tmp_path):
    args, repository, digest = setup_repository(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        paths = list(executor.map(lambda _: repository.publish(bindings=args["bindings"], expected_manifest_sha256=digest), range(2)))
    assert paths == [repository.entry_path(digest)] * 2


def test_same_id_cannot_repoint_to_different_binding(tmp_path):
    args, repository, digest = setup_repository(tmp_path)
    bindings = copy.deepcopy(args["bindings"])
    original = tmp_path / "manifest.json"
    alternate = tmp_path / "alternate.json"
    alternate.write_bytes(original.read_bytes())
    bindings["manifest"]["path"] = str(alternate)
    with pytest.raises(CvpProductionBlocked, match="different bindings"):
        repository.publish(bindings=bindings, expected_manifest_sha256=digest)
    assert repository.load(digest)["reference"]["bindings"] == args["bindings"]


@pytest.mark.parametrize("filename", ["manifest.json", "acceptance.json", "validation.json", "physical_review.json", "source.md", "diagram.svg"])
def test_every_consumer_rechecks_upstream_bytes(tmp_path, filename):
    _, repository, digest = setup_repository(tmp_path)
    (tmp_path / filename).write_text("changed", encoding="utf-8")
    with pytest.raises(CvpProductionBlocked):
        repository.load(digest)


@pytest.mark.parametrize("digest", ["latest", "../manifest", "A" * 64, "a" * 63])
def test_only_explicit_sha_identifiers_allowed(tmp_path, digest):
    with pytest.raises(CvpProductionBlocked):
        SharedGroupingRepository(tmp_path).load(digest)


@pytest.mark.parametrize("payload", ["{}", "[]", "null", "not-json"])
def test_corrupt_repository_reference_rejected(tmp_path, payload):
    _, repository, digest = setup_repository(tmp_path)
    repository.entry_path(digest).write_text(payload, encoding="utf-8")
    with pytest.raises(CvpProductionBlocked):
        repository.load(digest)


def test_runner_reads_same_shared_groups_without_model_call(tmp_path):
    args, repository, digest = setup_repository(tmp_path)
    namespace = argparse.Namespace(accepted_grouping_sha=digest, grouping_repository=repository.root)
    envelope = load_production_grouping(namespace, scope_packet=args["scope_packet"], freeze=args["freeze"])
    assert envelope["grouping"]["groups"] == args["manifest"]["groups"]
    assert envelope["grouping_reexecuted"] is False
    assert envelope["freeze_sha256"] == args["freeze"]["artifact_sha256"]


def test_shared_groups_cannot_bypass_current_claim_pin(tmp_path):
    args, repository, digest = setup_repository(tmp_path)
    scope = copy.deepcopy(args["scope_packet"])
    scope["claims"][0]["pinned_claim_revision"] += 1
    scope.pop("packet_sha256")
    scope["packet_sha256"] = sha256_json(scope)
    with pytest.raises(CvpProductionBlocked, match="revision drift"):
        repository.admit(digest, scope_packet=scope, freeze=args["freeze"])


def test_repository_denominator_tampering_rejected_even_if_resealed(tmp_path):
    _, repository, digest = setup_repository(tmp_path)
    path = repository.entry_path(digest)
    entry = json.loads(path.read_text())
    entry.pop("artifact_sha256")
    entry["claim_count"] += 1
    entry["artifact_sha256"] = sha256_json(entry)
    path.write_text(json.dumps(entry), encoding="utf-8")
    with pytest.raises(CvpProductionBlocked, match="denominator"):
        repository.load(digest)
