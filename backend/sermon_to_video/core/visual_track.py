from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any, Optional


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".webm")


@dataclass(frozen=True)
class VisualClip:
    clip_id: str
    clip_type: str
    trigger_scene_cue: Optional[str] = None
    trigger_mode: Optional[str] = None
    duration: Optional[float] = None
    description: str = ""
    motion: dict[str, Any] = field(default_factory=dict)
    prompt: Optional[str] = None
    playback_plan: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualShot:
    shot_id: str
    time_range: Optional[tuple[float, float]] = None
    meaning: str = ""
    transition: dict[str, Any] = field(default_factory=dict)
    clips: tuple[VisualClip, ...] = ()


@dataclass(frozen=True)
class VisualTrackEntry:
    visual_id: str
    time_range: Optional[tuple[float, float]] = None
    covered_scenes: tuple[int, ...] = ()
    purpose: str = ""
    overlay: Any = None
    motion: dict[str, Any] = field(default_factory=dict)
    playback_plan: dict[str, Any] = field(default_factory=dict)
    shots: tuple[VisualShot, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class VisualTrackDocument:
    title: str = ""
    mode: str = ""
    visual_track: tuple[VisualTrackEntry, ...] = ()


@dataclass(frozen=True)
class SceneVisualBinding:
    scene_id: int
    visual: VisualTrackEntry
    shot: Optional[VisualShot]
    clip: Optional[VisualClip]
    asset_path: Optional[Path]
    clip_schedule: tuple[tuple[Optional[VisualShot], VisualClip, Optional[Path]], ...] = ()

    def to_runtime_metadata(self) -> dict[str, Any]:
        clip = self.clip
        shot = self.shot
        return {
            "visual_id": self.visual.visual_id,
            "time_range": list(self.visual.time_range) if self.visual.time_range else None,
            "covered_scenes": list(self.visual.covered_scenes),
            "purpose": self.visual.purpose,
            "overlay": deepcopy(self.visual.overlay),
            "motion": deepcopy(clip.motion if clip and clip.motion else self.visual.motion),
            "playback_plan": deepcopy(
                clip.playback_plan if clip and clip.playback_plan else self.visual.playback_plan
            ),
            "shot_id": shot.shot_id if shot else None,
            "shot_time_range": list(shot.time_range) if shot and shot.time_range else None,
            "clip_id": clip.clip_id if clip else None,
            "clip_type": clip.clip_type if clip else None,
            "clip_duration": clip.duration if clip else None,
            "clip_trigger_scene_cue": clip.trigger_scene_cue if clip else None,
            "clip_trigger_mode": clip.trigger_mode if clip else None,
            "clip_description": clip.description if clip else "",
            "clip_schedule": [
                {
                    "shot_id": scheduled_shot.shot_id if scheduled_shot else None,
                    "clip_id": scheduled_clip.clip_id,
                    "clip_type": scheduled_clip.clip_type,
                    "trigger_scene_cue": scheduled_clip.trigger_scene_cue,
                    "trigger_mode": scheduled_clip.trigger_mode,
                    "clip_duration": scheduled_clip.duration,
                    "clip_description": scheduled_clip.description,
                    "motion": deepcopy(scheduled_clip.motion),
                    "playback_plan": deepcopy(scheduled_clip.playback_plan),
                    "asset_ref": (
                        _relative_or_absolute(scheduled_asset_path, scheduled_asset_path.parent.parent)
                        if scheduled_asset_path
                        else None
                    ),
                }
                for scheduled_shot, scheduled_clip, scheduled_asset_path in self.clip_schedule
            ],
        }


def load_visual_track_document(payload: dict[str, Any]) -> VisualTrackDocument:
    entries = payload.get("visual_track", [])
    normalized_entries = []
    for index, entry in enumerate(entries, start=1):
        if isinstance(entry, dict) and "shots" in entry:
            normalized_entries.append(_parse_visual_entry(entry, index))
        else:
            normalized_entries.append(_parse_legacy_visual_entry(entry, index))

    return VisualTrackDocument(
        title=str(payload.get("title", "")),
        mode=str(payload.get("mode", "")),
        visual_track=tuple(normalized_entries),
    )


def build_scene_visual_bindings(
    document: VisualTrackDocument,
    scenes: list[dict[str, Any]],
    work_dir: Path,
) -> dict[int, SceneVisualBinding]:
    bindings: dict[int, SceneVisualBinding] = {}
    scene_index = {
        scene_id: scene
        for scene in scenes
        if (scene_id := _coerce_scene_id(scene.get("scene_id"))) is not None
    }
    for visual in document.visual_track:
        for scene_id in visual.covered_scenes:
            scene = scene_index.get(scene_id, {"scene_id": scene_id})
            clip_schedule = _select_clip_schedule_for_scene(visual, scene)
            shot, clip = _select_clip_for_scene(visual, scene_id)
            if clip_schedule:
                primary_shot, primary_clip = clip_schedule[0]
                shot = primary_shot or shot
                clip = primary_clip or clip
            bindings[scene_id] = SceneVisualBinding(
                scene_id=scene_id,
                visual=visual,
                shot=shot,
                clip=clip,
                asset_path=_resolve_asset_path(work_dir, clip) if clip else None,
                clip_schedule=tuple(
                    (scheduled_shot, scheduled_clip, _resolve_asset_path(work_dir, scheduled_clip))
                    for scheduled_shot, scheduled_clip in clip_schedule
                ),
            )
    return bindings


def apply_visual_track_to_scenes(
    scenes: list[dict[str, Any]],
    visual_track_payload: dict[str, Any],
    work_dir: Path,
) -> VisualTrackDocument:
    document = load_visual_track_document(visual_track_payload)
    bindings = build_scene_visual_bindings(document, scenes, work_dir)

    for scene in scenes:
        scene_id = _coerce_scene_id(scene.get("scene_id"))
        if scene_id is None:
            continue

        binding = bindings.get(scene_id)
        if not binding:
            continue

        scene["visual_track_metadata"] = binding.to_runtime_metadata()
        _apply_binding_to_scene(scene, binding)

    return document


def infer_overlay_type(overlay_cfg: Any) -> Optional[str]:
    if isinstance(overlay_cfg, list):
        return "legacy_list"
    if not isinstance(overlay_cfg, dict):
        return None
    if overlay_cfg.get("type"):
        return str(overlay_cfg["type"])
    if overlay_cfg.get("layers"):
        return "multi_layer"
    if overlay_cfg.get("items") and (overlay_cfg.get("header") or overlay_cfg.get("anchor")):
        return "definition_parallel"
    if overlay_cfg.get("items"):
        return "multi_cue_concepts"
    return "simple"


def project_overlay_for_scene(
    overlay_cfg: Any,
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_end_abs: float,
    scene_duration: float,
) -> Any:
    overlay_type = infer_overlay_type(overlay_cfg)
    if overlay_type is None:
        return None

    if overlay_type == "legacy_list":
        return _project_legacy_overlay_list(
            overlay_cfg,
            cue_map=cue_map,
            scene_start_abs=scene_start_abs,
            scene_end_abs=scene_end_abs,
            scene_duration=scene_duration,
        )

    if not isinstance(overlay_cfg, dict):
        return None
    if not overlay_cfg.get("enabled", True):
        return None
    if not _is_overlay_visible_for_scene(
        overlay_cfg,
        cue_map=cue_map,
        scene_start_abs=scene_start_abs,
        scene_end_abs=scene_end_abs,
    ):
        return None

    if overlay_type == "multi_layer":
        layers = []
        for layer in overlay_cfg.get("layers", []):
            projected = project_overlay_for_scene(
                layer,
                cue_map=cue_map,
                scene_start_abs=scene_start_abs,
                scene_end_abs=scene_end_abs,
                scene_duration=scene_duration,
            )
            if projected:
                layers.append(projected)
        if not layers:
            return None
        return {**deepcopy(overlay_cfg), "type": "multi_layer", "layers": layers}

    if overlay_type == "definition_parallel":
        items = _project_item_sequence(
            overlay_cfg.get("items", []),
            cue_map=cue_map,
            scene_start_abs=scene_start_abs,
            scene_end_abs=scene_end_abs,
            scene_duration=scene_duration,
            mode="cumulative",
        )
        if not items and not overlay_cfg.get("header", {}).get("enabled", False):
            return None
        projected = deepcopy(overlay_cfg)
        projected["type"] = "definition_parallel"
        projected["items"] = items
        return projected

    if overlay_type == "exegesis_persistent":
        projected = _project_exegesis_persistent_overlay(
            overlay_cfg,
            cue_map=cue_map,
            scene_start_abs=scene_start_abs,
            scene_end_abs=scene_end_abs,
        )
        return projected

    if overlay_type == "multi_cue_concepts":
        behavior = overlay_cfg.get("behavior", {})
        mode = str(behavior.get("mode", "cumulative")).strip().lower() or "cumulative"
        items = _project_item_sequence(
            overlay_cfg.get("items", []),
            cue_map=cue_map,
            scene_start_abs=scene_start_abs,
            scene_end_abs=scene_end_abs,
            scene_duration=scene_duration,
            mode=mode,
        )
        if not items:
            return None
        projected = deepcopy(overlay_cfg)
        projected["type"] = "multi_cue_concepts"
        projected["items"] = items
        return projected

    projected = deepcopy(overlay_cfg)
    start_time = _project_single_overlay_start(
        overlay_cfg,
        cue_map=cue_map,
        scene_start_abs=scene_start_abs,
        scene_end_abs=scene_end_abs,
        scene_duration=scene_duration,
    )
    if start_time is None:
        return None
    projected["trigger_time"] = start_time
    return projected


def _is_overlay_visible_for_scene(
    overlay_cfg: dict[str, Any],
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_end_abs: float,
) -> bool:
    visible_from_cue = _clean_optional_str(overlay_cfg.get("visible_from_cue"))
    visible_to_cue = _clean_optional_str(overlay_cfg.get("visible_to_cue"))
    visible_from_abs = cue_map.get(visible_from_cue) if visible_from_cue else None
    visible_to_abs = cue_map.get(visible_to_cue) if visible_to_cue else None

    if visible_from_abs is not None and scene_end_abs <= visible_from_abs:
        return False
    if visible_to_abs is not None and scene_start_abs > visible_to_abs:
        return False
    return True


def _project_exegesis_persistent_overlay(
    overlay_cfg: dict[str, Any],
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_end_abs: float,
) -> Optional[dict[str, Any]]:
    if not _is_overlay_visible_for_scene(
        overlay_cfg,
        cue_map=cue_map,
        scene_start_abs=scene_start_abs,
        scene_end_abs=scene_end_abs,
    ):
        return None

    visible_from_cue = _clean_optional_str(overlay_cfg.get("visible_from_cue"))
    visible_from_abs = cue_map.get(visible_from_cue) if visible_from_cue else None

    projected = deepcopy(overlay_cfg)
    projected["type"] = "exegesis_persistent"

    highlight_events = []
    for event in overlay_cfg.get("highlights", []):
        cue_key = _clean_optional_str(event.get("trigger_cue"))
        if not cue_key:
            continue
        trigger_abs = cue_map.get(cue_key)
        if trigger_abs is None:
            continue
        highlight_events.append((trigger_abs, deepcopy(event)))

    highlight_events.sort(key=lambda value: value[0])
    active_before = [event for trigger_abs, event in highlight_events if trigger_abs < scene_start_abs]
    in_window = [
        (trigger_abs, event)
        for trigger_abs, event in highlight_events
        if scene_start_abs <= trigger_abs < scene_end_abs
    ]

    resolved_highlights = []
    if active_before:
        last_trigger_abs, last_event = max(
            ((cue_map.get(event.get("trigger_cue"), scene_start_abs), event) for event in active_before),
            key=lambda value: value[0],
        )
        resolved_highlights.append(_with_local_time(last_event, 0.0))

    for trigger_abs, event in in_window:
        resolved_highlights.append(_with_local_time(event, max(0.0, trigger_abs - scene_start_abs)))

    projected["highlights"] = resolved_highlights
    projected["visible_from_time"] = (
        max(0.0, visible_from_abs - scene_start_abs) if visible_from_abs is not None else 0.0
    )
    projected["visible_until_scene_end"] = True
    return projected


def _apply_binding_to_scene(scene: dict[str, Any], binding: SceneVisualBinding) -> None:
    clip = binding.clip
    overlay = binding.visual.overlay

    if binding.asset_path:
        if clip and clip.clip_type == "video":
            scene["visual_source"] = _relative_or_absolute(binding.asset_path, binding.asset_path.parent.parent)
        else:
            scene["visual_filepath"] = str(binding.asset_path)
    elif clip and clip.clip_type == "image":
        prompt = clip.prompt or clip.description
        if prompt:
            scene["visual_prompt"] = prompt

    if isinstance(overlay, dict) and overlay.get("enabled", True):
        if infer_overlay_type(overlay) == "simple" and not overlay.get("trigger_cue"):
            if overlay.get("kind") == "verse":
                scene["overlay_text"] = {
                    "verse": overlay.get("text"),
                    "reference": overlay.get("reference"),
                }
            else:
                scene["overlay_text"] = overlay.get("text")
            scene["overlay_start_ratio"] = overlay.get("start_ratio", 0.0)
        elif "overlay_text" not in scene:
            scene["overlay_text"] = None


def _parse_visual_entry(entry: dict[str, Any], index: int) -> VisualTrackEntry:
    visual_id = str(entry.get("visual_id") or index)
    shots = []
    for shot_index, shot in enumerate(entry.get("shots", []), start=1):
        clips = []
        for clip_index, clip in enumerate(shot.get("clips", []), start=1):
            clips.append(
                VisualClip(
                    clip_id=str(clip.get("clip_id") or f"{visual_id}_clip_{clip_index}"),
                    clip_type=str(clip.get("type", "image")),
                    trigger_scene_cue=_clean_optional_str(clip.get("trigger_scene_cue")),
                    trigger_mode=_clean_optional_str(clip.get("trigger_mode")),
                    duration=_coerce_float(clip.get("duration")),
                    description=str(clip.get("description", "")),
                    motion=deepcopy(clip.get("motion", {}) or {}),
                    prompt=_clean_optional_str(clip.get("prompt")),
                    playback_plan=deepcopy(clip.get("playback_plan", {}) or {}),
                    raw=deepcopy(clip),
                )
            )
        shots.append(
            VisualShot(
                shot_id=str(shot.get("shot_id") or f"{visual_id}_shot_{shot_index}"),
                time_range=_parse_time_range(shot.get("time_range")),
                meaning=str(shot.get("meaning", "")),
                transition=deepcopy(shot.get("transition", {}) or {}),
                clips=tuple(clips),
            )
        )

    covered_scenes = tuple(_coerce_scene_id(scene_id) for scene_id in entry.get("covered_scenes", []))
    covered_scenes = tuple(scene_id for scene_id in covered_scenes if scene_id is not None)

    return VisualTrackEntry(
        visual_id=visual_id,
        time_range=_parse_time_range(entry.get("time_range")),
        covered_scenes=covered_scenes,
        purpose=str(entry.get("purpose", "")),
        overlay=deepcopy(entry.get("overlay")),
        shots=tuple(shots),
        raw=deepcopy(entry),
    )


def _parse_legacy_visual_entry(entry: dict[str, Any], index: int) -> VisualTrackEntry:
    scene_id = _coerce_scene_id(entry.get("scene_id"))
    visual_id = str(entry.get("visual_id") or scene_id or index)
    asset = entry.get("asset", {}) or {}
    clip_type = str(asset.get("type", "image"))
    scene_cue = f"scene_{scene_id}" if scene_id is not None else None
    fallback_clip_id = f"scene_{scene_id}" if scene_id is not None else visual_id
    clip = VisualClip(
        clip_id=str(asset.get("clip_id") or asset.get("asset_id") or fallback_clip_id),
        clip_type=clip_type,
        trigger_scene_cue=scene_cue,
        trigger_mode=None,
        duration=_coerce_float(entry.get("duration")),
        description=str(asset.get("description", "")),
        motion=deepcopy(entry.get("motion", {}) or {}),
        prompt=_clean_optional_str(asset.get("prompt")),
        playback_plan=deepcopy(entry.get("playback_plan", {}) or {}),
        raw=deepcopy(asset),
    )
    shot = VisualShot(
        shot_id=f"{visual_id}_shot_1",
        clips=(clip,),
    )
    covered_scenes = (scene_id,) if scene_id is not None else ()
    return VisualTrackEntry(
        visual_id=visual_id,
        covered_scenes=covered_scenes,
        purpose=str(entry.get("purpose", "")),
        overlay=deepcopy(entry.get("overlay")),
        motion=deepcopy(entry.get("motion", {}) or {}),
        playback_plan=deepcopy(entry.get("playback_plan", {}) or {}),
        shots=(shot,),
        raw=deepcopy(entry),
    )


def _select_clip_for_scene(
    visual: VisualTrackEntry,
    scene_id: int,
) -> tuple[Optional[VisualShot], Optional[VisualClip]]:
    expected_cue = f"scene_{scene_id}"
    for shot in visual.shots:
        for clip in shot.clips:
            if clip.trigger_scene_cue == expected_cue:
                return shot, clip

    clips = [clip for shot in visual.shots for clip in shot.clips]
    if len(visual.covered_scenes) == 1 and len(clips) == 1:
        shot = visual.shots[0] if visual.shots else None
        return shot, clips[0]

    return (visual.shots[0], visual.shots[0].clips[0]) if visual.shots and visual.shots[0].clips else (None, None)


def _select_clip_schedule_for_scene(
    visual: VisualTrackEntry,
    scene: dict[str, Any],
) -> list[tuple[Optional[VisualShot], VisualClip]]:
    scene_id = _coerce_scene_id(scene.get("scene_id"))
    if scene_id is None:
        return []

    scene_triggers = {f"scene_{scene_id}", *_extract_scene_markers(scene.get("voiceover_text", ""))}
    flattened_clips = [
        (shot, clip)
        for shot in visual.shots
        for clip in shot.clips
    ]
    schedule: list[tuple[Optional[VisualShot], VisualClip]] = []
    include_followups = False
    for shot, clip in flattened_clips:
        cue_key = _clean_optional_str(clip.trigger_scene_cue)
        trigger_mode = _clean_optional_str(clip.trigger_mode)
        if cue_key:
            include_followups = cue_key in scene_triggers
            if include_followups:
                schedule.append((shot, clip))
            continue
        if include_followups and trigger_mode == "after_previous":
            schedule.append((shot, clip))

    if schedule:
        return schedule

    fallback_shot, fallback_clip = _select_clip_for_scene(visual, scene_id)
    if fallback_clip:
        return [(fallback_shot, fallback_clip)]
    return []


def _resolve_asset_path(work_dir: Path, clip: VisualClip) -> Optional[Path]:
    if not clip.clip_id:
        return None

    assets_dir = work_dir / "assets"
    if not assets_dir.exists():
        return None

    clip_name = str(clip.clip_id)
    direct_candidate = assets_dir / clip_name
    if direct_candidate.exists():
        return direct_candidate

    suffix = Path(clip_name).suffix.lower()
    if suffix:
        return direct_candidate if direct_candidate.exists() else None

    extensions = VIDEO_EXTENSIONS if clip.clip_type == "video" else IMAGE_EXTENSIONS
    for extension in extensions:
        candidate = assets_dir / f"{clip_name}{extension}"
        if candidate.exists():
            return candidate
    return None


def _relative_or_absolute(path: Path, base_dir: Path) -> str:
    try:
        return str(path.relative_to(base_dir))
    except ValueError:
        return str(path)


def _project_item_sequence(
    items: list[dict[str, Any]],
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_end_abs: float,
    scene_duration: float,
    mode: str,
) -> list[dict[str, Any]]:
    resolved = []
    for item in items:
        trigger_abs = _resolve_item_absolute_time(
            item,
            cue_map=cue_map,
            scene_start_abs=scene_start_abs,
            scene_duration=scene_duration,
        )
        if trigger_abs is None:
            trigger_abs = scene_start_abs
        resolved.append((trigger_abs, deepcopy(item)))

    resolved.sort(key=lambda value: value[0])
    carryover: list[tuple[float, dict[str, Any]]] = []
    in_window: list[tuple[float, dict[str, Any]]] = []
    for trigger_abs, item in resolved:
        if trigger_abs < scene_start_abs:
            carryover.append((trigger_abs, item))
        elif trigger_abs < scene_end_abs:
            in_window.append((trigger_abs, item))

    projected: list[dict[str, Any]] = []
    if mode == "replace":
        has_immediate_replacement = any(trigger_abs <= scene_start_abs for trigger_abs, _ in in_window)
        if carryover and not has_immediate_replacement:
            last_abs = carryover[-1][0]
            projected.extend(_with_local_time(item, 0.0) for trigger_abs, item in carryover if trigger_abs == last_abs)
    else:
        projected.extend(_with_local_time(item, 0.0) for _, item in carryover)

    projected.extend(
        _with_local_time(item, max(0.0, trigger_abs - scene_start_abs))
        for trigger_abs, item in in_window
    )
    return projected


def _project_single_overlay_start(
    overlay_cfg: dict[str, Any],
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_end_abs: float,
    scene_duration: float,
) -> Optional[float]:
    trigger_abs = _resolve_item_absolute_time(
        overlay_cfg,
        cue_map=cue_map,
        scene_start_abs=scene_start_abs,
        scene_duration=scene_duration,
    )
    if trigger_abs is None:
        trigger_abs = scene_start_abs
    if trigger_abs >= scene_end_abs:
        return None
    return max(0.0, trigger_abs - scene_start_abs)


def _project_legacy_overlay_list(
    overlays: list[dict[str, Any]],
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_end_abs: float,
    scene_duration: float,
) -> list[dict[str, Any]]:
    projected = []
    for overlay in overlays:
        trigger_abs = _resolve_item_absolute_time(
            overlay,
            cue_map=cue_map,
            scene_start_abs=scene_start_abs,
            scene_duration=scene_duration,
        )
        if trigger_abs is None:
            trigger_abs = scene_start_abs
        if trigger_abs >= scene_end_abs:
            continue
        projected.append(_with_local_time(overlay, max(0.0, trigger_abs - scene_start_abs)))
    return projected


def _resolve_item_absolute_time(
    item: dict[str, Any],
    cue_map: dict[str, float],
    scene_start_abs: float,
    scene_duration: float,
) -> Optional[float]:
    if "trigger_time" in item and item.get("trigger_time") is not None:
        return scene_start_abs + float(item["trigger_time"])

    cue_key = _clean_optional_str(item.get("trigger_cue")) or _clean_optional_str(item.get("trigger_mark"))
    if cue_key:
        return cue_map.get(cue_key)

    if item.get("start_ratio") is not None:
        try:
            return scene_start_abs + (float(item["start_ratio"]) * scene_duration)
        except (TypeError, ValueError):
            return scene_start_abs

    return None


def _with_local_time(item: dict[str, Any], trigger_time: float) -> dict[str, Any]:
    projected = deepcopy(item)
    projected["trigger_time"] = max(0.0, float(trigger_time))
    return projected


def _parse_time_range(value: Any) -> Optional[tuple[float, float]]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    start = _coerce_float(value[0])
    end = _coerce_float(value[1])
    if start is None or end is None:
        return None
    return (start, end)


def _coerce_scene_id(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _extract_scene_markers(text: Any) -> tuple[str, ...]:
    if text is None:
        return ()
    return tuple(match.group(1).strip() for match in re.finditer(r"\[([^\[\]]+)\]", str(text)) if match.group(1).strip())
