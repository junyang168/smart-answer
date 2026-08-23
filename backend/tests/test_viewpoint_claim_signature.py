from __future__ import annotations

import json

import pytest

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.viewpoint_claim_signature import (
    build_claim_signature_index,
    build_claim_signature_plan,
    validate_signature_response,
)
from backend.api.canonical_repository.viewpoint_foundation import semantic_record_sha, sha256_json
from backend.api.canonical_repository.viewpoint_candidate_recall import (
    ViewpointCandidateRecallArtifact,
)
from backend.api.canonical_repository.viewpoint_signature_recall import (
    build_final_candidate_graph,
    build_signature_recall,
)
from backend.api.canonical_repository.viewpoint_group_discovery import (
    build_group_discovery_plan,
    validate_group_discovery_response,
)
from backend.api.canonical_repository.viewpoint_group_recall_extension import (
    build_group_recall_extension,
)
from backend.api.semantic_index.embeddings import build_embedding_index_artifact
from backend.api.canonical_repository.viewpoint_semantic_scheduler import build_semantic_bundle_schedule
from backend.pipeline.viewpoint_claim_signature_runner import (
    ClaimSignatureCallArtifact,
    run_claim_signatures,
)
from backend.pipeline.viewpoint_signature_embedding_plan_runner import (
    build_signature_embedding_budget,
)


