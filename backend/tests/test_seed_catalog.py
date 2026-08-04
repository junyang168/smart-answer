from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend.pipeline.seed_catalog.generator import (
    DEFAULT_TAXONOMY_PATH,
    _classify_topic,
    _duplicate_candidates,
    _load_json,
    build_seed_catalog,
)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_seed_catalog_is_review_only_and_traceable(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    project_root = data_root / "notes_to_surmon" / "matthew_1"
    project_root.mkdir(parents=True)
    final_path = project_root / "final.md"
    final_path.write_text(
        "## 一、太 1:1–17 家譜\n\n### 釋經\n\n耶穌是大衛的子孫。\n",
        encoding="utf-8",
    )
    _write_json(
        project_root / "meta.json",
        {"id": "matthew_1", "title": "馬太福音第一章", "project_type": "sermon_note", "bible_verse": "太 1"},
    )
    _write_json(
        data_root / "notes_to_surmon" / "series_db.json",
        [
            {
                "id": "series-1",
                "title": "馬太福音釋經",
                "project_type": "sermon_note",
                "lectures": [
                    {
                        "id": "lecture-1",
                        "title": "耶穌基督",
                        "project_ids": ["matthew_1", "not-published"],
                    }
                ],
            }
        ],
    )
    _write_json(
        data_root / "sermon_search" / "topic_index.json",
        {
            "generated_at": "2026-01-01T00:00:00Z",
            "corpus_size": 1,
            "topics": [
                {
                    "id": "topic_001",
                    "name": "太 1:1–17：家譜與大衛之約",
                    "type": "passage",
                    "size": "medium",
                    "canonical_ref": "Matt.1.1-Matt.1.17",
                    "canonical_ref_raw": "太 1:1–17",
                    "taxonomy_aliases": ["大衛的子孫"],
                    "notes": None,
                    "sources": [
                        {
                            "series_id": "series-1",
                            "project_id": "matthew_1",
                            "project_title": "馬太福音第一章",
                            "lecture_title": "耶穌基督",
                            "source_sections": ["一（釋經）"],
                            "lun_dian": ["耶穌是大衛的子孫，具有作王的正統性。"],
                        }
                    ],
                }
            ],
        },
    )

    before = _sha256(final_path)
    output = tmp_path / "review-output"
    manifest = build_seed_catalog(data_root=data_root, series_id="series-1", output_dir=output)

    assert _sha256(final_path) == before
    assert manifest["published_project_count"] == 1
    assert manifest["canonical_unit_count"] == 1
    assert manifest["safety"]["published_manuscripts_modified"] is False
    assert manifest["safety"]["formal_topic_index_modified"] is False

    units = json.loads((output / "canonical_units.json").read_text(encoding="utf-8"))["units"]
    assert units[0]["unit_id"].startswith("CU-SEED-")
    assert units[0]["primary_bible_refs"][0]["osis"] == "Matt.1.1-Matt.1.17"
    assert units[0]["content_category_suggestions"][0]["category"] == "釋經"
    assert units[0]["sources"][0]["source_sha256"] == before
    assert units[0]["sources"][0]["public_links"][0].endswith("#一-太-1-1-17-家譜")

    bible = json.loads((output / "bible_index.json").read_text(encoding="utf-8"))
    assert bible["books"][0]["book"] == "Matt"
    assert bible["books"][0]["chapters"][0]["chapter"] == 1

    taxonomy = json.loads((output / "topic_taxonomy.json").read_text(encoding="utf-8"))
    davidic = next(
        child
        for parent in taxonomy["topics"]
        for child in parent["children"]
        if child["id"] == "davidic-covenant"
    )
    assert davidic["units"][0]["unit_id"] == units[0]["unit_id"]
    assert (output / "review.md").is_file()


def test_unit_ids_are_stable_across_runs(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    project_root = data_root / "notes_to_surmon" / "p1"
    project_root.mkdir(parents=True)
    (project_root / "final.md").write_text("## 主題\n\n內容", encoding="utf-8")
    _write_json(project_root / "meta.json", {"id": "p1", "title": "P1"})
    _write_json(
        data_root / "notes_to_surmon" / "series_db.json",
        [{"id": "s1", "title": "S", "lectures": [{"id": "l1", "title": "L", "project_ids": ["p1"]}]}],
    )
    _write_json(
        data_root / "sermon_search" / "topic_index.json",
        {
            "topics": [
                {
                    "id": "unstable-sequential-id",
                    "name": "福音書的性質",
                    "type": "concept",
                    "sources": [
                        {
                            "series_id": "s1",
                            "project_id": "p1",
                            "source_sections": ["主題"],
                            "lun_dian": ["福音書報導並解釋事件。"],
                        }
                    ],
                }
            ]
        },
    )

    build_seed_catalog(data_root=data_root, series_id="s1", output_dir=tmp_path / "out-1")
    build_seed_catalog(data_root=data_root, series_id="s1", output_dir=tmp_path / "out-2")
    first = json.loads((tmp_path / "out-1" / "canonical_units.json").read_text())["units"][0]["unit_id"]
    second = json.loads((tmp_path / "out-2" / "canonical_units.json").read_text())["units"][0]["unit_id"]
    assert first == second


def test_duplicate_candidates_detect_overlapping_refs_and_alias_groups() -> None:
    units = [
        {
            "unit_id": "one",
            "title": "太 16:28：人子再臨的保證",
            "unit_type": "passage",
            "aliases": ["人子"],
            "primary_bible_refs": [
                {
                    "osis": "Matt.16.28",
                    "book": "Matt",
                    "chapter_start": 16,
                    "verse_start": 28,
                    "chapter_end": None,
                    "verse_end": None,
                }
            ],
            "sources": [],
        },
        {
            "unit_id": "two",
            "title": "太 16:28–17:2：那個人子的榮耀",
            "unit_type": "passage",
            "aliases": ["那個人子"],
            "primary_bible_refs": [
                {
                    "osis": "Matt.16.28-Matt.17.2",
                    "book": "Matt",
                    "chapter_start": 16,
                    "verse_start": 28,
                    "chapter_end": 17,
                    "verse_end": 2,
                }
            ],
            "sources": [],
        },
    ]
    result = _duplicate_candidates(
        units,
        [{"preferred": "人子", "aliases": ["那個人子", "Son of Man"]}],
    )
    assert len(result["overlapping_reference_pairs"]) == 1
    assert result["alias_group_candidates"][0]["preferred"] == "人子"


def test_small_faith_is_primary_discipleship_topic_with_theological_cross_link() -> None:
    taxonomy = _load_json(DEFAULT_TAXONOMY_PATH)
    passage = {
        "name": "太 8:26：門徒「小信」的意義與信靠主的根基",
        "taxonomy_aliases": ["小信", "ὀλιγόπιστος"],
        "sources": [{"lun_dian": ["小信描述門徒尚未充分認識並信靠耶穌。"]}],
    }
    passage_assignments = _classify_topic(passage, taxonomy)
    assert passage_assignments[0]["path"] == ["教會論與門徒", "門徒的信心、認識與信靠"]
    assert passage_assignments[0]["role"] == "primary"

    summary = {
        "name": "信心與人同神的合宜關係",
        "taxonomy_aliases": ["信心小"],
        "sources": [{"lun_dian": ["信心與神人關係有關。"]}],
    }
    summary_assignments = _classify_topic(summary, taxonomy)
    by_path = {tuple(item["path"]): item for item in summary_assignments}
    assert by_path[("教會論與門徒", "門徒的信心、認識與信靠")]["role"] == "primary"
    assert by_path[("救恩論", "信心、悔改與順服")]["role"] == "secondary"


def test_confirmed_amen_seed_uses_nested_categories_and_leaves_review_queue(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    project_root = data_root / "notes_to_surmon" / "amen-project"
    project_root.mkdir(parents=True)
    (project_root / "final.md").write_text(
        "## 一、句首「Amen」彰顯宣告者的神性權柄\n\n"
        "### 釋經\n\nAmen 位於句首。\n\n"
        "### 神學意義\n\n耶穌的話具有神聖權威。\n",
        encoding="utf-8",
    )
    _write_json(project_root / "meta.json", {"id": "amen-project", "title": "Amen", "project_type": "transcript"})
    _write_json(
        data_root / "notes_to_surmon" / "series_db.json",
        [{"id": "s1", "title": "S", "lectures": [{"id": "l1", "title": "L", "project_ids": ["amen-project"]}]}],
    )
    _write_json(
        data_root / "sermon_search" / "topic_index.json",
        {
            "topics": [
                {
                    "id": "topic_100",
                    "name": "句首「Amen」的宣告公式與耶穌的神性權柄",
                    "type": "concept",
                    "sources": [
                        {
                            "series_id": "s1",
                            "project_id": "amen-project",
                            "source_sections": ["一、句首「Amen」彰顯宣告者的神性權柄"],
                            "lun_dian": ["耶穌親自以神的權柄宣告。"],
                        }
                    ],
                }
            ]
        },
    )

    output = tmp_path / "out"
    build_seed_catalog(data_root=data_root, series_id="s1", output_dir=output)
    unit = json.loads((output / "canonical_units.json").read_text())["units"][0]
    assert unit["status"] == "confirmed_seed"
    assert unit["content_category_suggestions"] == [
        {"category": "釋經", "basis": "human_review", "confidence": "high"},
        {"category": "神學意義", "basis": "human_review", "confidence": "high"},
    ]
    assert unit["topic_assignments"][0]["basis"] == "human_review"
    review = json.loads((output / "review_needed.json").read_text())
    assert review["items"] == []
