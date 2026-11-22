#!/usr/bin/env python3
"""Shared drawing helpers for @NBSHARMA3 news cards (code-drawn, no AI)."""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

# Output canvas (1x). Render at SCALE then downscale for crisp type.
WIDTH, HEIGHT = 1080, 1920
SCALE = 2

BG = (17, 17, 17)  # #111111
WHITE = (255, 255, 255)
ORANGE = (255, 170, 23)  # #FFAA17
MUTED = (236, 236, 236)  # near-white, stays clearly readable
DIVIDER = (70, 70, 70)
DOT_OFF = (72, 72, 72)
HANDLE = "@NBSHARMA3"
BRAND = "AROUND THE WORLD"

FONT_PATH = (
    "/usr/share/fonts/truetype/sand-box/google/Inter/"
    "Inter-VariableFont_opsz,wght.ttf"
)


def load_font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    if not os.path.isfile(FONT_PATH):
        raise FileNotFoundError(f"Font not found: {FONT_PATH}")
    if size < 1:
        size = 1
    font = ImageFont.truetype(FONT_PATH, size)
    opsz = max(14.0, min(32.0, float(size)))
    font.set_variation_by_axes([opsz, float(weight)])
    return font


def text_width(font: ImageFont.FreeTypeFont, text: str, tracking: float = 0) -> float:
    if not text:
        return 0.0
    width = font.getlength(text)
    if tracking and len(text) > 1:
        width += tracking * (len(text) - 1)
    return width


def text_height(font: ImageFont.FreeTypeFont, text: str = "Hg") -> int:
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]


def cap_baseline(font: ImageFont.FreeTypeFont, y_top: float) -> float:
    """Baseline so a capital letter's top sits at y_top."""
    top = font.getbbox("H")[1]
    return y_top - top


def draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    tracking: float = 0,
) -> float:
    """Letter-spaced text with a shared baseline.

    xy is the top-left of capital letters. Characters are drawn on one
    baseline so commas stay commas (anchor='lt' lifts a comma into an
    apostrophe).
    """
    x, y = xy
    baseline = cap_baseline(font, y)
    for i, ch in enumerate(text):
        draw.text((x, baseline), ch, font=font, fill=fill, anchor="ls")
        x += font.getlength(ch) + (tracking if i < len(text) - 1 else 0)
    return x


def draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
) -> None:
    """Top-left anchored text (y is the visual top, not the baseline)."""
    draw.text(xy, text, font=font, fill=fill, anchor="lt")


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        if text_width(font, word) > max_width:
            if current:
                lines.append(current)
                current = ""
            chunk = ""
            for ch in word:
                trial = chunk + ch
                if chunk and text_width(font, trial) > max_width:
                    lines.append(chunk)
                    chunk = ch
                else:
                    chunk = trial
            current = chunk
            continue
        trial = word if not current else f"{current} {word}"
        if text_width(font, trial) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def ellipsize(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> str:
    if text_width(font, text) <= max_width:
        return text
    ellipsis = "…"
    while text and text_width(font, text + ellipsis) > max_width:
        text = text[:-1].rstrip()
    return (text + ellipsis).rstrip() if text else ellipsis


def fit_wrapped(
    text: str,
    max_width: float,
    max_lines: int,
    max_size: int,
    min_size: int,
    weight: int,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    chosen_font = load_font(min_size, weight)
    chosen_lines = wrap_text(text, chosen_font, max_width)
    chosen_size = min_size
    for size in range(max_size, min_size - 1, -2):
        font = load_font(size, weight)
        lines = wrap_text(text, font, max_width)
        if len(lines) <= max_lines:
            chosen_font, chosen_lines, chosen_size = font, lines, size
            break
    if len(chosen_lines) > max_lines:
        kept = chosen_lines[:max_lines]
        kept[-1] = ellipsize(kept[-1], chosen_font, max_width)
        chosen_lines = kept
    else:
        # Guarantee the last line still fits (single oversized token).
        chosen_lines = [
            ellipsize(line, chosen_font, max_width) for line in chosen_lines
        ]
    return chosen_font, chosen_lines, chosen_size


def make_x_icon(size: int, color: tuple[int, int, int] = WHITE) -> Image.Image:
    """Crisp geometric X, drawn large then downscaled."""
    scale = 4
    s = size * scale
    tmp = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    pad = int(s * 0.10)
    thickness = max(scale * 2, int(s * 0.18))
    fill = color + (255,)
    d.line([(pad, pad), (s - pad, s - pad)], fill=fill, width=thickness)
    d.line([(s - pad, pad), (pad, s - pad)], fill=fill, width=thickness)
    r = thickness // 2
    for cx, cy in ((pad, pad), (s - pad, s - pad), (s - pad, pad), (pad, s - pad)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    return tmp.resize((size, size), Image.Resampling.LANCZOS)


def new_canvas() -> tuple[Image.Image, ImageDraw.ImageDraw, int]:
    """Return a SCALE-times canvas, its Draw, and the scale factor."""
    s = SCALE
    img = Image.new("RGB", (WIDTH * s, HEIGHT * s), BG)
    return img, ImageDraw.Draw(img), s


def downscale(img: Image.Image) -> Image.Image:
    if img.size == (WIDTH, HEIGHT):
        return img
    return img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)


def save_png(img: Image.Image, out_path: str) -> str:
    img = downscale(img)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def draw_top_bar(draw: ImageDraw.ImageDraw, s: int, bar_h: int = 12) -> int:
    draw.rectangle((0, 0, WIDTH * s, bar_h * s), fill=ORANGE)
    return bar_h * s


def draw_brand_row(
    draw: ImageDraw.ImageDraw,
    s: int,
    y: int,
    datetime_str: str = "",
    pad_x: int = 68,
    brand_size: int = 26,
    date_size: int = 22,
) -> int:
    """AROUND THE WORLD + optional datetime. Returns bottom y."""
    pad = pad_x * s
    brand_font = load_font(brand_size * s, 800)
    tracking = 4.0 * s
    bar_w, bar_h = 28 * s, 6 * s
    brand_h = text_height(brand_font, BRAND)
    bar_y = y + max(0, (brand_h - bar_h) // 2) + 1 * s
    draw.rectangle((pad, bar_y, pad + bar_w, bar_y + bar_h), fill=ORANGE)
    brand_x = pad + bar_w + 14 * s
    draw_tracked(draw, (brand_x, y), BRAND, brand_font, WHITE, tracking=tracking)
    row_h = brand_h
    if datetime_str:
        date_font = load_font(date_size * s, 650)
        max_date_w = WIDTH * s - pad - (brand_x + text_width(brand_font, BRAND, tracking) + 24 * s)
        date = ellipsize(datetime_str, date_font, max(40 * s, max_date_w))
        date_w = text_width(date_font, date)
        date_h = text_height(date_font, date)
        date_x = WIDTH * s - pad - date_w
        # Optically align with brand cap-height.
        date_y = y + max(0, (brand_h - date_h) // 2)
        draw_text(draw, (date_x, date_y), date, date_font, MUTED)
        row_h = max(row_h, date_h)
    return y + row_h


def draw_footer(
    draw: ImageDraw.ImageDraw,
    img: Image.Image,
    s: int,
    pad_x: int = 68,
    handle_size: int = 30,
    icon_size: int = 30,
    bottom_pad: int = 56,
) -> int:
    """Orange rule + X mark + handle. Returns the y of the rule (content floor)."""
    pad = pad_x * s
    handle_font = load_font(handle_size * s, 800)
    icon_px = icon_size * s
    footer_content_h = max(icon_px, text_height(handle_font, HANDLE))
    footer_bottom = HEIGHT * s - bottom_pad * s
    content_y = footer_bottom - footer_content_h
    rule_y = content_y - 18 * s
    draw.rectangle((pad, rule_y, WIDTH * s - pad, rule_y + 3 * s), fill=ORANGE)

    icon = make_x_icon(icon_px, WHITE)
    mid_y = content_y + footer_content_h / 2
    icon_y = int(round(mid_y - icon_px / 2))
    img.paste(icon, (int(pad), int(icon_y)), icon)
    handle_h = text_height(handle_font, HANDLE)
    handle_y = mid_y - handle_h / 2
    draw_text(
        draw,
        (pad + icon_px + 12 * s, handle_y),
        HANDLE,
        handle_font,
        WHITE,
    )
    return rule_y
