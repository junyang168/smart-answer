import os
import tempfile
import json
from typing import Optional
from pathlib import Path

import typer
from rich import print
from rich.console import Console

from backend.sermon_to_video.core.storyboard import generate_storyboard
from backend.sermon_to_video.core.audio import process_audio_for_scenes
from backend.sermon_to_video.core.visual import process_visuals_for_scenes
from backend.sermon_to_video.core.assembly import assemble_scene
from backend.sermon_to_video.core.concat import concatenate_and_cleanup

app = typer.Typer(help="Sermon-to-Video Automatic Pipeline")
console = Console()

@app.command()
def storyboard(
    input_file: Path = typer.Option(..., "--input", "-i", help="Path to the transcript markdown file", exists=True),
    output_file: Path = typer.Option(..., "--output", "-o", help="Path to output storyboard JSON file")
):
    """
    Phase 1: Generates the structured storyboard JSON from a transcript text.
    """
    with console.status(f"[bold green]Reading transcript from {input_file}..."):
        with open(input_file, "r", encoding="utf-8") as f:
            transcript_text = f.read()
            
    with console.status("[bold cyan]Generating storyboard via Gemini 3... This might take a minute."):
        try:
            storyboard_json = generate_storyboard(transcript_text)
        except Exception as e:
            console.print(f"[bold red]Failed to generate storyboard: {e}")
            raise typer.Exit(code=1)
            
    with console.status(f"[bold green]Saving storyboard to {output_file}..."):
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(storyboard_json, f, indent=2, ensure_ascii=False)
            
    console.print(f"\n[bold green]Success![/bold green] Storyboard written to {output_file}")
    console.print("Please review and fine-tune the JSON via Human-in-the-Loop before proceeding to `render`.")

from backend.api.config import SERMON_TO_VIDEO_DIR

@app.command()
def render(
    input_file: Path = typer.Option(..., "--input", "-i", help="Path to the storyboard JSON file", exists=True),
    output_file: Path = typer.Option(..., "--output", "-o", help="Path to the final MP4 output file"),
    font_path: Optional[Path] = typer.Option(None, "--font", help="Path to a Traditional Chinese .ttf font"),
    start_phase: int = typer.Option(1, "--start-phase", "-p", help="Phase to start from (1-7)")
):
    """
    Phases 1-7: Renders the final video using the provided storyboard JSON.
    1: Local Setup, 2: Audio, 3: Visuals, 4: Assembly, 5: Concat, 6: SRT, 7: Mux
    """
    with open(input_file, "r", encoding="utf-8") as f:
        storyboard_data = json.load(f)
        
    # We use the parent folder of the input JSON as our work directory
    work_dir = input_file.parent.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"[dim]Using working directory: {work_dir}[/dim]")
    
    # Phase 2: Audio
    if start_phase <= 2:
        with console.status("[bold yellow]Phase 2: Synthesizing Voiceover Audio with Azure TTS..."):
            storyboard_data = process_audio_for_scenes(storyboard_data, work_dir)
    else:
        # Load pre-existing audio data if skipped
        audio_filepath = work_dir / "full_audio.mp3"
        if not audio_filepath.exists():
            console.print("[bold red]Error: Skip-to-Phase requested but full_audio.mp3 missing![/bold red]")
            raise typer.Abort()
        
    # Phase 3: Visuals
    if start_phase <= 3:
        with console.status("[bold cyan]Phase 3: Generating B-Roll Visuals (Mock)..."):
            storyboard_data = process_visuals_for_scenes(storyboard_data, work_dir)
        
    # Phase 4: Assembly
    if start_phase <= 4:
        with console.status("[bold magenta]Phase 4: Assembling Scenes and Syncing text..."):
            for item in storyboard_data:
                scene_id = item.get("scene_id")
                scene_output = work_dir / f"scene_{scene_id}_final.mp4"
                
                console.print(f"  -> Assembling Scene {scene_id} ({item.get('duration_sec')}s)...")
                final_scene_path = assemble_scene(item, str(scene_output), font_path=str(font_path) if font_path else None)
                item["final_scene_filepath"] = final_scene_path
            
    # Phase 5: Concat
    if start_phase <= 5:
        with console.status("[bold green]Phase 5: Concatenating all scenes into Final Video..."):
            concatenate_and_cleanup(storyboard_data, output_file, work_dir)
    
    # Phase 6: Generate Closed Captions (Traditional Chinese SRT)
    srt_path = output_file.with_suffix(".srt")
    if start_phase <= 6:
        with console.status("[bold yellow]Phase 6: Generating Closed Captions (Traditional Chinese)..."):
            from backend.sermon_to_video.core.subtitle import generate_srt
            generate_srt(storyboard_data, srt_path)
    
    # Phase 7: Embed Subtitles into MP4 (Softsubs)
    final_output = output_file
    if start_phase <= 7 and srt_path.exists():
        with console.status("[bold blue]Phase 7: Embedding Subtitles into MP4..."):
            import subprocess
            temp_output = output_file.parent / f"temp_{output_file.name}"
            # Mux SRT into MP4 as mov_text stream
            cmd = [
                "ffmpeg", "-y",
                "-i", str(output_file),
                "-i", str(srt_path),
                "-map", "0:v",
                "-map", "0:a",
                "-map", "1:s",
                "-c", "copy",
                "-c:s", "mov_text",
                "-metadata:s:s:0", "language=chi",
                "-metadata:s:s:0", "title=Traditional Chinese CC",
                str(temp_output)
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                temp_output.replace(output_file)
                console.print(f"🎬 [bold blue]Subtitles embedded into MP4 container![/bold blue]")
            else:
                console.print(f"⚠️ [bold red]Failed to embed subtitles:[/bold red] {result.stderr}")
            
    console.print(f"\n[bold green]Successfully Generated Video:[/bold green] {output_file}")

if __name__ == "__main__":
    app()
