# Sermon-to-Video Console App 🎬

A powerful terminal-based automation tool to transform sermon/Bible study transcripts into high-quality YouTube videos with AI-generated visuals, Azure TTS voiceovers, and Traditional Chinese closed captions.

## 🚀 Quick Start

### 1. Setup Environment
Ensure you have Python 3.10+ and `ffmpeg` installed on your system.
```bash
# Activate the virtual environment
source backend/.venv/bin/activate

# Install dependencies if needed
pip install -r backend/requirements.txt
```

### 2. Generate Storyboard (Phase 1)
Convert your raw markdown transcript into a structured JSON storyboard.
```bash
python -m backend.sermon_to_video.cli storyboard -i transcript.md -o storyboard.json
```
*Note: This is the **Human-in-the-Loop** stage. Open `storyboard.json` to edit prompts, overlay text, or voiceover scripts before rendering.*

### 3. Render Final Video (Phase 2-7)
Execute the full 7-phase pipeline to generate the MP4 and embedded SRT subtitles.
```bash
python -m backend.sermon_to_video.cli render -i storyboard.json -o output.mp4
```

---

## 🛠️ Advanced Features

### Title Mode (`#` Prefix)
In `storyboard.json`, prefix any `overlay_text` with a `#` to render it as a **large (130px), centered title** with thick stroke. Ideal for video hooks or section titles.
```json
"overlay_text": "#神為什麼不聽我的禱告？"
```

### Bible Verse Mode
The system automatically detects multi-line verses, applying generous line spacing (**18px interline**) and scale constraints to ensure readability.

### Selective Execution (`--start-phase`)
Skip time-consuming steps (like TTS or AI Image generation) if you are just tweaking font sizes or timings:
```bash
# Restart from Phase 4 (MoviePy Assembly)
python -m backend.sermon_to_video.cli render ... --start-phase 4
```

---

## 🏗️ The 7-Phase Pipeline
1. **Setup**: Initialize environment and read storyboard.
2. **Audio (TTS)**: Synthesize voiceover via Azure TTS.
3. **Visuals (AI)**: Generate B-Roll via Imagen 3 (Nano Banana 2).
4. **Assembly**: Render individual scenes with advanced typography.
5. **Concat**: Merge scenes into a unified video (Hard Cuts for A/V sync).
6. **SRT**: Generate Traditional Chinese Closed Caption file.
7. **Mux**: Embed the SRT into the MP4 container as softsubs.

## 📂 Project Structure
- `cli.py`: The main entry point.
- `core/audio.py`: Azure TTS integration and bookmark sync.
- `core/visual.py`: AI Image generation.
- `core/assembly.py`: MoviePy scene composition & 1080p normalization.
- `core/concat.py`: Final video merging.
- `core/subtitle.py`: opencc conversion and SRT generation.
