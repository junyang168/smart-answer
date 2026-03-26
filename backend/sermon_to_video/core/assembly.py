import os
from pathlib import Path
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, ImageClip
try:
    from moviepy.video.fx import CrossFadeIn
except ImportError:
    # Older moviepy or different structure
    pass

import numpy as np

# Default Layout Config for Cue-Driven Overlays
DEFAULT_OVERLAY_CONFIG = {
    "base_x": 180,
    "base_y": 250,
    "indent_px": 100,
    "line_gap": 60,
    "font_size": 64,
    "font": "/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 
    "color": "white",
    "fade_duration": 0.3
}
from backend.sermon_to_video.core.overlay import OverlayRenderer

def resolve_overlay_times(overlays: list, cue_time_map: dict, scene_start_abs: float) -> list:
    """
    Maps trigger_mark to relative trigger_time for each overlay.
    """
    resolved = []
    for i, ov in enumerate(overlays):
        # Deep copy to avoid mutating original
        item = ov.copy()
        mark = item.get("trigger_mark")
        
        # Determine relative start time
        if not mark:
            # If no mark, default to scene start
            t_rel = 0.0
        else:
            t_abs = cue_time_map.get(mark)
            if t_abs is not None:
                t_rel = max(0.0, t_abs - scene_start_abs)
            else:
                # Fallback to start ratio if mark not found (legacy or error)
                t_rel = item.get("start_ratio", 0.0) * 5.0 # Just a guestimate if ratio used
        
        item["trigger_time"] = t_rel
        item["line_index"] = i
        resolved.append(item)
    return resolved

def render_scene_with_overlays(base_clip, overlays: list, config: dict, duration: float):
    """
    Renders cumulative overlays using Pillow (PIL) for precise text positioning.
    Each overlay is rendered onto a full 1920x1080 transparent PNG, then composited
    as an ImageClip with correct timing. This avoids MoviePy TextClip scaling issues.
    """
    from PIL import Image, ImageDraw, ImageFont
    import tempfile
    
    cfg = {**DEFAULT_OVERLAY_CONFIG, **config}
    frame_w, frame_h = base_clip.size if hasattr(base_clip, 'size') else (1920, 1080)
    is_centered = cfg.get("center", False)
    
    # Load font via Pillow
    font_path = cfg["font"]
    font_paths_to_try = [
        font_path,
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/System/Library/Fonts/ArialHB.ttc",
    ]
    
    def _load_font(size):
        for fp in font_paths_to_try:
            try:
                return ImageFont.truetype(fp, size=size)
            except Exception:
                continue
        return ImageFont.load_default()
    
    main_font = _load_font(cfg["font_size"])
    ref_font = _load_font(int(cfg["font_size"] * 0.7))
    
    # Flatten overlays into renderable lines (text + optional reference)
    lines = []
    for ov in overlays:
        txt = ov.get("text", "")
        if not txt: continue
        lines.append({
            "text": txt,
            "font": main_font,
            "trigger_time": ov.get("trigger_time", 0.0),
            "indent": ov.get("indent_level", 0),
        })
        ref = ov.get("reference")
        if ref:
            lines.append({
                "text": f"({ref})",
                "font": ref_font,
                "trigger_time": ov.get("trigger_time", 0.0),
                "indent": 0,
                "is_ref": True,
            })
    
    # Pre-calculate Y positions for all lines
    running_y = cfg["base_y"]
    for line in lines:
        # Measure text height using Pillow
        dummy_img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        dummy_draw = ImageDraw.Draw(dummy_img)
        bbox = dummy_draw.textbbox((0, 0), line["text"], font=line["font"])
        text_h = bbox[3] - bbox[1]
        text_w = bbox[2] - bbox[0]
        
        line["y"] = running_y
        line["text_h"] = text_h
        line["text_w"] = text_w
        
        gap = cfg["line_gap"] // 2 if line.get("is_ref") else cfg["line_gap"]
        running_y += text_h + gap
    
    # Group lines by trigger_time to create cumulative PNG snapshots
    # Each trigger time gets a full-frame PNG with ALL lines up to that point
    trigger_times = sorted(set(l["trigger_time"] for l in lines))
    
    overlay_clips = []
    for idx, t_start in enumerate(trigger_times):
        # Collect all lines that should be visible at this trigger time
        visible_lines = [l for l in lines if l["trigger_time"] <= t_start]
        
        # Create a full-frame transparent PNG
        canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        
        for vl in visible_lines:
            txt = vl["text"]
            font = vl["font"]
            y = vl["y"]
            indent = vl.get("indent", 0)
            
            # Calculate X position
            if is_centered:
                x = (frame_w - vl["text_w"]) // 2
            else:
                x = cfg["base_x"] + (indent * cfg["indent_px"])
            
            # Draw shadow
            draw.text((x + 3, y + 3), txt, font=font, fill=(0, 0, 0, 160))
            draw.text((x + 1, y + 1), txt, font=font, fill=(0, 0, 0, 120))
            # Draw text
            draw.text((x, y), txt, font=font, fill=(255, 255, 255, 255))
        
        # Save to temp file
        tmp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False, prefix=f"overlay_t{t_start:.1f}_")
        canvas.save(tmp_file.name)
        tmp_file.close()
        
        # Duration: from this trigger to the next trigger (or to end of scene)
        if idx < len(trigger_times) - 1:
            clip_dur = trigger_times[idx + 1] - t_start
        else:
            clip_dur = duration - t_start
        clip_dur = max(0.1, clip_dur)
        
        overlay_clip = ImageClip(tmp_file.name).with_start(t_start).with_duration(clip_dur)
        overlay_clip = overlay_clip.with_position((0, 0))  # Full-frame, positioned at origin
        
        # Apply fade-in
        if cfg["fade_duration"] > 0 and hasattr(overlay_clip, 'crossfadein'):
            overlay_clip = overlay_clip.crossfadein(cfg["fade_duration"])
        
        overlay_clips.append(overlay_clip)
        print(f"    ✅ Overlay PNG at t={t_start:.2f}s → {t_start+clip_dur:.2f}s with {len(visible_lines)} lines")
    
    return overlay_clips


