import hashlib
import json
from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.api.canonical_repository.viewpoint_production_safety import (
    CORPUS_COLLECTIONS,
    REGISTRY_COLLECTIONS,
    CvpProductionBlocked,
    build_batch_identity,
    build_apply_intent,
    build_cvp_freeze,
    build_plan_readback_receipt,
    claim_output_ownership,
    collection_fingerprint,
    exclusive_cvp_run_lock,
    validate_apply_authorization,
    validate_cvp_freeze,
    validate_grouping_envelope,
    validate_plan_readback_receipt,
    validate_execution_boundary,
    validate_registry_transition,
    _validate_scope_packet,
)


class FakeStore:
    def __init__(self) -> None:
        self.rows = {name: [] for name in (*CORPUS_COLLECTIONS, *REGISTRY_COLLECTIONS)}

    def list_records(self, collection: str):
        return [dict(row) for row in self.rows[collection]]


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _fixture(tmp_path: Path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = FakeStore()
    claim = ClaimRecord(
        claim_id="C1",
        statement="王教授所教导的命题",
        claim_type="teaching",
        evidence_step_ids=["E1"],
        revision=1,
        review_status="approved",
    )
    store.rows["claims"] = [claim.model_dump(mode="json")]
    store.rows["evidence_steps"] = [
        {
            "evidence_step_id": "E1",
            "statement": "证据",
            "source_fragment_id": "F1",
            "produced_claim_ids": ["C1"],
            "support_eligibility": "eligible",
            "revision": 1,
        }
    ]
    store.rows["source_fragments"] = [
        {
            "fragment_id": "F1",
            "source_id": "S1",
            "verbatim_excerpt": "逐字",
            "citation_id": "CIT1",
            "source_sha256": "source-sha",
            "anchor_state": "source_version_bound",
            "revision": 1,
        }
    ]
    store.rows["source_documents"] = [
        {
            "source_id": "S1",
            "source_type": "sermon",
            "source_sha256": "source-sha",
            "revision": 1,
        }
    ]
    source_lineage = {"claims": [{"claim_id": "C1"}]}
    source_lineage["artifact_sha256"] = sha256_json(source_lineage)
    source_attestation = {"status": "passed", "eligible_claim_ids": ["C1"]}
    source_attestation["artifact_sha256"] = sha256_json(source_attestation)
    packet = {
        "schema_version": "wang_canonical_viewpoint_scope_packet_v3",
        "scope_label": "matthew-test",
        "claim_manifest_sha256": "manifest-sha",
        "coverage_snapshot_id": "snapshot-1",
        "source_attestation_artifact_sha256": source_attestation["artifact_sha256"],
        "claims": [
            {
                "claim_id": "C1",
                "pinned_claim_revision": 1,
                "claim_revision_sha256": semantic_record_sha(claim),
                "source_id": "S1",
                "statement": claim.statement,
                "review_status": "approved",
                "scripture_refs": [],
                "evidence": [
                    {
                        "evidence_step_id": "E1",
                        "source_fragment_id": "F1",
                        "source_id": "S1",
                        "evidence_statement": "证据",
                        "scripture_refs": [],
                        "verbatim_excerpt": "逐字",
                        "citation_id": "CIT1",
                        "citation_revision": 1,
                        "citation_status": "approved",
                        "source_sha256": "source-sha",
                        "support_eligibility": "eligible",
                        "anchor_state": "source_version_bound",
                        "valid_for_identity_review": True,
                    }
                ],
            }
        ],
        "blocked_claims": [],
        "excluded_claims": [],
    }
    packet["packet_sha256"] = sha256_json(packet)
    packet_path = _write_json(tmp_path / "scope.json", packet)

    manifest = {"claims": ["C1"]}
    manifest["manifest_sha256"] = sha256_json(manifest)
    prereqs = {
        "source_universe_manifest": _write_json(tmp_path / "sources.json", {"sources": ["S1"]}),
        "claim_manifest": _write_json(tmp_path / "claims.json", manifest),
        "source_lineage_manifest": _write_json(
            tmp_path / "source-lineage.json", source_lineage
        ),
        "source_attestation": _write_json(
            tmp_path / "source-attestation.json", source_attestation
        ),
        "source_state_reconciliation": _write_json(tmp_path / "reconcile.json", {"status": "passed"}),
        "source_state_validation": _write_json(tmp_path / "reconcile-validation.json", {"status": "passed"}),
        "independent_audit": _write_json(
            tmp_path / "audit.json",
            {"layers": {"1": {"findings": []}, "2": {"findings": []}}},
        ),
        "audit_disposition": _write_json(tmp_path / "audit-disposition.json", {"status": "passed"}),
    }
    freeze = build_cvp_freeze(
        ticket_id=357,
        scope_packet_path=packet_path,
        prerequisite_paths=prereqs,
        store=store,
        cvp_policy_sha256="cvp-policy",
        route_policy_sha256="route-policy",
        runner_commit="abc123",
        worktree_root=tmp_path / "worktree",
        output_root=tmp_path / "output",
        global_lock_path=tmp_path / "locks" / "cvp.lock",
    )
    return store, packet_path, freeze


def test_freeze_blocks_packet_and_registry_drift(tmp_path):
    store, packet_path, freeze = _fixture(tmp_path)
    validate_cvp_freeze(
        freeze,
        store=store,
        cvp_policy_sha256="cvp-policy",
        route_policy_sha256="route-policy",
        runner_commit="abc123",
    )

    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["claims"][0]["statement"] = "tampered"
    _write_json(packet_path, packet)
    with pytest.raises(CvpProductionBlocked, match="bound artifact drift"):
        validate_cvp_freeze(
            freeze,
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
        )


def test_freeze_blocks_claim_revision_and_blocked_claims(tmp_path):
    store, packet_path, freeze = _fixture(tmp_path)
    store.rows["claims"][0]["revision"] = 2
    with pytest.raises(CvpProductionBlocked, match="Claim revision drift"):
        validate_cvp_freeze(
            freeze,
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
            validate_registry=False,
        )


def test_freeze_blocks_nonreciprocal_claim_evidence_graph(tmp_path):
    store, _, freeze = _fixture(tmp_path)
    store.rows["evidence_steps"][0]["produced_claim_ids"] = []
    with pytest.raises(CvpProductionBlocked, match="Claim-only evidence binding"):
        validate_cvp_freeze(
            freeze,
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
            validate_registry=False,
        )

    store, _, freeze = _fixture(tmp_path / "evidence-only")
    store.rows["evidence_steps"].append(
        {
            "evidence_step_id": "E2",
            "statement": "另一条证据",
            "produced_claim_ids": ["C1"],
            "revision": 1,
        }
    )
    with pytest.raises(CvpProductionBlocked, match="EvidenceStep-only binding"):
        validate_cvp_freeze(
            freeze,
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
            validate_registry=False,
        )


def test_freeze_blocks_nonfinal_claim_review_status(tmp_path):
    store, packet_path, _ = _fixture(tmp_path)
    store.rows["claims"][0]["review_status"] = "human_review_required"
    current = ClaimRecord.model_validate(store.rows["claims"][0])
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["claims"][0]["review_status"] = current.review_status
    packet["claims"][0]["claim_revision_sha256"] = semantic_record_sha(current)
    packet["packet_sha256"] = sha256_json(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    _write_json(packet_path, packet)
    with pytest.raises(CvpProductionBlocked, match="not identity eligible"):
        build_cvp_freeze(
            ticket_id=357,
            scope_packet_path=packet_path,
            prerequisite_paths={},
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
            worktree_root=tmp_path / "worktree",
            output_root=tmp_path / "output",
            global_lock_path=tmp_path / "lock",
        )


def test_freeze_blocks_self_consistent_but_forged_scope_projection(tmp_path):
    store, packet_path, _ = _fixture(tmp_path)
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["claims"][0]["statement"] = "不是当前 Claim 的文字"
    packet["claims"][0]["evidence"][0]["verbatim_excerpt"] = "不是当前逐字锚点"
    packet["packet_sha256"] = sha256_json(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    _write_json(packet_path, packet)
    with pytest.raises(CvpProductionBlocked) as exc_info:
        build_cvp_freeze(
            ticket_id=357,
            scope_packet_path=packet_path,
            prerequisite_paths={},
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
            worktree_root=tmp_path / "worktree",
            output_root=tmp_path / "output",
            global_lock_path=tmp_path / "lock",
        )
    assert "scope packet Claim projection differs" in str(exc_info.value)
    assert "scope packet evidence projection drift" in str(exc_info.value)


def test_freeze_accepts_only_sha_bound_current_superseded_exclusions(tmp_path):
    store, packet_path, _ = _fixture(tmp_path)
    superseded = ClaimRecord(
        claim_id="C2",
        statement="已经被最终裁定取代的命题",
        claim_type="teaching",
        revision=3,
        review_status="superseded",
    )
    store.rows["claims"].append(superseded.model_dump(mode="json"))
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["excluded_claims"] = [
        {
            "claim_id": "C2",
            "reason_code": "superseded_claim",
            "review_status": "superseded",
            "claim_revision": 3,
            "claim_revision_sha256": semantic_record_sha(superseded),
        }
    ]
    packet["packet_sha256"] = sha256_json(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    _write_json(packet_path, packet)
    assert _validate_scope_packet(packet, store) == []

    store.rows["claims"][-1]["review_status"] = "approved"
    assert any(
        "not bound to a current superseded" in finding
        for finding in _validate_scope_packet(packet, store)
    )

    store, packet_path, _ = _fixture(tmp_path / "blocked")
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["blocked_claims"] = [{"claim_id": "C2", "reason_code": "missing"}]
    packet["packet_sha256"] = sha256_json(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    _write_json(packet_path, packet)
    with pytest.raises(CvpProductionBlocked, match="blocked Claims"):
        build_cvp_freeze(
            ticket_id=357,
            scope_packet_path=packet_path,
            prerequisite_paths={},
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
            worktree_root=tmp_path / "blocked" / "worktree",
            output_root=tmp_path / "blocked" / "output",
            global_lock_path=tmp_path / "blocked" / "lock",
        )

    store, _, freeze = _fixture(tmp_path / "fresh")
    store.rows["canonical_viewpoints"].append({"viewpoint_id": "VP1", "revision": 1})
    with pytest.raises(CvpProductionBlocked, match="registry_fingerprint_sha256 drift"):
        validate_cvp_freeze(
            freeze,
            store=store,
            cvp_policy_sha256="cvp-policy",
            route_policy_sha256="route-policy",
            runner_commit="abc123",
        )


def test_grouping_batch_identity_and_apply_authorization_are_freeze_bound(tmp_path):
    _, _, freeze = _fixture(tmp_path)
    body = {
        "schema_version": "wang_canonical_viewpoint_grouping_envelope_v2",
        "freeze_sha256": freeze["artifact_sha256"],
        "scope_packet_sha256": freeze["scope_packet_sha256"],
        "cvp_policy_sha256": freeze["cvp_policy_sha256"],
        "grouping": {"scope_label": "matthew-test", "groups": []},
    }
    envelope = body | {"artifact_sha256": sha256_json(body)}
    grouping_sha = validate_grouping_envelope(envelope, freeze=freeze)
    foreign = dict(envelope)
    foreign["freeze_sha256"] = "foreign"
    foreign["artifact_sha256"] = sha256_json(
        {key: value for key, value in foreign.items() if key != "artifact_sha256"}
    )
    with pytest.raises(CvpProductionBlocked, match="another freeze"):
        validate_grouping_envelope(foreign, freeze=freeze)

    first = build_batch_identity(
        scope_label="matthew-test",
        freeze_sha256=freeze["artifact_sha256"],
        grouping_sha256=grouping_sha,
        claim_ids=["C1"],
        cvp_policy_sha256="cvp-policy",
    )
    second = build_batch_identity(
        scope_label="matthew-test",
        freeze_sha256=freeze["artifact_sha256"],
        grouping_sha256=grouping_sha,
        claim_ids=["C2"],
        cvp_policy_sha256="cvp-policy",
    )
    assert first["batch_id"] != second["batch_id"]

    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"database backup")
    auth_body = {
        "schema_version": "wang_cvp_apply_authorization_v1",
        "status": "authorized",
        "freeze_sha256": freeze["artifact_sha256"],
        "grouping_sha256": grouping_sha,
        "backup_dump": {
            "path": str(backup),
            "sha256": hashlib.sha256(backup.read_bytes()).hexdigest(),
        },
        "pg_restore_list_sha256": "restore-list-sha",
    }
    authorization = auth_body | {"artifact_sha256": sha256_json(auth_body)}
    validate_apply_authorization(
        authorization, freeze=freeze, grouping_sha256=grouping_sha
    )
    authorization["grouping_sha256"] = "other"
    with pytest.raises(CvpProductionBlocked):
        validate_apply_authorization(
            authorization, freeze=freeze, grouping_sha256=grouping_sha
        )


def test_output_ownership_and_global_lock_fail_closed(tmp_path):
    _, _, freeze = _fixture(tmp_path)
    output = tmp_path / "output"
    first = claim_output_ownership(
        output_root=output, freeze=freeze, runner_commit="abc123"
    )
    assert claim_output_ownership(
        output_root=output, freeze=freeze, runner_commit="abc123"
    ) == first

    changed = dict(freeze)
    changed["artifact_sha256"] = "another-run"
    with pytest.raises(CvpProductionBlocked, match="owned by another"):
        claim_output_ownership(
            output_root=output, freeze=changed, runner_commit="abc123"
        )

    with exclusive_cvp_run_lock(freeze):
        with pytest.raises(CvpProductionBlocked, match="global lock"):
            with exclusive_cvp_run_lock(freeze):
                pass

    inside = dict(freeze)
    inside["output_root"] = str((tmp_path / "worktree" / "outputs").resolve())
    with pytest.raises(CvpProductionBlocked, match="inside the worktree"):
        validate_execution_boundary(
            inside,
            worktree_root=tmp_path / "worktree",
            output_root=tmp_path / "worktree" / "outputs",
        )


def test_apply_intent_rejects_foreign_registry_delta():
    operation = SimpleNamespace(
        operation="create",
        collection="canonical_viewpoints",
        object_id="CV1",
        after_revision=1,
        payload={"viewpoint_id": "CV1"},
    )
    plan = SimpleNamespace(fingerprint_sha256="plan-sha", operations=(operation,))

    class IntentStore(FakeStore):
        def get_record_state(self, collection, object_id):
            return None

    store = IntentStore()
    pre = collection_fingerprint(store, REGISTRY_COLLECTIONS)
    intent = build_apply_intent(
        plan=plan,
        store=store,
        batch_identity={"batch_id": "CVB1"},
        freeze_sha256="freeze",
        grouping_sha256="grouping",
        expected_pre_registry_fingerprint_sha256=pre,
    )
    store.rows["canonical_viewpoints"] = [
        {"viewpoint_id": "CV1", "revision": 1},
        {"viewpoint_id": "FOREIGN", "revision": 1},
    ]
    with pytest.raises(CvpProductionBlocked, match="outside the authorized"):
        validate_registry_transition(intent, store=store)


def test_full_plan_readback_checks_payload_and_resume_chain():
    operation = SimpleNamespace(
        operation="create",
        collection="canonical_viewpoints",
        object_id="CV1",
        after_revision=1,
        after_sha256="content-sha",
        payload={"viewpoint_id": "CV1", "current_revision_id": "CVR1"},
    )
    plan = SimpleNamespace(
        fingerprint_sha256="changeset-sha",
        change_set_id="CS1",
        operations=(operation,),
    )

    class ReadbackStore:
        def __init__(self):
            self.payload = {
                "viewpoint_id": "CV1",
                "current_revision_id": "CVR1",
                "revision": 1,
            }

        def get_change_set_status(self, fingerprint):
            return "applied" if fingerprint == "changeset-sha" else None

        def get_record_state(self, collection, object_id):
            return {
                "payload": dict(self.payload),
                "revision": 1,
                "content_sha256": "content-sha",
                "retired": False,
            }

    store = ReadbackStore()
    identity = {"batch_id": "CVB1", "batch_key_sha256": "batch-sha"}
    receipt = build_plan_readback_receipt(
        plan=plan,
        store=store,
        batch_identity=identity,
        freeze_sha256="freeze-sha",
        grouping_sha256="grouping-sha",
        pre_registry_fingerprint_sha256="registry-before",
        post_registry_fingerprint_sha256="registry-after",
    )
    validate_plan_readback_receipt(
        receipt,
        store=store,
        batch_identity=identity,
        expected_pre_registry_fingerprint_sha256="registry-before",
    )
    with pytest.raises(CvpProductionBlocked, match="Registry cut chain"):
        validate_plan_readback_receipt(
            receipt,
            store=store,
            batch_identity=identity,
            expected_pre_registry_fingerprint_sha256="wrong-before",
        )

    store.payload["current_revision_id"] = "tampered"
    with pytest.raises(CvpProductionBlocked, match="payload mismatch"):
        build_plan_readback_receipt(
            plan=plan,
            store=store,
            batch_identity=identity,
            freeze_sha256="freeze-sha",
            grouping_sha256="grouping-sha",
            pre_registry_fingerprint_sha256="registry-before",
            post_registry_fingerprint_sha256="registry-after",
        )


def test_runner_dry_run_exits_before_any_model_client(tmp_path, monkeypatch):
    from backend.pipeline import viewpoint_batch_resolution_runner as runner

    store, packet_path, freeze = _fixture(tmp_path)
    grouping_body = {
        "schema_version": "wang_canonical_viewpoint_grouping_envelope_v2",
        "scope_label": "matthew-test",
        "freeze_sha256": freeze["artifact_sha256"],
        "scope_packet_sha256": freeze["scope_packet_sha256"],
        "cvp_policy_sha256": "cvp-policy",
        "grouping": {
            "scope_label": "matthew-test",
            "groups": [{"group_key": "g", "claim_ids": ["C1"], "rationale": "r"}],
        },
    }
    grouping = grouping_body | {"artifact_sha256": sha256_json(grouping_body)}
    grouping_path = _write_json(tmp_path / "grouping.json", grouping)
    freeze_path = _write_json(tmp_path / "freeze.json", freeze)
    partition_policy = {"max_request_bytes": 500000}
    partition_path = _write_json(
        tmp_path / "partition.json",
        {
            "partition_policy": partition_policy,
            "partition_policy_sha256": sha256_json(partition_policy),
            "partitions": [{"partition_id": "p00001", "claims": [{"claim_id": "C1"}]}],
        },
    )
    policy = {
        "batch_size": 20,
        "max_request_bytes": 500000,
        "grouping": {"provider": "claude", "model": "claude-opus-5", "effort": "high"},
        "proposal": {"provider": "codex", "model": "gpt-5.6-sol", "effort": "high"},
        "review": {"provider": "claude", "model": "claude-opus-5", "effort": "high"},
        "correction": {"provider": "codex", "model": "gpt-5.6-sol", "effort": "high"},
        "consolidation": {"provider": "claude", "model": "claude-opus-5", "effort": "high"},
    }
    monkeypatch.setattr(runner, "load_cvp_policy", lambda path: policy)
    monkeypatch.setattr(runner, "cvp_policy_prompt_sha256s", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "cvp_policy_fingerprint", lambda *args, **kwargs: "cvp-policy")
    monkeypatch.setattr(runner, "load_route_policy", lambda path: {})
    monkeypatch.setattr(runner, "route_policy_prompt_sha256s", lambda *args, **kwargs: {})
    monkeypatch.setattr(runner, "route_policy_fingerprint", lambda *args, **kwargs: "route-policy")
    monkeypatch.setattr(runner, "_repository_commit", lambda: "abc123")
    monkeypatch.setattr(runner, "PostgresKnowledgeStore", lambda url: store)
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path / "worktree")
    monkeypatch.setattr(runner, "load_partition_policy", lambda path: partition_policy)
    partition_checks = []
    monkeypatch.setattr(
        runner,
        "validate_partition_manifest",
        lambda *args, **kwargs: partition_checks.append(kwargs) or {"status": "valid"},
    )
    for name in (
        "build_grouper",
        "build_proposer",
        "build_reviewer",
        "build_reconsiderer",
        "build_consolidator",
    ):
        monkeypatch.setattr(
            runner,
            name,
            lambda *args, _name=name, **kwargs: (_ for _ in ()).throw(
                AssertionError(f"{_name} must not be called during dry-run")
            ),
        )
    args = Namespace(
        cvp_policy=tmp_path / "policy.json",
        route_policy=tmp_path / "route.json",
        database_url=None,
        freeze=freeze_path,
        global_freeze=freeze_path,
        partition_manifest=partition_path,
        partition_id="p00001",
        packet=packet_path,
        output_dir=tmp_path / "output",
        batch_size=None,
        proposal_provider="codex",
        proposal_model="gpt-5.6-sol",
        proposal_effort="high",
        review_provider="claude",
        review_model="claude-opus-5",
        review_effort="high",
        consolidation_provider="claude",
        consolidation_model="claude-opus-5",
        consolidation_effort="high",
        group_model="claude-opus-5",
        group_effort="high",
        no_reconsider=False,
        no_consolidate=False,
        apply=False,
        apply_authorization=None,
        grouping=grouping_path,
        group=False,
        group_key=None,
        max_batches=None,
        dry_run=True,
    )

    assert runner.execute(args) == 0
    assert len(partition_checks) == 1
    assert partition_checks[0]["partition_id"] == "p00001"


def test_runner_rejects_missing_partition_before_store_or_models(monkeypatch):
    from backend.pipeline import viewpoint_batch_resolution_runner as runner

    monkeypatch.setattr(
        runner, "PostgresKnowledgeStore",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("DB accessed")),
    )
    with pytest.raises(CvpProductionBlocked, match="--partition-manifest"):
        runner.execute(Namespace(global_freeze=None, partition_manifest=None, partition_id=None))


def test_runner_cli_defaults_match_the_fable_v2_policy(tmp_path, monkeypatch):
    import sys

    from backend.pipeline import viewpoint_batch_resolution_runner as runner

    captured = {}
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "viewpoint_batch_resolution_runner",
            "--packet",
            str(tmp_path / "packet.json"),
            "--output-dir",
            str(tmp_path / "output"),
            "--freeze",
            str(tmp_path / "freeze.json"),
            "--group",
        ],
    )
    monkeypatch.setattr(
        runner,
        "execute",
        lambda args: captured.update(vars(args)) or 0,
    )

    assert runner.main() == 0
    assert captured["review_model"] == "claude-fable-5-1"
    assert captured["consolidation_model"] == "claude-fable-5-1"
    assert captured["group_model"] == "claude-fable-5-1"
    assert captured["cvp_policy"].name == "wang_cvp_resolution_policy_v2.json"
    assert captured["route_policy"].name == "wang_route_resolution_policy_v2.json"


