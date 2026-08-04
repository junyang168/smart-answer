from __future__ import annotations

import json

import pytest

from backend.api import sermon_converter_service as sermon_service
from backend.api.canonical_repository.models import (
    BibleReference,
    CanonicalUnit,
    ManuscriptLocator,
    TopicAssignment,
)
from backend.api.canonical_repository.service import CanonicalRepositoryService


@pytest.fixture
def repository_workspace(tmp_path, monkeypatch):
    notes = tmp_path / "notes_to_surmon"
    manuscripts = tmp_path / "transcripts_to_manuscript"
    published = tmp_path / "script_published"
    reviewed = tmp_path / "script_review"
    raw = tmp_path / "script_patched"
    for folder in (notes, manuscripts, published, reviewed, raw, notes / "raw_ocr"):
        folder.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(sermon_service, "NOTES_TO_SERMON_DIR", notes)
    monkeypatch.setattr(sermon_service, "TRANSCRIPTS_TO_MANUSCRIPT_DIR", manuscripts)
    monkeypatch.setattr(
        sermon_service,
        "SERMON_TRANSCRIPT_DIRS",
        (("published", published), ("reviewed", reviewed), ("raw", raw)),
    )
    return {
        "service": CanonicalRepositoryService(tmp_path / "canonical_repository"),
        "notes": notes,
        "manuscripts": manuscripts,
        "published": published,
    }


