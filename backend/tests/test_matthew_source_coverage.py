import json
from pathlib import Path

from backend.pipeline.matthew_source_coverage import (
    build_matthew_source_coverage,
    render_matthew_source_coverage_markdown,
)


def test_coverage_uses_actual_refs_and_keeps_sources_separate(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    survey = {
        "source": {"transcript_id": "sermon-1"},
        "content_clusters": [
            {
                "cluster_id": "CL01",
                "title": "登山變像",
                "summary": "逐段解釋",
                "function": "exegesis",
                "segment_indexes": ["S1"],
                "scripture_refs": ["Matthew 17:1-8"],
            }
        ],
        "candidate_claims": [
            {
                "claim_id": "C1",
                "statement": "山上的事件顯明人子的榮耀。",
                "scripture_refs": ["Matthew 17:1-8"],
                "anchors": [{"segment_index": "S1", "verbatim_excerpt": "原文"}],
            }
        ],
    }
    (survey_dir / "one.first-pass.json").write_text(json.dumps(survey), encoding="utf-8")
    catalog_path = tmp_path / "sermon_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "transcript_id": "sermon-1",
                        "title": "第三講",
                        "series_title": "馬太福音釋經",
                        "source_category": "nysc",
                        "source_category_label": "紐約靈命進深會",
                        "source_organization": "紐約靈命進深會",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = build_matthew_source_coverage(
        survey_dir, catalog_path=catalog_path, notes_root=tmp_path / "notes"
    )
    chapter17 = next(item for item in payload["chapters"] if item["chapter"] == 17)
    assert chapter17["source_count"] == 1
    source = chapter17["sources"][0]
    assert source["source_category"] == "nysc"
    assert source["evidence_summary"]["evidence_level"] == "anchored_candidate_claims"
    assert source["evidence_summary"]["material_role"] == "single_claim_candidate"
    assert source["candidate_claims"][0]["anchors"][0]["verbatim_excerpt"] == "原文"
    assert next(item for item in payload["chapters"] if item["chapter"] == 18)["source_count"] == 0


def test_title_and_series_do_not_create_false_chapter_coverage(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    (survey_dir / "one.first-pass.json").write_text(
        json.dumps(
            {
                "source": {"transcript_id": "named-24-28"},
                "content_clusters": [
                    {"cluster_id": "CL1", "scripture_refs": ["Romans 3:21-31"]}
                ],
                "candidate_claims": [],
            }
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "sermon_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "transcript_id": "named-24-28",
                        "title": "馬太福音 24-28 釋經",
                        "series_title": "馬太福音 24-28 釋經",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    payload = build_matthew_source_coverage(
        survey_dir, catalog_path=catalog_path, notes_root=tmp_path / "notes"
    )
    assert payload["summary"]["distinct_candidate_source_count"] == 0
    assert all(chapter["source_count"] == 0 for chapter in payload["chapters"])


def test_multiple_refs_in_one_claim_do_not_inflate_evidence_count(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    (survey_dir / "one.first-pass.json").write_text(
        json.dumps(
            {
                "source": {"transcript_id": "sermon-1"},
                "content_clusters": [],
                "candidate_claims": [
                    {
                        "claim_id": "C1",
                        "statement": "一條主張引用同章兩段經文。",
                        "scripture_refs": ["Matthew 17:1", "Matthew 17:5"],
                        "anchors": [{"segment_index": "S1", "verbatim_excerpt": "原文"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "sermon_catalog.json"
    catalog_path.write_text(
        json.dumps({"records": [{"transcript_id": "sermon-1"}]}), encoding="utf-8"
    )

    payload = build_matthew_source_coverage(
        survey_dir, catalog_path=catalog_path, notes_root=tmp_path / "notes"
    )
    source = next(item for item in payload["chapters"] if item["chapter"] == 17)[
        "sources"
    ][0]
    assert source["evidence_summary"]["candidate_claim_count"] == 1
    assert len(source["candidate_claims"][0]["matched_references"]) == 2


def test_notes_projects_cover_explicit_chapters_and_keep_unscoped_sources(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    catalog_path = tmp_path / "sermon_catalog.json"
    catalog_path.write_text(json.dumps({"records": []}), encoding="utf-8")
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    series_id = "matthew-series"
    (notes_root / "series_db.json").write_text(
        json.dumps(
            [
                {
                    "id": series_id,
                    "title": "馬太福音釋經",
                    "project_type": "sermon_note",
                    "lectures": [
                        {
                            "id": "lecture-1",
                            "title": "登山寶訓",
                            "project_ids": ["chapter-6-7", "structure"],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    for project_id, title, bible_verse in (
        ("chapter-6-7", "登山寶訓（三）", "太 6,7"),
        ("structure", "登山寶訓的結構", None),
    ):
        project_root = notes_root / project_id
        project_root.mkdir()
        (project_root / "meta.json").write_text(
            json.dumps(
                {"id": project_id, "title": title, "bible_verse": bible_verse}
            ),
            encoding="utf-8",
        )
        (project_root / "final.md").write_text(f"# {title}\n", encoding="utf-8")

    payload = build_matthew_source_coverage(
        survey_dir,
        catalog_path=catalog_path,
        notes_root=notes_root,
        notes_series_id=series_id,
    )

    chapter6 = next(item for item in payload["chapters"] if item["chapter"] == 6)
    chapter7 = next(item for item in payload["chapters"] if item["chapter"] == 7)
    assert [item["project_id"] for item in chapter6["sources"]] == ["chapter-6-7"]
    assert [item["project_id"] for item in chapter7["sources"]] == ["chapter-6-7"]
    assert chapter6["sources"][0]["evidence_summary"]["chapter_assignment_basis"] == (
        "explicit_project_bible_verse"
    )
    assert [item["project_id"] for item in payload["book_level_sources"]] == ["structure"]
    assert payload["summary"]["notes_to_manuscript_project_count"] == 2
    assert payload["summary"]["total_listed_source_count"] == 2
    assert [item["project_id"] for item in payload["source_directory"]] == [
        "chapter-6-7",
        "structure",
    ]
    assert payload["source_directory"][0]["assigned_chapters"] == [6, 7]
    assert payload["source_directory"][1]["scope_status"] == "book_level_or_unscoped"


def test_transcript_project_is_excluded_but_linked_sermon_remains(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    transcript_id = "linked-sermon"
    (survey_dir / "linked.first-pass.json").write_text(
        json.dumps(
            {
                "source": {"transcript_id": transcript_id},
                "content_clusters": [
                    {"cluster_id": "CL1", "scripture_refs": ["Matthew 17:1-8"]}
                ],
                "candidate_claims": [],
            }
        ),
        encoding="utf-8",
    )
    catalog_path = tmp_path / "sermon_catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "records": [
                    {
                        "transcript_id": transcript_id,
                        "catalog_primary_passage": {
                            "book_osis": "Matt",
                            "chapter": 17,
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    series_id = "matthew-series"
    (notes_root / "series_db.json").write_text(
        json.dumps(
            [
                {
                    "id": series_id,
                    "title": "馬太福音釋經",
                    "lectures": [
                        {
                            "id": "lecture-1",
                            "title": "榮耀與信心",
                            "project_ids": ["transcript-project"],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    project_root = notes_root / "transcript-project"
    project_root.mkdir()
    (project_root / "meta.json").write_text(
        json.dumps(
            {
                "id": "transcript-project",
                "title": "登山變像",
                "project_type": "transcript",
                "bible_verse": "Matt.16.28",
                "sermon_transcript_id": transcript_id,
            }
        ),
        encoding="utf-8",
    )

    payload = build_matthew_source_coverage(
        survey_dir,
        catalog_path=catalog_path,
        notes_root=notes_root,
        notes_series_id=series_id,
    )

    chapter17 = next(item for item in payload["chapters"] if item["chapter"] == 17)
    assert [item["source_id"] for item in chapter17["sources"]] == [
        f"sermon:{transcript_id}"
    ]
    assert not any(
        item.get("project_id") == "transcript-project"
        for item in payload["source_directory"]
    )
    assert payload["summary"]["notes_to_manuscript_project_count"] == 0
    notes_meta = payload["source"]["notes_to_manuscript"]
    assert notes_meta["included_project_rule"] == "all_except_transcript"
    assert notes_meta["excluded_project_type"] == "transcript"
    assert notes_meta["excluded_transcript_project_ids"] == ["transcript-project"]


def test_markdown_report_lists_chapters_notes_and_unscoped_sources():
    report = render_matthew_source_coverage_markdown(
        {
            "summary": {
                "total_listed_source_count": 2,
                "notes_to_manuscript_project_count": 1,
                "book_level_or_unscoped_notes_source_count": 1,
            },
            "chapters": [
                {
                    "chapter": 1,
                    "sources": [
                        {
                            "title": "第一章講稿",
                            "source_url": "/notes/one",
                            "source_type": "notes_to_manuscript",
                            "source_type_label": "筆記轉講稿",
                            "series_title": "馬太福音釋經",
                        }
                    ],
                }
            ],
            "book_level_sources": [
                {
                    "title": "全書結構",
                    "source_url": "/notes/structure",
                    "source_type_label": "筆記轉講稿",
                }
            ],
        }
    )
    assert "## 第 1 章" in report
    assert "[第一章講稿](/notes/one)" in report
    assert "## 全書／結構材料（尚未定章）" in report
    assert "[全書結構](/notes/structure)" in report


def test_incomplete_notes_metadata_can_use_clear_manuscript_reference_majority(
    tmp_path: Path,
):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    catalog_path = tmp_path / "sermon_catalog.json"
    catalog_path.write_text(json.dumps({"records": []}), encoding="utf-8")
    notes_root = tmp_path / "notes"
    notes_root.mkdir()
    series_id = "matthew-series"
    (notes_root / "series_db.json").write_text(
        json.dumps(
            [
                {
                    "id": series_id,
                    "title": "馬太福音釋經",
                    "lectures": [
                        {
                            "id": "lecture-1",
                            "title": "課程",
                            "project_ids": ["incomplete-scope"],
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    project_root = notes_root / "incomplete-scope"
    project_root.mkdir()
    (project_root / "meta.json").write_text(
        json.dumps({"title": "不可用標題猜章", "bible_verse": "太"}),
        encoding="utf-8",
    )
    (project_root / "final.md").write_text(
        "太16:1。太16:13。太16:19。太18:18。",
        encoding="utf-8",
    )

    payload = build_matthew_source_coverage(
        survey_dir,
        catalog_path=catalog_path,
        notes_root=notes_root,
        notes_series_id=series_id,
    )

    chapter16 = next(item for item in payload["chapters"] if item["chapter"] == 16)
    source = next(
        item for item in chapter16["sources"] if item.get("project_id") == "incomplete-scope"
    )
    assert source["evidence_summary"]["chapter_assignment_basis"] == (
        "reviewed_manuscript_dominant_matthew_chapter"
    )
    assert source["evidence_summary"]["manuscript_scope_evidence"]["share"] == 0.75
