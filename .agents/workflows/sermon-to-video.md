---
description: Sermon-to-Video Workflow and Architecture
---

# Project: Sermon-to-Video

## Overview
This project renders a sermon or Bible-study project folder into a narrated MP4 with scene timing, visual overlays, captions, and subtitle burn-in.

The current system is project-oriented, not single-file oriented.

Primary project inputs:
- `storyboard.json`
- `visual_track.json`
- optional `motions.json`
- optional `assets/`
- optional `bgm.mp3` / `bgm.wav`

Primary generated outputs:
- `<project>/build/*`
- `<project>/final_video.mp4`
- `<project>/final_video.srt`

## Architecture Principle

The pipeline remains audio-driven:
- Azure TTS establishes cue timing and scene duration.
- Scene assembly uses that timing as the anchor for overlays and synchronization.
- Visual track data decorates scenes in memory; it does not replace scene timing as the master timeline.

Runtime mental model:
- `scene`: audio/subtitle sync unit and current export unit for phase 4 outputs
- `visual`: macro scheduling and overlay ownership unit
- `clip`: smallest visual render unit within a visual

## CLI Contract

Render always starts from a project folder:

```bash
python -m backend.sermon_to_video.cli render \
  --project <project_dir> \
  [--output <mp4>] \
  [--font <ttf>] \
  [--start-phase <1-7>] \
  [--scene-id <id>] \
  [--phase4-workers <n>] \
  [--cache | --no-cache]
```

Current defaults:
- `--output` defaults to `<project>/final_video.mp4`
- build artifacts go to `<project>/build`
- cache is on by default
- `--phase4-workers` bounds phase 4 concurrency; use `1` for sequential assembly

## Pipeline

### Phase 1: Storyboard
- Creates or updates `storyboard.json` from transcript input.

### Phase 2: Audio
- Generates Azure TTS SSML and `build/full_audio.mp3`
- Emits `build/cue_points.json`
- Populates `duration_sec`

### Phase 3: Visuals
- Currently bypassed in the render command.
- Visual generation is temporarily disabled in the active render path.
- Do not rely on phase 3 to produce runtime visuals right now.

### Phase 4: Assembly
- Loads scene data and runtime overlay metadata
- Resolves clip schedules per scene from cue anchors and relative chaining
- Resolves assets from:
  - `visual_track.json`
  - project `assets/`
  - previously generated `build/scene_*_visual.*`
  - blank fallback visuals
- Renders clips as the smallest visual units inside the current scene window
- Projects visual-level overlays into that scene window after clip assembly
- Can assemble multiple scenes in parallel with a bounded worker pool
- Renders `build/scene_<id>_final.mp4`

### Phase 5: Concat
- Concatenates assembled scenes in order
- Supports dissolve transitions from `motions.json`
- With `--no-cache`, stale phase-4 scene finals must not be silently reused

### Phase 6: SRT
- Generates or preserves final SRT captions

### Phase 7: Burn-In
- Burns subtitles into final MP4 with FFmpeg

## Visual Track

The new `visual_track.json` schema is first-class.

Top-level shape:

```json
{
  "title": "...",
  "mode": "exegesis_teaching",
  "visual_track": [
    {
      "visual_id": 1,
      "time_range": [0.0, 57.0],
      "covered_scenes": [1, 2, 3],
      "shots": [
        {
          "shot_id": "V1_S1",
          "clips": [
            {
              "clip_id": "IMG_1",
              "type": "image",
              "trigger_scene_cue": "scene_1"
            },
            {
              "clip_id": "IMG_2",
              "type": "image",
              "trigger_mode": "after_previous",
              "duration": 2.5
            }
          ]
        }
      ],
      "overlay": {}
    }
  ]
}
```

Current implementation details:
- `visual_id` replaces old `scene_id` assumptions at the visual-track layer
- one visual can cover multiple scenes via `covered_scenes`
- clips are resolved into a cue-aware `clip_schedule` per scene
- `trigger_scene_cue` starts a clip from an absolute scene/cue anchor
- `trigger_mode: "after_previous"` starts a clip immediately after the previous resolved clip ends
- overlays live at the visual level and are projected into scenes
- old scene-based visual-track formats are normalized through an adapter layer

Authoring rules:
- use explicit cues when the visual must sync to a narration beat
- use `after_previous` when the next clip should simply continue after the prior clip
- relative chains should usually include `duration` on the preceding clip, otherwise they run until the next explicit cue-triggered clip or scene end

## Overlay System

Supported overlay families:
- simple `verse` / `concept`
- `multi_cue_concepts`
- `definition_parallel`
- `multi_layer`
- `exegesis_persistent`

### `multi_layer`
Used to combine multiple overlay layers under a single visual.

Typical pattern:
- `opening_prompt` as `multi_cue_concepts`
- `scripture_anchor` as `exegesis_persistent`

### `multi_cue_concepts`
- cue-driven prompt or concept overlays
- supports replace/cumulative behavior
- scene-local timing is projected from Azure cue marks

### `exegesis_persistent`
Used for stable scripture exposition blocks across scenes.

Supported fields:
- `anchor`
- `header`
- `verse_block`
- `visible_from_cue`
- `visible_to_cue`
- `highlights`
- `dim_others`
- `dark_overlay`
- `behavior`

Current rules:
- layout stays stable across scenes
- highlight changes do not cause reflow or movement
- dimmed text stays opaque; dimming is done by color darkening, not alpha reduction

## Global Exegesis Defaults

Global defaults are loaded from:

```text
DATA_DIR/sermon_to_video/config.json
```

Key:

```json
{
  "exegesis_persistent_defaults": {}
}
```

This centralizes the baseline for:
- `anchor`
- `header.style`
- `verse_block.style`
- `highlight_style`
- `dim_others`
- `behavior`
- `dark_overlay`

Per-overlay settings in `visual_track.json` override these defaults.

## Highlight Matching

Exact substring matching is used.

Current authoring syntax:
- `"父"`: highlight all matches
- `"父[2]"`: highlight the second match
- `"父[-1]"`: highlight the last match

Occurrence targeting is counted across the full rendered verse block, not just within one wrapped line.

## Dark Overlay

Use the term `dark_overlay`.

Supported modes:
- text-box overlay
- frame-band gradient overlay

Supported keys include:
- `mode`
- `opacity`
- `padding_x`
- `padding_y`
- `radius`
- `blur`
- `feather`
- `direction`
- `start_opacity`
- `end_opacity`
- `width_ratio`

## Motion and Face Anchor

`motion.anchor = "face"` is supported for still-image zooms.

Implementation:
- OpenCV Haar cascade face detection
- cached per source image
- normalized anchor ratios
- fallback to center when no face is found

Important implementation detail:
- face-anchored zoom uses a cropped viewport on the scaled frame
- not absolute positioning on a fixed canvas
- this prevents black borders

## Cache and Build Semantics

Default build directory:

```text
<project>/build
```

Default final video:

```text
<project>/final_video.mp4
```

Cache behavior:
- `--cache` is default
- `--no-cache` forces regeneration for the current run
- phase 4 parallelism does not change cache ownership; each worker writes one scene output path
- when phase 4 ran with cache disabled, phase 5 must not silently reuse stale `scene_*_final.mp4` files

## Current Constraints

- Phase 3 visual generation is intentionally bypassed for now.
- Blank visuals are used when no asset is available.
- The active pipeline is optimized for reliable local rendering and visual-track-driven assembly, not AI generation during render.