# --------------------------------------------------------------------------
# definition_parallel overlay renderer
# --------------------------------------------------------------------------
FONT_PATHS = [
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/ArialHB.ttc",
]

def _load_pil_font(size: int):
    from PIL import ImageFont
    for fp in FONT_PATHS:
        try:
            return ImageFont.truetype(fp, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _measure_text(text: str, font) -> tuple[int, int]:
    """Returns (width, height) for a single line of text."""
    from PIL import Image, ImageDraw
    dummy = Image.new("RGBA", (1, 1))
    draw = ImageDraw.Draw(dummy)
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _wrap_text(text: str, font, max_width_px: int) -> list[str]:
    """Wrap text by explicit newlines first, then by width (char-by-char for CJK)."""
    lines = []
    for segment in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        segment = segment.strip()
        if not segment:
            continue

        current = ""
        for char in segment:
            test = current + char
            w, _ = _measure_text(test, font)
            if w <= max_width_px or not current:
                current = test
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)

    return lines


def _draw_text_shadowed(draw, x: int, y: int, text: str, font, opacity: float = 1.0):
    """Draws text with a drop shadow at pixel coords (x,y)."""
    alpha = int(opacity * 255)
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, int(alpha * 0.6)))
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, int(alpha * 0.45)))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))


def render_definition_parallel(
    overlay_cfg: dict,
    cue_map: dict,
    scene_abs_start: float,
    duration: float,
    frame_w: int = 1920,
    frame_h: int = 1080,
) -> tuple[list, bool]:
    """
    Renders a 'definition_parallel' overlay as a sequence of cumulative Pillow PNGs.

    Returns:
        (list of ImageClip, suppress_subtitle: bool)
        The bool is True if the first item has become visible and the
        behavior.hide_subtitle_when_overlay_active flag is set.
    """
    from PIL import Image, ImageDraw
    import tempfile

    anchor      = overlay_cfg.get("anchor", {})
    header_cfg  = overlay_cfg.get("header", {})
    items       = overlay_cfg.get("items", [])
    behavior    = overlay_cfg.get("behavior", {})

    # --- Anchor ---------------------------------------------------------------
    x0 = int(anchor.get("x_ratio", 0.12) * frame_w)
    y0 = int(anchor.get("y_ratio", 0.30) * frame_h)
    max_w = int(anchor.get("max_width_ratio", 0.65) * frame_w)

    # --- Resolve cue times for each item (relative to scene start) ------------
    resolved_items = []
    for item in items:
        cue_key = item.get("trigger_cue")
        t_abs = cue_map.get(cue_key)
        if t_abs is not None:
            t_rel = max(0.0, t_abs - scene_abs_start)
        else:
            t_rel = 0.0  # fallback: show immediately
        resolved_items.append({**item, "trigger_time": t_rel})

    # --- Build the static layout (all lines pre-measured) ---------------------
    # Header lines
    header_lines = []       # list of {text, font, y, opacity}
    cursor_y = y0

    if header_cfg.get("enabled", False):
        h_style  = header_cfg.get("style", {})
        h_size   = h_style.get("font_size", 36)
        h_gap    = h_style.get("line_gap", 1.15)
        h_opacity = h_style.get("opacity", 0.72)
        h_font   = _load_pil_font(h_size)

        for raw_line in _wrap_text(header_cfg.get("text", ""), h_font, max_w):
            _, lh = _measure_text(raw_line, h_font)
            header_lines.append({"text": raw_line, "font": h_font, "y": cursor_y, "opacity": h_opacity})
            cursor_y += int(lh * h_gap)

        ref_text = header_cfg.get("reference", "")
        if ref_text:
            ref_size = max(22, int(h_size * 0.72))
            ref_font = _load_pil_font(ref_size)
            _, lh = _measure_text(ref_text, ref_font)
            header_lines.append({"text": ref_text, "font": ref_font, "y": cursor_y, "opacity": h_opacity * 0.85})
            cursor_y += int(lh * h_gap)

        cursor_y += int(h_size * 0.6)   # fixed gap below header block

    # Item lines
    item_line_groups = []   # one group per item, each group is list of {text, font, y, opacity}
    for res in resolved_items:
        i_style   = res.get("style", {})
        i_size    = i_style.get("font_size", 56)
        i_gap     = i_style.get("line_gap", 1.22)
        i_opacity = i_style.get("opacity", 1.0)
        i_font    = _load_pil_font(i_size)

        group = []
        for raw_line in _wrap_text(res.get("text", ""), i_font, max_w):
            _, lh = _measure_text(raw_line, i_font)
            group.append({"text": raw_line, "font": i_font, "y": cursor_y, "opacity": i_opacity,
                          "trigger_time": res["trigger_time"]})
            cursor_y += int(lh * i_gap)

        item_line_groups.append(group)

    # --- Build cumulative PNG snapshots at each unique trigger time -----------
    # Always start with a header-only snapshot at t=0
    item_trigger_times = sorted(set(g[0]["trigger_time"] for g in item_line_groups if g))
    # Prepend 0.0 so the header is visible from scene start (before any items)
    trigger_times = sorted(set([0.0] + item_trigger_times))
    if not header_lines and not item_trigger_times:
        return [], False

    fade_dur    = behavior.get("fade_in_sec", 0.35)
    hide_sub    = behavior.get("hide_subtitle_when_overlay_active", False)
    first_item_t = trigger_times[0]

    overlay_clips = []
    for idx, t_start in enumerate(trigger_times):
        canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        draw   = ImageDraw.Draw(canvas)

        # Always draw header
        for hl in header_lines:
            _draw_text_shadowed(draw, x0, hl["y"], hl["text"], hl["font"], hl["opacity"])

        # Draw items up to this trigger
        for grp in item_line_groups:
            if not grp: continue
            if grp[0]["trigger_time"] <= t_start:
                for il in grp:
                    _draw_text_shadowed(draw, x0, il["y"], il["text"], il["font"], il["opacity"])

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                          prefix=f"defpar_t{t_start:.1f}_")
        canvas.save(tmp.name)
        tmp.close()

        # Clip duration: from this cue to the next (or scene end)
        if idx < len(trigger_times) - 1:
            clip_dur = trigger_times[idx + 1] - t_start
        else:
            clip_dur = duration - t_start
        clip_dur = max(0.1, clip_dur)

        clip = ImageClip(tmp.name).with_start(t_start).with_duration(clip_dur)
        clip = clip.with_position((0, 0))
        if fade_dur > 0 and hasattr(clip, "crossfadein"):
            clip = clip.crossfadein(fade_dur)

        overlay_clips.append(clip)
        print(f"    ✅ definition_parallel snapshot t={t_start:.2f}→{t_start+clip_dur:.2f}s")

    suppress_subtitle = hide_sub and len(trigger_times) > 0
    return overlay_clips, suppress_subtitle


