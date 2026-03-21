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
            
            # CRITICAL: Save exact calculated durations to JSON so phase skipping doesn't break A/V sync!
            with open(input_file, "w", encoding="utf-8") as f:
                json.dump(storyboard_data, f, indent=4, ensure_ascii=False)
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
            motions_file = work_dir / "motions.json"
            motions_data = {}
            if motions_file.exists():
                try:
                    with open(motions_file, "r", encoding="utf-8") as f:
                        md = json.load(f)
                        for m in md.get("motions", []):
                            motions_data[m.get("scene_id")] = m
                except Exception as e:
                    console.print(f"[bold yellow]Warning: Could not parse motions.json: {e}[/bold yellow]")
                    
            for item in storyboard_data:
                scene_id = item.get("scene_id")
                
                # Auto-discover visual_filepath if skipped Phase 3
                if not item.get("visual_filepath") and not item.get("visual_source"):
                    jpg_path = work_dir / f"scene_{scene_id}_visual.jpg"
                    png_path = work_dir / f"scene_{scene_id}_visual.png"
                    if jpg_path.exists():
                        item["visual_filepath"] = str(jpg_path)
                    elif png_path.exists():
                        item["visual_filepath"] = str(png_path)
                        
                scene_output = work_dir / f"scene_{scene_id}_final.mp4"
                scene_motion = motions_data.get(scene_id)
                
                # Extract transition to extend duration_sec
                trans_dur = 0.0
                if scene_motion and "transition" in scene_motion:
                    trans = scene_motion["transition"]
                    if trans.get("type") == "dissolve":
                        trans_dur = float(trans.get("duration", 0.0))
                
                item["transition_duration"] = trans_dur
                item["render_duration"] = item.get("duration_sec", 5.0) + trans_dur
                
                if trans_dur > 0:
                    console.print(f"  -> Assembling Scene {scene_id} ({item.get('duration_sec'):.2f}s + {trans_dur}s xfade padding)...")
                else:
                    console.print(f"  -> Assembling Scene {scene_id} ({item.get('duration_sec'):.2f}s)...")
                    
                final_scene_path = assemble_scene(item, str(scene_output), font_path=str(font_path) if font_path else None, motion_data=scene_motion)
                item["final_scene_filepath"] = final_scene_path
            
    # Phase 5: Concat
    if start_phase <= 5:
        with console.status("[bold green]Phase 5: Concatenating all scenes into Final Video..."):
            from backend.sermon_to_video.core.concat import concatenate_and_cleanup
            
            # Reattach motions_data
            motions_data = {}
            motions_path = work_dir / "motions.json"
            if motions_path.exists():
                with open(motions_path, "r", encoding="utf-8") as f:
                    md = json.load(f)
                    for m in md.get("motions", []):
                        motions_data[m.get("scene_id")] = m
                        
            for item in storyboard_data:
                scene_id = item.get("scene_id")
                if 'final_scene_filepath' not in item:
                    item['final_scene_filepath'] = str(work_dir / f"scene_{scene_id}_final.mp4")
                
                # Reattach transition duration
                scene_motion = motions_data.get(scene_id)
                trans_dur = 0.0
                if scene_motion and "transition" in scene_motion:
                    trans = scene_motion["transition"]
                    if trans.get("type") == "dissolve":
                        trans_dur = float(trans.get("duration", 0.0))
                item["transition_duration"] = trans_dur
                    
            concatenate_and_cleanup(storyboard_data, output_file, work_dir)
    
    # Phase 6: Generate Closed Captions (Traditional Chinese SRT)
    srt_path = output_file.with_suffix(".srt")
    if start_phase <= 6:
        if srt_path.exists():
            console.print(f"[bold green]Phase 6 Skipped: Found existing {srt_path.name}, preserving your manual edits![/bold green]")
        else:
            with console.status("[bold yellow]Phase 6: Generating Closed Captions (Traditional Chinese)..."):
                from backend.sermon_to_video.core.subtitle import generate_srt
                generate_srt(storyboard_data, srt_path)
    
    # Phase 7: Burn Subtitles into MP4 (Hardsubs)
    final_output = output_file
    if start_phase <= 7 and srt_path.exists():
        with console.status("[bold blue]Phase 7: Burning Subtitles into MP4 (Hardsubs)..."):
            import subprocess
            import shutil
            temp_output = output_file.parent / f"temp_{output_file.name}"
            
            # Avoid ffmpeg string escaping issues by copying SRT to a simple local name
            safe_srt = srt_path.name.replace(" ", "_")
            safe_srt_path = output_file.parent / safe_srt
            if str(srt_path) != str(safe_srt_path):
                shutil.copy(srt_path, safe_srt_path)
                
            # Burn SRT into MP4 pixels
            cmd = [
                "ffmpeg", "-y",
                "-i", str(output_file.name),
                "-vf", f"subtitles={safe_srt}:force_style='Fontname=Arial,Fontsize=16,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,BorderStyle=1,Outline=1,Alignment=2,MarginV=30'",
                "-c:v", "libx264",
                "-preset", "fast",
                "-c:a", "copy",
                str(temp_output.name)
            ]
            result = subprocess.run(cmd, cwd=str(output_file.parent), capture_output=True, text=True)
            
            # Cleanup safe copy
            if str(srt_path) != str(safe_srt_path) and safe_srt_path.exists():
                safe_srt_path.unlink()
                
            if result.returncode == 0:
                temp_output.replace(output_file)
                console.print(f"🎬 [bold blue]Subtitles permanently burned into video![/bold blue]")
            else:
                console.print(f"⚠️ [bold red]Failed to burn subtitles:[/bold red] {result.stderr}")
            
    console.print(f"\n[bold green]Successfully Generated Video:[/bold green] {output_file}")

if __name__ == "__main__":
    app()
