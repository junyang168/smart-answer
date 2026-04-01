import tempfile
import unittest
from pathlib import Path

from backend.sermon_to_video.core.runtime_paths import (
    BUILD_DIR_NAME,
    can_reuse_cache,
    resolve_render_paths,
    resolve_scene_output_for_concat,
)


class RuntimePathTests(unittest.TestCase):
    def test_resolve_render_paths_defaults_to_project_final_video_and_build_dir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            project_dir = Path(tmp_dir)
            storyboard_path, build_dir, output_path = resolve_render_paths(project_dir)

        resolved_project = project_dir.resolve()
        self.assertEqual(storyboard_path, resolved_project / "storyboard.json")
        self.assertEqual(build_dir, resolved_project / BUILD_DIR_NAME)
        self.assertEqual(output_path, resolved_project / "final_video.mp4")

    def test_can_reuse_cache_requires_all_outputs(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir)
            first = base_dir / "a.txt"
            second = base_dir / "b.txt"
            first.write_text("a", encoding="utf-8")

            self.assertFalse(can_reuse_cache(True, first, second))
            second.write_text("b", encoding="utf-8")
            self.assertTrue(can_reuse_cache(True, first, second))
            self.assertFalse(can_reuse_cache(False, first, second))

    def test_resolve_scene_output_for_concat_rejects_stale_scene_after_no_cache_phase4(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            stale_scene = work_dir / "scene_3_final.mp4"
            stale_scene.write_text("old", encoding="utf-8")
            item = {"scene_id": 3}

            with self.assertRaisesRegex(RuntimeError, "Refusing to reuse stale build output"):
                resolve_scene_output_for_concat(
                    item,
                    work_dir,
                    use_cache=False,
                    phase4_ran=True,
                )

    def test_resolve_scene_output_for_concat_allows_prereq_scene_when_only_one_scene_was_selected(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            cached_scene = work_dir / "scene_3_final.mp4"
            cached_scene.write_text("old", encoding="utf-8")
            item = {"scene_id": 3}

            resolved = resolve_scene_output_for_concat(
                item,
                work_dir,
                use_cache=False,
                phase4_ran=True,
                selected_scene_id=9,
            )

            self.assertEqual(resolved, str(cached_scene))
            self.assertEqual(item["final_scene_filepath"], str(cached_scene))

    def test_resolve_scene_output_for_concat_allows_existing_scene_when_phase4_was_skipped(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            cached_scene = work_dir / "scene_5_final.mp4"
            cached_scene.write_text("rendered", encoding="utf-8")
            item = {"scene_id": 5}

            resolved = resolve_scene_output_for_concat(
                item,
                work_dir,
                use_cache=False,
                phase4_ran=False,
            )

            self.assertEqual(resolved, str(cached_scene))
            self.assertEqual(item["final_scene_filepath"], str(cached_scene))


if __name__ == "__main__":
    unittest.main()
