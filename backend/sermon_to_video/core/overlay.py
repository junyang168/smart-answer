import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from PIL import Image, ImageDraw, ImageFont
from opencc import OpenCC

# =========================
# Config & Typography
# =========================

DEFAULT_FRAME_WIDTH = 1920
DEFAULT_FRAME_HEIGHT = 1080

SAFE_ZONES = {
    # x1, y1, x2, y2 in normalized coordinates
    "left-center": (0.06, 0.25, 0.42, 0.72),
    "left-lower": (0.06, 0.58, 0.42, 0.90),
    "bottom-left": (0.06, 0.68, 0.42, 0.93),
    "center": (0.20, 0.30, 0.80, 0.75),
    "right-center": (0.58, 0.25, 0.92, 0.72),
}

TYPOGRAPHY = {
    "concept": {
        "font_size": 68,
        "weight": "bold",
        "line_spacing": 1.25,
        "reference_font_size": None,
        "max_chars_per_line": 10,
    },
    "verse": {
        "font_size": 54,
        "weight": "regular",
        "line_spacing": 1.35,
        "reference_font_size": 34,
        "max_chars_per_line": 14,
    },
    "question": {
        "font_size": 62,
        "weight": "medium",
        "line_spacing": 1.25,
        "reference_font_size": None,
        "max_chars_per_line": 12,
    },
}

TEXT_FILL = (255, 255, 255, 255)
REF_FILL = (240, 240, 240, 240)
SHADOW_FILL = (0, 0, 0, 140)

DARK_OVERLAY_DEFAULTS = {
    "mode": "gradient_left",
    "opacity": 0.4,
    "padding_x": 24,
    "padding_y": 18,
    "radius": 12,
}


