import json
from pathlib import Path

from backend.api.sc_api.sermon_meta import SermonMetaManager
from backend.pipeline.sermon_catalog import build_catalog, classify_sermon, write_catalog


def _survey(transcript_id: str, clusters: list[dict]) -> dict:
    return {
        "source": {
            "transcript_id": transcript_id,
            "publication_status": "published",
            "sha256": "source-hash",
        },
        "content_clusters": clusters,
        "candidate_claims": [],
    }


def _cluster(function: str, indexes: list[str], refs: list[str], title: str = "") -> dict:
    return {
        "function": function,
        "segment_indexes": indexes,
        "scripture_refs": refs,
        "title": title,
        "summary": title,
        "topic_terms": [],
    }


def test_content_classifier_distinguishes_passage_flow_from_cross_scripture_topic():
    scripture_led = _survey(
        "passage",
        [
            _cluster("exegesis", [f"S{i}" for i in range(1, 8)], ["Matthew 17:1-8"]),
            _cluster("theology", ["S8"], ["Matthew 17:5"]),
        ],
    )
    topic_led = _survey(
        "topic",
        [
            _cluster("exegesis", ["S1", "S2"], ["Galatians 3:19", "Hebrews 7:11"]),
            _cluster("theology", ["S3", "S4", "S5"], ["Jeremiah 31:31", "2 Corinthians 3"]),
            _cluster("background", ["S6", "S7"], []),
        ],
    )

    assert classify_sermon(scripture_led)[0] == "scripture_led"
    assert classify_sermon(topic_led)[0] == "topic_led"


def test_exegesis_series_title_is_only_a_supporting_signal():
    survey = _survey(
        "mixed",
        [
            _cluster("exegesis", ["S1", "S2", "S3"], ["Matthew 17:1-8"]),
            _cluster("theology", ["S4", "S5", "S6"], ["Romans 4", "Genesis 15"]),
            _cluster("method", ["S7", "S8", "S9"], ["Revelation 20"]),
        ],
    )

    mode, _, reason, _ = classify_sermon(
        survey,
        metadata={"title": "馬太福音釋經"},
        series={"series_title": "馬太福音釋經系列"},
    )

    assert mode == "mixed"
    assert "釋經系列" in reason


def test_series_membership_does_not_override_cross_scripture_topic_structure():
    survey = _survey(
        "covenant-topic",
        [
            _cluster("exegesis", ["S1", "S2"], ["Galatians 3", "Hebrews 7"]),
            _cluster("exegesis", ["S3", "S4"], ["Jeremiah 31", "2 Corinthians 3"]),
            _cluster("theology", ["S5", "S6"], ["Romans 3", "Genesis 15"]),
        ],
    )

    mode, _, _, _ = classify_sermon(
        survey,
        metadata={"title": "從摩西律法到基督律法的恩典之約"},
        series={"series_title": "羅馬書釋經"},
    )

    assert mode == "topic_led"


def test_matching_scripture_series_and_core_book_preserve_mixed_classification():
    survey = _survey(
        "matthew-transition",
        [
            _cluster("exegesis", ["S1", "S2", "S3"], ["Matthew 16:28", "Matthew 17:1"]),
            _cluster("exegesis", ["S4", "S5"], ["Daniel 7", "Revelation 21"]),
            _cluster("method", ["S6", "S7"], ["John 1", "Romans 3"]),
        ],
    )

    mode, _, _, _ = classify_sermon(
        survey,
        metadata={
            "title": "人子：耶穌神性權柄的宣告",
            "core_bible_verse": [{"book": "馬太福音", "chapter_verse": "16:28"}],
        },
        series={"series_title": "馬太福音釋經"},
    )

    assert mode == "mixed"


