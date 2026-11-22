#!/usr/bin/env python3
"""Generate a vertical 2160x3840 (9:16) news list/hero card for @NBSHARMA3.

Code-drawn with Pillow, no AI, saved as a lossless native PNG (no downscale).

Usage:
  python3 make_vcard.py --json card.json --out card.png

JSON shape:
  {
    "title": "TOP 10 WORLD NEWS",
    "datetime": "6:00 AM IST, 22 Nov 2026",
    "items": [{"place": "TOKYO", "text": "Headline in our own words."}]
  }

Supports 1–10 items. A single item uses a large hero layout
(GOOD NEWS / BREAKING).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont

from cardutil import (
    BG,
    BRAND,
    BREAKING_RED,
    GOOD_GREEN,
    HANDLE,
    INK,
    ORANGE,
    PANEL,
    PANEL_DEEP,
    WHITE,
    draw_circle_badge,
    draw_pill,
    draw_text,
    draw_tracked,
    ellipsize,
    fit_wrapped,
    load_font,
    make_x_icon,
    measure_pill,
    save_png_native,
    text_height,
    text_width,
    vertical_gradient,
    wrap_text,
)

# Native output — 9:16, no later downscale. 2160x3840 stays sharp after
# social-network compression better than a 1080-wide source.
WIDTH, HEIGHT = 2160, 3840
PAD_X = 96
TOP_BAR = 18
# Floor for list headlines at this resolution (~50px at 1080-equivalent).
MIN_HEAD = 88


def load_payload(path: str) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("JSON root must be an object")
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("JSON must include a non-empty 'title'")
    datetime_str = str(data.get("datetime") or "").strip()
    items_in = data.get("items")
    if not isinstance(items_in, list) or not items_in:
        raise ValueError("JSON 'items' must be a non-empty list")
    if len(items_in) > 10:
        raise ValueError("JSON 'items' supports at most 10 entries")
    items: list[dict[str, str]] = []
    for i, raw in enumerate(items_in, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"items[{i}] must be an object with place and text")
        place = " ".join(str(raw.get("place") or "").split()).upper()
        text = " ".join(str(raw.get("text") or "").split())
        if not text:
            raise ValueError(f"items[{i}] is missing text")
        items.append({"place": place or f"ITEM {i}", "text": text})
    return {"title": title, "datetime": datetime_str, "items": items}



def lines_for(text: str, font, max_width: float, max_lines: int) -> list[str]:
    """Wrap to max_lines and always mark overflow with a clean ellipsis."""
    wrapped = wrap_text(text, font, max_width)
    if not wrapped:
        return ["…"]
    if len(wrapped) <= max_lines:
        return [ellipsize(ln, font, max_width) for ln in wrapped]
    kept = wrapped[:max_lines]
    last = kept[-1].rstrip().rstrip(" ,;:.-–—")
    while last and text_width(font, last + "…") > max_width:
        last = last[:-1].rstrip().rstrip(" ,;:.-–—")
    kept[-1] = (last + "…") if last else "…"
    return kept


def category_style(title: str) -> tuple[str, tuple[int, int, int], tuple[int, int, int]]:
    """Return (badge_label, badge_fill, badge_fg) from the card title."""
    raw = " ".join(title.split())
    upper = raw.upper()
    if "GOOD NEWS" in upper or upper == "GOOD":
        return "GOOD NEWS", GOOD_GREEN, WHITE
    if "BREAKING" in upper:
        # Keep the word BREAKING as the badge; leftover title is unused on hero.
        return "BREAKING", BREAKING_RED, WHITE
    return raw.upper(), ORANGE, INK


def draw_brand_mark(draw: ImageDraw.ImageDraw, x: int, y: int, size: int = 40) -> int:
    """Orange accent bar + AROUND THE WORLD. Returns bottom y."""
    font = load_font(size, 800)
    tracking = 6.0
    bar_w, bar_h = 40, 8
    brand_h = text_height(font, BRAND)
    bar_y = y + max(0, (brand_h - bar_h) // 2) + 1
    draw.rectangle((x, bar_y, x + bar_w, bar_y + bar_h), fill=ORANGE)
    draw_tracked(draw, (x + bar_w + 18, y), BRAND, font, WHITE, tracking=tracking)
    return y + brand_h


def draw_footer_block(img: Image.Image, draw: ImageDraw.ImageDraw) -> int:
    """Orange rule + X mark + handle. Returns the rule y (content floor)."""
    pad = PAD_X
    handle_font = load_font(44, 800)
    icon_px = 44
    bottom_pad = 88
    footer_h = max(icon_px, text_height(handle_font, HANDLE))
    content_y = HEIGHT - bottom_pad - footer_h
    rule_y = content_y - 28
    draw.rectangle((pad, rule_y, WIDTH - pad, rule_y + 5), fill=ORANGE)

    icon = make_x_icon(icon_px, WHITE)
    mid_y = content_y + footer_h / 2
    img.paste(icon, (int(pad), int(round(mid_y - icon_px / 2))), icon)
    handle_h = text_height(handle_font, HANDLE)
    draw_text(
        draw,
        (pad + icon_px + 16, mid_y - handle_h / 2),
        HANDLE,
        handle_font,
        WHITE,
    )
    return rule_y


def draw_header_band(
    draw: ImageDraw.ImageDraw,
    title: str,
    datetime_str: str,
) -> int:
    """Bold header band. Returns the y where content panels may start."""
    # Band background sits under the orange top bar as well.
    # Height is computed from the typography, then the rect is drawn first
    # in a second pass... We measure, then draw.
    pad = PAD_X
    brand_size = 38
    title_max, title_min = 118, 72
    date_size = 34

    brand_font = load_font(brand_size, 800)
    brand_h = text_height(brand_font, BRAND)

    inner_w = WIDTH - 2 * pad
    title_font, title_lines, title_size = fit_wrapped(
        title.upper(), inner_w, 2, title_max, title_min, 900
    )
    title_lh = int(round(title_size * 1.10))
    title_block = title_lh * (len(title_lines) - 1) + text_height(title_font, title_lines[0])

    date_h = 0
    date_font = None
    date_text = datetime_str
    if datetime_str:
        date_font = load_font(date_size, 700)
        date_text = ellipsize(datetime_str, date_font, inner_w - 80)
        _dw, date_h = measure_pill(date_text, date_font, 28, 12)

    top_pad, mid_gap, after_title, bot_pad = 44, 28, 24, 44
    band_h = (
        TOP_BAR
        + top_pad
        + brand_h
        + mid_gap
        + title_block
        + (after_title + date_h if date_h else 0)
        + bot_pad
    )
    draw.rectangle((0, 0, WIDTH, band_h), fill=PANEL)
    draw.rectangle((0, 0, WIDTH, TOP_BAR), fill=ORANGE)

    y = TOP_BAR + top_pad
    draw_brand_mark(draw, pad, y, size=brand_size)
    y = y + brand_h + mid_gap
    for i, line in enumerate(title_lines):
        draw_text(draw, (pad, y + i * title_lh), line, title_font, WHITE)
    y = y + title_block
    if date_font is not None:
        y += after_title
        draw_pill(
            draw,
            (pad, y),
            date_text,
            date_font,
            fill=BG,
            fg=WHITE,
            pad_x=28,
            pad_y=12,
            outline=ORANGE,
            outline_w=4,
        )
        y += date_h
    return band_h  # panels start after a gap applied by the caller


def render_list(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    title: str,
    datetime_str: str,
    items: list[dict[str, str]],
) -> None:
    n = len(items)
    header_bottom = draw_header_band(draw, title, datetime_str)
    rule_y = draw_footer_block(img, draw)

    gap_top = 28
    gap_bot = 28
    row_gap = 16 if n >= 8 else 20
    content_top = header_bottom + gap_top
    content_bot = rule_y - gap_bot
    available = content_bot - content_top
    slot = available / n
    panel_h = slot - row_gap
    if panel_h < 200:
        row_gap = 12
        panel_h = slot - row_gap

    pad = PAD_X
    panel_w = WIDTH - 2 * pad
    radius = 28
    badge_r = 64 if n >= 8 else (72 if n >= 5 else 80)
    badge_left = 28
    text_x = badge_left + badge_r * 2 + 36
    text_w = panel_w - text_x - 36

    # Sizes scale gently when fewer rows share the canvas.
    if n <= 3:
        place_size, head_max = 48, 128
    elif n <= 5:
        place_size, head_max = 44, 120
    elif n <= 7:
        place_size, head_max = 42, 112
    else:
        place_size, head_max = 42, 108

    place_font = load_font(place_size, 800)
    place_h = text_height(place_font, "H")
    place_gap = 14
    inner_v = 24
    # Room left for the headline block inside the panel.
    head_room = panel_h - 2 * inner_v - place_h - place_gap
    # Two-line line-height budget: pick the largest size that fits.
    # lh = size * 1.16, two lines ≈ size * 1.16 + size * (cap ratio ~0.72)
    # We measure properly in the loop below.

    prepared: list[tuple] = []
    # Try a shared headline size so every row has the same typography.
    chosen_head = MIN_HEAD
    chosen_font = load_font(MIN_HEAD, 900)
    chosen_lh = int(round(MIN_HEAD * 1.16))
    for size in range(int(head_max), MIN_HEAD - 1, -2):
        font = load_font(size, 900)
        lh = int(round(size * 1.16))
        ok = True
        trial = []
        for idx, it in enumerate(items, 1):
            lines = lines_for(it["text"], font, text_w, 2)
            block_h = lh * (len(lines) - 1) + text_height(font, lines[0])
            if block_h > head_room:
                ok = False
                break
            place = it["place"]
            tr = 3.0
            if text_width(place_font, place, tracking=tr) > text_w:
                tr = 0
                place = ellipsize(place, place_font, text_w)
            trial.append((idx, place, tr, lines, block_h))
        if ok:
            chosen_head, chosen_font, chosen_lh, prepared = size, font, lh, trial
            break
    if not prepared:
        font = load_font(MIN_HEAD, 900)
        lh = int(round(MIN_HEAD * 1.14))
        for idx, it in enumerate(items, 1):
            lines = lines_for(it["text"], font, text_w, 2)
            block_h = lh * (len(lines) - 1) + text_height(font, lines[0])
            if block_h > head_room and len(lines) > 1:
                lines = lines_for(it["text"], font, text_w, 1)
                block_h = text_height(font, lines[0])
            place = ellipsize(it["place"], place_font, text_w)
            prepared.append((idx, place, 0, lines, block_h))
        chosen_font, chosen_lh = font, lh

    badge_font = load_font(52 if badge_r < 70 else 58, 900)

    for i, (idx, place, tr, lines, block_h) in enumerate(prepared):
        slot_top = content_top + i * slot
        py = int(round(slot_top))
        ph = int(round(panel_h))
        box = (pad, py, pad + panel_w, py + ph)
        draw.rounded_rectangle(box, radius=radius, fill=PANEL)

        # Vertically centre badge + (place + headline) as a group.
        text_block_h = place_h + place_gap + block_h
        group_h = max(badge_r * 2, text_block_h)
        group_top = py + (ph - group_h) / 2
        # Keep a hard inset so nothing kisses the panel edge.
        inset = 26
        if group_top < py + inset:
            group_top = py + inset

        cx = pad + badge_left + badge_r
        cy = int(round(group_top + group_h / 2))
        # Keep the circle fully inside the panel.
        cy = min(max(cy, py + badge_r + 10), py + ph - badge_r - 10)
        draw_circle_badge(
            draw, int(cx), int(cy), int(badge_r), f"{idx:02d}", badge_font, fill=ORANGE, fg=INK
        )

        text_top = group_top + (group_h - text_block_h) / 2
        tx = pad + text_x
        draw_tracked(draw, (tx, text_top), place, place_font, ORANGE, tracking=tr)
        hy = text_top + place_h + place_gap
        for li, line in enumerate(lines):
            draw_text(draw, (tx, hy + li * chosen_lh), line, chosen_font, WHITE)


def _hero_backdrop(accent: tuple[int, int, int]) -> Image.Image:
    """Soft vertical gradient plus faint geometric accents."""
    img = vertical_gradient((WIDTH, HEIGHT), BG, PANEL_DEEP)
    ov = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    # Large soft disc, top-right.
    r = int(WIDTH * 0.55)
    cx, cy = int(WIDTH * 0.92), int(HEIGHT * 0.10)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=accent + (22,))
    # Thin outer ring.
    r2 = r + 80
    d.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), outline=accent + (36,), width=18)
    # Smaller disc, bottom-left.
    r3 = int(WIDTH * 0.28)
    cx3, cy3 = int(WIDTH * -0.02), int(HEIGHT * 0.88)
    d.ellipse((cx3 - r3, cy3 - r3, cx3 + r3, cy3 + r3), fill=accent + (16,))
    # Fine diagonal hairlines.
    for i, y0 in enumerate((-400, 200, 800)):
        d.line((0, y0 + HEIGHT // 6, WIDTH, y0 + HEIGHT // 6 + int(WIDTH * 0.55)), fill=(255, 255, 255, 12), width=3)
    # Corner ticks, top-left and bottom-right of the content well.
    tick, thick = 90, 6
    # (drawn later in the content area — keep only background here)
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def render_hero(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    title: str,
    datetime_str: str,
    item: dict[str, str],
) -> Image.Image:
    label, fill, fg = category_style(title)
    img = _hero_backdrop(fill if fill != ORANGE else ORANGE)
    draw = ImageDraw.Draw(img)

    # Orange top bar always — brand colour stays present even on breaking/good.
    draw.rectangle((0, 0, WIDTH, TOP_BAR), fill=ORANGE)

    pad = PAD_X
    y = TOP_BAR + 56
    y = draw_brand_mark(draw, pad, y, size=40)
    y += 36

    # Category badge (large).
    badge_font = load_font(42, 900)
    bw, bh = draw_pill(
        draw,
        (pad, y),
        label,
        badge_font,
        fill=fill,
        fg=fg,
        pad_x=36,
        pad_y=16,
    )
    # Optional date pill to the right of the category, if it fits.
    if datetime_str:
        date_font = load_font(32, 700)
        date = ellipsize(datetime_str, date_font, WIDTH - pad - (pad + bw + 28) - pad)
        dw, dh = measure_pill(date, date_font, 26, 12)
        if pad + bw + 24 + dw <= WIDTH - pad:
            dy = y + (bh - dh) / 2
            draw_pill(
                draw,
                (pad + bw + 24, int(dy)),
                date,
                date_font,
                fill=BG,
                fg=WHITE,
                pad_x=26,
                pad_y=12,
                outline=ORANGE,
                outline_w=4,
            )
    badge_bottom = y + bh

    rule_y = draw_footer_block(img, draw)

    # Content well between the badge and the footer, vertically centred.
    well_top = badge_bottom + 80
    well_bot = rule_y - 80
    well_h = well_bot - well_top
    inner_w = WIDTH - 2 * pad

    place = item["place"]
    place_size = 72
    place_font = load_font(place_size, 800)
    place_tr = 6.0
    while place_size > 48 and text_width(place_font, place, tracking=place_tr) > inner_w:
        place_size -= 2
        place_font = load_font(place_size, 800)
    if text_width(place_font, place, tracking=place_tr) > inner_w:
        place_tr = 0
        place = ellipsize(place, place_font, inner_w)
    place_h = text_height(place_font, "H")

    headline_font, lines, size = fit_wrapped(
        item["text"],
        max_width=inner_w,
        max_lines=6,
        max_size=168,
        min_size=92,
        weight=900,
    )
    lh = int(round(size * 1.14))
    head_h = lh * (len(lines) - 1) + text_height(headline_font, lines[0])
    gap = 36
    block_h = place_h + gap + head_h
    start = well_top + max(0, int((well_h - block_h) * 0.42))

    # Short orange rule above the place tag, for a broadcast-style lockup.
    rule_w = 72
    rule_h = 8
    draw.rectangle((pad, int(start) - 28, pad + rule_w, int(start) - 28 + rule_h), fill=ORANGE)

    draw_tracked(draw, (pad, start), place, place_font, ORANGE, tracking=place_tr)
    hy = start + place_h + gap
    for i, line in enumerate(lines):
        draw_text(draw, (pad, hy + i * lh), line, headline_font, WHITE)

    return img


def render_vcard(payload: dict[str, Any], out_path: str) -> str:
    items = payload["items"]
    if len(items) == 1:
        # Backdrop is built inside render_hero (returns the image).
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(img)
        img = render_hero(img, draw, payload["title"], payload["datetime"], items[0])
    else:
        img = Image.new("RGB", (WIDTH, HEIGHT), BG)
        draw = ImageDraw.Draw(img)
        render_list(img, draw, payload["title"], payload["datetime"], items)
    return save_png_native(img, out_path)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a 2160x3840 vertical news card for @NBSHARMA3")
    p.add_argument("--json", required=True, help="Path to JSON with title, datetime, items")
    p.add_argument("--out", required=True, help="Output PNG path")
    return p.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    payload = load_payload(args.json)
    path = render_vcard(payload, args.out)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