def resolve_dark_overlay_config(overlay_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(overlay_data, dict):
        return None

    raw = overlay_data.get("dark_overlay", False)
    if not raw:
        return None

    cfg = dict(DARK_OVERLAY_DEFAULTS)
    if isinstance(raw, dict):
        cfg.update({k: raw[k] for k in DARK_OVERLAY_DEFAULTS if k in raw})

    if "dark_overlay_mode" in overlay_data:
        cfg["mode"] = overlay_data["dark_overlay_mode"]
    if "dark_overlay_opacity" in overlay_data:
        cfg["opacity"] = overlay_data["dark_overlay_opacity"]
    if "dark_overlay_padding_x" in overlay_data:
        cfg["padding_x"] = overlay_data["dark_overlay_padding_x"]
    if "dark_overlay_padding_y" in overlay_data:
        cfg["padding_y"] = overlay_data["dark_overlay_padding_y"]
    if "dark_overlay_radius" in overlay_data:
        cfg["radius"] = overlay_data["dark_overlay_radius"]

    cfg["mode"] = "box" if str(cfg.get("mode", "")).strip().lower() == "box" else "gradient_left"
    cfg["opacity"] = max(0.0, min(1.0, float(cfg.get("opacity", 0.4))))
    cfg["padding_x"] = max(0, int(cfg.get("padding_x", 24)))
    cfg["padding_y"] = max(0, int(cfg.get("padding_y", 18)))
    cfg["radius"] = max(0, int(cfg.get("radius", 12)))
    return cfg


def create_text_background_overlay(
    frame_size: Tuple[int, int],
    text_box: Tuple[int, int, int, int],
    mode: str = "gradient_left",
    opacity: float = 0.4,
    padding_x: int = 24,
    padding_y: int = 18,
    radius: int = 12,
) -> Image.Image:
    frame_w, frame_h = frame_size
    x, y, w, h = text_box
    overlay = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
    if w <= 0 or h <= 0:
        return overlay

    left = max(0, int(x - padding_x))
    top = max(0, int(y - padding_y))
    right = min(frame_w, int(x + w + padding_x))
    bottom = min(frame_h, int(y + h + padding_y))
    if right <= left or bottom <= top:
        return overlay

    alpha = int(max(0.0, min(1.0, opacity)) * 255)
    if mode == "box":
        draw = ImageDraw.Draw(overlay)
        box_radius = min(radius, max(0, (right - left) // 2), max(0, (bottom - top) // 2))
        draw.rounded_rectangle(
            (left, top, right, bottom),
            radius=box_radius,
            fill=(0, 0, 0, alpha),
        )
        return overlay

    box_w = right - left
    box_h = bottom - top
    gradient_alpha = Image.new("L", (box_w, box_h), 0)
    if box_w == 1:
        column = [alpha]
    else:
        column = [int(alpha * (1.0 - (idx / (box_w - 1)))) for idx in range(box_w)]
    gradient_alpha.putdata(column * box_h)

    gradient = Image.new("RGBA", (box_w, box_h), (0, 0, 0, 0))
    gradient.putalpha(gradient_alpha)
    overlay.alpha_composite(gradient, (left, top))
    return overlay

# =========================
# Overlay Renderer
# =========================

class OverlayRenderer:
    def __init__(self, font_path: str = None):
        import platform
        if not font_path:
            if platform.system() == "Darwin":
                # Priority for high-quality CJK fonts on macOS
                paths = [
                    "/System/Library/Fonts/PingFang.ttc",
                    "/System/Library/Fonts/STHeiti Light.ttc",
                    "/Library/Fonts/Arial Unicode.ttf"
                ]
                for p in paths:
                    if os.path.exists(p):
                        font_path = p
                        break
            else:
                # Fallback for Linux/Windows
                font_path = "Arial" 
        
        self.font_path = font_path
        self.cc = OpenCC('s2t') # Simplified to Traditional

    def to_traditional(self, text: str) -> str:
        if not text:
            return ""
        return self.cc.convert(text)

    def load_font(self, size: int) -> ImageFont.FreeTypeFont:
        try:
            return ImageFont.truetype(self.font_path, size=size)
        except Exception as e:
            print(f"Warning: Could not load font {self.font_path}, falling back to default. Error: {e}")
            return ImageFont.load_default()

    def get_text_bbox(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    def wrap_text(
        self,
        draw: ImageDraw.ImageDraw,
        text: str,
        font: ImageFont.FreeTypeFont,
        max_width: int,
        max_chars: int,
        preserve_manual_lines: bool = False,
    ) -> List[str]:
        text = text.strip()
        if not text:
            return []

        # Respect explicit manual line breaks first, then auto-wrap each segment.
        lines: List[str] = []
        for segment in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            segment = segment.strip()
            if not segment:
                continue

            if preserve_manual_lines:
                lines.append(segment)
                continue

            current_line = ""
            for char in segment:
                test_line = current_line + char
                w, _ = self.get_text_bbox(draw, test_line, font)
                if (len(test_line) <= max_chars and w <= max_width) or not current_line:
                    current_line = test_line
                else:
                    lines.append(current_line)
                    current_line = char

            if current_line:
                lines.append(current_line)

        return lines

    def render_to_png(self, scene_id: int, overlay_data: Dict[str, Any], output_dir: str, 
                      frame_w=DEFAULT_FRAME_WIDTH, frame_h=DEFAULT_FRAME_HEIGHT) -> str:
        """
        Renders a professional transparent PNG overlay based on visual_track parameters.
        """
        os.makedirs(output_dir, exist_ok=True)
        
        kind = overlay_data.get("kind", "concept")
        text = self.to_traditional(overlay_data.get("text", ""))
        reference = self.to_traditional(overlay_data.get("reference", ""))
        position = overlay_data.get("position", "left-center")
        dark_overlay_cfg = resolve_dark_overlay_config(overlay_data)
        
        # Configuration
        cfg = TYPOGRAPHY.get(kind, TYPOGRAPHY["concept"])
        # Long, manually broken quote text should not use oversized "concept" typography.
        if kind == "concept" and "\n" in text:
            cfg = TYPOGRAPHY["verse"]
        scale = frame_w / 1920.0
        font_size = int(cfg["font_size"] * scale)
        ref_size = int(cfg["reference_font_size"] * scale) if cfg.get("reference_font_size") else None
        line_spacing = cfg["line_spacing"]
        max_chars = cfg["max_chars_per_line"]
        
        # Resources
        font = self.load_font(font_size)
        ref_font = self.load_font(ref_size) if reference and ref_size else None
        
        canvas = Image.new("RGBA", (frame_w, frame_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(canvas)
        
        # Zone coordinates
        x1_norm, y1_norm, x2_norm, y2_norm = SAFE_ZONES.get(position, SAFE_ZONES["left-center"])
        zx1, zy1, zx2, zy2 = int(x1_norm * frame_w), int(y1_norm * frame_h), int(x2_norm * frame_w), int(y2_norm * frame_h)
        zone_w, zone_h = zx2 - zx1, zy2 - zy1
        
        # Layout & Wrapping
        preserve_manual_lines = kind == "verse" and "\n" in text
        lines = self.wrap_text(
            draw,
            text,
            font,
            zone_w,
            max_chars,
            preserve_manual_lines=preserve_manual_lines,
        )
        
        # Height computation
        total_h = 0
        line_heights = []
        line_widths = []
        for line in lines:
            lw, lh = self.get_text_bbox(draw, line, font)
            line_widths.append(lw)
            line_heights.append(lh)
            total_h += lh
        
        if len(lines) > 1:
            total_h += int(sum(line_heights[:-1]) * (line_spacing - 1.0))
            
        ref_h = 0
        ref_w = 0
        if reference and ref_font:
            ref_w, ref_h = self.get_text_bbox(draw, reference, ref_font)
            total_h += int(ref_h * 1.6) # Padding
            
        # Vertical alignment within zone
        y_cursor = zy1
        if "center" in position:
            y_cursor = zy1 + (zone_h - total_h) // 2
        elif "lower" in position or "bottom" in position:
            y_cursor = zy2 - total_h
            
        x_cursor = zx1
        if position == "center":
            # Fully centered text needs horizontal offset
            pass # wrap_text currently left-aligns in the zone. 
                 # For 'center' zone, we'll keep it left-aligned within that central block for readability.

        block_w = max(line_widths + ([ref_w] if ref_w else [0]))
        if dark_overlay_cfg and block_w > 0 and total_h > 0:
            overlay_layer = create_text_background_overlay(
                frame_size=(frame_w, frame_h),
                text_box=(x_cursor, y_cursor, block_w, total_h),
                **dark_overlay_cfg,
            )
            canvas = Image.alpha_composite(canvas, overlay_layer)
            draw = ImageDraw.Draw(canvas)

        # Draw Shadow & Text
        def draw_shadowed(draw_ptr, xy, txt, fnt, fill, shadow_fill=SHADOW_FILL):
            sx, sy = xy
            draw_ptr.text((sx+2, sy+2), txt, font=fnt, fill=shadow_fill)
            draw_ptr.text((sx+1, sy+1), txt, font=fnt, fill=shadow_fill)
            draw_ptr.text((sx, sy), txt, font=fnt, fill=fill)

        for i, line in enumerate(lines):
            draw_shadowed(draw, (x_cursor, y_cursor), line, font, TEXT_FILL)
            y_cursor += line_heights[i] + int(line_heights[i] * (line_spacing - 1.0))
            
        if reference and ref_font:
            y_cursor += 4 # Small gap
            draw_shadowed(draw, (x_cursor, y_cursor), reference, ref_font, REF_FILL)
            
        output_path = os.path.join(output_dir, f"scene_{scene_id}_overlay.png")
        canvas.save(output_path)
        return output_path
