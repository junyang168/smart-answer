# Sermon-to-Video

Project-based CLI for turning a sermon or Bible-study project folder into a narrated video with scene assembly, overlays, subtitles, and final hardsubs.

## Quick Start

### Environment
Ensure Python 3.10+ and `ffmpeg` are installed.

```bash
source backend/.venv/bin/activate
pip install -r backend/requirements.txt
```

### Phase 1: Create Storyboard
Generate a project storyboard from a transcript:

```bash
python -m backend.sermon_to_video.cli storyboard -i transcript.md
```

This creates a project under `DATA_DIR/sermon_to_video/<project_name>/` and writes `storyboard.json`.

### Render
Render from a project folder, not from an individual storyboard file:

```bash
python -m backend.sermon_to_video.cli render \
  --project /opt/homebrew/var/www/church/web/data/sermon_to_video/启示
```

Default final output:

```text
<project>/final_video.mp4
```

Default intermediate build directory:

```text
<project>/build
```

## Current CLI Contract

```bash
python -m backend.sermon_to_video.cli render \
  --project <project_dir> \
  [--output <mp4>] \
  [--font <ttf>] \
  [--start-phase 1-7] \
  [--scene-id <id>] \
  [--phase4-workers <n>] \
  [--cache | --no-cache]
```

Important behavior:
- `--project` is required.
- `--output` is optional. If omitted, output defaults to `<project>/final_video.mp4`.
- `--cache` is the default.
- `--no-cache` forces regeneration for the current run instead of reusing prior stage outputs.
- `--scene-id` only narrows phase 4 scene assembly.
- `--phase4-workers` bounds phase 4 scene assembly concurrency. `1` forces sequential assembly.

## Project Layout

Authored inputs stay in the project root:

```text
<project>/
  storyboard.json
  visual_track.json
  motions.json
  assets/
  bgm.mp3 | bgm.wav
```

Generated intermediates go under `build/`:

```text
<project>/build/
  full_audio.mp3
  cue_points.json
  scene_*_visual.jpg
  scene_*_final.mp4
  overlays/
```

Global sermon-to-video defaults live in:

```text
DATA_DIR/sermon_to_video/config.json
```

At the moment this file is used for `exegesis_persistent_defaults`.

## Pipeline

### Phase 1: Storyboard
- Creates the project folder.
- Writes `storyboard.json`.

### Phase 2: Audio
- Builds Azure TTS SSML.
- Generates `build/full_audio.mp3`.
- Extracts cue timing into `build/cue_points.json`.
- Stores per-scene `duration_sec`.

### Phase 3: Visuals
- Currently bypassed in the render flow.
- Visual generation is temporarily disabled.
- Phase 4 resolves project assets, existing generated visuals, or blank fallbacks.

### Phase 4: Assembly
- Main scene render phase.
- Phase 4 scene assembly can run in parallel with a bounded worker pool via `--phase4-workers`.
- Uses 1920x1080 output.
- Renders clips as the smallest visual unit.
- Resolves clip start times from cue anchors or relative `after_previous` chaining.
- Assembles the resolved clip timeline into each scene window.
- Applies Ken Burns motion for stills.
- Supports face-aware anchor resolution.
- Projects visual-level overlays into the current scene window, then composites them above the assembled clip result.
- Subtitles remain a later burn-in stage, not part of clip scheduling.
- Writes `build/scene_<id>_final.mp4`.

### Phase 5: Concat
- Concatenates scene finals with FFmpeg.
- Supports dissolve transitions from `motions.json`.
- With `--no-cache`, concat no longer silently falls back to stale phase-4 scene finals from previous runs.

### Phase 6: SRT
- Generates or preserves `final_video.srt`.
- Existing `.srt` files are preserved when cache is on.

### Phase 7: Hardsub Burn-In
- Uses `ffmpeg -vf subtitles`.
- Burns the final SRT into the MP4.

## Visual Track Schema

