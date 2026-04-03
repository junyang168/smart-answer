import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from backend.sermon_to_video.core import assembly as assembly_module
from backend.sermon_to_video.core import face_detection
from backend.sermon_to_video.core.assembly import (
    _find_highlight_spans_across_lines,
    _find_highlight_spans,
    _load_exegesis_persistent_defaults,
    _normalize_exegesis_persistent_overlay_cfg,
    _parse_highlight_term_spec,
    _scale_color_brightness,
    _scale_alpha,
    _compute_scaled_text_origin,
    _compute_viewport_origin,
    _crop_viewport_frame,
    _resolve_scene_clip_schedule,
    _resolve_face_anchor_point,
    _resolve_motion_anchor_ratios,
    render_multi_cue_concepts,
    render_exegesis_persistent,
)
from backend.sermon_to_video.core.overlay import (
    create_text_background_overlay,
    resolve_dark_overlay_config,
)
from backend.sermon_to_video.core.visual import ensure_blank_visual
from backend.sermon_to_video.core.visual_track import (
    apply_visual_track_to_scenes,
    infer_overlay_type,
    load_visual_track_document,
    project_overlay_for_scene,
)


NEW_VISUAL_TRACK = {
    "title": "Test Visual Track",
    "mode": "exegesis_teaching",
    "visual_track": [
        {
            "visual_id": 1,
            "time_range": [0.0, 20.0],
            "covered_scenes": [1, 2],
            "purpose": "Visual spans two scenes.",
            "shots": [
                {
                    "shot_id": "V1_S1",
                    "time_range": [0.0, 20.0],
                    "clips": [
                        {
                            "clip_id": "IMG_1",
                            "type": "image",
                            "trigger_scene_cue": "scene_1",
                            "duration": 10.0,
                            "description": "Scene one image",
                            "motion": {"type": "zoom_in", "scale_start": 1.0, "scale_end": 1.03},
                        },
                        {
                            "clip_id": "VID_2",
                            "type": "video",
                            "trigger_scene_cue": "scene_2",
                            "duration": 10.0,
                            "description": "Scene two video",
                        },
                    ],
                }
            ],
            "overlay": {
                "enabled": True,
                "type": "multi_cue_concepts",
                "position": "left-center",
                "items": [
                    {"trigger_cue": "scene_1", "text": "Initial question"},
                    {"trigger_cue": "s2_1", "text": "Mid-scene pivot"},
                ],
                "behavior": {"mode": "replace", "fade_in_sec": 0.3},
            },
        },
        {
            "visual_id": 2,
            "time_range": [20.0, 30.0],
            "covered_scenes": [3],
            "purpose": "Simple verse overlay",
            "shots": [
                {
                    "shot_id": "V2_S1",
                    "time_range": [20.0, 30.0],
                    "clips": [
                        {
                            "clip_id": "IMG_3",
                            "type": "image",
                            "trigger_scene_cue": "scene_3",
                            "duration": 10.0,
                            "description": "Scene three image",
                        }
                    ],
                }
            ],
            "overlay": {
                "enabled": True,
                "kind": "verse",
                "text": "Verse text",
                "reference": "Matt 11:27",
                "trigger_cue": "scene_3",
                "dark_overlay": True,
            },
        },
    ],
}


