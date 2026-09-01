from __future__ import annotations

import hashlib
import json

import pytest

from backend.api.canonical_repository.knowledge_models import (
    ClaimRecord,
    EvidenceStepRecord,
    KnowledgeSourceDocument,
    SourceFragmentRecord,
    ViewpointIdentityCandidateRecord,
)
from backend.api.canonical_repository.matthew16_viewpoint_pilot import (
    build_matthew16_pilot_scope,
)
from backend.api.canonical_repository.matthew16_viewpoint_candidate import (
    AdjacentPropositionUnit,
    ArticleViewpointAcceptance,
    Matthew16ViewpointPilotArtifact,
    PilotViewpointMember,
    build_pilot_composition_projection,
    classify_pilot_viewpoint,
)
from backend.api.canonical_repository.matthew16_viewpoint_finalization import (
    build_matthew16_viewpoint_finalization_bundle,
)
from backend.api.canonical_repository.matthew16_viewpoint_promotion import (
    build_matthew16_viewpoint_promotion_proposal,
)
from backend.api.canonical_repository.knowledge_models import (
    ViewpointPropositionSignature,
    ViewpointScope,
)
from backend.api.canonical_repository.viewpoint_proposition_units import (
    ClaimAtomicDecompositionArtifact,
    PropositionUnitCandidate,
)
from backend.api.canonical_repository.viewpoint_resolution import (
    ReviewClaim,
    ReviewEvidence,
    ViewpointIdentityReviewPacket,
)
from backend.api.canonical_repository.viewpoint_runtime_projection import (
    ViewpointRuntimeCompiler,
    build_projection_dependencies,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)
from backend.pipeline.occurrence_section_projection import claim_universe_sha256


