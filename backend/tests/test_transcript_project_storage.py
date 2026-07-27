import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.api import sermon_converter_service as service
from backend.api import lecture_manager


class TranscriptProjectStorageTests(unittest.TestCase):
    def test_transcript_project_files_live_in_dedicated_root(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = Path(tmp_dir)
            notes_root = data_root / "notes_to_surmon"
            transcript_root = data_root / "transcripts_to_manuscript"

            with (
                patch.object(service, "NOTES_TO_SERMON_DIR", notes_root),
                patch.object(service, "TRANSCRIPTS_TO_MANUSCRIPT_DIR", transcript_root),
            ):
                project = service.create_sermon_project(
                    title="Matthew 17 transcript",
                    pages=[],
                    project_type="transcript",
                )

            actual_project_dir = transcript_root / project.id
            compatibility_path = notes_root / project.id

            self.assertTrue(actual_project_dir.is_dir())
            self.assertTrue(compatibility_path.is_symlink())
            self.assertEqual(compatibility_path.resolve(), actual_project_dir.resolve())
            self.assertTrue((actual_project_dir / "unified_source.md").is_file())

            metadata = json.loads(
                (actual_project_dir / "meta.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["project_type"], "transcript")
            self.assertEqual(metadata["storage_root"], "transcripts_to_manuscript")

    def test_notes_project_stays_in_existing_root(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = Path(tmp_dir)
            notes_root = data_root / "notes_to_surmon"
            transcript_root = data_root / "transcripts_to_manuscript"

            with (
                patch.object(service, "NOTES_TO_SERMON_DIR", notes_root),
                patch.object(service, "TRANSCRIPTS_TO_MANUSCRIPT_DIR", transcript_root),
            ):
                project = service.create_sermon_project(
                    title="Matthew notes",
                    pages=[],
                    project_type="sermon_note",
                )

            notes_project_dir = notes_root / project.id
            self.assertTrue(notes_project_dir.is_dir())
            self.assertFalse(notes_project_dir.is_symlink())
            self.assertFalse((transcript_root / project.id).exists())

    def test_transcript_project_can_join_existing_manuscript_series(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            data_root = Path(tmp_dir)
            notes_root = data_root / "notes_to_surmon"
            transcript_root = data_root / "transcripts_to_manuscript"
            series_db = notes_root / "series_db.json"

            with (
                patch.object(service, "NOTES_TO_SERMON_DIR", notes_root),
                patch.object(service, "TRANSCRIPTS_TO_MANUSCRIPT_DIR", transcript_root),
                patch.object(lecture_manager, "SERIES_DB_PATH", series_db),
            ):
                series = lecture_manager.create_series(
                    "Matthew",
                    project_type="sermon_note",
                )
                lecture = lecture_manager.add_lecture(series.id, "Matthew 17")
                self.assertIsNotNone(lecture)

                project = service.create_sermon_project(
                    title="Matthew 17 transcript",
                    pages=[],
                    series_id=series.id,
                    lecture_id=lecture.id,
                    project_type="transcript",
                )

                reloaded = lecture_manager.get_series(series.id)

            self.assertIsNotNone(reloaded)
            self.assertIn(project.id, reloaded.lectures[0].project_ids)


if __name__ == "__main__":
    unittest.main()
