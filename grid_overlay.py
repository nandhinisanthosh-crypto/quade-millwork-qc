"""
grid_overlay.py — Draws a fine coordinate grid on a PNG before sending to GPT-4o.

Grid density:
  - Major lines every 10% (labeled, semi-opaque)
  - Minor lines every 5%  (lighter, no label)

This gives GPT a reliable spatial reference for estimating bbox_pct values
accurately, even on dense multi-drawing sheets with small elements.
"""
from __future__ import annotations
import io
import os
from typing import Tuple


def add_grid_overlay(
    image_path: str,
    major_every: int = 10,   # major lines every N percent
    minor_every: int = 5,    # minor lines every N percent (must divide major evenly)
    major_color: Tuple[int, int, int, int] = (255, 0, 0, 160),    # red, semi-transparent
    minor_color: Tuple[int, int, int, int] = (255, 0, 0, 70),     # same, lighter
    label_color: Tuple[int, int, int, int] = (220, 0, 0, 220),
    line_width_major: int = 2,
    line_width_minor: int = 1,
    font_size: int = 18,
) -> bytes:
    """
    Draw a labeled percentage grid onto the image and return the result as PNG bytes.

    Args:
        image_path:   Absolute path to the source PNG.
        major_every:  Percentage interval for major (labeled) grid lines (default 10).
        minor_every:  Percentage interval for minor (unlabeled) grid lines (default 5).
        ...           Color/style overrides.

    Returns:
        PNG bytes of the image with the grid overlay.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise ImportError("Pillow is not installed. Run: pip install Pillow")

    img = Image.open(image_path).convert("RGBA")
    w, h = img.size

    # Draw layer (RGBA so we can use alpha)
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ── Try to load a small system font; fall back to default ──────────────
    font = None
    font_paths = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for fp in font_paths:
        if os.path.exists(fp):
            try:
                from PIL import ImageFont
                font = ImageFont.truetype(fp, font_size)
                break
            except Exception:
                pass
    if font is None:
        font = ImageFont.load_default()

    # ── Draw grid lines ────────────────────────────────────────────────────
    pct_values = list(range(0, 101, minor_every))

    for pct in pct_values:
        is_major = (pct % major_every == 0)
        color    = major_color if is_major else minor_color
        lw       = line_width_major if is_major else line_width_minor

        # Vertical line at x = pct%
        x = int(pct / 100.0 * w)
        draw.line([(x, 0), (x, h)], fill=color, width=lw)

        # Horizontal line at y = pct%
        y = int(pct / 100.0 * h)
        draw.line([(0, y), (w, y)], fill=color, width=lw)

    # ── Draw labels at major intersections ────────────────────────────────
    major_pcts = list(range(0, 101, major_every))

    for xp in major_pcts:
        x = int(xp / 100.0 * w)
        # X-axis label at top (skip 0 and 100 to avoid edge clip)
        if 0 < xp < 100:
            label = f"{xp}%"
            _draw_label(draw, font, label, x + 3, 4, label_color)

    for yp in major_pcts:
        y = int(yp / 100.0 * h)
        if 0 < yp < 100:
            label = f"{yp}%"
            _draw_label(draw, font, label, 4, y + 2, label_color)

    # ── Composite and return bytes ─────────────────────────────────────────
    result = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    result.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _draw_label(
    draw,
    font,
    text: str,
    x: int,
    y: int,
    color: Tuple[int, int, int, int],
):
    """Draw a label with a thin white outline for readability on any background."""
    # White outline
    for dx, dy in [(-1, -1), (1, -1), (-1, 1), (1, 1)]:
        draw.text((x + dx, y + dy), text, font=font, fill=(255, 255, 255, 200))
    # Colored text
    draw.text((x, y), text, font=font, fill=color)
