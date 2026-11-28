"""Preserve mixed-colour glyphs on flat dark teaching slides in a white SVG.

Use the source glyph positions directly when OCR grouped differently coloured
words into one text box. The separate OCR description stays machine-readable.
"""

import xml.etree.ElementTree as ET

import cairosvg
import cv2
import numpy as np

from backend.pipeline.slide_visual_evidence import contrast_color_for_white


def source_ink_svg(frame, content, image_sha):
    if frame is None or content["has_diagram"] or content["shapes"] or content["edges"]:
        return None
    h, s, v = cv2.split(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV))
    # Limited to a flat dark blue canvas with light text. Photos and other
    # palettes require the regular reconstruction and independent review.
    blue = (h >= 99) & (h <= 130) & (s > 120) & (v < 160)
    if blue.mean() < 0.65:
        return None
    background = np.median(frame[blue], axis=0)
    if np.mean(np.max(np.abs(frame.astype(float) - background), axis=2) < 28) < 0.60:
        return None
    # Bright cores identify colour; assign antialiased edges from their
    # nearest core so the blue canvas cannot tint white or yellow outlines.
    cores = v >= 155
    yellow = cores & (h >= 17) & (h < 37) & (s >= 64)
    green = cores & (h >= 37) & (h <= 88) & (s >= 64)
    white = cores & (s < 100) & ~yellow & ~green
    recognized = yellow | green | white
    near_ink = cv2.dilate(recognized.astype("uint8"), np.ones((5, 5), np.uint8)).astype(
        bool
    )
    blue_fringe = (h >= 93) & (h <= 130) & near_ink
    if not recognized.any() or (cores & ~(recognized | blue_fringe)).sum() > max(
        6, cores.sum() * 0.005
    ):
        return None
    height, width = v.shape
    roi = np.zeros_like(blue)
    for region in content["text_regions"]:
        x, y, r, b = region["bbox"]
        # Include anti-aliasing beyond approximate OCR rectangles.
        roi[
            max(0, int(y * height / 1000) - 4) : min(
                height, int(b * height / 1000) + 5
            ),
            max(0, int(x * width / 1000) - 4) : min(width, int(r * width / 1000) + 5),
        ] = True
    if np.count_nonzero(cores & ~roi) > max(12, cores.sum() * 0.01):
        return None
    root = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width=str(width),
        height=str(height),
        viewBox=f"0 0 {width} {height}",
        role="img",
        attrib={
            "data-source-image-sha256": image_sha,
            "data-background-mode": "white",
            "data-text-renderer": "source-glyphs.v1",
        },
    )
    ET.SubElement(root, "desc").text = "\n".join(
        r["text"] for r in content["text_regions"]
    )
    ET.SubElement(root, "rect", width=str(width), height=str(height), fill="#FFFFFF")
    masks = [white, yellow, green]
    distances = np.stack(
        [
            cv2.distanceTransform((~mask).astype("uint8"), cv2.DIST_L2, 5)
            for mask in masks
        ]
    )
    nearest = distances.argmin(axis=0)
    ink = roi & (distances.min(axis=0) <= 2)
    # Preserve antialiasing from the camera image at the edges of each stroke.
    levels = np.rint(
        4
        * np.clip(
            (frame[:, :, 1].astype(float) - background[1]) / (175 - background[1]), 0, 1
        )
    ).astype("uint8")
    levels[recognized & (v > 190)] = 4
    for index, colour in enumerate(("#ffffff", "#ffff00", "#00ff00")):
        for level in range(1, 5):
            pixels = ink & (nearest == index) & (levels == level)
            if not pixels.any():
                continue
            paths = []
            for y, line in enumerate(pixels):
                bounds = np.flatnonzero(np.diff(np.r_[False, line, False])).reshape(
                    -1, 2
                )
                paths.extend(f"M{l} {y}h{r - l}v1H{l}z" for l, r in bounds)
            ET.SubElement(
                root,
                "path",
                fill=contrast_color_for_white(colour),
                attrib={
                    "d": "".join(paths),
                    "fill-opacity": str(level / 4),
                    "shape-rendering": "crispEdges",
                },
            )
    svg = ET.tostring(root, encoding="unicode")
    # Verify the delivered SVG, rasterized at the source resolution. Allow
    # isolated codec noise while checking each semantic colour separately.
    png = cairosvg.svg2png(bytestring=svg.encode())
    rendered = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    cores_by_colour = (
        (v > 190) & (s < 35),
        (v > 190) & (s > 130) & (h >= 19) & (h <= 34),
        (v > 190) & (s > 128) & (h >= 43) & (h <= 86),
    )
    for core, expected_bgr in zip(
        cores_by_colour, ((39, 24, 17), (14, 64, 146), (61, 128, 21))
    ):
        if (
            core.sum() >= 12
            and np.mean(np.all(rendered[core] == expected_bgr, axis=1)) < 0.99
        ):
            return None
    if np.any(rendered[~roi] != 255):
        return None
    return svg
