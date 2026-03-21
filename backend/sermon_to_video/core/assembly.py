import os
from pathlib import Path
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip

def assemble_scene(scene_data: dict, output_path: str, font_path: str = None, motion_data: dict = None) -> str:
    """
    Assembles a single scene with B-Roll, Voiceover, and precise Subtitle overlays.
    """
    audio_path = scene_data.get("audio_filepath")
    visual_path = scene_data.get("visual_filepath")
    visual_source_override = scene_data.get("visual_source")
    overlay_text = scene_data.get("overlay_text", "")
    duration_sec = scene_data.get("render_duration", scene_data.get("duration_sec", 5.0))
    overlay_start_ratio = scene_data.get("overlay_start_ratio", 0.0)
    
    # Load Media
    if visual_source_override:
        # Individual video file per scene — apply auto slow-motion to match audio duration
        source_fp = str(Path(output_path).parent / visual_source_override)
        video_clip = VideoFileClip(source_fp)
        if hasattr(video_clip, 'without_audio'):
            video_clip = video_clip.without_audio()
        else:
            video_clip = video_clip.set_audio(None)
        
        actual_duration = video_clip.duration or duration_sec
        
        # Compute speed factor: if audio is longer, slow down the video to fill it
        if actual_duration < duration_sec:
            speed_factor = actual_duration / duration_sec  # e.g. 8s video / 12s audio = 0.667x
            print(f"  🐢 Slow motion: {visual_source_override} ({actual_duration:.1f}s → {duration_sec:.1f}s, speed={speed_factor:.2f}x)")
            if hasattr(video_clip, 'with_speed_scaled'):
                video_clip = video_clip.with_speed_scaled(speed_factor)
            else:
                import moviepy.video.fx.all as vfx
                video_clip = video_clip.fx(vfx.speedx, speed_factor).with_duration(duration_sec)
        
        # If the source is longer than audio, just subclip the needed amount
        elif actual_duration > duration_sec:
            if hasattr(video_clip, 'subclipped'):
                video_clip = video_clip.subclipped(0, duration_sec)
            else:
                video_clip = video_clip.subclip(0, duration_sec)
    elif visual_path and (visual_path.endswith('.jpg') or visual_path.endswith('.png')):
        from moviepy import ImageClip
        video_clip = ImageClip(visual_path)
    elif visual_path:
        video_clip = VideoFileClip(visual_path)
    else:
        raise ValueError(f"Neither visual_filepath nor visual_source is provided for scene {scene_data.get('scene_id')}")
    
    # Force global 1080p resolution universally before compositing overlaps
    # using a professional fill-and-center-crop technique.
    try:
        # Scale height to 1080
        if hasattr(video_clip, 'resized'):
            video_clip = video_clip.resized(height=1080)
        else:
            import moviepy.video.fx.all as vfx
            video_clip = video_clip.fx(vfx.resize, height=1080)
            
        # Scale width if it's too thin
        if video_clip.size[0] < 1920:
            if hasattr(video_clip, 'resized'):
                video_clip = video_clip.resized(width=1920)
            else:
                video_clip = video_clip.fx(vfx.resize, width=1920)
                
        # Center crop to exactly 1920x1080
        if hasattr(video_clip, 'cropped'):
            video_clip = video_clip.cropped(x_center=video_clip.size[0]/2, y_center=video_clip.size[1]/2, width=1920, height=1080)
        else:
            video_clip = video_clip.fx(vfx.crop, x_center=video_clip.size[0]/2, y_center=video_clip.size[1]/2, width=1920, height=1080)
            
    except Exception as e:
        print(f"Resolution normalization failed: {e}")
        
    # If the generated video is somehow shorter/longer, force it to match audio
    video_clip = video_clip.with_duration(duration_sec)
    
    # Apply Ken Burns Zoom-In effect
    if motion_data and motion_data.get("asset_type") == "image" and motion_data.get("motion_type") == "zoom_in":
        scale_end = float(motion_data.get("scale_end", 1.05))
        
        def make_zoom_func(target_scale, duration):
            return lambda t: 1.0 + (target_scale - 1.0) * (t / duration)
            
        zoom_func = make_zoom_func(scale_end, duration_sec)
        print(f"  🔍 Applying Ken Burns Zoom-In (1.0 -> {scale_end})")
        
        if hasattr(video_clip, 'resized'):
            video_clip = video_clip.resized(zoom_func)
            video_clip = video_clip.with_position(('center', 'center'))
        else:
            import moviepy.video.fx.all as vfx
            video_clip = video_clip.fx(vfx.resize, zoom_func)
            if hasattr(video_clip, 'set_position'):
                video_clip = video_clip.set_position(('center', 'center'))
                
    # No audio added during individual scene assembly anymore.
    # The global audio track is applied at the final concatenation stage.
    clips_to_compose = [video_clip]
    
    # Subtitles Overlay
    if overlay_text:
        start_time = duration_sec * overlay_start_ratio
        
        # Resolve font
        resolved_font = "Arial"
        if font_path and os.path.exists(font_path):
            resolved_font = font_path
        else:
            import platform
            if platform.system() == "Darwin":
                mac_fallbacks = [
                    "/System/Library/Fonts/STHeiti Light.ttc",
                    "/System/Library/Fonts/PingFang.ttc",
                    "/Library/Fonts/Arial Unicode.ttf",
                    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
                ]
                for f in mac_fallbacks:
                    if os.path.exists(f):
                        resolved_font = f
                        break
        
        try:
            bg_w, bg_h = video_clip.size
            font_scale = bg_h / 1080.0
            max_text_width = int(bg_w * 0.85)

            if isinstance(overlay_text, dict):
                # Advanced rendering: Bible Verses layout
                verse_text = overlay_text.get("verse", "")
                reference_text = overlay_text.get("reference", "")
                
                v_clip = None
                r_clip = None
                
                if verse_text:
                    # Count newlines to apply generous line spacing for multi-line verses
                    n_lines = verse_text.count('\n') + 1
                    interline_spacing = int(18 * font_scale) if n_lines > 1 else 0
                    v_clip = TextClip(
                        text=verse_text, font_size=int(65*font_scale), color='white', font=resolved_font,
                        stroke_color='black', stroke_width=int(3*font_scale), method='caption', size=(max_text_width, None),
                        interline=interline_spacing
                    )
                if reference_text:
                    r_clip = TextClip(
                        text=reference_text, font_size=int(45*font_scale), color='#FFD700', font=resolved_font,
                        stroke_color='black', stroke_width=int(3*font_scale), method='label'
                    )
                
                if v_clip and r_clip:
                    # Get robust dimensions
                    v_h = v_clip.size[1] if hasattr(v_clip, 'size') else v_clip.h
                    v_w = v_clip.size[0] if hasattr(v_clip, 'size') else v_clip.w
                    r_w = r_clip.size[0] if hasattr(r_clip, 'size') else r_clip.w
                    
                    # Stack them beautifully (Verse centered, Reference right-aligned)
                    v_y = (bg_h / 2) - (v_h / 2) - int(40 * font_scale)
                    r_y = v_y + v_h + int(20 * font_scale)
                    
                    # Align the right edge of the reference to the right edge of the verse block
                    v_right_edge = (bg_w / 2) + (v_w / 2)
                    r_x = v_right_edge - r_w 
                    
                    v_clip = v_clip.with_position(('center', v_y)).with_start(start_time).with_duration(duration_sec - start_time)
                    r_clip = r_clip.with_position((r_x, r_y)).with_start(start_time).with_duration(duration_sec - start_time)
                    clips_to_compose.extend([v_clip, r_clip])
                elif v_clip:
                    v_clip = v_clip.with_position(('center', 'center')).with_start(start_time).with_duration(duration_sec - start_time)
                    clips_to_compose.append(v_clip)
                elif r_clip:
                    r_clip = r_clip.with_position(('center', 'center')).with_start(start_time).with_duration(duration_sec - start_time)
                    clips_to_compose.append(r_clip)
                    
            elif isinstance(overlay_text, str) and overlay_text.strip():
                # Detect # prefix → Title mode (large centered, like a YouTube hook title)
                is_title = overlay_text.startswith('#')
                clean_text = overlay_text.lstrip('#').strip()
                
                if is_title:
                    # TITLE mode: 130px, dead center, thicker stroke, wider wrap
                    title_font_size = int(130 * font_scale)
                    txt_clip = TextClip(
                        text=clean_text,
                        font_size=title_font_size,
                        color='white',
                        font=resolved_font,
                        stroke_color='black',
                        stroke_width=int(5 * font_scale),
                        method='caption',
                        size=(int(bg_w * 0.80), None),
                        interline=int(20 * font_scale)
                    )
                    txt_clip = txt_clip.with_position(('center', 'center')).with_start(start_time).with_duration(duration_sec - start_time)
                    clips_to_compose.append(txt_clip)
        except Exception as e:
            print(f"Warning: Failed to render TextClip. Font issue? {e}")

    # Composite everything together, enforcing global 1920x1080 canvas
    # This automatically hides any zoom overflow from Ken Burns effects while keeping texts static!
    final_clip = CompositeVideoClip(clips_to_compose, size=(1920, 1080))
    final_clip = final_clip.with_duration(duration_sec)
    
    final_clip.write_videofile(
        output_path, 
        fps=24, 
        codec="libx264", 
        audio_codec="aac",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )
    
    # Cleanup clips from memory
    video_clip.close()
    for clip in clips_to_compose[1:]:
        try:
            clip.close()
        except Exception:
            pass
    final_clip.close()
        
    return output_path
