#!/usr/bin/env python3
"""Generate a 1600x900 news card for @NBSHARMA3 (code-drawn, no AI)."""

from __future__ import annotations

import argparse
import os
import sys
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont

# Canvas
WIDTH, HEIGHT = 1600, 900
BG = (17, 17, 17)  # #111111
WHITE = (255, 255, 255)
ORANGE = (255, 170, 23)  # #FFAA17
MUTED = (225, 225, 225)
CONTEXT_BODY = (245, 245, 245)
DIVIDER = (78, 78, 78)
HANDLE = "@NBSHARMA3"
BRAND = "AROUND THE WORLD"

FONT_PATH = (
    "/usr/share/fonts/truetype/sand-box/google/Inter/"
    "Inter-VariableFont_opsz,wght.ttf"
)

# Layout
PAD_X = 88
TOP_BAR_H = 8
HEADER_TOP = 38
FOOTER_BOTTOM_PAD = 44


def load_font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    if not os.path.isfile(FONT_PATH):
        raise FileNotFoundError(f"Font not found: {FONT_PATH}")
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


def text_height(font: ImageFont.FreeTypeFont, text: str = "Ag") -> int:
    bbox = font.getbbox(text)
    return bbox[3] - bbox[1]


def draw_tracked(
    draw: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    tracking: float = 0,
    anchor: str = "lt",
) -> float:
    x, y = xy
    for i, ch in enumerate(text):
        draw.text((x, y), ch, font=font, fill=fill, anchor=anchor)
        x += font.getlength(ch) + (tracking if i < len(text) - 1 else 0)
    return x


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        # Break an overlong word by character if needed
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
        # Ellipsize the last allowed line
        kept = chosen_lines[:max_lines]
        last = kept[-1]
        ellipsis = "…"
        while last and text_width(chosen_font, last + ellipsis) > max_width:
            last = last[:-1].rstrip()
        kept[-1] = (last + ellipsis).rstrip()
        chosen_lines = kept
    return chosen_font, chosen_lines, chosen_size


def split_context(context: str) -> tuple[str, str]:
    context = context.strip()
    if not context:
        return "", ""
    if ":" in context:
        label, _, rest = context.partition(":")
        return label.strip(), rest.strip()
    return "", context


def make_x_icon(size: int, color: tuple[int, int, int] = WHITE) -> Image.Image:
    """Crisp geometric X, drawn large then downscaled."""
    scale = 4
    s = size * scale
    tmp = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp)
    pad = int(s * 0.10)
    thickness = max(scale * 2, int(s * 0.18))
    fill = color + (255,)
    # Rounded caps via extra circles at the ends
    d.line([(pad, pad), (s - pad, s - pad)], fill=fill, width=thickness)
    d.line([(s - pad, pad), (pad, s - pad)], fill=fill, width=thickness)
    r = thickness // 2
    for cx, cy in ((pad, pad), (s - pad, s - pad), (s - pad, pad), (pad, s - pad)):
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)
    return tmp.resize((size, size), Image.Resampling.LANCZOS)