def _write_transcript_project(workspace):
    transcript_id = "lecture-four"
    paragraphs = [
        {"index": "subtitle-1", "text": "## 登山變像", "type": "subtitle"},
        {
            "index": 31,
            "text": "耶穌帶著三個門徒上山，在他們面前變了形像。",
            "type": "content",
            "start_time": 99,
            "end_time": 130,
            "start_index": 31,
            "end_index": 48,
        },
        {
            "index": 49,
            "text": "有雲彩遮蓋他們；雲的出現顯示神的臨在。",
            "type": "content",
            "start_time": 130,
            "end_time": 170,
            "start_index": 49,
            "end_index": 72,
        },
    ]
    (workspace["published"] / f"{transcript_id}.json").write_text(
        json.dumps({"metadata": {"title": "第四講"}, "script": paragraphs}, ensure_ascii=False),
        encoding="utf-8",
    )
    project_id = "matthew-17"
    project = workspace["manuscripts"] / project_id
    project.mkdir()
    (project / "meta.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "title": "馬太福音十七章",
                "project_type": "transcript",
                "sermon_transcript_id": transcript_id,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (project / "unified_source.md").write_text(
        "## 登山變像\n\n耶穌帶著三個門徒上山，在他們面前變了形像。\n\n有雲彩遮蓋他們；雲的出現顯示神的臨在。\n",
        encoding="utf-8",
    )
    (project / "final.md").write_text("## 一、登山變像\n\n整理後的文稿。\n", encoding="utf-8")
    return project_id, transcript_id


def test_transcript_source_map_and_exact_citation_resolution(repository_workspace):
    service = repository_workspace["service"]
    project_id, _ = _write_transcript_project(repository_workspace)

    registered = service.register_project_source(project_id)
    assert registered["mapped_count"] == 3
    assert registered["missing"] == []
    assert registered["source"]["title"] == "第四講"
    source_id = registered["source"]["source_id"]
    assert registered["source"]["media"]["kind"] == "video"
    assert registered["source"]["media"]["url"].endswith("lecture-four.mp4")
    source_map = service.store.get_source_map(source_id)
    timed = next(item for item in source_map.entries if item["paragraph_key"] == "49")
    assert timed["start_time"] == 130
    assert timed["end_time"] == 170

    citation = service.create_citation_from_source_range(
        source_id,
        timed["source_line_start"],
        timed["source_line_end"],
        highlight_text="雲的出現顯示神的臨在",
        evidence_ids=["E-CLOUD"],
    )
    resolution = service.resolve_citation(citation.citation_id)
    assert resolution.state == "valid"
    assert resolution.locator.start_time == 130
    assert "citation=" in resolution.deep_link_url
    assert "t=130" in resolution.deep_link_url


def test_changed_transcript_makes_citation_stale(repository_workspace):
    service = repository_workspace["service"]
    project_id, transcript_id = _write_transcript_project(repository_workspace)
    registered = service.register_project_source(project_id)
    source_id = registered["source"]["source_id"]
    source_map = service.store.get_source_map(source_id)
    entry = next(item for item in source_map.entries if item["paragraph_key"] == "31")
    citation = service.create_citation_from_source_range(source_id, entry["source_line_start"], entry["source_line_end"])

    path = repository_workspace["published"] / f"{transcript_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["script"][1]["text"] += "（已修改）"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    resolution = service.resolve_citation(citation.citation_id)
    assert resolution.state == "stale"
    assert "requires review" in (resolution.message or "")


def test_notes_source_map_preserves_page_identity(repository_workspace):
    service = repository_workspace["service"]
    project_id = "notes-matthew-16"
    project = repository_workspace["notes"] / project_id
    project.mkdir()
    page_file = "notes_main/chapter16/22.jpg"
    (project / "meta.json").write_text(
        json.dumps({"id": project_id, "title": "生命", "project_type": "sermon_note", "pages": [page_file]}),
        encoding="utf-8",
    )
    (project / "unified_source.md").write_text(
        f"# 生命\n\n<!-- Page: {page_file} -->\n\n凡要救自己生命的，必喪掉生命。\n",
        encoding="utf-8",
    )
    (repository_workspace["notes"] / "raw_ocr" / "notes_main_chapter16_22.jpg.md").write_text(
        "凡要救自己生命的，必喪掉生命。\n", encoding="utf-8"
    )

    registered = service.register_project_source(project_id)
    source_map = service.store.get_source_map(registered["source"]["source_id"])
    assert source_map.entries[0]["page_file"] == page_file
    assert source_map.entries[0]["page_ocr_sha256"]


def test_compiler_keeps_passage_and_concept_units_in_separate_indexes(repository_workspace):
    service = repository_workspace["service"]
    project_id, _ = _write_transcript_project(repository_workspace)
    registered = service.register_project_source(project_id)
    source_id = registered["source"]["source_id"]
    source_map = service.store.get_source_map(source_id)
    entry = next(item for item in source_map.entries if item["paragraph_key"] == "49")
    citation = service.create_citation_from_source_range(source_id, entry["source_line_start"], entry["source_line_end"])
    citation.status = "approved"
    service.store.save_citation(citation)
    passage_unit = CanonicalUnit(
        unit_id="CU-cloud",
        title="雲彩與神的臨在",
        unit_type="passage",
        status="published",
        primary_bible_refs=[BibleReference(osis="Matt.17.5", display="太 17:5")],
        topic_assignments=[TopicAssignment(topic_ids=["theophany"], path=["神論", "神的臨在"])],
        manuscript=ManuscriptLocator(
            project_id=project_id,
            project_type="transcript",
            heading_title="一、登山變像",
            heading_anchor="一-登山變像",
        ),
        citation_ids=[citation.citation_id],
    )
    concept_unit = CanonicalUnit(
        unit_id="CU-presence",
        title="神臨在的記號",
        unit_type="concept",
        status="published",
        topic_assignments=[TopicAssignment(topic_ids=["theophany"], path=["神論", "神的臨在"])],
        manuscript=ManuscriptLocator(
            project_id=project_id,
            project_type="transcript",
            heading_title="一、登山變像",
            heading_anchor="一-登山變像",
        ),
        citation_ids=[citation.citation_id],
    )
    service.store.save_unit(passage_unit)
    service.store.save_unit(concept_unit)

    manifest = service.compiler.build()
    assert manifest["unit_count"] == 2
    assert service.status().available is True
    bible = service.compiled_index("bible_index.json")
    topic = service.compiled_index("topic_index.json")
    assert bible["references"]["Matt.17.5"][0]["unit_id"] == "CU-cloud"
    assert topic["topics"][0]["units"][0]["unit_id"] == "CU-presence"
    assert all(unit["unit_id"] != "CU-cloud" for card in topic["topics"] for unit in card["units"])


def test_saving_published_unit_refreshes_public_index(repository_workspace):
    service = repository_workspace["service"]
    project_id, _ = _write_transcript_project(repository_workspace)
    registered = service.register_project_source(project_id)
    source_id = registered["source"]["source_id"]
    source_map = service.store.get_source_map(source_id)
    entry = next(item for item in source_map.entries if item["paragraph_key"] == "49")
    citation = service.create_citation_from_source_range(source_id, entry["source_line_start"], entry["source_line_end"])
    citation.status = "approved"
    service.store.save_citation(citation)
    unit = CanonicalUnit(
        unit_id="CU-auto-publish",
        title="雲彩與神的臨在",
        unit_type="concept",
        manuscript=ManuscriptLocator(
            project_id=project_id,
            project_type="transcript",
            heading_title="一、登山變像",
            heading_anchor="一-登山變像",
        ),
        citation_ids=[citation.citation_id],
        topic_assignments=[TopicAssignment(topic_ids=["presence"], path=["神的臨在"])],
    )
    service.store.save_unit(unit)

    unit.status = "published"
    service.save_unit_and_refresh_public_index(unit)

    assert service.status().available is True
    assert service.compiled_index("topic_index.json")["topics"][0]["units"][0]["unit_id"] == "CU-auto-publish"

    unit.status = "reviewed"
    service.save_unit_and_refresh_public_index(unit)

    assert service.compiled_index("topic_index.json")["topics"] == []


def test_repository_record_ids_reject_path_traversal(repository_workspace):
    with pytest.raises(ValueError):
        repository_workspace["service"].store.get_unit("../outside")


def test_seed_import_stays_candidate_and_passages_sort_numerically(repository_workspace, tmp_path):
    service = repository_workspace["service"]
    project_id, _ = _write_transcript_project(repository_workspace)
    seed = {
        "units": [
            {
                "unit_id": "CU-10",
                "title": "第十章",
                "unit_type": "passage",
                "primary_bible_refs": [{"osis": "Matt.10.1", "display": "太 10:1"}],
                "topic_assignments": [],
                "sources": [{"project_id": project_id, "project_type": "transcript", "resolved_source_headings": ["十"], "section_anchors": ["十"]}],
            },
            {
                "unit_id": "CU-5",
                "title": "第五章",
                "unit_type": "passage",
                "primary_bible_refs": [{"osis": "Matt.5.1", "display": "太 5:1"}],
                "topic_assignments": [],
                "sources": [{"project_id": project_id, "project_type": "transcript", "resolved_source_headings": ["五"], "section_anchors": ["五"]}],
            },
        ]
    }
    path = tmp_path / "canonical_units.json"
    path.write_text(json.dumps(seed, ensure_ascii=False), encoding="utf-8")
    result = service.import_seed_catalog(path)
    units = service.list_unit_summaries(unit_type="passage")
    assert result["imported"] == 2
    assert [unit["unit_id"] for unit in units] == ["CU-5", "CU-10"]
    assert all(unit["status"] == "candidate" for unit in units)


def test_merge_preview_then_apply_preserves_lineage(repository_workspace):
    service = repository_workspace["service"]
    project_id, _ = _write_transcript_project(repository_workspace)
    manuscript = ManuscriptLocator(project_id=project_id, project_type="transcript", heading_title="一、登山變像", heading_anchor="一-登山變像")
    target = CanonicalUnit(unit_id="CU-target", title="小信", unit_type="concept", manuscript=manuscript, aliases=["信心小"])
    absorbed = CanonicalUnit(
        unit_id="CU-absorbed",
        title="芥菜種信心",
        unit_type="concept",
        manuscript=manuscript,
        primary_bible_refs=[BibleReference(osis="Matt.17.20", display="太 17:20")],
        topic_assignments=[TopicAssignment(topic_ids=["faith"], path=["信心"])],
        citation_ids=["CIT-existing"],
    )
    service.store.save_unit(target)
    service.store.save_unit(absorbed)
    preview = service.merge_units("CU-target", ["CU-absorbed"], apply=False)
    assert preview["applied"] is False
    assert service.store.get_unit("CU-absorbed").status == "candidate"
    applied = service.merge_units("CU-target", ["CU-absorbed"], apply=True)
    assert applied["applied"] is True
    merged = service.store.get_unit("CU-target")
    assert merged.primary_bible_refs[0].osis == "Matt.17.20"
    assert merged.citation_ids == ["CIT-existing"]
    assert "芥菜種信心" in merged.aliases
    assert service.store.get_unit("CU-absorbed").status == "archived"
    assert service.store.list_relationships()[0].relationship_type == "merged_into"
