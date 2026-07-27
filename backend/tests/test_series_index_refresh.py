from types import SimpleNamespace

from backend.api import series_index_refresh as refresh
from backend.api.sermon_search import discovery
from backend.api.sermon_search.models import DiscoveredManuscript
from backend.pipeline.topic_index.extractor import extract_topics_from_chunk


def _reset_refresh_state() -> None:
    refresh._statuses.clear()
    refresh._active_series_id = None


def test_series_index_refresh_updates_topic_and_search_indexes(monkeypatch):
    _reset_refresh_state()
    calls = {}

    def fake_topic_pipeline(**kwargs):
        calls["topic"] = kwargs
        return SimpleNamespace(topics=["one", "two", "three"])

    class FakeSearchService:
        def status(self):
            return SimpleNamespace(embedding_enabled=True)

        def reindex(self, request):
            calls["search"] = request
            return SimpleNamespace(
                status="completed",
                documents_indexed=21,
                source_units_indexed=800,
            )

    monkeypatch.setattr(refresh, "run_topic_index_pipeline", fake_topic_pipeline)
    monkeypatch.setattr(refresh, "sermon_search_service", FakeSearchService())

    queued, accepted = refresh.queue_series_index_refresh("series-1")
    assert accepted is True
    assert queued.status == "queued"

    refresh.run_series_index_refresh("series-1")
    result = refresh.get_series_index_refresh_status("series-1")

    assert result.status == "completed"
    assert result.topic_count == 3
    assert result.documents_indexed == 21
    assert result.source_units_indexed == 800
    assert calls["topic"]["series_ids"] == ["series-1"]
    assert calls["topic"]["project_types"] == ["sermon_note", "transcript"]
    assert calls["search"].project_types == ["sermon_note", "transcript"]
    assert calls["search"].include_embeddings is True


def test_only_one_global_index_refresh_can_run_at_a_time():
    _reset_refresh_state()
    first, first_accepted = refresh.queue_series_index_refresh("series-1")
    active, second_accepted = refresh.queue_series_index_refresh("series-2")

    assert first_accepted is True
    assert second_accepted is False
    assert active.series_id == "series-1"


def test_series_index_refresh_reports_failure(monkeypatch):
    _reset_refresh_state()

    def fail_topic_pipeline(**kwargs):
        raise RuntimeError("topic extraction failed")

    monkeypatch.setattr(refresh, "run_topic_index_pipeline", fail_topic_pipeline)
    refresh.queue_series_index_refresh("series-1")
    refresh.run_series_index_refresh("series-1")

    result = refresh.get_series_index_refresh_status("series-1")
    assert result.status == "failed"
    assert result.message == "topic extraction failed"
    assert result.finished_at is not None


def test_discovery_filters_mixed_series_by_project_type(monkeypatch, tmp_path):
    projects = {
        "notes": SimpleNamespace(
            title="Notes manuscript",
            project_type="sermon_note",
            bible_verse=None,
            google_doc_id=None,
        ),
        "transcript": SimpleNamespace(
            title="Transcript manuscript",
            project_type="transcript",
            bible_verse=None,
            google_doc_id=None,
        ),
    }
    paths = {}
    for project_id in projects:
        path = tmp_path / f"{project_id}.md"
        path.write_text(f"# {project_id}", encoding="utf-8")
        paths[project_id] = path

    mixed_series = SimpleNamespace(
        id="series-1",
        title="Matthew",
        description=None,
        project_type="sermon_note",
        lectures=[
            SimpleNamespace(
                id="lecture-1",
                title="Lecture",
                description=None,
                project_ids=["notes", "transcript"],
            )
        ],
    )
    monkeypatch.setattr(discovery, "list_series", lambda: [mixed_series])
    monkeypatch.setattr(discovery, "get_sermon_project_metadata", projects.get)
    monkeypatch.setattr(discovery, "get_sermon_final_path", paths.get)

    manuscripts = discovery.discover_manuscripts(project_types=["transcript"])

    assert [item.project_id for item in manuscripts] == ["transcript"]


def test_transcript_passage_topics_are_derived_from_content_without_metadata(tmp_path):
    class FakeLLM:
        def generate_json(self, **kwargs):
            assert "只根據本逐字稿正文判定" in kwargs["user_prompt"]
            return {
                "topics": [
                    {
                        "name": "太 17:1–8：登山變像",
                        "type": "passage",
                        "size": "medium",
                        "source_sections": ["登山變像"],
                        "lun_dian": ["父神從雲中為愛子作見證。"],
                        "notes": None,
                    }
                ]
            }

    manuscript_path = tmp_path / "final.md"
    manuscript_path.write_text("## 登山變像\n\n馬太福音17:1–8記載登山變像。", encoding="utf-8")
    manuscript = DiscoveredManuscript(
        series_id="series-1",
        series_title="Matthew",
        lecture_id="lecture-1",
        lecture_title="Lecture",
        project_id="transcript-1",
        project_title="An editorial title with no chapter number",
        project_type="transcript",
        bible_verse="",
        manuscript_path=manuscript_path,
        content_hash="hash",
        modified_time=1,
    )

    topics = extract_topics_from_chunk(
        FakeLLM(),
        manuscript,
        ["登山變像"],
        manuscript_path.read_text(encoding="utf-8"),
    )

    assert [topic.name for topic in topics] == ["太 17:1–8：登山變像"]