def render_card(
    place: str,
    headline: str,
    context: str = "",
    datetime_str: str = "",
    out_path: str = "card.png",
) -> str:
    place = place.strip().upper()
    headline = " ".join(headline.split())
    context = " ".join(context.split()) if context else ""
    datetime_str = datetime_str.strip()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Top orange bar (full width, no letterbox)
    draw.rectangle((0, 0, WIDTH, TOP_BAR_H), fill=ORANGE)

    # Header fonts
    brand_font = load_font(42, 800)
    date_font = load_font(32, 650)
    brand_tracking = 6.0

    brand_y = HEADER_TOP
    bar_w, bar_h = 44, 7
    brand_h = text_height(brand_font, BRAND)
    # Vertically centre the accent bar with the cap-height of the header
    bar_y = brand_y + max(0, (brand_h - bar_h) // 2) + 2
    draw.rectangle((PAD_X, bar_y, PAD_X + bar_w, bar_y + bar_h), fill=ORANGE)

    brand_x = PAD_X + bar_w + 18
    draw_tracked(
        draw, (brand_x, brand_y), BRAND, brand_font, WHITE, tracking=brand_tracking
    )

    if datetime_str:
        date_w = text_width(date_font, datetime_str)
        date_x = WIDTH - PAD_X - date_w
        # Align datetime optically with the header baseline
        date_bbox = date_font.getbbox(datetime_str)
        brand_bbox = brand_font.getbbox(BRAND)
        date_y = brand_y + brand_bbox[3] - date_bbox[3]
        draw.text((date_x, date_y), datetime_str, font=date_font, fill=MUTED)

    # Hairline under the header
    header_bottom = brand_y + brand_h + 26
    draw.rectangle((PAD_X, header_bottom, WIDTH - PAD_X, header_bottom + 1), fill=DIVIDER)

    # Footer first, so we know the content window
    handle_font = load_font(34, 750)
    icon_size = 34
    footer_line_y = HEIGHT - FOOTER_BOTTOM_PAD - icon_size - 22
    draw.rectangle((PAD_X, footer_line_y, WIDTH - PAD_X, footer_line_y + 2), fill=ORANGE)

    icon = make_x_icon(icon_size, WHITE)
    handle_h = text_height(handle_font, HANDLE)
    footer_content_y = footer_line_y + 18
    # Centre icon and handle on a shared midline
    mid_y = footer_content_y + max(icon_size, handle_h) / 2
    icon_y = int(round(mid_y - icon_size / 2))
    handle_bbox = handle_font.getbbox(HANDLE)
    handle_y = mid_y - (handle_bbox[3] + handle_bbox[1]) / 2
    img.paste(icon, (PAD_X, icon_y), icon)
    draw.text(
        (PAD_X + icon_size + 12, handle_y),
        HANDLE,
        font=handle_font,
        fill=WHITE,
    )

    content_top = header_bottom + 36
    content_bottom = footer_line_y - 36
    content_width = WIDTH - 2 * PAD_X

    # Place tag
    place_font = load_font(42, 800)
    place_tracking = 8.0
    place_h = text_height(place_font, place or "A")

    # Headline: large, bold, max 3 lines, auto-shrink
    headline_font, headline_lines, headline_size = fit_wrapped(
        headline,
        max_width=content_width,
        max_lines=4,
        max_size=100,
        min_size=58,
        weight=800,
    )
    headline_lh = int(round(headline_size * 1.14))
    headline_block_h = headline_lh * (len(headline_lines) - 1) + text_height(
        headline_font, headline_lines[0] if headline_lines else "Ag"
    )

    # Context
    label, body = split_context(context)
    context_font = load_font(40, 600)
    label_font = load_font(40, 800)
    context_lines: list[tuple[str, str]] = []  # (label_on_this_line_or_empty, text)
    context_block_h = 0
    context_lh = 54
    if context:
        prefix = f"{label}: " if label else ""
        # Wrap the full context, then paint the label in a stronger weight on line 1
        wrapped = wrap_text(prefix + body if body else prefix.strip(), context_font, content_width)
        # If wrapping broke the label itself (unlikely), just use wrapped lines as body
        if label and wrapped:
            first = wrapped[0]
            if first.startswith(prefix):
                context_lines.append((label, first[len(prefix) :]))
                context_lines.extend(("", line) for line in wrapped[1:])
            elif first.startswith(label):
                rest = first[len(label) :].lstrip(": ").strip()
                context_lines.append((label, rest))
                context_lines.extend(("", line) for line in wrapped[1:])
            else:
                context_lines.extend(("", line) for line in wrapped)
        else:
            context_lines.extend(("", line) for line in wrapped)
        if context_lines:
            context_block_h = context_lh * (len(context_lines) - 1) + text_height(
                context_font, "Ag"
            )

    gap_place_headline = 18
    gap_headline_context = 34 if context_lines else 0
    block_h = (
        place_h
        + gap_place_headline
        + headline_block_h
        + gap_headline_context
        + context_block_h
    )

    window = content_bottom - content_top
    # Slightly above geometric centre so the block sits in the optical middle
    start_y = content_top + max(0, int((window - block_h) * 0.52))

    # Place
    y = start_y
    draw_tracked(draw, (PAD_X, y), place, place_font, ORANGE, tracking=place_tracking)
    y += place_h + gap_place_headline

    # Headline
    for i, line in enumerate(headline_lines):
        draw.text((PAD_X, y + i * headline_lh), line, font=headline_font, fill=WHITE)

    y += headline_block_h + gap_headline_context

    # Context
    if context_lines:
        cy = y
        for i, (lbl, rest) in enumerate(context_lines):
            x = PAD_X
            ly = cy + i * context_lh
            if lbl:
                label_text = f"{lbl}: "
                draw.text((x, ly), label_text, font=label_font, fill=ORANGE)
                x += text_width(label_font, label_text)
                if rest:
                    draw.text((x, ly), rest, font=context_font, fill=CONTEXT_BODY)
            else:
                draw.text((x, ly), rest, font=context_font, fill=CONTEXT_BODY)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a 1600x900 X news card for @NBSHARMA3")
    p.add_argument("--place", required=True, help="Location/topic tag, e.g. HORMUZ")
    p.add_argument("--headline", required=True, help="Main headline text")
    p.add_argument("--context", default="", help="Optional context line, e.g. 'Why it matters: …'")
    p.add_argument("--datetime", default="", dest="datetime_str", help="Date/time line")
    p.add_argument("--out", required=True, help="Output PNG path")
    return p.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    path = render_card(
        place=args.place,
        headline=args.headline,
        context=args.context,
        datetime_str=args.datetime_str,
        out_path=args.out,
    )
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