def _schedule():
    claims = [
        ClaimRecord(claim_id="C1", statement="人应当有力地进入天国",
                    claim_type="interpretive_judgment", attribution="professor",
                    scripture_refs=["Matt.11.12"], evidence_step_ids=["E1"], revision=1),
        ClaimRecord(claim_id="C2", statement="信徒应竭力回应天国",
                    claim_type="application", attribution="professor",
                    scripture_refs=["Matt.11.12"], evidence_step_ids=["E2"], revision=1),
    ]
    manifest = {
        "schema_version": "test_claim_manifest_v1",
        "claims": [{"claim_id": c.claim_id, "pinned_claim_revision": c.revision,
                    "claim_revision_sha256": semantic_record_sha(c)} for c in claims],
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    return build_semantic_bundle_schedule(
        preflight_packet_sha256="preflight", resolution_queue_sha256="queue",
        claim_manifest=manifest,
        candidates=[
            {"identity_candidate_id": f"VIC-{c.claim_id}", "candidate_claim_ids": [c.claim_id],
             "candidate_viewpoint_ids": [], "seed_relation_ids": [], "proposed_action": "create_new",
             "coverage_snapshot_id": "VCS", "blocker_codes": [], "generation_fingerprint": "x"}
            for c in claims
        ],
        claims=claims,
        evidence_steps=[
            {"evidence_step_id": f"E{i}", "source_fragment_ids": [f"F{i}"],
             "statement": claim.statement} for i, claim in enumerate(claims, 1)
        ],
        source_fragments=[
            {"fragment_id": f"F{i}", "source_id": f"S{i}", "source_sha256": f"sha-{i}",
             "verbatim_excerpt": claim.statement, "anchor_state": "source_version_bound"}
            for i, claim in enumerate(claims, 1)
        ],
    )


def test_signature_plan_keeps_application_and_validates_exact_once():
    schedule = _schedule()
    plan = build_claim_signature_plan(
        schedule=schedule,
        candidate_recall_artifact_sha256=schedule.candidate_recall_artifact_sha256,
        source_ineligible_claim_ids=[], model_id="gpt-5.6-sol",
        reasoning_effort="medium", max_output_tokens=32000,
        prompt_sha256="prompt-sha",
    )
    assert plan.statistics["source_eligible_claim_count"] == 2
    assert {claim.claim_type for packet in plan.packets for claim in packet.claims} == {
        "application", "interpretive_judgment"
    }
    packet = plan.packets[0]
    response = {
        "schema_version": "wang_claim_semantic_signature_response_v2",
        "packet_sha256": packet.packet_sha256,
        "signatures": [
            {"claim_id": claim.claim_id, "claim_revision_sha256": claim.claim_revision_sha256,
             "semantic_atoms": [{"atom_index": 0, "subject": "人", "predicate": "进入",
                                  "object": "天国", "polarity": "affirmed", "stance": "endorsed",
                                  "modality": "asserted", "discourse_roles": ["application"],
                                  "population_scope": [], "temporal_scope": [],
                                  "conditions": ["乙", "甲", "乙"],
                                  "material_qualifications": []}],
             "evidence_sufficient": True, "ambiguities": [], "screening_only": True,
             "identity_evidence": False}
            for claim in packet.claims
        ],
    }
    response["signatures"][0]["claim_revision_sha256"] = "model-miscopied-sha"
    validated = validate_signature_response(packet, response)
    assert validated.signatures[0].claim_revision_sha256 == packet.claims[0].claim_revision_sha256
    assert validated.signatures[0].semantic_atoms[0].conditions == ["乙", "甲"]
    response["signatures"] = response["signatures"][:-1]
    with pytest.raises(ValueError, match="exactly once"):
        validate_signature_response(packet, response)


class _FakeAdapter:
    model_id = "gpt-5.6-sol"
    backend = "codex_subscription"
    prompt_sha256 = "prompt-sha"
    generation_config_sha256 = sha256_json({
        "reasoning_effort": "medium", "max_output_tokens": 32000, "temperature": 0.0,
    })

    def __init__(self):
        self.calls = 0

    def generate(self, payload):
        self.calls += 1
        return {
            "schema_version": "wang_claim_semantic_signature_response_v2",
            "packet_sha256": payload["packet_sha256"],
            "signatures": [
                {"claim_id": claim["claim_id"],
                 "claim_revision_sha256": claim["claim_revision_sha256"],
                 "semantic_atoms": [{"atom_index": 0, "subject": "信徒",
                                      "predicate": "回应", "object": "天国",
                                      "polarity": "affirmed", "stance": "endorsed",
                                      "modality": "normative", "discourse_roles": ["application"],
                                      "population_scope": [], "temporal_scope": [],
                                      "conditions": [], "material_qualifications": []}],
                 "evidence_sufficient": True, "ambiguities": [],
                 "screening_only": True, "identity_evidence": False}
                for claim in payload["claims"]
            ],
        }


def test_signature_executor_is_resumable_and_never_mutates_master_data(tmp_path):
    schedule = _schedule()
    plan = build_claim_signature_plan(
        schedule=schedule, candidate_recall_artifact_sha256=None,
        source_ineligible_claim_ids=[], model_id="gpt-5.6-sol",
        reasoning_effort="medium", max_output_tokens=32000,
        prompt_sha256="prompt-sha",
    )
    adapter = _FakeAdapter()
    first = run_claim_signatures(plan=plan, adapter=adapter, output_dir=tmp_path, workers=1)
    assert adapter.calls == 1
    assert first.signature_count == 2
    assert first.semantic_atom_count == 2
    assert first.master_data_mutations == 0
    resumed = _FakeAdapter()
    second = run_claim_signatures(plan=plan, adapter=resumed, output_dir=tmp_path, workers=1)
    assert resumed.calls == 0
    assert second.reused_packet_count == 1


def test_signature_index_compiles_exact_once_immutable_screening_artifact(tmp_path):
    schedule = _schedule()
    plan = build_claim_signature_plan(
        schedule=schedule, candidate_recall_artifact_sha256=None,
        source_ineligible_claim_ids=["OLD-CLAIM"], model_id="gpt-5.6-sol",
        reasoning_effort="medium", max_output_tokens=32000,
        prompt_sha256="prompt-sha",
    )
    adapter = _FakeAdapter()
    run_claim_signatures(plan=plan, adapter=adapter, output_dir=tmp_path, workers=1)
    call_path = next((tmp_path / "calls").glob("*.json"))
    call_payload = json.loads(call_path.read_text(encoding="utf-8"))
    call = ClaimSignatureCallArtifact.model_validate(call_payload)

    index = build_claim_signature_index(
        plan=plan,
        responses_by_packet_id={call.packet_id: call.response},
        call_artifact_sha_by_packet_id={call.packet_id: call.artifact_sha256},
        generation_config_sha256=call.generation_config_sha256,
    )

    assert [signature.claim_id for signature in index.signatures] == ["C1", "C2"]
    assert index.source_ineligible_claim_ids == ["OLD-CLAIM"]
    assert index.statistics == {
        "input_claim_count": 3,
        "signature_count": 2,
        "source_ineligible_claim_count": 1,
        "semantic_atom_count": 2,
        "multi_atom_claim_count": 0,
        "insufficient_evidence_count": 0,
        "endorsed_atom_count": 2,
        "rejected_atom_count": 0,
        "possibility_atom_count": 0,
        "external_atom_count": 0,
        "unknown_stance_atom_count": 0,
    }
    assert index.identity_evidence is False
    assert index.apply_allowed is False

    with pytest.raises(ValueError, match="cover plan packets exactly once"):
        build_claim_signature_index(
            plan=plan,
            responses_by_packet_id={},
            call_artifact_sha_by_packet_id={},
            generation_config_sha256=call.generation_config_sha256,
        )

    embedding = build_signature_embedding_budget(
        signature_index=index,
        batch_size=2,
    )
    projection_manifest = embedding["projection_manifest"]
    embedding_plan = embedding["plan"]
    budget = embedding["summary"]
    assert projection_manifest.object_kind == "claim_signature"
    assert [item.object_id for item in projection_manifest.projections] == ["C1", "C2"]
    assert "语义原子 1" in projection_manifest.projections[0].text
    assert embedding_plan.object_kind == "claim_signature"
    assert budget.signature_count == 2
    assert budget.semantic_atom_count == 2
    assert budget.estimated_provider_call_count == 1
    assert budget.model_calls_executed == 0
    assert budget.identity_evidence is False
    assert budget.apply_allowed is False

    signature_embedding_index = build_embedding_index_artifact(
        plan=embedding_plan,
        projections=projection_manifest.projections,
        vectors_by_object_id={
            "C1": [1.0] + [0.0] * 767,
            "C2": [0.99, 0.01] + [0.0] * 766,
        },
    )
    signature_recall = build_signature_recall(
        signature_index=index,
        embedding_index=signature_embedding_index,
        top_k=1,
    )
    assert signature_recall.statistics["unique_candidate_pair_count"] == 1
    baseline_payload = {
        "schema_version": "wang_viewpoint_candidate_recall_v1",
        "claim_manifest_sha256": "manifest-sha",
        "rule_artifact_sha256": "rule-sha",
        "embedding_artifact_sha256": "embedding-sha",
        "neighborhoods": [
            {"focal_claim_id": "C1", "neighbors": []},
            {"focal_claim_id": "C2", "neighbors": []},
        ],
        "uncovered_claim_ids": ["C1", "C2"],
        "source_ineligible_claim_ids": ["OLD-CLAIM"],
        "known_positive_recall": {
            "eligible_pair_count": 0,
            "union_found_pair_count": 0,
            "union_recall": None,
            "measurement_status": "no_scoped_positive_pairs",
        },
        "statistics": {
            "input_claim_count": 3,
            "eligible_claim_count": 2,
            "source_ineligible_claim_count": 1,
            "covered_claim_count": 0,
            "uncovered_claim_count": 2,
            "rule_directed_neighbor_count": 0,
            "embedding_directed_neighbor_count": 0,
            "overlap_directed_neighbor_count": 0,
            "union_directed_neighbor_count": 0,
            "rule_unique_candidate_pair_count": 0,
            "embedding_unique_candidate_pair_count": 0,
            "overlap_unique_candidate_pair_count": 0,
            "union_unique_candidate_pair_count": 0,
        },
        "recall_only": True,
    }
    baseline = ViewpointCandidateRecallArtifact(
        **baseline_payload, artifact_sha256=sha256_json(baseline_payload)
    )
    final_graph = build_final_candidate_graph(
        candidate_recall=baseline,
        signature_recall=signature_recall,
        signature_index=index,
    )
    assert final_graph.edges[0].claim_ids == ["C1", "C2"]
    assert final_graph.edges[0].channels == ["signature"]
    assert final_graph.statistics == {
        "baseline_unique_pair_count": 0,
        "signature_unique_pair_count": 1,
        "overlap_unique_pair_count": 0,
        "signature_added_pair_count": 1,
        "union_unique_pair_count": 1,
    }
    assert final_graph.identity_evidence is False
    assert final_graph.apply_allowed is False
    group_plan = build_group_discovery_plan(
        signature_index=index,
        final_graph=final_graph,
        model_id="gpt-5.6-sol",
        reasoning_effort="medium",
        max_output_tokens=32000,
        prompt_sha256="group-prompt-sha",
    )
    assert group_plan.statistics["packet_count"] == 1
    assert group_plan.statistics["signature_edge_exposure_count"] == 1
    assert group_plan.statistics["baseline_only_retained_pair_count"] == 0
    assert group_plan.model_calls_executed == 0
    assert group_plan.identity_evidence is False
    assert group_plan.apply_allowed is False
    packet = group_plan.packets[0]
    group_response = validate_group_discovery_response(
        packet,
        {
            "schema_version": "wang_viewpoint_group_discovery_response_v1",
            "packet_sha256": packet.packet_sha256,
            "proposals": [{
                "local_group_id": "G001",
                "relation_kind": "possible_equivalent",
                "participants": [
                    {"claim_id": "C2", "role": "candidate_member"},
                    {"claim_id": "C1", "role": "candidate_member"},
                ],
                "proposed_core_proposition": "信徒应回应天国",
                "rationale": "两个签名表达相同规范性判断",
                "material_differences": [],
                "evidence_required_claim_ids": ["C2", "C1"],
                "requires_recall_extension": False,
                "screening_only": True,
                "identity_evidence": False,
            }],
            "unresolved_notes": [],
        },
    )
    assert [p.claim_id for p in group_response.proposals[0].participants] == ["C1", "C2"]
    extension = build_group_recall_extension(
        plan=group_plan,
        final_graph=final_graph,
        signature_embedding_index=signature_embedding_index,
        responses_by_packet_id={packet.packet_id: group_response},
        call_artifact_sha_by_packet_id={packet.packet_id: "call-sha"},
    )
    assert extension.edges == []
    assert extension.statistics["recall_extension_proposal_count"] == 0
    assert extension.statistics["overlay_union_unique_pair_count"] == 1
    assert extension.identity_evidence is False
    assert extension.apply_allowed is False
