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
    input_file: Path = typer.Option(..., "--input", "-i", help="Path to the storyboard JSON file", exists=True),
    output_file: Optional[Path] = typer.Option(None, "--output", "-o", help="Path to the final MP4 output file"),
    font_path: Optional[Path] = typer.Option(None, "--font", help="Path to a Traditional Chinese .ttf font"),
    start_phase: int = typer.Option(1, "--start-phase", "-p", help="Phase to start from (1-7)"),
    no_ai: bool = typer.Option(True, "--no-ai", help="If True, bypass AI visual generation and use black images for missing assets"),
    scene_id: Optional[int] = typer.Option(None, "--scene-id", "-s", help="Render only a specific scene ID (Phase 4 only)")
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
    
    # Handle both old (list) and new (dict with metadata) formats
    if isinstance(storyboard_data, dict):
        metadata = storyboard_data.get("metadata", {})
        scenes = storyboard_data.get("scenes", [])
        mode = metadata.get("mode", "Short Sermon")
    else:
        scenes = storyboard_data
        mode = "Short Sermon"

    # Phase 2: Audio Synthesis
    cue_data = {}
    if start_phase <= 2:
        with console.status("[bold yellow]Phase 2: Synthesizing Voiceover Audio with Azure TTS..."):
            storyboard_data, cue_data = process_audio_for_scenes(storyboard_data, work_dir)
            
            # Re-read if it was updated
            scenes = storyboard_data.get("scenes", []) if isinstance(storyboard_data, dict) else storyboard_data
            
            # Save cue data (cue_points + scene_offsets) to separate file
            cue_points_file = work_dir / "cue_points.json"
            with open(cue_points_file, "w", encoding="utf-8") as f:
                json.dump(cue_data, f, indent=4, ensure_ascii=False)
            console.print(f"[bold green]Cue points saved to: {cue_points_file}[/bold green]")
            
            # Save storyboard with only duration_sec (clean, no runtime fields)
            with open(input_file, "w", encoding="utf-8") as f:
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
    visual_track_file = work_dir / "visual_track.json"
    if not visual_track_file.exists():
        visual_track_file = work_dir / "visial_track.json"  # Handle typo
        
    if visual_track_file.exists():
        console.print(f"[bold cyan]Found visual track: {visual_track_file.name}, merging visual metadata...[/bold cyan]")
        with open(visual_track_file, "r", encoding="utf-8") as f:
            vt_data = json.load(f)
            vt_list = vt_data.get("visual_track", [])
            vt_map = {item.get("scene_id"): item for item in vt_list}
            
            for item in scenes:
                sid = item.get("scene_id")
                if sid in vt_map:
                    # Merge visual/motion/overlay data into the scene item
                    vt_item = vt_map[sid]
                    item["visual_track_metadata"] = vt_item
                    
                    # Update overlay_text if present in visual_track
                    vt_overlay = vt_item.get("overlay", {})
                    if isinstance(vt_overlay, list):
                        # List format (cued overlays) - handled in assembly.py
                        pass
                    elif isinstance(vt_overlay, dict) and vt_overlay.get("enabled", True):
                        if vt_overlay.get("kind") == "verse":
                            item["overlay_text"] = {
                                "verse": vt_overlay.get("text"),
                                "reference": vt_overlay.get("reference")
                            }
                        else:
                            item["overlay_text"] = vt_overlay.get("text")
                        item["overlay_start_ratio"] = vt_overlay.get("start_ratio", 0.0)
                    else:
                        item["overlay_text"] = None

                    # Update visual_prompt from vt_item if present
                    vt_asset = vt_item.get("asset", {})
                    if vt_asset.get("prompt"):
                        item["visual_prompt"] = vt_asset.get("prompt")

    # Inject cue data into scene items for downstream use (assembly.py needs these)
    if cue_data:
        scene_offsets = cue_data.get("scene_offsets", {})
        for item in scenes:
            sid = item.get("scene_id")
            item["storyboard_metadata"] = {"cue_points": cue_data.get("cue_points", {})}
            item["audio_start_offset"] = scene_offsets.get(str(sid), 0.0)
        
    # Phase 3: Visuals
    if start_phase <= 3:
        with console.status("[bold cyan]Phase 3: Processing Visual Assets..."):
            # Check for missing assets before calling process_visuals_for_scenes
            assets_dir = work_dir / "assets"
            for item in scenes:
                scene_id = item.get("scene_id")
                v_mp4 = assets_dir / f"scene_{scene_id}.mp4"
                v_png = assets_dir / f"scene_{scene_id}.png"
                if v_mp4.exists():
                    item["visual_source"] = f"assets/scene_{scene_id}.mp4"
                elif v_png.exists():
                    item["visual_filepath"] = str(v_png)
            
            storyboard_data = process_visuals_for_scenes(storyboard_data, work_dir, no_ai=no_ai)
            scenes = storyboard_data.get("scenes", []) if isinstance(storyboard_data, dict) else storyboard_data
        
    # Phase 4: Assembly
    if start_phase <= 4:
        console.print("[bold magenta]▶ Phase 4: Assembling Scenes and Syncing text...[/bold magenta]")
        
        # Proactively create overlays directory
        overlay_dir = work_dir / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        
        if True:
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
                    
            for item in scenes:
                current_scene_id = item.get("scene_id")
                
                # Filter by scene_id if specified
                if scene_id is not None and current_scene_id != scene_id:
                    continue
                
                # Priority 1: assets/ folder from visual_track.json refactor
                assets_dir = work_dir / "assets"
                if assets_dir.exists():
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
                        
                scene_output = work_dir / f"scene_{current_scene_id}_final.mp4"
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
                    sb_dir = Path(input_file).parent
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
                            
                if trans_dur > 0:
                    console.print(f"  -> Assembling Scene {current_scene_id} ({item.get('duration_sec'):.2f}s + {trans_dur}s xfade padding)...")
                else:
                    console.print(f"  -> Assembling Scene {current_scene_id} ({item.get('duration_sec'):.2f}s)...")
                    
                final_scene_path = assemble_scene(item, str(scene_output), font_path=str(font_path) if font_path else None, motion_data=scene_motion)
                item["final_scene_filepath"] = final_scene_path
            
    # Phase 5: Concat
    if start_phase <= 5:
        if output_file is None:
            console.print("[bold red]Error: Phase 5+ requires an output file path (-o).[/bold red]")
            raise typer.Exit(code=1)
            
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
                        
            # Resolve scenes list for iteration
            scenes_to_concat = storyboard_data.get("scenes", []) if isinstance(storyboard_data, dict) else storyboard_data
            
            for item in scenes_to_concat:
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
                    
            concatenate_and_cleanup(scenes_to_concat, output_file, work_dir)
    
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
