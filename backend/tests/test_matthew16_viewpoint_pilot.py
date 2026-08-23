from __future__ import annotations

import hashlib
import json

from backend.api.canonical_repository.knowledge_models import ClaimRecord
from backend.api.canonical_repository.matthew16_viewpoint_pilot import (
    build_matthew16_pilot_scope,
)
from backend.api.canonical_repository.viewpoint_foundation import (
    semantic_record_sha,
    sha256_json,
)


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
