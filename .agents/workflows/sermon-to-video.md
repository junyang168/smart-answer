---
description: Sermon-to-Video Workflow and Architecture
---

# Project: Sermon-to-Video (查经视频自动化生成工具)

## 1. Project Overview
A pure Python Console App. Core functionality: Read a literal transcript (markdown) of a Bible study/sermon and automatically generate an MP4 video with voiceover, imagery B-Roll, and precise traditional Chinese subtitles, ready for YouTube publishing via a series of API pipelines.

## 2. Core Architecture Principle: Audio-Driven Pipeline
**Absolute Constraint**: Must use an "Audio-Driven" architecture. The system first generates TTS audio, extracts the exact physical duration (`duration_sec`), and passes this duration as a strict parameter to downstream video generation and MoviePy synthesis modules.

## 3. Tech Stack
*   **LLM**: Gemini 3.1 for Storyboard Generation.
*   **TTS**: Azure TTS (SSML-based with bookmark sync).
*   **Visuals**: Google Imagen 3 (Nano Banana 2) or local Video files.
*   **CLI**: `typer` + `rich`.
*   **Core Processing**: `moviepy` (Assembly), `opencc` (Traditional Chinese conversion), `ffmpeg` (Muxing).

## 4. Functional Specification (The 7-Phase Pipeline)

### Phase 1: Local Setup & Storyboard
- Reads `storyboard.json`.
- Supports **Continuous Slicing**: If `visual_source` points to a long video (e.g., `S18.mp4`), the system sub-clips it based on accumulated scene durations.

### Phase 2: Audio Synthesis (The Anchor)
- Generates `full_audio.mp3` and `full_sermon.ssml`.
- Calculates precise `duration_sec` for each scene via Azure bookmark events.

### Phase 3: Visual Generation
- Generates Images/Videos for each scene.
- Automatically handles **Slow Motion**: If a video source is shorter than its audio, it is slowed down to match exactly.

### Phase 4: Assembly & Advanced Typography
- **Resolution**: Enforces global **1920x1080** (Full HD) via aspect-ratio-preserving center-crop.
- **Title Mode**: Text prefixed with `#` (e.g., `"#Welcome"`) renders as a **130px** centered title with thick stroke.
- **Bible Verse Mode**: Detects multi-line verses, providing **18px interline** spacing and auto-scaling.
- **Subtitles**: Standard 70px centered captions for regular overlay text.

### Phase 5: Final Concatenation
- Joins all scenes using **Hard Cuts** to preserve A/V sync (Dissolve crossfades are avoided to prevent progressive audio drift).

### Phase 6: Closed Captioning (SRT)
- Uses `opencc` to convert voiceover text to **Traditional Chinese**.
- Generates a `.srt` sidecar file with timestamps synced to the master audio.

### Phase 7: Subtitle Muxing
- Uses `ffmpeg` to embed the SRT as a **Soft Subtitle Track** (mov_text) into the MP4 container.

## 5. Usage & Selective Execution
The `render` command supports skipping to specific phases:
```bash
python -m backend.sermon_to_video.cli render -i <json> -o <mp4> --start-phase 4
```
- **Use Phase 4** to fix typos or font styles without re-generating audio/images.
- **Use Phase 6** to regenerate only the CC/SRT files.