class VisualTrackRuntimeTests(unittest.TestCase):
    def tearDown(self):
        face_detection._FACE_ANCHOR_CACHE.clear()
        _load_exegesis_persistent_defaults.cache_clear()

    def test_parse_new_visual_track_document(self):
        document = load_visual_track_document(NEW_VISUAL_TRACK)

        self.assertEqual(document.mode, "exegesis_teaching")
        self.assertEqual(len(document.visual_track), 2)
        first_visual = document.visual_track[0]
        self.assertEqual(first_visual.visual_id, "1")
        self.assertEqual(first_visual.covered_scenes, (1, 2))
        self.assertEqual(first_visual.shots[0].shot_id, "V1_S1")
        self.assertEqual(first_visual.shots[0].clips[0].clip_id, "IMG_1")
        self.assertEqual(first_visual.shots[0].clips[1].trigger_scene_cue, "scene_2")

    def test_apply_visual_track_to_scenes_resolves_nested_clips_and_assets(self):
        scenes = [{"scene_id": 1}, {"scene_id": 2}, {"scene_id": 3}]
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            assets_dir = work_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "IMG_1.png").write_bytes(b"png")
            (assets_dir / "VID_2.mp4").write_bytes(b"mp4")
            (assets_dir / "IMG_3.png").write_bytes(b"png")

            apply_visual_track_to_scenes(scenes, NEW_VISUAL_TRACK, work_dir)

        self.assertEqual(scenes[0]["visual_track_metadata"]["clip_id"], "IMG_1")
        self.assertTrue(scenes[0]["visual_filepath"].endswith("IMG_1.png"))
        self.assertEqual(scenes[1]["visual_track_metadata"]["clip_id"], "VID_2")
        self.assertEqual(scenes[1]["visual_source"], "assets/VID_2.mp4")
        self.assertEqual(scenes[2]["visual_track_metadata"]["covered_scenes"], [3])
        self.assertTrue(scenes[2]["visual_filepath"].endswith("IMG_3.png"))

    def test_missing_visual_asset_can_fall_back_to_blank_build_image(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            blank_path = ensure_blank_visual(scene_id=9, work_dir=work_dir)

            self.assertTrue(blank_path.exists())
            self.assertEqual(blank_path.name, "scene_9_visual.jpg")

    def test_apply_visual_track_to_scenes_builds_cue_level_clip_schedule(self):
        payload = {
            "title": "Cue-level schedule",
            "mode": "exegesis_teaching",
            "visual_track": [
                {
                    "visual_id": 1,
                    "covered_scenes": [7],
                    "shots": [
                        {
                            "shot_id": "V7_S1",
                            "clips": [
                                {"clip_id": "scene_7A", "type": "video", "trigger_scene_cue": "scene_7"},
                                {"clip_id": "scene_7B", "type": "video", "trigger_scene_cue": "s7_1"},
                                {"clip_id": "scene_7C", "type": "video", "trigger_scene_cue": "s7_2"},
                            ],
                        }
                    ],
                }
            ],
        }
        scenes = [
            {
                "scene_id": 7,
                "voiceover_text": "Alpha [s7_1] Beta [s7_2] Gamma",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            assets_dir = work_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "scene_7A.mp4").write_bytes(b"a")
            (assets_dir / "scene_7B.mp4").write_bytes(b"b")
            (assets_dir / "scene_7C.mp4").write_bytes(b"c")

            apply_visual_track_to_scenes(scenes, payload, work_dir)

        metadata = scenes[0]["visual_track_metadata"]
        self.assertEqual(metadata["clip_id"], "scene_7A")
        self.assertEqual(
            [entry["clip_id"] for entry in metadata["clip_schedule"]],
            ["scene_7A", "scene_7B", "scene_7C"],
        )
        self.assertEqual(metadata["clip_schedule"][1]["trigger_scene_cue"], "s7_1")
        self.assertEqual(metadata["clip_schedule"][2]["trigger_scene_cue"], "s7_2")

    def test_apply_visual_track_to_scenes_includes_after_previous_followups(self):
        payload = {
            "title": "Relative follow-ups",
            "mode": "exegesis_teaching",
            "visual_track": [
                {
                    "visual_id": 1,
                    "covered_scenes": [7, 8],
                    "shots": [
                        {
                            "shot_id": "V7_S1",
                            "clips": [
                                {"clip_id": "scene_7A", "type": "video", "trigger_scene_cue": "scene_7", "duration": 3.0},
                                {"clip_id": "scene_7B", "type": "video", "trigger_mode": "after_previous", "duration": 2.0},
                                {"clip_id": "scene_7C", "type": "video", "trigger_scene_cue": "s7_2", "duration": 4.0},
                                {"clip_id": "scene_8A", "type": "video", "trigger_scene_cue": "scene_8", "duration": 5.0},
                            ],
                        }
                    ],
                }
            ],
        }
        scenes = [
            {
                "scene_id": 7,
                "voiceover_text": "Alpha [s7_2] Gamma",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            work_dir = Path(tmp_dir)
            assets_dir = work_dir / "assets"
            assets_dir.mkdir()
            for clip_id in ("scene_7A", "scene_7B", "scene_7C", "scene_8A"):
                (assets_dir / f"{clip_id}.mp4").write_bytes(b"x")

            apply_visual_track_to_scenes(scenes, payload, work_dir)

        metadata = scenes[0]["visual_track_metadata"]
        self.assertEqual(
            [entry["clip_id"] for entry in metadata["clip_schedule"]],
            ["scene_7A", "scene_7B", "scene_7C"],
        )
        self.assertEqual(metadata["clip_schedule"][1]["trigger_mode"], "after_previous")

    def test_resolve_scene_clip_schedule_uses_cue_points_for_local_timing(self):
        scene = {
            "scene_id": 7,
            "duration_sec": 12.0,
            "render_duration": 12.5,
            "audio_start_offset": 100.0,
            "storyboard_metadata": {
                "cue_points": {"scene_7": 100.0, "s7_1": 103.5, "s7_2": 108.0}
            },
            "visual_track_metadata": {
                "clip_schedule": [
                    {"clip_id": "scene_7A", "trigger_scene_cue": "scene_7", "asset_ref": "assets/scene_7A.mp4"},
                    {"clip_id": "scene_7B", "trigger_scene_cue": "s7_1", "asset_ref": "assets/scene_7B.mp4"},
                    {"clip_id": "scene_7C", "trigger_scene_cue": "s7_2", "asset_ref": "assets/scene_7C.mp4"},
                ]
            },
        }

        schedule = _resolve_scene_clip_schedule(scene)

        self.assertEqual([entry["clip_id"] for entry in schedule], ["scene_7A", "scene_7B", "scene_7C"])
        self.assertEqual([entry["local_start"] for entry in schedule], [0.0, 3.5, 8.0])
        self.assertEqual([entry["local_duration"] for entry in schedule], [3.5, 4.5, 4.5])

    def test_resolve_scene_clip_schedule_supports_after_previous(self):
        scene = {
            "scene_id": 7,
            "duration_sec": 12.0,
            "render_duration": 12.0,
            "audio_start_offset": 100.0,
            "storyboard_metadata": {
                "cue_points": {"scene_7": 100.0, "s7_2": 108.0}
            },
            "visual_track_metadata": {
                "clip_schedule": [
                    {
                        "clip_id": "scene_7A",
                        "trigger_scene_cue": "scene_7",
                        "clip_duration": 3.0,
                        "asset_ref": "assets/scene_7A.mp4",
                    },
                    {
                        "clip_id": "scene_7B",
                        "trigger_mode": "after_previous",
                        "clip_duration": 2.5,
                        "asset_ref": "assets/scene_7B.mp4",
                    },
                    {
                        "clip_id": "scene_7C",
                        "trigger_scene_cue": "s7_2",
                        "asset_ref": "assets/scene_7C.mp4",
                    },
                ]
            },
        }

        schedule = _resolve_scene_clip_schedule(scene)

        self.assertEqual([entry["clip_id"] for entry in schedule], ["scene_7A", "scene_7B", "scene_7C"])
        self.assertEqual([entry["local_start"] for entry in schedule], [0.0, 3.0, 8.0])
        self.assertEqual([entry["local_duration"] for entry in schedule], [3.0, 2.5, 4.0])

    def test_project_visual_level_overlay_keeps_carryover_and_scene_trigger_timing(self):
        overlay = NEW_VISUAL_TRACK["visual_track"][0]["overlay"]
        cue_map = {"scene_1": 0.0, "scene_2": 10.0, "s2_1": 15.0}

        projected = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=10.0,
            scene_end_abs=20.0,
            scene_duration=10.0,
        )

        self.assertEqual(infer_overlay_type(projected), "multi_cue_concepts")
        self.assertEqual(len(projected["items"]), 2)
        self.assertEqual(projected["items"][0]["text"], "Initial question")
        self.assertEqual(projected["items"][0]["trigger_time"], 0.0)
        self.assertEqual(projected["items"][1]["text"], "Mid-scene pivot")
        self.assertEqual(projected["items"][1]["trigger_time"], 5.0)

    def test_replace_mode_drops_carryover_when_scene_has_immediate_replacement(self):
        overlay = {
            "enabled": True,
            "type": "multi_cue_concepts",
            "items": [
                {"trigger_cue": "scene_1", "text": "#Title"},
                {"trigger_cue": "scene_2", "text": "Replacement"},
            ],
            "behavior": {"mode": "replace"},
        }
        cue_map = {"scene_1": 0.0, "scene_2": 10.0}

        projected = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=10.0,
            scene_end_abs=20.0,
            scene_duration=10.0,
        )

        self.assertEqual([item.get("text") for item in projected["items"]], ["Replacement"])
        self.assertEqual(projected["items"][0]["trigger_time"], 0.0)

    def test_multi_cue_concepts_visible_to_cue_stops_persistence_after_window(self):
        overlay = {
            "enabled": True,
            "type": "multi_cue_concepts",
            "visible_from_cue": "scene_1",
            "visible_to_cue": "scene_2",
            "items": [
                {"trigger_cue": "scene_1", "text": "Prompt A"},
                {"trigger_cue": "scene_2", "text": "Prompt B"},
            ],
            "behavior": {"mode": "replace"},
        }
        cue_map = {"scene_1": 0.0, "scene_2": 10.0, "scene_3": 20.0}

        projected_scene2 = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=10.0,
            scene_end_abs=20.0,
            scene_duration=10.0,
        )
        projected_scene3 = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=20.0,
            scene_end_abs=30.0,
            scene_duration=10.0,
        )

        self.assertEqual([item.get("text") for item in projected_scene2["items"]], ["Prompt B"])
        self.assertIsNone(projected_scene3)

    def test_project_simple_overlay_respects_trigger_cue_window(self):
        overlay = NEW_VISUAL_TRACK["visual_track"][1]["overlay"]
        cue_map = {"scene_1": 0.0, "scene_2": 10.0, "scene_3": 20.0}

        projected_before = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=10.0,
            scene_end_abs=20.0,
            scene_duration=10.0,
        )
        projected_at_start = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=20.0,
            scene_end_abs=30.0,
            scene_duration=10.0,
        )

        self.assertIsNone(projected_before)
        self.assertEqual(infer_overlay_type(projected_at_start), "simple")
        self.assertEqual(projected_at_start["trigger_time"], 0.0)
        self.assertTrue(projected_at_start["dark_overlay"])

    def test_project_exegesis_persistent_visibility_and_highlights(self):
        overlay = {
            "enabled": True,
            "type": "exegesis_persistent",
            "anchor": {"x_ratio": 0.1, "y_ratio": 0.2, "max_width_ratio": 0.52},
            "header": {"enabled": True, "text": "马太福音 11:25"},
            "verse_block": {"text": "父啊，天地的主\n向聪明通达人就藏起来\n向婴孩就显出来。"},
            "visible_from_cue": "scene_3",
            "visible_to_cue": "scene_6",
            "highlights": [
                {"trigger_cue": "scene_4", "ranges": ["聪明通达人"]},
                {"trigger_cue": "s4_1", "ranges": ["父", "向婴孩就显出来"]},
            ],
            "dim_others": {"enabled": True, "opacity": 0.32},
        }
        cue_map = {"scene_3": 20.0, "scene_4": 30.0, "s4_1": 35.0, "scene_6": 50.0}

        projected_scene2 = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=10.0,
            scene_end_abs=20.0,
            scene_duration=10.0,
        )
        projected_scene3 = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=20.0,
            scene_end_abs=30.0,
            scene_duration=10.0,
        )
        projected_scene4 = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=30.0,
            scene_end_abs=40.0,
            scene_duration=10.0,
        )
        projected_scene5 = project_overlay_for_scene(
            overlay,
            cue_map=cue_map,
            scene_start_abs=40.0,
            scene_end_abs=50.0,
            scene_duration=10.0,
        )

        self.assertIsNone(projected_scene2)
        self.assertEqual(infer_overlay_type(projected_scene3), "exegesis_persistent")
        self.assertEqual(projected_scene3["highlights"], [])
        self.assertEqual([event["ranges"] for event in projected_scene4["highlights"]], [["聪明通达人"], ["父", "向婴孩就显出来"]])
        self.assertEqual(projected_scene4["highlights"][0]["trigger_time"], 0.0)
        self.assertEqual(projected_scene4["highlights"][1]["trigger_time"], 5.0)
        self.assertEqual([event["ranges"] for event in projected_scene5["highlights"]], [["父", "向婴孩就显出来"]])
        self.assertEqual(projected_scene5["highlights"][0]["trigger_time"], 0.0)

    def test_render_exegesis_persistent_handles_scene_before_first_highlight(self):
        overlay = {
            "type": "exegesis_persistent",
            "anchor": {"x_ratio": 0.12, "y_ratio": 0.46, "max_width_ratio": 0.52},
            "header": {"enabled": True, "text": "马太福音 11:25"},
            "verse_block": {
                "text": "父啊，天地的主，我感谢你，\n因为你将这些事\n向聪明通达人就藏起来，\n向婴孩就显出来。",
                "style": {
                    "font_size": 44,
                    "line_gap": 1.2,
                    "opacity": 1.0,
                    "color": "#F2F2F2",
                    "text_shadow": {"color": "rgba(0,0,0,0.45)", "blur": 8, "offset_x": 0, "offset_y": 2},
                },
            },
            "highlights": [],
            "dim_others": {"enabled": True, "opacity": 0.65},
            "dark_overlay": {
                "type": "gradient",
                "direction": "left_to_right",
                "start_opacity": 0.45,
                "end_opacity": 0.0,
                "width_ratio": 0.35,
                "blur": 26,
                "feather": 0.6,
            },
        }

        clips, suppress_subtitle = render_exegesis_persistent(overlay, duration=5.0, frame_w=1920, frame_h=1080)

        self.assertFalse(suppress_subtitle)
        self.assertEqual(len(clips), 1)

    def test_render_multi_cue_concepts_handles_verse_reference_without_name_error(self):
        overlay = {
            "type": "multi_cue_concepts",
            "position": "left-center",
            "items": [
                {
                    "trigger_time": 0.0,
                    "kind": "verse",
                    "text": "父啊，天地的主",
                    "reference": "马太福音 11:25",
                }
            ],
            "behavior": {"mode": "replace"},
        }

        clips, suppress_subtitle = render_multi_cue_concepts(
            overlay_cfg=overlay,
            cue_map={},
            scene_abs_start=0.0,
            duration=5.0,
            frame_w=1920,
            frame_h=1080,
        )

        self.assertFalse(suppress_subtitle)
        self.assertEqual(len(clips), 1)

    def test_exegesis_persistent_global_defaults_are_applied_and_overrideable(self):
        overlay = {
            "type": "exegesis_persistent",
            "header": {"text": "马太福音 11:25"},
            "verse_block": {"text": "父啊，天地的主"},
            "highlights": [
                {
                    "trigger_cue": "scene_4",
                    "ranges": ["父"],
                    "style": {"scale": 1.03},
                }
            ],
            "dim_others": {"opacity": 0.61},
        }

        normalized = _normalize_exegesis_persistent_overlay_cfg(overlay)

        self.assertEqual(normalized["anchor"]["x_ratio"], 0.12)
        self.assertEqual(normalized["header"]["style"]["font_size"], 30)
        self.assertEqual(normalized["verse_block"]["style"]["font_size"], 44)
        self.assertEqual(normalized["dark_overlay"]["type"], "gradient")
        self.assertEqual(normalized["dim_others"]["opacity"], 0.61)
        self.assertEqual(normalized["highlights"][0]["style"]["color"], "#FFFFFF")
        self.assertEqual(normalized["highlights"][0]["style"]["scale"], 1.03)

    def test_exegesis_persistent_defaults_can_be_loaded_from_config_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "config.json"
            config_path.write_text(
                """
{
  "exegesis_persistent_defaults": {
    "anchor": {"x_ratio": 0.2},
    "dim_others": {"opacity": 0.61},
    "highlight_style": {"scale": 1.04}
  }
}
""".strip(),
                encoding="utf-8",
            )

            with patch.object(assembly_module, "SERMON_TO_VIDEO_CONFIG_FILE", config_path):
                _load_exegesis_persistent_defaults.cache_clear()
                defaults = _load_exegesis_persistent_defaults()

        self.assertEqual(defaults["anchor"]["x_ratio"], 0.2)
        self.assertEqual(defaults["dim_others"]["opacity"], 0.61)
        self.assertEqual(defaults["highlight_style"]["scale"], 1.04)

    def test_face_detection_cache_reuses_result_for_same_source_key(self):
        frame = np.zeros((200, 200, 3), dtype=np.uint8)
        with patch(
            "backend.sermon_to_video.core.face_detection._detect_primary_face_anchor",
            return_value=(0.4, 0.3),
        ) as detect_mock:
            first = face_detection.detect_primary_face_anchor(frame, cache_key="img:1")
            second = face_detection.detect_primary_face_anchor(frame, cache_key="img:1")

        self.assertEqual(first, (0.4, 0.3))
        self.assertEqual(second, (0.4, 0.3))
        self.assertEqual(detect_mock.call_count, 1)

    def test_resolve_face_anchor_point_returns_normalized_face_center(self):
        clip = type("DummyClip", (), {"get_frame": lambda self, t: np.zeros((120, 200, 3), dtype=np.uint8)})()
        with patch(
            "backend.sermon_to_video.core.assembly.detect_primary_face_anchor",
            return_value=(0.42, 0.38),
        ) as detect_mock:
            anchor_point = _resolve_face_anchor_point(clip, source_key="image:face")

        self.assertEqual(anchor_point, (0.42, 0.38))
        detect_mock.assert_called_once()

    def test_resolve_face_anchor_point_falls_back_when_no_face_detected(self):
        clip = type("DummyClip", (), {"get_frame": lambda self, t: np.zeros((120, 200, 3), dtype=np.uint8)})()
        with patch(
            "backend.sermon_to_video.core.assembly.detect_primary_face_anchor",
            return_value=None,
        ):
            anchor_point = _resolve_face_anchor_point(clip, source_key="image:none")

        self.assertIsNone(anchor_point)

    def test_resolve_motion_anchor_ratios_uses_face_detection(self):
        clip = type("DummyClip", (), {"get_frame": lambda self, t: np.zeros((120, 200, 3), dtype=np.uint8)})()
        with patch(
            "backend.sermon_to_video.core.assembly.detect_primary_face_anchor",
            return_value=(0.42, 0.12),
        ):
            x_ratio, y_ratio, label = _resolve_motion_anchor_ratios("face", clip=clip, source_key="image:face-top")

        self.assertAlmostEqual(x_ratio, 0.42)
        self.assertAlmostEqual(y_ratio, 0.12)
        self.assertEqual(label, "face(0.420, 0.120)")

    def test_viewport_origin_clamps_face_anchor_near_top_edge(self):
        left, top = _compute_viewport_origin(
            scaled_w=2304,
            scaled_h=1296,
            target_w=1920,
            target_h=1080,
            x_ratio=0.25,
            y_ratio=0.08,
        )

        self.assertGreaterEqual(left, 0)
        self.assertEqual(top, 0)

    def test_crop_viewport_frame_keeps_full_output_size_for_face_anchor_near_top(self):
        frame = np.ones((1296, 2304, 3), dtype=np.uint8) * 255
        cropped = _crop_viewport_frame(
            frame,
            target_w=1920,
            target_h=1080,
            x_ratio=0.5,
            y_ratio=0.06,
        )

        self.assertEqual(cropped.shape, (1080, 1920, 3))
        self.assertTrue(np.all(cropped[0, 0] == 255))

    def test_dark_overlay_config_supports_blur_and_feather(self):
        cfg = resolve_dark_overlay_config(
            {
                "dark_overlay": {
                    "mode": "box",
                    "opacity": 0.45,
                    "blur": 26,
                    "feather": 0.6,
                }
            }
        )

        self.assertEqual(cfg["mode"], "box")
        self.assertEqual(cfg["opacity"], 0.45)
        self.assertEqual(cfg["blur"], 26.0)
        self.assertEqual(cfg["feather"], 0.6)

    def test_dark_overlay_config_supports_frame_gradient_band(self):
        cfg = resolve_dark_overlay_config(
            {
                "dark_overlay": {
                    "type": "gradient",
                    "direction": "left_to_right",
                    "start_opacity": 0.45,
                    "end_opacity": 0.0,
                    "width_ratio": 0.35,
                }
            }
        )

        self.assertEqual(cfg["type"], "gradient")
        self.assertEqual(cfg["direction"], "left_to_right")
        self.assertEqual(cfg["start_opacity"], 0.45)
        self.assertEqual(cfg["end_opacity"], 0.0)
        self.assertEqual(cfg["width_ratio"], 0.35)

    def test_dark_overlay_blur_and_feather_soften_edges_outside_text_box(self):
        sharp = create_text_background_overlay(
            frame_size=(100, 100),
            text_box=(20, 20, 30, 30),
            mode="box",
            opacity=0.5,
            padding_x=0,
            padding_y=0,
            radius=0,
            blur=0,
            feather=0,
        )
        soft = create_text_background_overlay(
            frame_size=(100, 100),
            text_box=(20, 20, 30, 30),
            mode="box",
            opacity=0.5,
            padding_x=0,
            padding_y=0,
            radius=0,
            blur=12,
            feather=0.6,
        )

        self.assertEqual(sharp.getchannel("A").getpixel((15, 35)), 0)
        self.assertGreater(soft.getchannel("A").getpixel((15, 35)), 0)

    def test_dark_overlay_gradient_band_covers_left_side_without_text_box_bounds(self):
        overlay = create_text_background_overlay(
            frame_size=(100, 60),
            text_box=(20, 20, 10, 10),
            type="gradient",
            direction="left_to_right",
            start_opacity=0.45,
            end_opacity=0.0,
            width_ratio=0.35,
            blur=0,
            feather=0,
        )

        alpha = overlay.getchannel("A")
        self.assertGreater(alpha.getpixel((0, 30)), 0)
        self.assertGreater(alpha.getpixel((20, 30)), 0)
        self.assertEqual(alpha.getpixel((60, 30)), 0)

    def test_scaled_highlight_origin_stays_centered_on_base_segment(self):
        draw_x, draw_y = _compute_scaled_text_origin(
            base_x=100,
            base_y=200,
            base_w=80,
            base_h=40,
            scaled_w=96,
            scaled_h=48,
        )

        self.assertEqual(draw_x, 92)
        self.assertEqual(draw_y, 196)

    def test_scale_alpha_preserves_full_alpha_at_full_opacity(self):
        self.assertEqual(_scale_alpha(255, 1.0), 255)
        self.assertEqual(_scale_alpha(140, 1.0), 140)
        self.assertEqual(_scale_alpha(255, 0.5), 128)

    def test_scale_color_brightness_dims_rgb_without_reducing_alpha(self):
        self.assertEqual(
            _scale_color_brightness((240, 240, 240, 255), 0.25),
            (60, 60, 60, 255),
        )

    def test_parse_highlight_term_spec_supports_occurrence_suffix(self):
        self.assertEqual(_parse_highlight_term_spec("父"), ("父", None))
        self.assertEqual(_parse_highlight_term_spec("父[2]"), ("父", 2))
        self.assertEqual(_parse_highlight_term_spec("父[-1]"), ("父", -1))
        self.assertEqual(_parse_highlight_term_spec("父[abc]"), ("父[abc]", None))

    def test_find_highlight_spans_defaults_to_all_matches(self):
        line = "父啊，父啊，父啊"
        self.assertEqual(_find_highlight_spans(line, ["父"]), [(0, 1), (3, 4), (6, 7)])

    def test_find_highlight_spans_can_target_second_match(self):
        line = "除了这个，除了那个，除了别的"
        self.assertEqual(_find_highlight_spans(line, ["除了[2]"]), [(5, 7)])

    def test_find_highlight_spans_can_target_last_match(self):
        line = "父啊，父啊，父啊"
        self.assertEqual(_find_highlight_spans(line, ["父[-1]"]), [(6, 7)])

    def test_find_highlight_spans_across_lines_can_target_second_global_match(self):
        lines = [
            "除了父，没有人知道子；",
            "除了子和子所愿意指示的，",
        ]
        spans = _find_highlight_spans_across_lines(lines, ["除了[2]"])

        self.assertEqual(spans[0], [])
        self.assertEqual(spans[1], [(0, 2)])


if __name__ == "__main__":
    unittest.main()
