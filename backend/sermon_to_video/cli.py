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
from backend.sermon_to_video.core.visual import ensure_blank_visual, process_visuals_for_scenes
from backend.sermon_to_video.core.assembly import assemble_scene
from backend.sermon_to_video.core.concat import concatenate_and_cleanup
from backend.sermon_to_video.core.runtime_paths import (
    can_reuse_cache,
    resolve_render_paths,
    resolve_scene_output_for_concat,
)
from backend.sermon_to_video.core.visual_track import apply_visual_track_to_scenes

app = typer.Typer(help="Sermon-to-Video Automatic Pipeline")
console = Console()

import shutil
from backend.api.config import SERMON_TO_VIDEO_DIR

@app.command()
def storyboard(
    input_file: Path = typer.Option(..., "--input", "-i", help="Path to the transcript markdown file", exists=True),
    output_file: Optional[Path] = typer.Option(None, "--output", "-o", help="Path to output storyboard JSON file (default: project folder)")
):
    """
    Phase 1: Generates the structured storyboard JSON from a transcript text.
    Automatically creates a project folder in the data directory and copies the script.
    """
    # 1. Determine Project Directory
    project_name = input_file.stem
    project_dir = SERMON_TO_VIDEO_DIR / project_name
    
    # 2. Setup Folder and Copy Script
    with console.status(f"[bold green]Setting up project folder: {project_dir}..."):
        project_dir.mkdir(parents=True, exist_ok=True)
        target_script_path = project_dir / input_file.name
        if input_file.resolve() != target_script_path.resolve():
            shutil.copy(input_file, target_script_path)
    
    # 3. Resolve Output File
    if output_file is None:
        output_file = project_dir / "storyboard.json"
    else:
        # If user provides a custom path, ensure its parent exists
        output_file.parent.mkdir(parents=True, exist_ok=True)

    with console.status(f"[bold green]Reading input from {target_script_path}..."):
        with open(target_script_path, "r", encoding="utf-8") as f:
            raw_content = f.read()
            
    # 4. Parse Input (JSON or Text)
    if target_script_path.suffix.lower() == ".json":
        try:
            input_data = json.loads(raw_content)
        except json.JSONDecodeError as e:
            console.print(f"[bold red]Failed to parse JSON input: {e}")
            raise typer.Exit(code=1)
    else:
        # Backward compatibility for Markdown
        input_data = {
            "title": project_name,
            "script": raw_content
        }
            
    with console.status("[bold cyan]Generating storyboard via OpenAI... This might take a minute."):
        try:
            storyboard_json = generate_storyboard(input_data)
        except Exception as e:
            console.print(f"[bold red]Failed to generate storyboard: {e}")
            raise typer.Exit(code=1)
            
    with console.status(f"[bold green]Saving storyboard to {output_file}..."):
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(storyboard_json, f, indent=2, ensure_ascii=False)
            
    console.print(f"\n[bold green]Success![/bold green] Project setup at: [bold cyan]{project_dir}[/bold cyan]")
    console.print(f"Storyboard written to: [bold white]{output_file}[/bold white]")
    console.print("Please review and fine-tune the JSON via Human-in-the-Loop before proceeding to `render`.")

from backend.api.config import SERMON_TO_VIDEO_DIR