The renderer now treats the new `visual_track.json` schema as first-class:

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
              "trigger_scene_cue": "scene_1",
              "duration": 10.8,
              "motion": {
                "type": "zoom_in",
                "anchor": "face"
              }
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
      "overlay": {
        "type": "multi_layer",
        "layers": []
      }
    }
  ]
}
```

Current runtime model:
- `scene` is still the audio/subtitle sync unit and the current export unit: phase 4 still writes `build/scene_<id>_final.mp4`.
- `visual` is the macro scheduling and overlay ownership unit.
- `clip` is the smallest visual render unit.
- `covered_scenes` binds one visual to multiple scenes.
- `trigger_scene_cue` starts a clip from an absolute scene/cue anchor.
- `trigger_mode: "after_previous"` starts a clip immediately after the previous resolved clip ends.
- per-scene runtime metadata now includes a cue-aware `clip_schedule`, not just one selected clip.
- visual-level `overlay` is projected into each covered scene using cue timing.

Legacy scene-based visual-track formats are still normalized through an adapter layer.

Authoring note:
- use explicit cues when the visual should sync to narration
- use `trigger_mode: "after_previous"` when a clip should simply continue the visual chain after the prior clip
- for relative chains, `duration` controls when the next `after_previous` clip starts; without `duration`, the clip extends until the next explicit cue-triggered clip or scene end

## Overlay Types

Supported overlay forms include:
- simple `verse` / `concept`
- `multi_cue_concepts`
- `definition_parallel`
- `multi_layer`
- `exegesis_persistent`

### `multi_layer`
Lets one visual own multiple independent overlay layers.

Common pattern:
- opening prompt layer as `multi_cue_concepts`
- scripture anchor layer as `exegesis_persistent`

### `exegesis_persistent`
Designed for stable scripture exposition blocks across multiple scenes.

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

Current behavior:
- stable verse block layout across scenes
- no reflow or movement between highlight changes
- highlight matching by exact substring
- occurrence targeting via suffix syntax:
  - `"父"`: all matches
  - `"父[2]"`: second match
  - `"父[-1]"`: last match
- occurrence counting works across the full rendered verse block, not just per line

### Global `exegesis_persistent` Defaults
Global defaults are loaded from:

[`config.json`](/opt/homebrew/var/www/church/web/data/sermon_to_video/config.json)

Specifically:

```json
{
  "exegesis_persistent_defaults": {
    "...": "..."
  }
}
```

These defaults currently cover:
- `anchor`
- `header.style`
- `verse_block.style`
- `highlight_style`
- `dim_others`
- `behavior`
- `dark_overlay`

Per-overlay values in `visual_track.json` still override the global defaults.

## `dark_overlay`

Keep using the term `dark_overlay`.

Supported forms:
- `dark_overlay: true`
- text-box overlay config
- frame-band gradient config

Text-box example:

```json
"dark_overlay": {
  "type": "text_box",
  "mode": "box",
  "opacity": 0.82,
  "padding_x": 44,
  "padding_y": 34,
  "radius": 20,
  "blur": 2,
  "feather": 0.02
}
```

Gradient example:

```json
"dark_overlay": {
  "type": "gradient",
  "direction": "left_to_right",
  "start_opacity": 0.62,
  "end_opacity": 0.18,
  "width_ratio": 0.6,
  "blur": 6,
  "feather": 0.06
}
```

Supported keys now include:
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

`motion.anchor = "face"` is supported for still-image Ken Burns motion.

Implementation details:
- face detection uses OpenCV Haar cascade
- detection result is cached per source image
- primary face is selected from detections
- anchor is normalized to `(x_ratio, y_ratio)`
- if no valid face is found, motion falls back to center

Important rendering detail:
- face-anchored zoom uses `resize + crop/viewport`
- it no longer uses `resize + absolute position on fixed canvas`
- this avoids black borders when the face is near the top or edge of frame

## Cache Behavior

Default is cache on:

```bash
python -m backend.sermon_to_video.cli render --project <project>
```

Disable cache with:

```bash
python -m backend.sermon_to_video.cli render --project <project> --no-cache
```

Current cache model:
- phase 2 can reuse audio and cue points
- phase 4 can reuse scene finals only when cache is on
- phase 4 parallelism does not change cache semantics; each worker still owns one scene output path
- phase 5 can reuse the final output only when cache is on
- phase 6 preserves an existing `.srt` when cache is on

Important fix:
- with `--no-cache`, phase 5 no longer silently pulls stale `build/scene_*_final.mp4` files into concat after a phase-4 run

## Notes

- Missing assets now fall back to blank visuals instead of aborting assembly.
- Project assets can be provided directly under `assets/`.
- `visial_track.json` is still accepted as a typo-compatible fallback name.
- The render path is currently optimized for deterministic local assembly, not AI image generation during render.