def test_runner_resume_rejects_forged_applied_local_state(tmp_path):
    from backend.pipeline.viewpoint_batch_resolution_runner import (
        _validate_applied_resume,
    )

    identity = {"batch_id": "CVB1", "batch_key_sha256": "key"}
    receipt_body = {
        "schema_version": "wang_cvp_plan_readback_receipt_v1",
        "batch_identity": identity,
        "freeze_sha256": "freeze",
        "grouping_sha256": "grouping",
        "pre_registry_fingerprint_sha256": "before",
        "post_registry_fingerprint_sha256": "after",
        "change_set_id": "CS1",
        "change_set_sha256": "not-in-db",
        "change_set_status": "applied",
        "records": [],
        "status": "passed",
    }
    receipt = receipt_body | {"artifact_sha256": sha256_json(receipt_body)}
    _write_json(tmp_path / "receipt.json", receipt)
    state_body = {
        "schema_version": "wang_canonical_viewpoint_batch_current_state_v1",
        **identity,
        "status": "applied_and_route_enqueued",
        "authoritative_artifact": "receipt.json",
        "authoritative_artifact_sha256": receipt["artifact_sha256"],
        "superseded_artifacts": [],
    }
    state = state_body | {"artifact_sha256": sha256_json(state_body)}

    class EmptyStore:
        def get_change_set_status(self, fingerprint):
            return None

    with pytest.raises(CvpProductionBlocked, match="not applied"):
        _validate_applied_resume(
            state=state,
            batch_dir=tmp_path,
            batch_identity=identity,
            store=EmptyStore(),
            expected_pre_registry_fingerprint_sha256="before",
        )