def _fixture(tmp_path):
    sources = [
        {
            "source_id": "notes_manuscript:16章釋經",
            "source_type": "notes_manuscript",
            "transcript_id": "notes_manuscript:16章釋經",
            "title": "notes",
            "source_sha256": "a" * 64,
        },
        {
            "source_id": "SRC-FOUR-1",
            "source_type": "sermon_transcript",
            "transcript_id": "四1",
            "title": "四1",
            "source_sha256": "b" * 64,
        },
    ]
    catalog_sources = [
        {"source_id": "notes_manuscript:16章釋經", "title": "notes", "source_type": "notes_to_manuscript"},
        {"source_id": "sermon:四1", "title": "四1", "source_type": "sermon_transcript"},
        {"source_id": "sermon:missing", "title": "missing", "source_type": "sermon_transcript"},
    ]
    catalog_sources.extend(
        {"source_id": f"sermon:filler-{index}", "title": f"filler-{index}", "source_type": "sermon_transcript"}
        for index in range(9)
    )
    catalog = {"chapters": [{"chapter": 16, "sources": catalog_sources}]}
    selection = {
        "schema_version": "wang_viewpoint_backfill_source_selection_v1",
        "selection_id": "SEL",
        "selected_by": "test",
        "selected_at": "2026-08-22T00:00:00Z",
        "selection_basis": "test",
        "members": [
            {"source_id": row["source_id"], "latest_extraction_status": "applied", "lineage_ref": f"KCS-{index}"}
            for index, row in enumerate(sources)
        ],
    }
    selection["selection_sha256"] = sha256_json(selection)
    claims = [
        {
            "claim_id": "C-CORE",
            "statement": "彼得的认信是磐石。",
            "claim_type": "interpretive_judgment",
            "attribution": "professor",
            "scripture_refs": ["太16:18"],
        },
        {
            "claim_id": "C-CONTEXT",
            "statement": "这个应用需要保留。",
            "claim_type": "application",
            "attribution": "professor",
            "scripture_refs": [],
        },
    ]
    manifest = {
        "schema_version": "viewpoint_input_claim_manifest_v1",
        "source_manifest_sha256": "c" * 64,
        "claims": [
            {
                "claim_id": claim["claim_id"],
                "pinned_claim_revision": 1,
                "claim_revision_sha256": semantic_record_sha(
                    ClaimRecord.model_validate(claim)
                ),
                "source_id": sources[index]["source_id"],
            }
            for index, claim in enumerate(claims)
        ],
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    article = tmp_path / "DRAFT-M16-TEST"
    article.mkdir()
    (article / "manuscript.md").write_text("# test\n", encoding="utf-8")
    (article / "program-audit.json").write_text(
        json.dumps(
            {
                "draft_id": "DRAFT-M16-TEST",
                "paragraph_provenance": [
                    {"claim_ids": ["C-CORE", "LEGACY-CLAIM"]}
                ],
            }
        ),
        encoding="utf-8",
    )
    return catalog, selection, manifest, sources, claims, article


def test_pilot_scope_preserves_context_and_reports_source_gap(tmp_path):
    catalog, selection, manifest, sources, claims, article = _fixture(tmp_path)
    result = build_matthew16_pilot_scope(
        source_catalog=catalog,
        source_catalog_sha256="1" * 64,
        source_map_sha256="2" * 64,
        source_selection=selection,
        claim_manifest=manifest,
        source_documents=sources,
        claims=claims,
        article_dirs=[article],
        thematic_source_ids=["sermon:missing"],
    )

    assert result.statistics["mapped_source_total"] == 12
    assert result.statistics["latest_detailed_source_total"] == 2
    assert result.statistics["thematic_deferred_source_total"] == 1
    assert result.statistics["latest_detailed_source_gap_total"] == 9
    assert {item.claim_id: item.lane for item in result.claims} == {
        "C-CONTEXT": "source_context_candidate",
        "C-CORE": "core",
    }
    assert next(item for item in result.claims if item.claim_id == "C-CORE").passage_unit_ids == ["16:13-18"]
    fixture = result.article_acceptance_fixtures[0]
    assert fixture.exact_current_claim_ids == ["C-CORE"]
    assert fixture.requires_semantic_alignment_claim_ids == ["LEGACY-CLAIM"]
    assert result.model_calls_executed == 0
    assert result.master_data_mutations == 0
    assert result.apply_allowed is False


def test_pilot_scope_sha_binds_article_bytes(tmp_path):
    catalog, selection, manifest, sources, claims, article = _fixture(tmp_path)
    result = build_matthew16_pilot_scope(
        source_catalog=catalog,
        source_catalog_sha256="1" * 64,
        source_map_sha256="2" * 64,
        source_selection=selection,
        claim_manifest=manifest,
        source_documents=sources,
        claims=claims,
        article_dirs=[article],
        thematic_source_ids=["sermon:missing"],
    )
    expected = hashlib.sha256((article / "manuscript.md").read_bytes()).hexdigest()
    assert result.article_acceptance_fixtures[0].manuscript_sha256 == expected
    assert result.artifact_sha256


def test_pilot_scope_producer_uses_argument_dependency_selector(tmp_path):
    catalog, selection, manifest, sources, claims, article = _fixture(tmp_path)
    # The second Claim cites no Matthew passage, but is an upstream premise for
    # the direct Matthew seed in the same source.
    claims[1]["statement"] = "弗二章的论证支持这项结论。"
    manifest["claims"][1]["source_id"] = manifest["claims"][0]["source_id"]
    manifest["claims"][1]["claim_revision_sha256"] = semantic_record_sha(
        ClaimRecord.model_validate(claims[1])
    )
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = sha256_json(manifest)
    result = build_matthew16_pilot_scope(
        source_catalog=catalog,
        source_catalog_sha256="1" * 64,
        source_map_sha256="2" * 64,
        source_selection=selection,
        claim_manifest=manifest,
        source_documents=sources,
        claims=claims,
        claim_relations=[
            {
                "claim_relation_id": "CR-CONTEXT-CORE",
                "from_id": "C-CONTEXT",
                "to_id": "C-CORE",
                "relation_type": "supports",
                "review_status": "candidate",
            }
        ],
        article_dirs=[article],
        thematic_source_ids=["sermon:missing"],
    )
    context = next(item for item in result.claims if item.claim_id == "C-CONTEXT")
    assert context.lane == "core"
    assert context.passage_unit_ids == ["16:13-18"]
    assert context.admission_basis[0]["signal"] == "claim_relation"
    assert context.admission_basis[0]["authority"] == "recall_only"


def test_pilot_scope_producer_consumes_sha_bound_occurrence_admissions(tmp_path):
    catalog, selection, manifest, sources, claims, article = _fixture(tmp_path)
    manifest["claims"][1]["source_id"] = manifest["claims"][0]["source_id"]
    manifest.pop("manifest_sha256")
    manifest["manifest_sha256"] = sha256_json(manifest)
    expected_universe = claim_universe_sha256(manifest["claims"])

    result = build_matthew16_pilot_scope(
        source_catalog=catalog,
        source_catalog_sha256="1" * 64,
        source_map_sha256="2" * 64,
        source_selection=selection,
        claim_manifest=manifest,
        source_documents=sources,
        claims=claims,
        occurrence_admissions_by_claim={
            "C-CONTEXT": [
                {
                    "passage_unit_ids": ["16:13-18"],
                    "evidence_step_id": "E-CONTEXT",
                    "source_fragment_id": "F-CONTEXT",
                    "section_index": 1,
                }
            ]
        },
        occurrence_projection_sha256="d" * 64,
        occurrence_projection_claim_universe_sha256=expected_universe,
        article_dirs=[article],
        thematic_source_ids=["sermon:missing"],
    )

    context = next(item for item in result.claims if item.claim_id == "C-CONTEXT")
    assert context.lane == "core"
    assert context.passage_unit_ids == ["16:13-18"]
    assert context.admission_basis[0]["signal"] == "occurrence_section"
    assert context.admission_basis[0]["source_fragment_id"] == "F-CONTEXT"
    assert result.occurrence_projection_sha256 == "d" * 64


def test_pilot_viewpoint_classification_is_explicit_and_fail_closed():
    pilot = Matthew16ViewpointPilotArtifact.model_construct(
        proposition_signature=ViewpointPropositionSignature(
            subject="太16:18的磐石",
            predicate="指向",
            object="彼得本人",
            polarity="denied",
            modality="教授的释经判断",
        ),
        scope=ViewpointScope(scripture_scope=["Matt.16.18"]),
    )

    classification = classify_pilot_viewpoint(pilot)

    assert classification.knowledge_role == "passage_interpretation"
    assert classification.processing_phase == "passage_exegesis"
    assert classification.scripture_scope == ["Matt.16.18"]
    assert classification.basis_fields == [
        "proposition_signature.modality",
        "scope.scripture_scope",
    ]

    unscoped = pilot.model_copy(update={"scope": ViewpointScope()})
    with pytest.raises(ValueError, match="cannot be classified"):
        classify_pilot_viewpoint(unscoped)


def test_master_promotion_preserves_atomic_membership_boundary():
    evidence = ReviewEvidence(
        evidence_step_id="EV-1",
        source_fragment_id="FR-1",
        source_id="SRC-1",
        evidence_statement="证据",
        verbatim_excerpt="原文",
        citation_id="",
        citation_revision=1,
        citation_status="unresolved",
        source_sha256="s" * 64,
        support_eligibility="eligible_candidate",
        anchor_state="source_version_bound",
        source_eligibility_attestation_sha256="a" * 64,
        valid_for_identity_review=True,
    )

    def unit(start: int, end: int, text: str) -> PropositionUnitCandidate:
        payload = {
            "parent_claim_id": "CL-1",
            "pinned_claim_revision": 1,
            "claim_revision_sha256": "c" * 64,
            "source_id": "SRC-1",
            "unit_statement": text,
            "structural_role": "conjunct",
            "claim_statement_spans": [{"start_char": start, "end_char": end, "exact_text": text}],
            "evidence": [evidence],
            "candidate_status": "atomic_candidate",
            "approval_status": "not_human_approved",
        }
        return PropositionUnitCandidate(
            proposition_unit_id=f"VPU-{sha256_json({
                **payload,
                'evidence': [evidence.model_dump(mode='json')],
            })[:20]}",
            **payload,
        )

    units = [
        unit(0, 1, "甲"),
        unit(1, 2, "乙"),
        unit(2, 3, "丙"),
    ]
    member_ids = sorted([units[0].proposition_unit_id, units[1].proposition_unit_id])
    excluded_id = units[2].proposition_unit_id
    claim = ReviewClaim(
        claim_id="CL-1",
        pinned_claim_revision=1,
        claim_revision_sha256="c" * 64,
        source_id="SRC-1",
        statement="甲乙丙",
        review_status="candidate",
        source_eligibility_attestation_sha256="a" * 64,
        evidence=[evidence],
    )
    decomposition = ClaimAtomicDecompositionArtifact.model_construct(
        parent_packet_sha256="packet-sha",
        claim=claim,
        proposition_units=units,
        artifact_sha256="d" * 64,
    )
    pilot = Matthew16ViewpointPilotArtifact.model_construct(
        viewpoint_candidate_id="CVP-TEST",
        viewpoint_revision_candidate_id="CVPR-TEST",
        core_proposition="甲与乙表达同一释经判断",
        proposition_signature=ViewpointPropositionSignature(
            subject="太16:18的磐石",
            predicate="指向",
            object="彼得本人",
            polarity="denied",
            modality="教授的释经判断",
        ),
        scope=ViewpointScope(scripture_scope=["Matt.16.18"]),
        members=sorted(
            [
                PilotViewpointMember(proposition_unit=units[0], parent_claim=claim),
                PilotViewpointMember(proposition_unit=units[1], parent_claim=claim),
            ],
            key=lambda item: item.proposition_unit.proposition_unit_id,
        ),
        adjacent_non_members=[
            AdjacentPropositionUnit(
                proposition_unit_id=excluded_id,
                parent_claim_id="CL-1",
                unit_statement="丙",
            )
        ],
        article_acceptance=ArticleViewpointAcceptance(
            draft_id="DRAFT-1",
            manuscript_sha256="m" * 64,
            article_proposition="甲与乙",
            start_char=0,
            end_char=3,
            supporting_proposition_unit_ids=member_ids,
        ),
        parent_scope_artifact_sha256="q" * 64,
        boundary_run_artifact_sha256="b" * 64,
        artifact_sha256="p" * 64,
        model_ids=["claude-opus-5", "gpt-5.6-sol"],
        blockers=["not_master_applied", "pilot_scope_only"],
    )
    boundary = {
        "artifact_sha256": "b" * 64,
        "semantic_agreement": True,
        "synthesis_eligible": True,
        "model_ids": ["claude-opus-5", "gpt-5.6-sol"],
        "decomposition_artifact_sha256s": ["d" * 64],
        "unit_universe_ids": sorted([*member_ids, excluded_id]),
        "participant_unit_ids": member_ids,
        "adjacent_unit_ids": [excluded_id],
        "assessment_artifact_sha256s": ["a" * 64, "z" * 64],
    }
    evidence_packet = ViewpointIdentityReviewPacket.model_construct(
        candidate=ViewpointIdentityCandidateRecord.model_construct(
            candidate_claim_ids=["CL-1"]
        ),
        claims=[claim],
        deterministic_blockers=[],
        packet_sha256="packet-sha",
    )

    proposal = build_matthew16_viewpoint_promotion_proposal(
        pilot=pilot,
        boundary_run=boundary,
        evidence_packet=evidence_packet,
        decompositions=[decomposition],
        proposed_at="2026-08-23T00:00:00Z",
    )

    assert len(proposal.proposition_units) == 3
    assert [item.proposition_unit_id for item in proposal.proposition_unit_links] == member_ids
    assert proposal.excluded_proposition_unit_ids == [excluded_id]
    assert proposal.claim_membership_link_count == 0
    assert proposal.apply_allowed is False
    assert "formal_quality_report_missing" in proposal.blockers

    projection = build_pilot_composition_projection(pilot)
    finalization = build_matthew16_viewpoint_finalization_bundle(
        proposal=proposal,
        pilot=pilot,
        projection=projection,
        decided_at="2026-08-23T01:00:00Z",
    )

    assert finalization.apply_allowed is True
    assert finalization.master_data_mutation_count == 12
    assert finalization.atomic_resolution_ledger.statistics.model_dump() == {
        "input_unit_count": 3,
        "member_count": 2,
        "adjacent_non_member_count": 1,
        "unresolved_count": 0,
    }
    assert [row.disposition for row in finalization.atomic_resolution_ledger.rows].count(
        "member"
    ) == 2
    assert len(finalization.atomic_quality_report.checks) == 7
    assert finalization.atomic_quality_report.eligibility_decision == "pass"
    assert finalization.automated_promotion_decision.human_approval is False
    assert finalization.automated_promotion_decision.decision == "approve"
    assert all(item.effective_state == "active" for item in finalization.proposition_units)
    assert all(
        item.effective_state == "active" for item in finalization.proposition_unit_links
    )
    assert "viewpoint_claim_links" not in finalization.knowledge_package

    incomplete_report = finalization.atomic_quality_report.model_dump(mode="json")
    incomplete_report["checks"] = incomplete_report["checks"][:-1]
    incomplete_report["artifact_sha256"] = sha256_json(
        {key: value for key, value in incomplete_report.items() if key != "artifact_sha256"}
    )
    with pytest.raises(ValueError, match="every required check"):
        type(finalization.atomic_quality_report).model_validate(incomplete_report)

    records = {
        "source_documents": [
            KnowledgeSourceDocument(
                source_id="SRC-1",
                source_type="sermon_transcript",
                source_sha256="s" * 64,
            )
        ],
        "source_fragments": [
            SourceFragmentRecord(
                fragment_id="FR-1",
                source_id="SRC-1",
                verbatim_excerpt="原文",
                source_sha256="s" * 64,
                anchor_state="source_version_bound",
            )
        ],
        "claims": [
            ClaimRecord(
                claim_id="CL-1",
                statement="甲乙丙",
                claim_type="interpretive_judgment",
                evidence_step_ids=["EV-1"],
            )
        ],
        "evidence_steps": [
            EvidenceStepRecord(
                evidence_step_id="EV-1",
                statement="证据",
                source_fragment_id="FR-1",
                support_eligibility="eligible_candidate",
            )
        ],
        "canonical_viewpoints": [finalization.canonical_viewpoint],
        "viewpoint_revisions": [finalization.viewpoint_revision],
        "viewpoint_proposition_units": finalization.proposition_units,
        "viewpoint_proposition_unit_links": finalization.proposition_unit_links,
        "viewpoint_atomic_coverage_snapshots": [
            finalization.atomic_coverage_snapshot
        ],
        "viewpoint_atomic_resolution_ledgers": [
            finalization.atomic_resolution_ledger
        ],
        "viewpoint_atomic_quality_reports": [finalization.atomic_quality_report],
        "viewpoint_automated_promotion_decisions": [
            finalization.automated_promotion_decision
        ],
    }
    active_projection = ViewpointRuntimeCompiler(records).compile_projection(
        consumer_kind="composition_plan",
        coverage_snapshot_id=(
            finalization.atomic_coverage_snapshot.atomic_coverage_snapshot_id
        ),
        viewpoint_ids=[finalization.canonical_viewpoint.viewpoint_id],
    )

    assert active_projection.eligibility == "composition"
    assert active_projection.blocker_codes == ["evidence_not_public"]
    assert len(active_projection.viewpoints[0]["member_proposition_units"]) == 2
    assert len(active_projection.expanded_claims) == 1
    assert len(active_projection.expanded_evidence) == 1
    dependencies = build_projection_dependencies(
        active_projection, consumer_id="DRAFT-SYNTHETIC"
    )
    assert len(dependencies) == 1
    assert dependencies[0].quality_report_id == (
        finalization.atomic_quality_report.atomic_quality_report_id
    )