@app.command()
def render(
    project_dir: Path = typer.Option(..., "--project", "-p", help="Path to the sermon-to-video project folder", exists=True, file_okay=False, dir_okay=True),
    output_file: Optional[Path] = typer.Option(None, "--output", "-o", help="Path to the final MP4 output file"),
    font_path: Optional[Path] = typer.Option(None, "--font", help="Path to a Traditional Chinese .ttf font"),
    start_phase: int = typer.Option(1, "--start-phase", help="Phase to start from (1-7)"),
    no_ai: bool = typer.Option(True, "--no-ai", help="If True, bypass AI visual generation and use black images for missing assets"),
    scene_id: Optional[int] = typer.Option(None, "--scene-id", "-s", help="Render only a specific scene ID (Phase 4 only)"),
    use_cache: bool = typer.Option(True, "--cache/--no-cache", help="Reuse existing stage outputs when available"),
):
    """
    Phases 1-7: Renders the final video using the project directory.
    1: Local Setup, 2: Audio, 3: Visuals, 4: Assembly, 5: Concat, 6: SRT, 7: Mux
    """
    storyboard_path, build_dir, output_file = resolve_render_paths(project_dir, output_file)
    if not storyboard_path.exists():
        console.print(f"[bold red]Error: storyboard.json not found in project: {project_dir}[/bold red]")
        raise typer.Exit(code=1)

    project_dir = project_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(storyboard_path, "r", encoding="utf-8") as f:
        storyboard_data = json.load(f)

    work_dir = build_dir
    console.print(f"[dim]Using project directory: {project_dir}[/dim]")
    console.print(f"[dim]Using build directory: {work_dir}[/dim]")
    console.print(f"[dim]Cache: {'on' if use_cache else 'off'}[/dim]")
    
    # Handle both old (list) and new (dict with metadata) formats
    if isinstance(storyboard_data, dict):
        metadata = storyboard_data.get("metadata", {})
        scenes = storyboard_data.get("scenes", [])
        mode = metadata.get("mode", "Short Sermon")
    else:
        scenes = storyboard_data
        mode = "Short Sermon"

    for item in scenes:
        item["project_dir"] = str(project_dir)

    # Phase 2: Audio Synthesis
    cue_data = {}
    if start_phase <= 2:
        cue_points_file = work_dir / "cue_points.json"
        audio_filepath = work_dir / "full_audio.mp3"
        if can_reuse_cache(use_cache, audio_filepath, cue_points_file):
            with open(cue_points_file, "r", encoding="utf-8") as f:
                cue_data = json.load(f)
            console.print(f"[bold green]Phase 2 Skipped:[/bold green] Using cached audio and cue points from {work_dir}")
        else:
            with console.status("[bold yellow]Phase 2: Synthesizing Voiceover Audio with Azure TTS..."):
                storyboard_data, cue_data = process_audio_for_scenes(storyboard_data, work_dir, project_dir=project_dir)

                # Re-read if it was updated
                scenes = storyboard_data.get("scenes", []) if isinstance(storyboard_data, dict) else storyboard_data

                # Save cue data (cue_points + scene_offsets) to separate file
                with open(cue_points_file, "w", encoding="utf-8") as f:
                    json.dump(cue_data, f, indent=4, ensure_ascii=False)
                console.print(f"[bold green]Cue points saved to: {cue_points_file}[/bold green]")

                # Save storyboard with only duration_sec (clean, no runtime fields)
                with open(storyboard_path, "w", encoding="utf-8") as f:
                    json.dump(storyboard_data, f, indent=4, ensure_ascii=False)
    else:
        audio_filepath = work_dir / "full_audio.mp3"
        if not audio_filepath.exists():
            console.print("[bold red]Error: Skip-to-Phase requested but full_audio.mp3 missing![/bold red]")
            raise typer.Abort()
        # Load cue_points.json for downstream phases
        cue_points_file = work_dir / "cue_points.json"
        if cue_points_file.exists():
            with open(cue_points_file, "r", encoding="utf-8") as f:
                cue_data = json.load(f)
            console.print(f"[bold cyan]Loaded cue points from cue_points.json[/bold cyan]")

    # === Runtime merges below — these only live in memory, never saved back ===

    # Merge visual_track.json if it exists
    visual_track_file = project_dir / "visual_track.json"
    if not visual_track_file.exists():
        visual_track_file = project_dir / "visial_track.json"  # Handle typo
        
    if visual_track_file.exists():
        console.print(f"[bold cyan]Found visual track: {visual_track_file.name}, merging visual metadata...[/bold cyan]")
        with open(visual_track_file, "r", encoding="utf-8") as f:
            vt_data = json.load(f)
            apply_visual_track_to_scenes(scenes, vt_data, project_dir)

    # Inject cue data into scene items for downstream use (assembly.py needs these)
    if cue_data:
        scene_offsets = cue_data.get("scene_offsets", {})
        for item in scenes:
            sid = item.get("scene_id")
            item["storyboard_metadata"] = {"cue_points": cue_data.get("cue_points", {})}
            item["audio_start_offset"] = scene_offsets.get(str(sid), 0.0)
            item["project_dir"] = str(project_dir)
        
    # Phase 3: Visuals
    if start_phase <= 3:
        console.print(
            "[bold yellow]Phase 3 Bypassed:[/bold yellow] "
            "Visual generation is temporarily disabled; phase 4 will resolve project assets or blank fallbacks."
        )
        
    # Phase 4: Assembly
    if start_phase <= 4:
        console.print("[bold magenta]▶ Phase 4: Assembling Scenes and Syncing text...[/bold magenta]")
        if not use_cache:
            console.print("[dim]Phase 4 cache is off; selected scenes will be regenerated before concat.[/dim]")
        
        # Proactively create overlays directory
        overlay_dir = work_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        
        if True:
            motions_file = project_dir / "motions.json"
            motions_data = {}
            if motions_file.exists():
                try:
                    with open(motions_file, "r", encoding="utf-8") as f:
                        md = json.load(f)
                        for m in md.get("motions", []):
                            motions_data[m.get("scene_id")] = m
                except Exception as e:
                    console.print(f"[bold yellow]Warning: Could not parse motions.json: {e}[/bold yellow]")
                    
            for item in scenes:
                current_scene_id = item.get("scene_id")
                
                # Filter by scene_id if specified
                if scene_id is not None and current_scene_id != scene_id:
                    continue
                
                # Priority 1: assets/ folder from visual_track.json refactor
                assets_dir = project_dir / "assets"
                if assets_dir.exists() and not item.get("visual_source") and not item.get("visual_filepath"):
                    v_mp4 = assets_dir / f"scene_{current_scene_id}.mp4"
                    v_png = assets_dir / f"scene_{current_scene_id}.png"
                    if v_mp4.exists():
                        item["visual_source"] = f"assets/scene_{current_scene_id}.mp4"
                    elif v_png.exists():
                        item["visual_filepath"] = str(v_png)

                # Priority 2: Auto-discover visual_filepath if skipped Phase 3
                if not item.get("visual_filepath") and not item.get("visual_source"):
                    jpg_path = work_dir / f"scene_{current_scene_id}_visual.jpg"
                    png_path = work_dir / f"scene_{current_scene_id}_visual.png"
                    if jpg_path.exists():
                        item["visual_filepath"] = str(jpg_path)
                    elif png_path.exists():
                        item["visual_filepath"] = str(png_path)
                    else:
                        project_jpg_path = project_dir / f"scene_{current_scene_id}_visual.jpg"
                        project_png_path = project_dir / f"scene_{current_scene_id}_visual.png"
                        if project_jpg_path.exists():
                            item["visual_filepath"] = str(project_jpg_path)
                        elif project_png_path.exists():
                            item["visual_filepath"] = str(project_png_path)
                        
                scene_output = work_dir / f"scene_{current_scene_id}_final.mp4"
                if use_cache and scene_output.exists():
                    console.print(f"  -> Reusing cached scene output for Scene {current_scene_id}")
                    item["final_scene_filepath"] = str(scene_output)
                    continue

                scene_motion = motions_data.get(current_scene_id)
                
                # Extract transition to extend duration_sec
                trans_dur = 0.0
                if scene_motion and "transition" in scene_motion:
                    trans = scene_motion["transition"]
                    if trans.get("type") == "dissolve":
                        trans_dur = float(trans.get("duration", 0.0))
                
                item["transition_duration"] = trans_dur
                item["render_duration"] = item.get("duration_sec", 5.0) + trans_dur
                
                # Auto-resolve missing visual asset paths
                if not item.get("visual_filepath") and not item.get("visual_source"):
                    sb_dir = project_dir
                    assets_dir = sb_dir / "assets"
                    if assets_dir.exists():
                        # Try png for images, then mp4 for videos
                        png_path = assets_dir / f"scene_{current_scene_id}.png"
                        mp4_path = assets_dir / f"scene_{current_scene_id}.mp4"
                        jpg_path = assets_dir / f"scene_{current_scene_id}.jpg"
                        if png_path.exists():
                            item["visual_filepath"] = str(png_path)
                        elif mp4_path.exists():
                            item["visual_filepath"] = str(mp4_path)
                        elif jpg_path.exists():
                            item["visual_filepath"] = str(jpg_path)

                if not item.get("visual_filepath") and not item.get("visual_source"):
                    blank_visual_path = ensure_blank_visual(current_scene_id, work_dir)
                    item["visual_filepath"] = str(blank_visual_path)
                    console.print(f"  -> Missing visual asset for Scene {current_scene_id}, using blank fallback")
                            
                if trans_dur > 0:
                    console.print(f"  -> Assembling Scene {current_scene_id} ({item.get('duration_sec'):.2f}s + {trans_dur}s xfade padding)...")
                else:
                    console.print(f"  -> Assembling Scene {current_scene_id} ({item.get('duration_sec'):.2f}s)...")

                item["project_dir"] = str(project_dir)
                final_scene_path = assemble_scene(item, str(scene_output), font_path=str(font_path) if font_path else None, motion_data=scene_motion)
                item["final_scene_filepath"] = final_scene_path
            
    # Phase 5: Concat
    phase5_reused = False
    if start_phase <= 5:
        phase5_reused = can_reuse_cache(use_cache, output_file)
        if phase5_reused:
            console.print(f"[bold green]Phase 5 Skipped:[/bold green] Using cached final video {output_file.name}")
        else:
            with console.status("[bold green]Phase 5: Concatenating all scenes into Final Video..."):
                from backend.sermon_to_video.core.concat import concatenate_and_cleanup

                # Reattach motions_data
                motions_data = {}
                motions_path = project_dir / "motions.json"
                if motions_path.exists():
                    with open(motions_path, "r", encoding="utf-8") as f:
                        md = json.load(f)
                        for m in md.get("motions", []):
                            motions_data[m.get("scene_id")] = m

                # Resolve scenes list for iteration
                scenes_to_concat = storyboard_data.get("scenes", []) if isinstance(storyboard_data, dict) else storyboard_data

                for item in scenes_to_concat:
                    resolve_scene_output_for_concat(
                        item,
                        work_dir,
                        use_cache=use_cache,
                        phase4_ran=start_phase <= 4,
                        selected_scene_id=scene_id,
                    )

                    scene_id = item.get("scene_id")

                    # Reattach transition duration
                    scene_motion = motions_data.get(scene_id)
                    trans_dur = 0.0
                    if scene_motion and "transition" in scene_motion:
                        trans = scene_motion["transition"]
                        if trans.get("type") == "dissolve":
                            trans_dur = float(trans.get("duration", 0.0))
                    item["transition_duration"] = trans_dur

                concatenate_and_cleanup(scenes_to_concat, output_file, work_dir, project_dir=project_dir)
    
    # Phase 6: Generate Closed Captions (Traditional Chinese SRT)
    srt_path = output_file.with_suffix(".srt")
    if start_phase <= 6:
        if can_reuse_cache(use_cache, srt_path):
            console.print(f"[bold green]Phase 6 Skipped: Found existing {srt_path.name}, preserving your manual edits![/bold green]")
        else:
            with console.status("[bold yellow]Phase 6: Generating Closed Captions (Traditional Chinese)..."):
                from backend.sermon_to_video.core.subtitle import generate_srt
                generate_srt(storyboard_data, srt_path, audio_dir=work_dir)

    # Phase 7: Burn Subtitles into MP4 (Hardsubs)
    final_output = output_file
    if start_phase <= 7 and srt_path.exists():
        if phase5_reused and use_cache:
            console.print(f"[bold green]Phase 7 Skipped:[/bold green] Reusing existing output video {output_file.name}")
        else:
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
