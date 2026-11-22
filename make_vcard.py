#!/usr/bin/env python3
"""Generate a vertical 1080x1920 news list/hero card for @NBSHARMA3 (code-drawn, no AI).

Usage:
  python3 make_vcard.py --json card.json --out card.png

JSON shape:
  {
    "title": "TOP 10 WORLD NEWS",
    "datetime": "6:00 AM IST, 22 Nov 2026",
    "items": [{"place": "TOKYO", "text": "Headline in our own words."}]
  }

Supports 1–10 items. A single item uses a large hero layout (good news).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Iterable

# Allow running from any cwd / copied location next to cardutil.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw, ImageFont

from cardutil import (
    BG,
    BRAND,
    DIVIDER,
    HANDLE,
    HEIGHT,
    ORANGE,
    SCALE,
    WHITE,
    WIDTH,
    draw_brand_row,
    draw_footer,
    draw_text,
    draw_top_bar,
    draw_tracked,
    ellipsize,
    fit_wrapped,
    load_font,
    new_canvas,
    save_png,
    text_height,
    text_width,
    wrap_text,
)

PAD_X = 68
MIN_HEAD = 34  # never shrink list headlines below this (1x pixels)


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


def _title_font(
    title: str, max_width: float, s: int, max_size: int, min_size: int
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    return fit_wrapped(title, max_width, 2, max_size * s, min_size * s, 800)


def render_hero(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    s: int,
    title: str,
    datetime_str: str,
    item: dict[str, str],
) -> None:
    top = draw_top_bar(draw, s, bar_h=12)
    y = top + 36 * s
    y = draw_brand_row(draw, s, y, datetime_str, pad_x=PAD_X, brand_size=26, date_size=22)
    y = y + 28 * s

    pad = PAD_X * s
    content_w = WIDTH * s - 2 * pad

    title_font, title_lines, title_size = _title_font(title, content_w, s, 52, 36)
    title_lh = int(round(title_size * 1.12))
    # Title in orange for good-news / section feel
    for i, line in enumerate(title_lines):
        draw_text(draw, (pad, y + i * title_lh), line, title_font, ORANGE)
    y = y + title_lh * (len(title_lines) - 1) + text_height(title_font, title_lines[0])
    y = y + 22 * s
    draw.rectangle((pad, y, WIDTH * s - pad, y + 2 * s), fill=DIVIDER)
    header_bottom = y + 2 * s

    rule_y = draw_footer(draw, img, s, pad_x=PAD_X, handle_size=32, icon_size=32, bottom_pad=56)
    content_top = header_bottom + 48 * s
    content_bottom = rule_y - 48 * s
    window = content_bottom - content_top
    inner_w = content_w

    place = item["place"]
    place_font, place_size = load_font(58 * s, 800), 58 * s
    # Shrink the place tag if it is unusually long (e.g. WASHINGTON, DC).
    while place_size > 36 * s and text_width(place_font, place, tracking=6.0 * s * (place_size / (58 * s))) > inner_w:
        place_size -= 2 * s
        place_font = load_font(place_size, 800)
    place_tracking = max(4.0 * s, 6.0 * s * (place_size / (58 * s)))
    if text_width(place_font, place, tracking=place_tracking) > inner_w:
        # Fall back to untracked ellipsis rather than overflow.
        place_tracking = 0
        place = ellipsize(place, place_font, inner_w)
    place_h = text_height(place_font, place or "H")

    headline_font, headline_lines, headline_size = fit_wrapped(
        item["text"],
        max_width=inner_w,
        max_lines=7,
        max_size=96 * s,
        min_size=52 * s,
        weight=800,
    )
    headline_lh = int(round(headline_size * 1.16))
    headline_block_h = headline_lh * (len(headline_lines) - 1) + text_height(
        headline_font, headline_lines[0] if headline_lines else "Hg"
    )

    gap = 28 * s
    block_h = place_h + gap + headline_block_h
    start = content_top + max(0, int((window - block_h) * 0.42))

    draw_tracked(draw, (pad, start), place, place_font, ORANGE, tracking=place_tracking)
    hy = start + place_h + gap
    for i, line in enumerate(headline_lines):
        draw_text(draw, (pad, hy + i * headline_lh), line, headline_font, WHITE)


def _list_line_budget(n: int) -> int:
    if n <= 3:
        return 3
    if n <= 6:
        return 3 if n <= 4 else 2
    return 2


def render_list(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    s: int,
    title: str,
    datetime_str: str,
    items: list[dict[str, str]],
) -> None:
    n = len(items)
    top = draw_top_bar(draw, s, bar_h=12)
    y = top + 32 * s
    y = draw_brand_row(draw, s, y, datetime_str, pad_x=PAD_X, brand_size=24, date_size=22)
    y = y + 22 * s

    pad = PAD_X * s
    content_w = WIDTH * s - 2 * pad

    # Bigger title when there are fewer rows to fill.
    max_title = 50 if n >= 8 else 54
    title_font, title_lines, title_size = _title_font(title, content_w, s, max_title, 34)
    title_lh = int(round(title_size * 1.12))
    for i, line in enumerate(title_lines):
        draw_text(draw, (pad, y + i * title_lh), line, title_font, WHITE)
    y = y + title_lh * (len(title_lines) - 1) + text_height(title_font, title_lines[0])
    y = y + 20 * s
    draw.rectangle((pad, y, WIDTH * s - pad, y + 2 * s), fill=DIVIDER)
    header_bottom = y + 2 * s

    rule_y = draw_footer(draw, img, s, pad_x=PAD_X, handle_size=30, icon_size=30, bottom_pad=56)
    content_top = header_bottom + 22 * s
    content_bottom = rule_y - 22 * s
    available = content_bottom - content_top
    row_h = available / n

    max_lines = _list_line_budget(n)
    # Starting sizes scale up when fewer items share the card.
    if n <= 3:
        num_max, head_max = 56, 48
    elif n <= 5:
        num_max, head_max = 50, 44
    elif n <= 7:
        num_max, head_max = 46, 40
    else:
        num_max, head_max = 42, 38

    min_head = MIN_HEAD
    chosen = None
    # Walk sizes down until every row fits, never going below min_head.
    for num_size in range(num_max, 34, -2):
        for head_size in range(head_max, min_head - 1, -2):
            place_size = max(26, int(round(num_size * 0.80)))
            num_font = load_font(num_size * s, 800)
            place_font = load_font(place_size * s, 800)
            head_font = load_font(head_size * s, 800)
            num_col = int(text_width(num_font, "10") + 22 * s)
            text_col = content_w - num_col
            if text_col < 200 * s:
                continue
            num_h = text_height(num_font, "10")
            place_h = text_height(place_font, "H")
            tag_h = max(num_h, place_h)
            head_lh = int(round(head_size * s * 1.18))
            # Measure each item
            prepared = []
            max_used = 0
            ok = True
            for idx, it in enumerate(items, 1):
                num = f"{idx:02d}"
                place = it["place"]
                place_tr = 3.0 * s
                if text_width(place_font, place, tracking=place_tr) > text_col:
                    place_tr = 0
                    place = ellipsize(place, place_font, text_col)
                lines = wrap_text(it["text"], head_font, text_col)
                if len(lines) > max_lines:
                    lines = lines[:max_lines]
                    lines[-1] = ellipsize(lines[-1], head_font, text_col)
                else:
                    lines = [ellipsize(ln, head_font, text_col) for ln in lines]
                if not lines:
                    lines = ["…"]
                head_block = head_lh * (len(lines) - 1) + text_height(head_font, lines[0])
                used = tag_h + 8 * s + head_block
                max_used = max(max_used, used)
                prepared.append((num, place, place_tr, lines, head_block, tag_h))
            # Vertical padding inside the row (divider lives in the leftover).
            if max_used + 16 * s <= row_h:
                chosen = {
                    "num_font": num_font,
                    "place_font": place_font,
                    "head_font": head_font,
                    "head_lh": head_lh,
                    "num_col": num_col,
                    "prepared": prepared,
                    "num_size": num_size,
                    "head_size": head_size,
                }
                ok = True
                break
            ok = False
        if chosen:
            break

    if not chosen:
        # Last-resort fit at minimum headline size, truncating as needed.
        num_size, head_size = 36, min_head
        place_size = 22
        num_font = load_font(num_size * s, 800)
        place_font = load_font(place_size * s, 800)
        head_font = load_font(head_size * s, 800)
        num_col = int(text_width(num_font, "10") + 22 * s)
        text_col = content_w - num_col
        num_h = text_height(num_font, "10")
        place_h = text_height(place_font, "H")
        tag_h = max(num_h, place_h)
        head_lh = int(round(head_size * s * 1.16))
        prepared = []
        for idx, it in enumerate(items, 1):
            num = f"{idx:02d}"
            place = ellipsize(it["place"], place_font, text_col)
            lines = wrap_text(it["text"], head_font, text_col) or ["…"]
            # Drop lines until the row fits.
            while lines:
                head_block = head_lh * (len(lines) - 1) + text_height(head_font, lines[0])
                if tag_h + 8 * s + head_block + 10 * s <= row_h:
                    break
                if len(lines) == 1:
                    lines[0] = ellipsize(lines[0], head_font, text_col)
                    break
                lines = lines[:-1]
                lines[-1] = ellipsize(lines[-1], head_font, text_col)
            head_block = head_lh * (len(lines) - 1) + text_height(head_font, lines[0])
            prepared.append((num, place, 0, lines, head_block, tag_h))
        chosen = {
            "num_font": num_font,
            "place_font": place_font,
            "head_font": head_font,
            "head_lh": head_lh,
            "num_col": num_col,
            "prepared": prepared,
        }

    # Draw rows, vertically centred within each row slot.
    for i, (num, place, place_tr, lines, head_block, tag_h) in enumerate(chosen["prepared"]):
        slot_top = content_top + i * row_h
        used = tag_h + 8 * s + head_block
        inner_top = slot_top + max(0, (row_h - used) * 0.42)
        # Number
        draw_text(draw, (pad, inner_top), num, chosen["num_font"], ORANGE)
        # Place on the same baseline band as the number
        num_h = text_height(chosen["num_font"], num)
        place_h = text_height(chosen["place_font"], place)
        place_y = inner_top + max(0, (num_h - place_h) / 2)
        draw_tracked(
            draw,
            (pad + chosen["num_col"], place_y),
            place,
            chosen["place_font"],
            ORANGE,
            tracking=place_tr,
        )
        hy = inner_top + tag_h + 8 * s
        for li, line in enumerate(lines):
            draw_text(
                draw,
                (pad + chosen["num_col"], hy + li * chosen["head_lh"]),
                line,
                chosen["head_font"],
                WHITE,
            )
        # Hairline between rows (not after the last).
        if i < n - 1:
            line_y = slot_top + row_h - s  # 0.5px at 1x, use 1px at 1x = 2s? keep 1s
            draw.rectangle((pad, int(slot_top + row_h) - 2, WIDTH * s - pad, int(slot_top + row_h)), fill=DIVIDER)


def render_vcard(payload: dict[str, Any], out_path: str) -> str:
    items = payload["items"]
    img, draw, s = new_canvas()
    if len(items) == 1:
        render_hero(img, draw, s, payload["title"], payload["datetime"], items[0])
    else:
        render_list(img, draw, s, payload["title"], payload["datetime"], items)
    return save_png(img, out_path)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a 1080x1920 vertical news card for @NBSHARMA3")
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