def render_multi_cue_concepts(
    overlay_cfg: dict,
    cue_map: dict,
    scene_abs_start: float,
    duration: float,
    frame_w: int = 1920,
    frame_h: int = 1080,
) -> tuple[list, bool]:
    """
    Renders a 'multi_cue_concepts' overlay.

    Supports two modes via behavior.mode:
      - "cumulative": all triggered items stack vertically (default)
      - "replace": only the most recently triggered item is shown

    Returns:
        (list of ImageClip, suppress_subtitle: bool)
    """
    from PIL import Image, ImageDraw
    import tempfile

    items     = overlay_cfg.get("items", [])
    behavior  = overlay_cfg.get("behavior", {})
    position  = overlay_cfg.get("position", "left-center")
    mode      = behavior.get("mode", "cumulative")
    fade_dur  = behavior.get("fade_in_sec", 0.3)
    hide_sub  = behavior.get("hide_subtitle_when_overlay_active", False)

    if not items:
        return [], False

    # --- Layout defaults based on position ---
    concept_font = _load_pil_font(56)
    verse_font = _load_pil_font(54)
    verse_ref_font = _load_pil_font(34)
    title_font = _load_pil_font(max(80, int(130 * (frame_w / 1920.0))))
    line_gap = 1.3
    verse_gap = 1.35
    title_gap = 1.2

    if "center" in position and "left" not in position:
        default_align = "center"
        base_x = frame_w // 2
    else:
        default_align = "left"
        base_x = 180

    # --- Resolve cue times ---
    resolved = []
    for item in items:
        cue_key = item.get("trigger_cue")
        t_abs = cue_map.get(cue_key)
        if t_abs is not None:
            t_rel = max(0.0, t_abs - scene_abs_start)
        else:
            # Fallback for non-cue overlays in multi_cue_concepts.
            # Allows simple sequencing via start_ratio in [0,1].
            start_ratio = item.get("start_ratio", 0.0)
            try:
                t_rel = max(0.0, min(duration, float(start_ratio) * duration))
            except Exception:
                t_rel = 0.0
        resolved.append({**item, "trigger_time": t_rel})

    # --- Pre-measure all item lines (with wrapping) ---
    max_w = int(frame_w * 0.65)
    title_max_w = int(frame_w * 0.86)
    item_groups = []  # each group: list of {text, font, text_w, text_h, trigger_time, ...}
    for res in resolved:
        group = []
        raw_text = str(res.get("text", "")).strip()
        if not raw_text:
            item_groups.append(group)
            continue

        is_title = raw_text.startswith("#")
        clean_text = raw_text.lstrip("#").strip() if is_title else raw_text
        kind = str(res.get("kind", "concept")).strip().lower()

        if is_title:
            item_font = title_font
            item_align = "center"
            item_gap = title_gap
            wrap_max_w = title_max_w
        elif kind == "verse":
            item_font = verse_font
            item_align = default_align
            item_gap = verse_gap
            wrap_max_w = max_w
        else:
            item_font = concept_font
            item_align = default_align
            item_gap = line_gap
            wrap_max_w = max_w

        for raw_line in _wrap_text(clean_text, item_font, wrap_max_w):
            w, h = _measure_text(raw_line, item_font)
            group.append({
                "text": raw_line,
                "font": item_font,
                "text_w": w,
                "text_h": h,
                "trigger_time": res["trigger_time"],
                "align": item_align,
                "line_gap": item_gap,
                "is_title": is_title,
            })

        # Support verse reference line in multi_cue_concepts.
        if kind == "verse":
            ref_text = str(res.get("reference", "")).strip()
            if ref_text:
                rw, rh = _measure_text(ref_text, verse_ref_font)
                group.append({
                    "text": ref_text,
                    "font": verse_ref_font,
                    "text_w": rw,
                    "text_h": rh,
                    "trigger_time": res["trigger_time"],
                    "align": item_align,
                    "line_gap": 1.2,
                    "is_title": False,
                })
        item_groups.append(group)

    trigger_times = sorted(set(g[0]["trigger_time"] for g in item_groups if g))
    if not trigger_times:
        return [], False

    overlay_clips = []
    for idx, t_start in enumerate(trigger_times):
        # Determine which items are visible
        if mode == "replace":
            # Only the item(s) whose trigger_time == t_start
            visible_groups = [g for g in item_groups if g and g[0]["trigger_time"] == t_start]
        else:
            # Cumulative: all items triggered up to this point
            visible_groups = [g for g in item_groups if g and g[0]["trigger_time"] <= t_start]

        # Calculate total block height for vertical centering
        total_h = 0
        for grp in visible_groups:
            for line in grp:
                total_h += int(line["text_h"] * line.get("line_gap", line_gap))

        if "center" in position.split("-")[-1]:  # vertical center
            start_y = max(0, (frame_h - total_h) // 2)
        elif "top" in position:
            start_y = 100
        else:
            start_y = max(0, (frame_h - total_h) // 2)

        # Render onto canvas
        canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        cursor_y = start_y

        for grp in visible_groups:
            for line in grp:
                line_align = line.get("align", default_align)
                if line_align == "center":
                    x = (frame_w - line["text_w"]) // 2
                else:
                    x = base_x
                if line.get("is_title"):
                    # Stronger title rendering to match documented title card feel.
                    draw.text((x + 4, cursor_y + 4), line["text"], font=line["font"], fill=(0, 0, 0, 180))
                    draw.text((x + 2, cursor_y + 2), line["text"], font=line["font"], fill=(0, 0, 0, 140))
                    draw.text((x, cursor_y), line["text"], font=line["font"], fill=(255, 255, 255, 255))
                else:
                    _draw_text_shadowed(draw, x, cursor_y, line["text"], line["font"])
                cursor_y += int(line["text_h"] * line.get("line_gap", line_gap))

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False,
                                          prefix=f"mcc_{mode}_t{t_start:.1f}_")
        canvas.save(tmp.name)
        tmp.close()

        # Clip duration
        if idx < len(trigger_times) - 1:
            clip_dur = trigger_times[idx + 1] - t_start
        else:
            clip_dur = duration - t_start
        clip_dur = max(0.1, clip_dur)

        clip = ImageClip(tmp.name).with_start(t_start).with_duration(clip_dur)
        clip = clip.with_position((0, 0))
        if fade_dur > 0 and hasattr(clip, "crossfadein"):
            clip = clip.crossfadein(fade_dur)

        overlay_clips.append(clip)
        n_visible = sum(len(g) for g in visible_groups)
        print(f"    ✅ multi_cue_concepts ({mode}) t={t_start:.2f}→{t_start+clip_dur:.2f}s, {n_visible} lines")

    suppress_subtitle = hide_sub and len(trigger_times) > 0
    return overlay_clips, suppress_subtitle

def normalize_resolution(clip, target_w=1920, target_h=1080):
    """Force 1920x1080 resolution with center-crop."""
    try:
        # Scale to match height
        if hasattr(clip, "resized"):
            clip = clip.resized(height=target_h)
        elif hasattr(clip, "resize"):
            clip = clip.resize(height=target_h)
        else:
            raise RuntimeError("Clip has neither resized() nor resize()")
            
        # Scale width if too thin
        if clip.size[0] < target_w:
            if hasattr(clip, "resized"):
                clip = clip.resized(width=target_w)
            elif hasattr(clip, "resize"):
                clip = clip.resize(width=target_w)
            else:
                raise RuntimeError("Clip has neither resized() nor resize()")
                
        # Center crop
        if hasattr(clip, "cropped"):
            clip = clip.cropped(
                x_center=clip.size[0] / 2,
                y_center=clip.size[1] / 2,
                width=target_w,
                height=target_h,
            )
        elif hasattr(clip, "crop"):
            clip = clip.crop(
                x_center=clip.size[0] / 2,
                y_center=clip.size[1] / 2,
                width=target_w,
                height=target_h,
            )
        else:
            raise RuntimeError("Clip has neither cropped() nor crop()")
    except Exception as e:
        print(f"  ⚠️ Resolution normalization warning: {e}")
    return clip

def apply_ken_burns(clip, motion_data, duration):
    """Apply Ken Burns zoom effect to a clip."""
    if not motion_data or motion_data.get("type") != "zoom_in":
        return clip
        
    scale_start = float(motion_data.get("scale_start", 1.0))
    scale_end = float(motion_data.get("scale_end", 1.05))
    anchor = motion_data.get("anchor", 'center')
    
    def make_zoom_func(s_start, s_end, d):
        return lambda t: s_start + (s_end - s_start) * (t / d)
        
    zoom_func = make_zoom_func(scale_start, scale_end, duration)
    print(f"  🔍 Applying Ken Burns Zoom ({scale_start} -> {scale_end}) at {anchor}")
    
    if hasattr(clip, "resized"):
        clip = clip.resized(zoom_func)
        clip = clip.with_position((anchor, "center") if anchor != "center" else "center")
    elif hasattr(clip, "resize"):
        clip = clip.resize(zoom_func)
        if hasattr(clip, "set_position"):
            clip = clip.set_position((anchor, "center") if anchor != "center" else "center")
        else:
            clip = clip.with_position((anchor, "center") if anchor != "center" else "center")
    else:
        raise RuntimeError("Clip has neither resized() nor resize()")
    return clip

def assemble_scene(scene_data: dict, output_path: str, font_path: str = None, motion_data: dict = None) -> str:
    """
    Assembles a single scene with B-Roll, Voiceover, and precise Subtitle overlays.
    Supports complex playback_plan segments (freeze, video) from visual_track.json.
    """
    audio_path = scene_data.get("audio_filepath")
    visual_path = scene_data.get("visual_filepath")
    visual_source_override = scene_data.get("visual_source")
    overlay_text = scene_data.get("overlay_text", "")
    duration_sec = scene_data.get("render_duration", scene_data.get("duration_sec", 5.0))
    overlay_start_ratio = scene_data.get("overlay_start_ratio", 0.0)
    
    # Extract visual_track metadata
    vt_metadata = scene_data.get("visual_track_metadata", {})
    vt_overlay = vt_metadata.get("overlay", {})
    vt_motion = vt_metadata.get("motion", {})
    playback_plan = vt_metadata.get("playback_plan", {})
    segments = playback_plan.get("segments", [])
    
    # Load Master Video Clip
    raw_video_clip = None
    if visual_source_override:
        source_fp = str(Path(output_path).parent / visual_source_override)
        raw_video_clip = VideoFileClip(source_fp)
    elif visual_path and (visual_path.endswith('.jpg') or visual_path.endswith('.png')):
        raw_video_clip = ImageClip(visual_path)
    elif visual_path:
        raw_video_clip = VideoFileClip(visual_path)
    else:
        raise ValueError(f"Neither visual_filepath nor visual_source provided for scene {scene_data.get('scene_id')}")

    # Remove audio from source video
    if hasattr(raw_video_clip, 'without_audio'):
        raw_video_clip = raw_video_clip.without_audio()
    else:
        raw_video_clip = raw_video_clip.set_audio(None)

    # --- THE CORE ASSEMBLY LOGIC ---
    if segments:
        print(f"  🎬 Executing playback plan with {len(segments)} segments...")
        # Normalize resolution BEFORE segmenting/frame-extraction
        normalized_source = normalize_resolution(raw_video_clip)
        
        segment_clips = []
        consumed_time = 0.0
        
        for i, seg in enumerate(segments):
            seg_type = seg.get("type", "video")
            seg_motion = seg.get("motion")
            
            # Determine segment duration
            if "range_sec" in seg:
                seg_dur = float(seg["range_sec"])
            elif i == len(segments) - 1:
                # Last segment fills the remaining time
                seg_dur = max(0.0, duration_sec - consumed_time)
            else:
                # Fallback for video segments without explicit range
                if seg_type == "video":
                    seg_dur = min(normalized_source.duration, max(0.0, duration_sec - consumed_time))
                else:
                    seg_dur = 0.0
            
            if seg_dur <= 0: continue
            
            # Create segment clip
            if seg_type == "freeze":
                source = seg.get("source_frame", "first")
                t = 0 if source == "first" else normalized_source.duration - 0.1
                frame = normalized_source.get_frame(t)
                seg_clip = ImageClip(frame).with_duration(seg_dur)
            else: # video playback
                # Play from the beginning up to the duration
                if hasattr(normalized_source, 'subclipped'):
                    seg_clip = normalized_source.subclipped(0, min(normalized_source.duration, seg_dur))
                else:
                    seg_clip = normalized_source.subclip(0, min(normalized_source.duration, seg_dur))
                seg_clip = seg_clip.with_duration(seg_dur)
            
            # Apply individual segment motion
            if seg_motion:
                seg_clip = apply_ken_burns(seg_clip, seg_motion, seg_dur)
            
            segment_clips.append(seg_clip)
            consumed_time += seg_dur
            if consumed_time >= duration_sec: break

        from moviepy import concatenate_videoclips
        video_clip = concatenate_videoclips(segment_clips)
        # Final safety duration check
        video_clip = video_clip.with_duration(duration_sec)
    else:
        # LEGACY / DEFAULT: Simple single-clip assembly
        video_clip = raw_video_clip
        actual_duration = video_clip.duration or duration_sec
        
        # Match audio duration
        if actual_duration < duration_sec:
            speed_factor = actual_duration / duration_sec
            print(f"  🐢 Auto-slow motion: {actual_duration:.1f}s → {duration_sec:.1f}s")
            if hasattr(video_clip, "with_speed_scaled"):
                video_clip = video_clip.with_speed_scaled(speed_factor)
            elif hasattr(video_clip, "fx"):
                from moviepy.video.fx.speedx import speedx
                video_clip = video_clip.fx(speedx, speed_factor).with_duration(duration_sec)
            else:
                raise RuntimeError("Clip has neither with_speed_scaled() nor fx()")
        elif actual_duration > duration_sec:
            if hasattr(video_clip, 'subclipped'):
                video_clip = video_clip.subclipped(0, duration_sec)
            else:
                video_clip = video_clip.subclip(0, duration_sec)
        
        # Normalize and apply master motion
        video_clip = normalize_resolution(video_clip)
        master_motion = vt_motion if vt_motion else motion_data
        video_clip = apply_ken_burns(video_clip, master_motion, duration_sec)

    # --- OVERLAYS & COMPOSITING ---
    clips_to_compose = [video_clip]
    
    # PHASE: Overlay dispatch
    visual_track = scene_data.get("visual_track_metadata", {})
    overlays_raw = visual_track.get("overlay")
    
    _scene_id    = scene_data.get("scene_id")
    _sb_meta     = scene_data.get("storyboard_metadata", {})
    _cue_map     = _sb_meta.get("cue_points", {})
    _scene_start = _cue_map.get(f"scene_{_scene_id}",
                       scene_data.get("audio_start_offset", 0.0))
    
    # ── definition_parallel (new structured overlay type) ───────────────────
    if (isinstance(overlays_raw, dict)
            and overlays_raw.get("enabled", True)
            and overlays_raw.get("type") == "definition_parallel"):
        print(f"  📖 Rendering definition_parallel overlay for scene {_scene_id}...")
        _fw = video_clip.size[0] if hasattr(video_clip, "size") else 1920
        _fh = video_clip.size[1] if hasattr(video_clip, "size") else 1080
        dp_clips, suppress_sub = render_definition_parallel(
            overlay_cfg=overlays_raw,
            cue_map=_cue_map,
            scene_abs_start=_scene_start,
            duration=duration_sec,
            frame_w=_fw,
            frame_h=_fh,
        )
        clips_to_compose.extend(dp_clips)
        scene_data["_suppress_subtitle"] = suppress_sub
    
    # ── multi_cue_concepts (replace / cumulative text overlays) ──────────────
    elif (isinstance(overlays_raw, dict)
            and overlays_raw.get("enabled", True)
            and overlays_raw.get("type") == "multi_cue_concepts"):
        print(f"  🔄 Rendering multi_cue_concepts overlay for scene {_scene_id}...")
        _fw = video_clip.size[0] if hasattr(video_clip, "size") else 1920
        _fh = video_clip.size[1] if hasattr(video_clip, "size") else 1080
        mcc_clips, suppress_sub = render_multi_cue_concepts(
            overlay_cfg=overlays_raw,
            cue_map=_cue_map,
            scene_abs_start=_scene_start,
            duration=duration_sec,
            frame_w=_fw,
            frame_h=_fh,
        )
        clips_to_compose.extend(mcc_clips)
        scene_data["_suppress_subtitle"] = suppress_sub
    
    # ── legacy list-of-cues overlays ────────────────────────────────────────
    elif overlays_raw and isinstance(overlays_raw, list):
        print(f"  🎬 Processing {len(overlays_raw)} cued overlays...")
        resolved_overlays = resolve_overlay_times(overlays_raw, _cue_map, _scene_start)
        config = {}
        if font_path:
            config["font"] = font_path
        first_ov = overlays_raw[0]
        if "center" in first_ov.get("position", ""):
            config["center"] = True
        if first_ov.get("position") == "left-top":
            config["base_x"] = 100
            config["base_y"] = 100
        text_clips = render_scene_with_overlays(video_clip, resolved_overlays, config, duration_sec)
        clips_to_compose.extend(text_clips)
    
    elif overlay_text:
        # LEGACY: Simple single-overlay logic
        start_time = duration_sec * overlay_start_ratio
        overlay_dir = str(Path(output_path).parent / "overlays")
        try:
            renderer = OverlayRenderer(font_path=font_path)
            overlay_input = {}
            if isinstance(overlay_text, dict):
                overlay_input = {
                    "kind": vt_overlay.get("kind", "verse"),
                    "text": overlay_text.get("verse", ""),
                    "reference": overlay_text.get("reference", ""),
                    "position": vt_overlay.get("position", "left-center")
                }
            else:
                overlay_input = {
                    "kind": vt_overlay.get("kind", "concept"),
                    "text": str(overlay_text),
                    "position": vt_overlay.get("position", "left-center")
                }
            
            overlay_png = renderer.render_to_png(
                scene_id=scene_data.get("scene_id", 0),
                overlay_data=overlay_input,
                output_dir=overlay_dir,
                frame_w=1920, frame_h=1080
            )
            
            if os.path.exists(overlay_png):
                txt_clip = ImageClip(overlay_png).with_start(start_time).with_duration(duration_sec - start_time)
                txt_clip = txt_clip.with_position(('center', 'center'))
                clips_to_compose.append(txt_clip)
        except Exception as e:
            print(f"  ⚠️ Overlay warning: {e}")

    final_clip = CompositeVideoClip(clips_to_compose, size=(1920, 1080)).with_duration(duration_sec)
    
    final_clip.write_videofile(
        output_path, fps=24, codec="libx264", audio_codec="aac",
        ffmpeg_params=["-pix_fmt", "yuv420p"]
    )
    
    # Cleanup
    try:
        raw_video_clip.close()
        video_clip.close()
        for c in clips_to_compose[1:]: c.close()
        final_clip.close()
    except: pass
        
    return output_path