def test_catalog_is_written_separately_from_manual_sermon_metadata(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    survey = _survey(
        "sermon-1",
        [_cluster("exegesis", ["S1", "S2"], ["Matthew 17:1-8"], "登山變像")],
    )
    (survey_dir / "sermon-1.first-pass.json").write_text(
        json.dumps(survey, ensure_ascii=False), encoding="utf-8"
    )
    metadata_path = tmp_path / "sermon.json"
    metadata_path.write_text(
        json.dumps(
            [
                {
                    "item": "sermon-1",
                    "title": "人工標題",
                    "deliver_date": "2020-01-02",
                    "core_bible_verse": [{"book": "馬太福音", "chapter_verse": "17:1-8"}],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    series_path = tmp_path / "series.json"
    series_path.write_text(
        json.dumps([{"id": "series-1", "title": "系列一", "sermons": ["sermon-1"]}], ensure_ascii=False),
        encoding="utf-8",
    )
    before = metadata_path.read_text(encoding="utf-8")

    payload = build_catalog(
        survey_dir,
        metadata_path=metadata_path,
        series_path=series_path,
    )
    output_path = write_catalog(payload, tmp_path / "sermon_catalog.json")

    assert metadata_path.read_text(encoding="utf-8") == before
    assert output_path.is_file()
    record = payload["records"][0]
    assert record["title"] == "人工標題"
    assert record["series_id"] == "series-1"
    assert record["series_order"] == 1
    assert record["primary_scriptures"][0] == "馬太福音 17:1–8"
    assert record["catalog_primary_passage"] == {
        "osis": "Matt.17.1-Matt.17.8",
        "book_osis": "Matt",
        "book": "馬太福音",
        "chapter": 17,
        "verse_start": 1,
        "chapter_end": None,
        "verse_end": 8,
        "display": "馬太福音 17:1–8",
    }
    assert record["scripture_catalog_eligible"] is True
    assert payload["summary"]["matched_website_count"] == 1


def test_reviewed_catalog_override_separates_primary_and_substantial_passages(tmp_path: Path):
    survey_dir = tmp_path / "survey"
    survey_dir.mkdir()
    survey = _survey(
        "sermon-1",
        [
            _cluster("exegesis", ["S1", "S2", "S3"], ["Matthew 5"]),
            _cluster("exegesis", ["S4", "S5"], ["Matthew 19"]),
        ],
    )
    (survey_dir / "sermon-1.first-pass.json").write_text(json.dumps(survey), encoding="utf-8")
    metadata_path = tmp_path / "sermon.json"
    metadata_path.write_text(json.dumps([{"item": "sermon-1", "title": "解經與生命"}]), encoding="utf-8")
    series_path = tmp_path / "series.json"
    series_path.write_text("[]", encoding="utf-8")
    overrides_path = tmp_path / "overrides.json"
    overrides_path.write_text(
        json.dumps(
            {
                "sermons": {
                    "sermon-1": {
                        "catalog_primary_passage": "Matthew 19",
                        "substantial_passages": ["Matthew 5"],
                        "reason": "系列按馬太福音十九章推進，第五章為重點展開。",
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    record = build_catalog(
        survey_dir,
        metadata_path=metadata_path,
        series_path=series_path,
        overrides_path=overrides_path,
    )["records"][0]

    assert record["catalog_primary_passage"]["osis"] == "Matt.19"
    assert record["substantial_passages"][0]["osis"] == "Matt.5"
    assert record["primary_scriptures"][:2] == ["馬太福音 19", "馬太福音 5"]
    assert record["catalog_assignment"] == "reviewed_override"


def test_sermon_api_merges_catalog_from_data_base_root(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sermon.json").write_text(
        json.dumps(
            [
                {
                    "item": "sermon-1",
                    "title": "人工標題",
                    "deliver_date": "2020-01-02",
                    "core_bible_verse": [],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "sermon_catalog.json").write_text(
        json.dumps(
            {
                "records": [
                    {
                        "transcript_id": "sermon-1",
                        "organization_mode": "topic_led",
                        "organization_mode_label": "專題講論",
                        "classification_confidence": "high",
                        "classification_reason": "由問題帶領",
                        "primary_scriptures": ["羅馬書 3:21–31"],
                        "books": ["羅馬書"],
                        "topics": ["約與律法"],
                        "series_id": "series-1",
                        "series_title": "系列一",
                        "series_order": 2,
                        "year": 2020,
                        "catalog_primary_passage": {
                            "osis": "Rom.3.21-Rom.3.31",
                            "book_osis": "Rom",
                            "book": "羅馬書",
                            "chapter": 3,
                            "verse_start": 21,
                            "chapter_end": 3,
                            "verse_end": 31,
                            "display": "羅馬書 3:21–31",
                        },
                        "scripture_catalog_eligible": True,
                        "scripture_catalog_reason": "屬於明確標示的釋經或查經系列",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    manager = SermonMetaManager(tmp_path, lambda _user_id: {"name": ""})
    sermon = manager.get_sermon_metadata("", "sermon-1")

    assert sermon.organization_mode == "topic_led"
    assert sermon.scripture == ["羅馬書 3:21–31"]
    assert sermon.topic == ["約與律法"]
    assert sermon.series_title == "系列一"
    assert sermon.catalog_year == 2020
    assert sermon.catalog_primary_passage["osis"] == "Rom.3.21-Rom.3.31"
    assert sermon.scripture_catalog_eligible is True
