import os
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from typing import Any
from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, ImageClip, ColorClip
from opencc import OpenCC
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

EXEGESIS_PERSISTENT_DEFAULTS = {
    "anchor": {
        "x_ratio": 0.12,
        "y_ratio": 0.30,
        "max_width_ratio": 0.52,
    },
    "header": {
        "enabled": True,
        "style": {
            "font_size": 30,
            "line_gap": 1.15,
            "opacity": 0.78,
            "align": "c",
        },
    },
    "verse_block": {
        "style": {
            "font_size": 44,
            "line_gap": 1.2,
            "align": "left",
            "opacity": 1.0,
            "color": "#F2F2F2",
            "text_shadow": {
                "color": "rgba(0,0,0,0.28)",
                "blur": 3,
                "offset_x": 0,
                "offset_y": 2,
            },
        },
    },
    "highlight_style": {
        "weight": "bold",
        "opacity": 1.0,
        "color": "#FFFFFF",
        "scale": 1.0,
        "text_shadow": {
            "color": "rgba(0,0,0,0.38)",
            "blur": 1,
            "offset_x": 0,
            "offset_y": 2,
        },
    },
    "dim_others": {
        "enabled": True,
        "opacity": 0.52,
    },
    "behavior": {
        "mode": "persistent",
        "highlight_fade_in_sec": 0.28,
    },
    "dark_overlay": {
        "type": "gradient",
        "direction": "left_to_right",
        "start_opacity": 0.62,
        "end_opacity": 0.18,
        "width_ratio": 0.60,
        "blur": 6,
        "feather": 0.06,
    },
}
from backend.sermon_to_video.core.overlay import (
    OverlayRenderer,
    create_text_background_overlay,
    resolve_dark_overlay_config,
)
from backend.api.config import SERMON_TO_VIDEO_DIR
from backend.sermon_to_video.core.face_detection import detect_primary_face_anchor
from backend.sermon_to_video.core.visual_track import infer_overlay_type, project_overlay_for_scene

SERMON_TO_VIDEO_CONFIG_FILE = SERMON_TO_VIDEO_DIR / "config.json"


_ASSEMBLY_OPENCC = OpenCC("s2t")


def _to_traditional_text(text: str) -> str:
    if not text:
        return ""
    return _ASSEMBLY_OPENCC.convert(str(text))


def _deep_merge_dict(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


@lru_cache(maxsize=1)
def _load_exegesis_persistent_defaults() -> dict:
    defaults = deepcopy(EXEGESIS_PERSISTENT_DEFAULTS)
    try:
        if not SERMON_TO_VIDEO_CONFIG_FILE.exists():
            return defaults
        with SERMON_TO_VIDEO_CONFIG_FILE.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception as exc:
        print(f"Warning: failed to load sermon_to_video config from {SERMON_TO_VIDEO_CONFIG_FILE}: {exc}")
        return defaults

    file_defaults = payload.get("exegesis_persistent_defaults")
    if not isinstance(file_defaults, dict):
        return defaults
    return _deep_merge_dict(defaults, file_defaults)


def _normalize_exegesis_persistent_overlay_cfg(overlay_cfg: dict) -> dict:
    defaults = _load_exegesis_persistent_defaults()
    merged = _deep_merge_dict(defaults, overlay_cfg or {})

    default_highlight_style = deepcopy(defaults["highlight_style"])
    normalized_highlights = []
    for event in merged.get("highlights", []):
        normalized_event = deepcopy(event)
        normalized_event["style"] = _deep_merge_dict(
            default_highlight_style,
            normalized_event.get("style", {}),
        )
        normalized_highlights.append(normalized_event)
    merged["highlights"] = normalized_highlights
    return merged


def _update_text_bounds(bounds, x: int, y: int, w: int, h: int):
    if w <= 0 or h <= 0:
        return bounds

    x1, y1, x2, y2 = x, y, x + w, y + h
    if bounds is None:
        return [x1, y1, x2, y2]

    bounds[0] = min(bounds[0], x1)
    bounds[1] = min(bounds[1], y1)
    bounds[2] = max(bounds[2], x2)
    bounds[3] = max(bounds[3], y2)
    return bounds


def _bounds_to_box(bounds):
    if not bounds:
        return None
    return (bounds[0], bounds[1], bounds[2] - bounds[0], bounds[3] - bounds[1])

def resolve_overlay_times(overlays: list, cue_time_map: dict, scene_start_abs: float) -> list:
    """
    Maps trigger_mark to relative trigger_time for each overlay.
    """
    resolved = []
    for i, ov in enumerate(overlays):
        # Deep copy to avoid mutating original
        item = ov.copy()
        if item.get("trigger_time") is not None:
            t_rel = max(0.0, float(item["trigger_time"]))
            item["trigger_time"] = t_rel
            item["line_index"] = i
            resolved.append(item)
            continue

        mark = item.get("trigger_mark") or item.get("trigger_cue")
        
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
    dark_overlay_cfg = resolve_dark_overlay_config(cfg)
    
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
        txt = _to_traditional_text(ov.get("text", ""))
        if not txt: continue
        lines.append({
            "text": txt,
            "font": main_font,
            "trigger_time": ov.get("trigger_time", 0.0),
            "indent": ov.get("indent_level", 0),
        })
        ref = _to_traditional_text(ov.get("reference", ""))
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
        positioned_lines = []
        text_bounds = None
        
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

            positioned_lines.append((x, y, txt, font))
            text_bounds = _update_text_bounds(text_bounds, x, y, vl["text_w"], vl["text_h"])

        text_box = _bounds_to_box(text_bounds)
        if dark_overlay_cfg and text_box:
            canvas = Image.alpha_composite(
                canvas,
                create_text_background_overlay((frame_w, frame_h), text_box, **dark_overlay_cfg),
            )
            draw = ImageDraw.Draw(canvas)

        for x, y, txt, font in positioned_lines:
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


def _parse_rgba_color(color, default=(255, 255, 255, 255)) -> tuple[int, int, int, int]:
    if not color:
        return default
    if isinstance(color, (list, tuple)):
        if len(color) == 3:
            r, g, b = color
            return (int(r), int(g), int(b), default[3])
        if len(color) >= 4:
            r, g, b, a = color[:4]
            return (int(r), int(g), int(b), int(a))
        return default

    raw = str(color).strip()
    if not raw:
        return default

    if raw.startswith("#"):
        hex_color = raw[1:]
        try:
            if len(hex_color) == 6:
                return (
                    int(hex_color[0:2], 16),
                    int(hex_color[2:4], 16),
                    int(hex_color[4:6], 16),
                    default[3],
                )
            if len(hex_color) == 8:
                return (
                    int(hex_color[0:2], 16),
                    int(hex_color[2:4], 16),
                    int(hex_color[4:6], 16),
                    int(hex_color[6:8], 16),
                )
        except ValueError:
            return default

    if raw.lower().startswith("rgba(") and raw.endswith(")"):
        try:
            parts = [part.strip() for part in raw[5:-1].split(",")]
            if len(parts) == 4:
                r, g, b = (int(float(parts[idx])) for idx in range(3))
                alpha_raw = float(parts[3])
                a = int(round(alpha_raw * 255)) if alpha_raw <= 1.0 else int(round(alpha_raw))
                return (r, g, b, max(0, min(255, a)))
        except Exception:
            return default
    return default


def _scale_alpha(alpha: int, opacity: float) -> int:
    return max(0, min(255, int(round(int(alpha) * float(opacity)))))


def _scale_color_brightness(color, ratio: float):
    rgba = _parse_rgba_color(color, default=(255, 255, 255, 255))
    ratio = max(0.0, min(1.0, float(ratio)))
    return (
        int(round(rgba[0] * ratio)),
        int(round(rgba[1] * ratio)),
        int(round(rgba[2] * ratio)),
        rgba[3],
    )


def _draw_text_shadowed(draw, x: int, y: int, text: str, font, opacity: float = 1.0, fill=None):
    """Draws text with a drop shadow at pixel coords (x,y)."""
    alpha = int(opacity * 255)
    fill_rgba = _parse_rgba_color(fill, default=(255, 255, 255, 255))
    fill_alpha = _scale_alpha(fill_rgba[3], opacity)
    draw.text((x + 3, y + 3), text, font=font, fill=(0, 0, 0, int(alpha * 0.6)))
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, int(alpha * 0.45)))
    draw.text((x, y), text, font=font, fill=(fill_rgba[0], fill_rgba[1], fill_rgba[2], fill_alpha))


