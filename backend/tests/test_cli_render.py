import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.sermon_to_video import cli


class RenderCliTests(unittest.TestCase):
    def test_render_bypasses_phase_3_and_assembles_all_scenes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = Path(tmp_dir)
            storyboard_path = project_dir / "storyboard.json"
            storyboard_path.write_text(
                json.dumps(
                    {
                        "metadata": {"mode": "Short Sermon"},
                        "scenes": [
                            {"scene_id": 1, "duration_sec": 1.2, "voiceover_text": "One"},
                            {"scene_id": 2, "duration_sec": 1.4, "voiceover_text": "Two"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            assemble_calls: list[int] = []

            def fake_audio(storyboard_data, work_dir, project_dir=None):
                return storyboard_data, {"cue_points": {}, "scene_offsets": {"1": 0.0, "2": 1.2}}

            def fake_assemble(item, output_path, font_path=None, motion_data=None):
                assemble_calls.append(item["scene_id"])
                Path(output_path).write_text(f"scene {item['scene_id']}", encoding="utf-8")
                return output_path

            with patch.object(cli, "process_audio_for_scenes", side_effect=fake_audio), patch.object(
                cli, "process_visuals_for_scenes"
            ) as process_visuals_mock, patch.object(cli, "assemble_scene", side_effect=fake_assemble), patch(
                "backend.sermon_to_video.core.concat.concatenate_and_cleanup"
            ) as concat_mock, patch(
                "backend.sermon_to_video.core.subtitle.generate_srt"
            ) as generate_srt_mock:
                cli.render(
                    project_dir=project_dir,
                    output_file=None,
                    font_path=None,
                    start_phase=2,
                    no_ai=True,
                    scene_id=None,
                    use_cache=False,
                    phase4_workers=1,
                )

            process_visuals_mock.assert_not_called()
            self.assertEqual(assemble_calls, [1, 2])
            concat_mock.assert_called_once()
            generate_srt_mock.assert_called_once()

    def test_render_phase4_can_use_multiple_workers(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = Path(tmp_dir)
            storyboard_path = project_dir / "storyboard.json"
            storyboard_path.write_text(
                json.dumps(
                    {
                        "metadata": {"mode": "Short Sermon"},
                        "scenes": [
                            {"scene_id": 1, "duration_sec": 1.0, "voiceover_text": "One"},
                            {"scene_id": 2, "duration_sec": 1.0, "voiceover_text": "Two"},
                            {"scene_id": 3, "duration_sec": 1.0, "voiceover_text": "Three"},
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            thread_ids = set()

            def fake_audio(storyboard_data, work_dir, project_dir=None):
                return storyboard_data, {"cue_points": {}, "scene_offsets": {"1": 0.0, "2": 1.0, "3": 2.0}}

            def fake_assemble(item, output_path, font_path=None, motion_data=None):
                thread_ids.add(threading.get_ident())
                time.sleep(0.05)
                Path(output_path).write_text(f"scene {item['scene_id']}", encoding="utf-8")
                return output_path

            with patch.object(cli, "process_audio_for_scenes", side_effect=fake_audio), patch.object(
                cli, "process_visuals_for_scenes"
            ), patch.object(cli, "assemble_scene", side_effect=fake_assemble), patch(
                "backend.sermon_to_video.core.concat.concatenate_and_cleanup"
            ), patch(
                "backend.sermon_to_video.core.subtitle.generate_srt"
            ):
                cli.render(
                    project_dir=project_dir,
                    output_file=None,
                    font_path=None,
                    start_phase=2,
                    no_ai=True,
                    scene_id=None,
                    use_cache=False,
                    phase4_workers=2,
                )

            self.assertGreater(len(thread_ids), 1)


if __name__ == "__main__":
    unittest.main()