def _draw_text_with_shadow_style(canvas, x: int, y: int, text: str, font, opacity: float = 1.0, fill=None, shadow=None):
    from PIL import Image, ImageDraw, ImageFilter

    draw = ImageDraw.Draw(canvas)
    fill_rgba = _parse_rgba_color(fill, default=(255, 255, 255, 255))
    fill_alpha = _scale_alpha(fill_rgba[3], opacity)

    shadow_cfg = shadow or {}
    shadow_color = _parse_rgba_color(shadow_cfg.get("color"), default=(0, 0, 0, 140))
    shadow_alpha = _scale_alpha(shadow_color[3], opacity)
    offset_x = int(round(float(shadow_cfg.get("offset_x", 0))))
    offset_y = int(round(float(shadow_cfg.get("offset_y", 2))))
    blur = max(0.0, float(shadow_cfg.get("blur", 0)))

    if shadow_alpha > 0:
        shadow_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow_layer)
        shadow_draw.text(
            (x + offset_x, y + offset_y),
            text,
            font=font,
            fill=(shadow_color[0], shadow_color[1], shadow_color[2], shadow_alpha),
        )
        if blur > 0:
            shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=blur))
        canvas.alpha_composite(shadow_layer)
        draw = ImageDraw.Draw(canvas)

    draw.text((x, y), text, font=font, fill=(fill_rgba[0], fill_rgba[1], fill_rgba[2], fill_alpha))


def _compute_scaled_text_origin(base_x: int, base_y: int, base_w: int, base_h: int, scaled_w: int, scaled_h: int) -> tuple[int, int]:
    return (
        int(round(base_x - max(0, scaled_w - base_w) / 2.0)),
        int(round(base_y - max(0, scaled_h - base_h) / 2.0)),
    )


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
    dark_overlay_cfg = resolve_dark_overlay_config(overlay_cfg)

    # --- Anchor ---------------------------------------------------------------
    x0 = int(anchor.get("x_ratio", 0.12) * frame_w)
    y0 = int(anchor.get("y_ratio", 0.30) * frame_h)
    max_w = int(anchor.get("max_width_ratio", 0.65) * frame_w)

    # --- Resolve cue times for each item (relative to scene start) ------------
    resolved_items = []
    for item in items:
        if item.get("trigger_time") is not None:
            t_rel = max(0.0, float(item.get("trigger_time", 0.0)))
        else:
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

        header_text = _to_traditional_text(header_cfg.get("text", ""))
        for raw_line in _wrap_text(header_text, h_font, max_w):
            lw, lh = _measure_text(raw_line, h_font)
            header_lines.append(
                {
                    "text": raw_line,
                    "font": h_font,
                    "font_size": h_size,
                    "y": cursor_y,
                    "opacity": h_opacity,
                    "text_w": lw,
                    "text_h": lh,
                }
            )
            cursor_y += int(lh * h_gap)

        ref_text = _to_traditional_text(header_cfg.get("reference", ""))
        if ref_text:
            ref_size = max(22, int(h_size * 0.72))
            ref_font = _load_pil_font(ref_size)
            lw, lh = _measure_text(ref_text, ref_font)
            header_lines.append(
                {"text": ref_text, "font": ref_font, "y": cursor_y, "opacity": h_opacity * 0.85, "text_w": lw, "text_h": lh}
            )
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
        item_text = _to_traditional_text(res.get("text", ""))
        for raw_line in _wrap_text(item_text, i_font, max_w):
            lw, lh = _measure_text(raw_line, i_font)
            group.append(
                {
                    "text": raw_line,
                    "font": i_font,
                    "y": cursor_y,
                    "opacity": i_opacity,
                    "trigger_time": res["trigger_time"],
                    "text_w": lw,
                    "text_h": lh,
                }
            )
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
        render_lines = []
        text_bounds = None

        # Always draw header
        for hl in header_lines:
            render_lines.append((x0, hl["y"], hl["text"], hl["font"], hl["opacity"]))
            text_bounds = _update_text_bounds(text_bounds, x0, hl["y"], hl["text_w"], hl["text_h"])

        # Draw items up to this trigger
        for grp in item_line_groups:
            if not grp: continue
            if grp[0]["trigger_time"] <= t_start:
                for il in grp:
                    render_lines.append((x0, il["y"], il["text"], il["font"], il["opacity"]))
                    text_bounds = _update_text_bounds(text_bounds, x0, il["y"], il["text_w"], il["text_h"])

        text_box = _bounds_to_box(text_bounds)
        if dark_overlay_cfg and text_box:
            canvas = Image.alpha_composite(
                canvas,
                create_text_background_overlay((frame_w, frame_h), text_box, **dark_overlay_cfg),
            )
            draw = ImageDraw.Draw(canvas)

        for x, y, text, font, opacity in render_lines:
            _draw_text_shadowed(draw, x, y, text, font, opacity)

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
    dark_overlay_cfg = resolve_dark_overlay_config(overlay_cfg)

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
        if item.get("trigger_time") is not None:
            t_rel = max(0.0, float(item.get("trigger_time", 0.0)))
        else:
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
        kind = str(res.get("kind", "concept")).strip().lower()

        def _append_text_block(raw_text: str):
            if not raw_text:
                return

            is_title = raw_text.startswith("#")
            clean_text = raw_text.lstrip("#").strip() if is_title else raw_text
            if not clean_text:
                return

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

        _append_text_block(_to_traditional_text(res.get("text", "")).strip())
        _append_text_block(_to_traditional_text(res.get("text_secondary", "")).strip())
        if not group:
            item_groups.append(group)
            continue

        # Support verse reference line in multi_cue_concepts.
        if kind == "verse":
            ref_text = _to_traditional_text(res.get("reference", "")).strip()
            if ref_text:
                rw, rh = _measure_text(ref_text, verse_ref_font)
                ref_align = group[-1]["align"] if group else default_align
                group.append({
                    "text": ref_text,
                    "font": verse_ref_font,
                    "text_w": rw,
                    "text_h": rh,
                    "trigger_time": res["trigger_time"],
                    "align": ref_align,
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
        render_lines = []
        text_bounds = None

        for grp in visible_groups:
            for line in grp:
                line_align = line.get("align", default_align)
                if line_align == "center":
                    x = (frame_w - line["text_w"]) // 2
                else:
                    x = base_x
                render_lines.append((x, cursor_y, line))
                text_bounds = _update_text_bounds(text_bounds, x, cursor_y, line["text_w"], line["text_h"])
                cursor_y += int(line["text_h"] * line.get("line_gap", line_gap))

        text_box = _bounds_to_box(text_bounds)
        if dark_overlay_cfg and text_box:
            canvas = Image.alpha_composite(
                canvas,
                create_text_background_overlay((frame_w, frame_h), text_box, **dark_overlay_cfg),
            )
            draw = ImageDraw.Draw(canvas)

        for x, y, line in render_lines:
            if line.get("is_title"):
                # Stronger title rendering to match documented title card feel.
                draw.text((x + 4, y + 4), line["text"], font=line["font"], fill=(0, 0, 0, 180))
                draw.text((x + 2, y + 2), line["text"], font=line["font"], fill=(0, 0, 0, 140))
                draw.text((x, y), line["text"], font=line["font"], fill=(255, 255, 255, 255))
            else:
                _draw_text_shadowed(draw, x, y, line["text"], line["font"])

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


def _parse_highlight_term_spec(term_spec: str) -> tuple[str, int | None]:
    raw = str(term_spec or "").strip()
    if not raw.endswith("]"):
        return raw, None

    open_idx = raw.rfind("[")
    if open_idx <= 0:
        return raw, None

    occurrence_raw = raw[open_idx + 1 : -1].strip()
    if not occurrence_raw:
        return raw, None

    try:
        occurrence = int(occurrence_raw)
    except ValueError:
        return raw, None

    if occurrence == 0:
        return raw, None
    return raw[:open_idx], occurrence


def _find_all_term_occurrences(line_text: str, term_text: str) -> list[tuple[int, int]]:
    if not line_text or not term_text:
        return []

    matches = []
    start = 0
    while True:
        idx = line_text.find(term_text, start)
        if idx < 0:
            break
        matches.append((idx, idx + len(term_text)))
        start = idx + len(term_text)
    return matches


def _find_highlight_spans(line_text: str, highlight_terms: list[str]) -> list[tuple[int, int]]:
    if not line_text:
        return []

    matches = []
    for term_spec in highlight_terms:
        term_text, occurrence = _parse_highlight_term_spec(term_spec)
        if not term_text:
            continue
        term_matches = _find_all_term_occurrences(line_text, term_text)
        if not term_matches:
            continue

        if occurrence is None:
            matches.extend(term_matches)
        elif occurrence > 0:
            if occurrence <= len(term_matches):
                matches.append(term_matches[occurrence - 1])
        elif occurrence == -1:
            matches.append(term_matches[-1])

    if not matches:
        return []

    matches.sort()
    merged = []
    for start, end in matches:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not spans:
        return []
    spans = sorted(spans)
    merged = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _find_highlight_spans_across_lines(line_texts: list[str], highlight_terms: list[str]) -> list[list[tuple[int, int]]]:
    line_matches: list[list[tuple[int, int]]] = [[] for _ in line_texts]

    for term_spec in highlight_terms:
        term_text, occurrence = _parse_highlight_term_spec(term_spec)
        if not term_text:
            continue

        all_occurrences: list[tuple[int, int, int]] = []
        for line_idx, line_text in enumerate(line_texts):
            for start, end in _find_all_term_occurrences(line_text, term_text):
                all_occurrences.append((line_idx, start, end))

        if not all_occurrences:
            continue

        selected: list[tuple[int, int, int]]
        if occurrence is None:
            selected = all_occurrences
        elif occurrence > 0:
            selected = [all_occurrences[occurrence - 1]] if occurrence <= len(all_occurrences) else []
        elif occurrence == -1:
            selected = [all_occurrences[-1]]
        else:
            selected = []

        for line_idx, start, end in selected:
            line_matches[line_idx].append((start, end))

    return [_merge_spans(spans) for spans in line_matches]


def _split_line_by_highlights(line_text: str, highlight_terms: list[str]) -> list[tuple[str, bool]]:
    if not line_text:
        return []

    matches = _find_highlight_spans(line_text, highlight_terms)
    if not matches:
        return [(line_text, False)]

    parts = []
    cursor = 0
    for start, end in matches:
        if start > cursor:
            parts.append((line_text[cursor:start], False))
        parts.append((line_text[start:end], True))
        cursor = end
    if cursor < len(line_text):
        parts.append((line_text[cursor:], False))
    return [(text, is_highlight) for text, is_highlight in parts if text]


def render_exegesis_persistent(
    overlay_cfg: dict,
    duration: float,
    frame_w: int = 1920,
    frame_h: int = 1080,
) -> tuple[list, bool]:
    from PIL import Image, ImageDraw
    import tempfile

    overlay_cfg = _normalize_exegesis_persistent_overlay_cfg(overlay_cfg)
    anchor = overlay_cfg.get("anchor", {})
    header_cfg = overlay_cfg.get("header", {})
    verse_cfg = overlay_cfg.get("verse_block", {})
    behavior = overlay_cfg.get("behavior", {})
    dim_cfg = overlay_cfg.get("dim_others", {})
    dark_overlay_cfg = resolve_dark_overlay_config(overlay_cfg)

    x0 = int(anchor.get("x_ratio", 0.12) * frame_w)
    y0 = int(anchor.get("y_ratio", 0.24) * frame_h)
    max_w = int(anchor.get("max_width_ratio", 0.58) * frame_w)

    header_lines = []
    verse_lines = []
    cursor_y = y0
    text_bounds = None

    if header_cfg.get("enabled", False):
        h_style = header_cfg.get("style", {})
        h_size = int(h_style.get("font_size", 32))
        h_gap = float(h_style.get("line_gap", 1.15))
        h_opacity = float(h_style.get("opacity", 0.72))
        h_font = _load_pil_font(h_size)

        header_text = _to_traditional_text(header_cfg.get("text", ""))
        for raw_line in _wrap_text(header_text, h_font, max_w):
            lw, lh = _measure_text(raw_line, h_font)
            header_lines.append(
                {"text": raw_line, "font": h_font, "y": cursor_y, "opacity": h_opacity, "text_w": lw, "text_h": lh}
            )
            text_bounds = _update_text_bounds(text_bounds, x0, cursor_y, lw, lh)
            cursor_y += int(lh * h_gap)

        cursor_y += int(h_size * 0.45)

    verse_style = verse_cfg.get("style", {})
    verse_font_size = int(verse_style.get("font_size", 44))
    verse_gap = float(verse_style.get("line_gap", 1.18))
    verse_opacity = float(verse_style.get("opacity", 0.92))
    verse_fill = verse_style.get("color")
    verse_shadow = verse_style.get("text_shadow", {"color": "rgba(0,0,0,0.32)", "blur": 4, "offset_x": 0, "offset_y": 2})
    verse_font = _load_pil_font(verse_font_size)
    verse_text = _to_traditional_text(verse_cfg.get("text", ""))
    for raw_line in _wrap_text(verse_text, verse_font, max_w):
        lw, lh = _measure_text(raw_line, verse_font)
        verse_lines.append(
            {
                "text": raw_line,
                "font": verse_font,
                "font_size": verse_font_size,
                "y": cursor_y,
                "opacity": verse_opacity,
                "text_w": lw,
                "text_h": lh,
            }
        )
        text_bounds = _update_text_bounds(text_bounds, x0, cursor_y, lw, lh)
        cursor_y += int(lh * verse_gap)

    if not header_lines and not verse_lines:
        return [], False

    dim_enabled = bool(dim_cfg.get("enabled", False))
    dim_opacity = float(dim_cfg.get("opacity", 0.32))
    highlight_events = overlay_cfg.get("highlights", [])
    highlight_times = sorted(set(float(event.get("trigger_time", 0.0)) for event in highlight_events))
    trigger_times = [0.0]
    for trigger_time in highlight_times:
        if trigger_time > 0:
            trigger_times.append(trigger_time)
    trigger_times = sorted(set(trigger_times))

    fade_dur = float(behavior.get("highlight_fade_in_sec", 0.28))
    overlay_clips = []
    text_box = _bounds_to_box(text_bounds)

    for idx, t_start in enumerate(trigger_times):
        active_event = None
        for event in highlight_events:
            if float(event.get("trigger_time", 0.0)) <= t_start:
                active_event = event

        highlight_terms = []
        highlight_style = {}
        if active_event:
            highlight_terms = [
                _to_traditional_text(term).strip()
                for term in active_event.get("ranges", [])
                if str(term).strip()
            ]
            highlight_style = active_event.get("style", {})

        canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        if dark_overlay_cfg and text_box:
            canvas = Image.alpha_composite(
                canvas,
                create_text_background_overlay((frame_w, frame_h), text_box, **dark_overlay_cfg),
            )
            draw = ImageDraw.Draw(canvas)

        for line in header_lines:
            _draw_text_shadowed(draw, x0, line["y"], line["text"], line["font"], line["opacity"])

        highlight_opacity = float(highlight_style.get("opacity", 1.0))
        highlight_scale = max(1.0, float(highlight_style.get("scale", 1.0)))
        highlight_fill = highlight_style.get("color")
        highlight_shadow = highlight_style.get("text_shadow")
        if not highlight_shadow and active_event:
            highlight_shadow = active_event.get("text_shadow")
        if not highlight_shadow:
            highlight_shadow = verse_shadow
        normal_opacity = verse_opacity
        normal_fill = verse_fill
        if dim_enabled and highlight_terms:
            normal_fill = _scale_color_brightness(verse_fill, dim_opacity)

        line_highlight_spans = _find_highlight_spans_across_lines(
            [line["text"] for line in verse_lines],
            highlight_terms,
        )

        for line, highlight_spans in zip(verse_lines, line_highlight_spans):
            _draw_text_with_shadow_style(
                canvas,
                x0,
                line["y"],
                line["text"],
                line["font"],
                normal_opacity if highlight_terms else line["opacity"],
                fill=normal_fill if highlight_terms else verse_fill,
                shadow=verse_shadow,
            )
            if not highlight_terms or not highlight_spans:
                continue

            for start_idx, end_idx in highlight_spans:
                prefix = line["text"][:start_idx]
                segment_text = line["text"][start_idx:end_idx]
                prefix_w, _ = _measure_text(prefix, line["font"])
                seg_w, _ = _measure_text(segment_text, line["font"])
                draw_x = x0 + prefix_w
                draw_y = line["y"]

                if highlight_scale > 1.0:
                    scaled_font_size = max(line["font_size"], int(round(line["font_size"] * highlight_scale)))
                    scaled_font = _load_pil_font(scaled_font_size)
                    scaled_w, scaled_h = _measure_text(segment_text, scaled_font)
                    draw_x, draw_y = _compute_scaled_text_origin(
                        base_x=draw_x,
                        base_y=line["y"],
                        base_w=seg_w,
                        base_h=line["text_h"],
                        scaled_w=scaled_w,
                        scaled_h=scaled_h,
                    )
                    _draw_text_with_shadow_style(
                        canvas,
                        draw_x,
                        draw_y,
                        segment_text,
                        scaled_font,
                        highlight_opacity,
                        fill=highlight_fill or verse_fill,
                        shadow=highlight_shadow,
                    )
                else:
                    _draw_text_with_shadow_style(
                        canvas,
                        draw_x,
                        draw_y,
                        segment_text,
                        line["font"],
                        highlight_opacity,
                        fill=highlight_fill or verse_fill,
                        shadow=highlight_shadow,
                    )

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False, prefix=f"exegesis_t{t_start:.1f}_")
        canvas.save(tmp.name)
        tmp.close()

        if idx < len(trigger_times) - 1:
            clip_dur = trigger_times[idx + 1] - t_start
        else:
            clip_dur = duration - t_start
        clip_dur = max(0.1, clip_dur)

        clip = ImageClip(tmp.name).with_start(t_start).with_duration(clip_dur)
        clip = clip.with_position((0, 0))
        if fade_dur > 0 and hasattr(clip, "crossfadein") and t_start > 0:
            clip = clip.crossfadein(fade_dur)
        overlay_clips.append(clip)
        print(f"    ✅ exegesis_persistent snapshot t={t_start:.2f}→{t_start+clip_dur:.2f}s")

    return overlay_clips, False


def render_simple_overlay(
    scene_id: int,
    overlay_cfg: dict,
    output_path: str,
    font_path: str | None,
    duration: float,
    layer_key: str = "base",
):
    start_time = overlay_cfg.get("trigger_time")
    if start_time is None:
        start_time = float(overlay_cfg.get("start_ratio", 0.0)) * duration
    start_time = max(0.0, float(start_time))
    if start_time >= duration:
        return []

    overlay_dir = str(Path(output_path).parent / "overlays")
    renderer = OverlayRenderer(font_path=font_path)
    overlay_png = renderer.render_to_png(
        scene_id=scene_id,
        overlay_data=overlay_cfg,
        output_dir=overlay_dir,
        frame_w=1920,
        frame_h=1080,
        output_name=f"scene_{scene_id}_{layer_key}_overlay.png",
    )
    if not os.path.exists(overlay_png):
        return []

    txt_clip = ImageClip(overlay_png).with_start(start_time).with_duration(max(0.1, duration - start_time))
    txt_clip = txt_clip.with_position(("center", "center"))
    return [txt_clip]


def render_overlay_bundle(
    scene_id: int,
    overlay_cfg: dict | list | None,
    cue_map: dict,
    scene_start_abs: float,
    scene_end_abs: float,
    scene_duration: float,
    render_duration: float,
    video_clip,
    output_path: str,
    font_path: str | None,
    layer_key: str = "base",
) -> tuple[list, bool]:
    projected = project_overlay_for_scene(
        overlay_cfg,
        cue_map=cue_map,
        scene_start_abs=scene_start_abs,
        scene_end_abs=scene_end_abs,
        scene_duration=scene_duration,
    )
    if not projected:
        return [], False

    overlay_type = infer_overlay_type(projected)
    frame_w = video_clip.size[0] if hasattr(video_clip, "size") else 1920
    frame_h = video_clip.size[1] if hasattr(video_clip, "size") else 1080

    if overlay_type == "multi_layer":
        overlay_clips = []
        suppress_subtitle = False
        for idx, layer in enumerate(projected.get("layers", [])):
            layer_clips, layer_suppress = render_overlay_bundle(
                scene_id=scene_id,
                overlay_cfg=layer,
                cue_map=cue_map,
                scene_start_abs=scene_start_abs,
                scene_end_abs=scene_end_abs,
                scene_duration=scene_duration,
                render_duration=render_duration,
                video_clip=video_clip,
                output_path=output_path,
                font_path=font_path,
                layer_key=f"{layer_key}_layer{idx}",
            )
            overlay_clips.extend(layer_clips)
            suppress_subtitle = suppress_subtitle or layer_suppress
        return overlay_clips, suppress_subtitle

    if overlay_type == "definition_parallel":
        return render_definition_parallel(
            overlay_cfg=projected,
            cue_map=cue_map,
            scene_abs_start=scene_start_abs,
            duration=render_duration,
            frame_w=frame_w,
            frame_h=frame_h,
        )

    if overlay_type == "exegesis_persistent":
        return render_exegesis_persistent(
            overlay_cfg=projected,
            duration=render_duration,
            frame_w=frame_w,
            frame_h=frame_h,
        )

    if overlay_type == "multi_cue_concepts":
        return render_multi_cue_concepts(
            overlay_cfg=projected,
            cue_map=cue_map,
            scene_abs_start=scene_start_abs,
            duration=render_duration,
            frame_w=frame_w,
            frame_h=frame_h,
        )

    if overlay_type == "legacy_list":
        resolved_overlays = resolve_overlay_times(projected, cue_map, scene_start_abs)
        config = {}
        if font_path:
            config["font"] = font_path
        if resolved_overlays:
            first_ov = resolved_overlays[0]
            if "center" in first_ov.get("position", ""):
                config["center"] = True
            if first_ov.get("position") == "left-top":
                config["base_x"] = 100
                config["base_y"] = 100
            if first_ov.get("dark_overlay"):
                config["dark_overlay"] = first_ov.get("dark_overlay")
                for key in (
                    "dark_overlay_type",
                    "dark_overlay_mode",
                    "dark_overlay_opacity",
                    "dark_overlay_padding_x",
                    "dark_overlay_padding_y",
                    "dark_overlay_radius",
                    "dark_overlay_blur",
                    "dark_overlay_feather",
                    "dark_overlay_direction",
                    "dark_overlay_start_opacity",
                    "dark_overlay_end_opacity",
                    "dark_overlay_width_ratio",
                ):
                    if key in first_ov:
                        config[key] = first_ov[key]
        return render_scene_with_overlays(video_clip, resolved_overlays, config, render_duration), False

    return render_simple_overlay(
        scene_id=scene_id,
        overlay_cfg=projected,
        output_path=output_path,
        font_path=font_path,
        duration=render_duration,
        layer_key=layer_key,
    ), False

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


def _resolve_motion_anchor_ratios(anchor, clip=None, source_key: str | None = None):
    raw_anchor = str(anchor or "center").strip().lower()
    if raw_anchor == "face":
        face_anchor = _resolve_face_anchor_point(clip, source_key=source_key) if clip is not None else None
        if face_anchor is not None:
            return face_anchor[0], face_anchor[1], f"face({face_anchor[0]:.3f}, {face_anchor[1]:.3f})"
        return 0.5, 0.5, "center"

    ratio_map = {
        "center": (0.5, 0.5),
        "left": (0.0, 0.5),
        "right": (1.0, 0.5),
        "top-left": (0.0, 0.0),
        "top-center": (0.5, 0.0),
        "top-right": (1.0, 0.0),
        "bottom-left": (0.0, 1.0),
        "bottom-center": (0.5, 1.0),
        "bottom-right": (1.0, 1.0),
    }
    semantic_anchor_map = {
        "subject": "center",
        "person": "center",
    }
    normalized = semantic_anchor_map.get(raw_anchor, raw_anchor)
    if normalized in ratio_map:
        x_ratio, y_ratio = ratio_map[normalized]
        return x_ratio, y_ratio, normalized

    print(f"  ⚠️ Unsupported motion anchor '{anchor}', falling back to center")
    return 0.5, 0.5, "center"


def _resolve_face_anchor_point(clip, source_key: str | None = None):
    try:
        frame = clip.get_frame(0)
    except Exception as exc:
        print(f"  ⚠️ Face anchor frame extraction failed: {exc}")
        print("  ⚠️ face not detected, fallback to center")
        return None

    anchor_point = detect_primary_face_anchor(frame, cache_key=source_key)
    if anchor_point is None:
        print("  🙂 face detected: false")
        print("  ⚠️ face not detected, fallback to center")
        return None

    print("  🙂 face detected: true")
    print(f"  📍 anchor_point used ({anchor_point[0]:.3f}, {anchor_point[1]:.3f})")
    return anchor_point


def _build_zoom_function(scale_start: float, scale_end: float, duration: float, min_scale: float = 1.0):
    safe_start = max(min_scale, float(scale_start))
    safe_end = max(min_scale, float(scale_end))

    if duration <= 0:
        return lambda t: safe_end

    def zoom_func(t):
        progress = max(0.0, min(1.0, float(t) / duration))
        return safe_start + ((safe_end - safe_start) * progress)

    return zoom_func


def _compute_viewport_origin(
    scaled_w: int,
    scaled_h: int,
    target_w: int,
    target_h: int,
    x_ratio: float,
    y_ratio: float,
):
    x_ratio = max(0.0, min(1.0, float(x_ratio)))
    y_ratio = max(0.0, min(1.0, float(y_ratio)))
    anchor_x = scaled_w * x_ratio
    anchor_y = scaled_h * y_ratio
    left = anchor_x - (target_w / 2.0)
    top = anchor_y - (target_h / 2.0)
    max_left = max(0.0, scaled_w - target_w)
    max_top = max(0.0, scaled_h - target_h)
    clamped_left = min(max(0.0, left), max_left)
    clamped_top = min(max(0.0, top), max_top)
    return int(round(clamped_left)), int(round(clamped_top))


def _crop_viewport_frame(frame, target_w: int, target_h: int, x_ratio: float, y_ratio: float):
    frame_h, frame_w = frame.shape[:2]
    left, top = _compute_viewport_origin(frame_w, frame_h, target_w, target_h, x_ratio, y_ratio)
    right = min(frame_w, left + target_w)
    bottom = min(frame_h, top + target_h)
    cropped = frame[top:bottom, left:right]

    if cropped.shape[0] != target_h or cropped.shape[1] != target_w:
        # Safety fallback: if a weird undersized frame slips through, resize to cover.
        import cv2

        cropped = cv2.resize(cropped, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
    return cropped


def apply_ken_burns(clip, motion_data, duration, source_key: str | None = None):
    """Apply Ken Burns zoom effect to a clip."""
    if not motion_data:
        return clip

    motion_type = str(motion_data.get("type", "")).strip().lower()
    if motion_type not in {"zoom_in", "zoom_out"}:
        return clip

    scale_start = float(motion_data.get("scale_start", 1.0))
    scale_end = float(motion_data.get("scale_end", 1.05))
    anchor = motion_data.get("anchor", "center")
    frame_w, frame_h = clip.size if hasattr(clip, "size") else (1920, 1080)
    zoom_func = _build_zoom_function(scale_start, scale_end, duration, min_scale=1.0)
    x_ratio, y_ratio, normalized_anchor = _resolve_motion_anchor_ratios(anchor, clip=clip, source_key=source_key)

    print(f"  🔍 Applying Ken Burns {motion_type} ({scale_start} -> {scale_end}) at {normalized_anchor}")
    
    if hasattr(clip, "resized"):
        clip = clip.resized(zoom_func)
    elif hasattr(clip, "resize"):
        clip = clip.resize(zoom_func)
    else:
        raise RuntimeError("Clip has neither resized() nor resize()")

    clip = clip.transform(
        lambda get_frame, t: _crop_viewport_frame(get_frame(t), frame_w, frame_h, x_ratio, y_ratio),
        keep_duration=True,
    )
    return clip


def _load_visual_source_clip(source_ref: str, project_dir: Path):
    source_path = Path(source_ref)
    source_fp = str(source_path if source_path.is_absolute() else (project_dir / source_path))
    if source_fp.endswith((".jpg", ".jpeg", ".png", ".webp")):
        clip = ImageClip(source_fp)
    else:
        clip = VideoFileClip(source_fp)

    if hasattr(clip, "without_audio"):
        clip = clip.without_audio()
    else:
        clip = clip.set_audio(None)
    return clip, source_fp


def _build_video_clip_from_source(
    raw_video_clip,
    *,
    duration_sec: float,
    playback_plan: dict[str, Any] | None,
    motion_data: dict[str, Any] | None,
    source_key: str,
):
    playback_plan = playback_plan or {}
    segments = playback_plan.get("segments", [])

    if segments:
        print(f"  🎬 Executing playback plan with {len(segments)} segments...")
        normalized_source = normalize_resolution(raw_video_clip)

        segment_clips = []
        consumed_time = 0.0
        for i, seg in enumerate(segments):
            seg_type = seg.get("type", "video")
            seg_motion = seg.get("motion")
            remaining_scene_dur = max(0.0, duration_sec - consumed_time)
            source_duration = float(normalized_source.duration or 0.0)
            video_start_sec = 0.0

            if seg_type == "video":
                try:
                    video_start_sec = max(0.0, float(seg.get("start_sec", 0.0)))
                except (TypeError, ValueError):
                    video_start_sec = 0.0
                video_start_sec = min(video_start_sec, source_duration)

            if seg_type == "video":
                available_video_dur = max(0.0, source_duration - video_start_sec)
                if "range_sec" in seg:
                    try:
                        seg_dur = max(0.0, float(seg["range_sec"]))
                    except (TypeError, ValueError):
                        seg_dur = 0.0
                    seg_dur = min(seg_dur, available_video_dur)
                else:
                    seg_dur = available_video_dur
                seg_dur = min(seg_dur, remaining_scene_dur)
            elif "range_sec" in seg:
                seg_dur = float(seg["range_sec"])
            elif i == len(segments) - 1:
                seg_dur = remaining_scene_dur
            else:
                seg_dur = 0.0

            if seg_dur <= 0:
                continue

            if seg_type == "freeze":
                source = seg.get("source_frame", "first")
                t = 0 if source == "first" else normalized_source.duration - 0.1
                frame = normalized_source.get_frame(t)
                seg_clip = ImageClip(frame).with_duration(seg_dur)
                seg_source_key = f"{source_key}#freeze:{source}:{t:.2f}"
            else:
                video_end_sec = min(source_duration, video_start_sec + seg_dur)
                if hasattr(normalized_source, "subclipped"):
                    seg_clip = normalized_source.subclipped(video_start_sec, video_end_sec)
                else:
                    seg_clip = normalized_source.subclip(video_start_sec, video_end_sec)
                seg_clip = seg_clip.with_duration(seg_dur)
                seg_source_key = f"{source_key}#video:{video_start_sec:.2f}"

            if seg_motion:
                seg_clip = apply_ken_burns(seg_clip, seg_motion, seg_dur, source_key=seg_source_key)

            segment_clips.append(seg_clip)
            consumed_time += seg_dur
            if consumed_time >= duration_sec:
                break

        if not segment_clips:
            raise RuntimeError(f"Playback plan produced no segments for {source_key}")
        if len(segment_clips) == 1:
            return segment_clips[0].with_duration(duration_sec)

        from moviepy import concatenate_videoclips

        return concatenate_videoclips(segment_clips, method="compose").with_duration(duration_sec)

    video_clip = raw_video_clip
    actual_duration = video_clip.duration or duration_sec

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
        if hasattr(video_clip, "subclipped"):
            video_clip = video_clip.subclipped(0, duration_sec)
        else:
            video_clip = video_clip.subclip(0, duration_sec)

    video_clip = normalize_resolution(video_clip)
    return apply_ken_burns(video_clip, motion_data, duration_sec, source_key=source_key)


def _resolve_scene_clip_schedule(scene_data: dict[str, Any]) -> list[dict[str, Any]]:
    vt_metadata = scene_data.get("visual_track_metadata", {})
    clip_schedule = vt_metadata.get("clip_schedule") or []
    if not clip_schedule:
        return []

    scene_id = scene_data.get("scene_id")
    cue_map = scene_data.get("storyboard_metadata", {}).get("cue_points", {})
    scene_start_abs = cue_map.get(f"scene_{scene_id}", scene_data.get("audio_start_offset", 0.0))
    render_duration = float(scene_data.get("render_duration", scene_data.get("duration_sec", 5.0)))
    scene_end_abs = scene_start_abs + render_duration

    def _coerce_positive_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    entries: list[dict[str, Any]] = []
    for index, clip_entry in enumerate(clip_schedule):
        cue_key = clip_entry.get("trigger_scene_cue")
        trigger_mode = str(clip_entry.get("trigger_mode") or "").strip().lower() or None

        explicit_abs = None
        if cue_key == f"scene_{scene_id}":
            explicit_abs = scene_start_abs
        elif cue_key:
            trigger_abs = cue_map.get(cue_key)
            if trigger_abs is not None:
                explicit_abs = max(scene_start_abs, float(trigger_abs))
        elif index == 0 and trigger_mode != "after_previous":
            explicit_abs = scene_start_abs

        entries.append(
            {
                **deepcopy(clip_entry),
                "_order": index,
                "_trigger_mode": trigger_mode,
                "_explicit_abs": explicit_abs,
            }
        )

    next_explicit_abs_after: list[float | None] = [None] * len(entries)
    next_explicit_abs = None
    for index in range(len(entries) - 1, -1, -1):
        next_explicit_abs_after[index] = next_explicit_abs
        explicit_abs = entries[index].get("_explicit_abs")
        if explicit_abs is not None:
            next_explicit_abs = float(explicit_abs)

    scheduled = []
    previous_end_abs = None
    for index, clip_entry in enumerate(entries):
        trigger_mode = clip_entry.get("_trigger_mode")
        explicit_abs = clip_entry.get("_explicit_abs")

        if trigger_mode == "after_previous":
            if previous_end_abs is None:
                continue
            start_abs = previous_end_abs
        else:
            if explicit_abs is None:
                continue
            start_abs = float(explicit_abs)

        if start_abs >= scene_end_abs:
            continue

        duration_hint = _coerce_positive_float(clip_entry.get("clip_duration"))
        next_explicit_abs = next_explicit_abs_after[index]
        end_abs = scene_end_abs
        if next_explicit_abs is not None and next_explicit_abs > start_abs:
            end_abs = min(end_abs, next_explicit_abs)
        if duration_hint is not None:
            end_abs = min(end_abs, start_abs + duration_hint)

        local_duration = max(0.0, end_abs - start_abs)
        if local_duration <= 0:
            continue

        enriched = deepcopy(clip_entry)
        enriched["local_start"] = max(0.0, start_abs - scene_start_abs)
        enriched["local_duration"] = local_duration
        scheduled.append(enriched)
        previous_end_abs = end_abs

    return scheduled

def assemble_scene(scene_data: dict, output_path: str, font_path: str = None, motion_data: dict = None) -> str:
    """
    Assembles a single scene with B-Roll, Voiceover, and precise Subtitle overlays.
    Supports complex playback_plan segments (freeze, video) from visual_track.json.
    """
    audio_path = scene_data.get("audio_filepath")
    visual_path = scene_data.get("visual_filepath")
    visual_source_override = scene_data.get("visual_source")
    project_dir = Path(scene_data.get("project_dir", Path(output_path).parent))
    overlay_text = scene_data.get("overlay_text", "")
    duration_sec = scene_data.get("render_duration", scene_data.get("duration_sec", 5.0))
    overlay_start_ratio = scene_data.get("overlay_start_ratio", 0.0)
    
    # Extract visual_track metadata
    vt_metadata = scene_data.get("visual_track_metadata", {})
    vt_overlay = vt_metadata.get("overlay", {})
    vt_motion = vt_metadata.get("motion", {})
    playback_plan = vt_metadata.get("playback_plan", {})

    _scene_id = scene_data.get("scene_id")
    _sb_meta = scene_data.get("storyboard_metadata", {})
    _cue_map = _sb_meta.get("cue_points", {})
    _scene_start = _cue_map.get(
        f"scene_{_scene_id}",
        scene_data.get("audio_start_offset", 0.0),
    )
    _scene_audio_duration = float(scene_data.get("duration_sec", duration_sec))
    _scene_end = _scene_start + _scene_audio_duration

    clips_to_close = []
    scheduled_clip_entries = _resolve_scene_clip_schedule(scene_data)

    if len(scheduled_clip_entries) > 1:
        rendered_scene_segments = []
        cursor = 0.0
        for clip_entry in scheduled_clip_entries:
            asset_ref = clip_entry.get("asset_ref")
            if not asset_ref:
                raise ValueError(
                    f"Scheduled clip '{clip_entry.get('clip_id')}' for scene {_scene_id} has no resolved asset_ref"
                )

            local_start = float(clip_entry.get("local_start", 0.0))
            if local_start > cursor:
                rendered_scene_segments.append(ColorClip(size=(1920, 1080), color=(0, 0, 0)).with_duration(local_start - cursor))
                cursor = local_start

            raw_video_clip, source_key = _load_visual_source_clip(asset_ref, project_dir)
            clips_to_close.append(raw_video_clip)
            rendered_segment = _build_video_clip_from_source(
                raw_video_clip,
                duration_sec=float(clip_entry.get("local_duration", 0.0)),
                playback_plan=clip_entry.get("playback_plan", {}),
                motion_data=clip_entry.get("motion", {}) or motion_data,
                source_key=source_key,
            )
            rendered_scene_segments.append(rendered_segment)
            cursor += float(clip_entry.get("local_duration", 0.0))

        if not rendered_scene_segments:
            raise RuntimeError(f"Scene {_scene_id} clip schedule resolved to no renderable segments")
        if cursor < duration_sec:
            rendered_scene_segments.append(ColorClip(size=(1920, 1080), color=(0, 0, 0)).with_duration(duration_sec - cursor))
        from moviepy import concatenate_videoclips
        video_clip = concatenate_videoclips(rendered_scene_segments, method="compose").with_duration(duration_sec)
    else:
        source_ref = visual_source_override or visual_path
        if not source_ref and scheduled_clip_entries:
            source_ref = scheduled_clip_entries[0].get("asset_ref")
        if not source_ref:
            raise ValueError(f"Neither visual_filepath nor visual_source provided for scene {scene_data.get('scene_id')}")

        raw_video_clip, source_key = _load_visual_source_clip(str(source_ref), project_dir)
        clips_to_close.append(raw_video_clip)
        master_motion = vt_motion if vt_motion else motion_data
        video_clip = _build_video_clip_from_source(
            raw_video_clip,
            duration_sec=duration_sec,
            playback_plan=playback_plan,
            motion_data=master_motion,
            source_key=source_key,
        )

    # --- OVERLAYS & COMPOSITING ---
    clips_to_compose = [video_clip]
    
    # PHASE: Overlay dispatch
    visual_track = scene_data.get("visual_track_metadata", {})
    overlays_raw = visual_track.get("overlay")
    
    if overlays_raw:
        overlay_type = infer_overlay_type(overlays_raw)
        if overlay_type == "definition_parallel":
            print(f"  📖 Rendering definition_parallel overlay for scene {_scene_id}...")
        elif overlay_type == "exegesis_persistent":
            print(f"  📜 Rendering exegesis_persistent overlay for scene {_scene_id}...")
        elif overlay_type == "multi_cue_concepts":
            print(f"  🔄 Rendering multi_cue_concepts overlay for scene {_scene_id}...")
        elif overlay_type == "multi_layer":
            print(f"  🧩 Rendering multi_layer overlay for scene {_scene_id}...")
        elif overlay_type == "legacy_list" and isinstance(overlays_raw, list):
            print(f"  🎬 Processing {len(overlays_raw)} cued overlays...")
        else:
            print(f"  📝 Rendering simple overlay for scene {_scene_id}...")

        rendered_overlays, suppress_sub = render_overlay_bundle(
            scene_id=_scene_id,
            overlay_cfg=overlays_raw,
            cue_map=_cue_map,
            scene_start_abs=_scene_start,
            scene_end_abs=_scene_end,
            scene_duration=_scene_audio_duration,
            render_duration=duration_sec,
            video_clip=video_clip,
            output_path=output_path,
            font_path=font_path,
        )
        clips_to_compose.extend(rendered_overlays)
        scene_data["_suppress_subtitle"] = suppress_sub

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
                    "position": vt_overlay.get("position", "left-center"),
                    "dark_overlay": vt_overlay.get("dark_overlay", False),
                }
            else:
                overlay_input = {
                    "kind": vt_overlay.get("kind", "concept"),
                    "text": str(overlay_text),
                    "position": vt_overlay.get("position", "left-center"),
                    "dark_overlay": vt_overlay.get("dark_overlay", False),
                }

            for key in (
                "dark_overlay_type",
                "dark_overlay_mode",
                "dark_overlay_opacity",
                "dark_overlay_padding_x",
                "dark_overlay_padding_y",
                "dark_overlay_radius",
                "dark_overlay_blur",
                "dark_overlay_feather",
                "dark_overlay_direction",
                "dark_overlay_start_opacity",
                "dark_overlay_end_opacity",
                "dark_overlay_width_ratio",
            ):
                if key in vt_overlay:
                    overlay_input[key] = vt_overlay[key]
            
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
        video_clip.close()
        for c in clips_to_compose[1:]: c.close()
        for c in clips_to_close: c.close()
        final_clip.close()
    except: pass
        
    return output_path
