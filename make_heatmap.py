#!/usr/bin/env python3
"""Render a vertical 9:16 global-market heatmap video (Pillow + ffmpeg, no AI).

Usage:
  python3 make_heatmap.py --json markets.json --out heatmap.mp4
  python3 make_heatmap.py --json markets.json --out heatmap.mp4 --duration 8
  python3 make_heatmap.py --json markets.json --out heatmap.mp4 --png heatmap.png

The first half of the duration (capped at 4s) counts every percentage up
from 0.00 and animates tile fills from burgundy toward red (losses) or
green (gains). The rest of the clip holds the final frame. Output is a
1080x1920 H.264 High yuv420p 30fps MP4 with faststart and a silent AAC track (Buffer/X friendly).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw

from cardutil import (
    BG,
    HANDLE,
    HEIGHT,
    ORANGE,
    WHITE,
    WIDTH,
    draw_footer,
    draw_pill,
    draw_text,
    load_font,
    measure_pill,
    new_canvas,
    text_height,
    text_width,
)

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
FLAG_DIR = os.path.join(REPO_DIR, "assets", "flags")
FLAG_URL = "https://flagcdn.com/w1280/{iso}.png"
FLAG_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

FPS = 30
OUT_W, OUT_H = WIDTH, HEIGHT

# Colour scale (matches the reference heatmap language).
ZERO = (0x5A, 0x04, 0x08)       # #5a0408  — 0%
RED_MAX = (0xFE, 0x2D, 0x2D)    # #fe2d2d  — large loss
GREEN_LO = (0x0F, 0x8B, 0x3B)   # #0f8b3b  — modest gain
GREEN_HI = (0x1F, 0xC4, 0x5A)   # #1fc45a  — large gain
TEXT = (250, 250, 250)
GUTTER_BG = (11, 11, 11)
PILL_FILL = (28, 30, 34)

RED_FULL = 16.0    # |loss| % at which red saturates
GREEN_FULL = 8.0   # gain % at which green saturates

# Code-drawn asset discs.
GOLD_FILL = (201, 154, 39)
GOLD_RIM = (232, 197, 86)
GOLD_TEXT = (62, 40, 8)
SILVER_FILL = (176, 182, 188)
SILVER_RIM = (220, 224, 228)
SILVER_TEXT = (40, 44, 48)
BTC_FILL = (247, 147, 26)  # bitcoin orange
BTC_RIM = (255, 190, 80)
BTC_TEXT = (255, 255, 255)

TINY_CODES = {"GOLD": "AU", "SILVER": "AG", "BTC": "BTC"}


# ---------------------------------------------------------------------------
# Colour + number helpers
# ---------------------------------------------------------------------------

def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def lerp_rgb(
    c0: tuple[int, int, int], c1: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return (
        int(round(lerp(c0[0], c1[0], t))),
        int(round(lerp(c0[1], c1[1], t))),
        int(round(lerp(c0[2], c1[2], t))),
    )


def color_for(pct: float) -> tuple[int, int, int]:
    """Fill colour for a (possibly in-flight) percentage value."""
    if pct <= 0:
        t = min(1.0, abs(pct) / RED_FULL)
        t = t ** 0.9
        return lerp_rgb(ZERO, RED_MAX, t)
    # Jump quickly off burgundy so even a small gain reads as green,
    # then climb toward the brighter green.
    if pct < 0.6:
        return lerp_rgb(ZERO, GREEN_LO, pct / 0.6)
    t = min(1.0, (pct - 0.6) / (GREEN_FULL - 0.6))
    return lerp_rgb(GREEN_LO, GREEN_HI, t)


def ease_out_quad(t: float) -> float:
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    return 1.0 - (1.0 - t) * (1.0 - t)


def format_pct(value: float, final: float) -> str:
    """Two decimals with an explicit + / − sign (Unicode minus)."""
    if abs(value) < 0.005 and abs(final) < 0.005:
        return "0.00%"
    if final < 0 or value < 0:
        return f"−{abs(value):.2f}%"
    if final > 0 or value > 0:
        return f"+{value:.2f}%"
    return "0.00%"


# ---------------------------------------------------------------------------
# Squarified treemap (Bruls, Huizing, van Wijk)
# ---------------------------------------------------------------------------

def _layout_row(sizes: list[float], x: float, y: float, dx: float, dy: float) -> list[tuple[float, float, float, float]]:
    covered = sum(sizes)
    width = covered / dy if dy else 0.0
    rects = []
    yy = y
    for size in sizes:
        h = size / width if width else 0.0
        rects.append((x, yy, width, h))
        yy += h
    return rects


def _layout_col(sizes: list[float], x: float, y: float, dx: float, dy: float) -> list[tuple[float, float, float, float]]:
    covered = sum(sizes)
    height = covered / dx if dx else 0.0
    rects = []
    xx = x
    for size in sizes:
        w = size / height if height else 0.0
        rects.append((xx, y, w, height))
        xx += w
    return rects


def _layout(sizes: list[float], x: float, y: float, dx: float, dy: float):
    return _layout_row(sizes, x, y, dx, dy) if dx >= dy else _layout_col(sizes, x, y, dx, dy)


def _leftover(sizes: list[float], x: float, y: float, dx: float, dy: float):
    covered = sum(sizes)
    if dx >= dy:
        width = covered / dy if dy else 0.0
        return x + width, y, dx - width, dy
    height = covered / dx if dx else 0.0
    return x, y + height, dx, dy - height


def _worst(sizes: list[float], x: float, y: float, dx: float, dy: float) -> float:
    rects = _layout(sizes, x, y, dx, dy)
    worst = 1.0
    for _x, _y, w, h in rects:
        if w <= 0 or h <= 0:
            return math.inf
        worst = max(worst, w / h, h / w)
    return worst


def squarify(
    sizes: list[float], x: float, y: float, dx: float, dy: float
) -> list[tuple[float, float, float, float]]:
    sizes = [s for s in sizes if s > 0]
    if not sizes or dx <= 0 or dy <= 0:
        return []
    if len(sizes) == 1:
        return _layout(sizes, x, y, dx, dy)
    i = 1
    while i < len(sizes) and _worst(sizes[:i], x, y, dx, dy) >= _worst(sizes[:i + 1], x, y, dx, dy):
        i += 1
    current, rest = sizes[:i], sizes[i:]
    return _layout(current, x, y, dx, dy) + squarify(rest, *_leftover(current, x, y, dx, dy))


def layout_tiles(
    items: list[dict], x: int, y: int, w: int, h: int, gutter: int
) -> list[dict]:
    """Pack items into the rectangle and inset each tile by a half-gutter."""
    ordered = sorted(items, key=lambda it: (-float(it["weight"]), it["name"]))
    sizes = [float(it["weight"]) for it in ordered]
    total = sum(sizes)
    if total <= 0:
        return []
    area = float(w) * float(h)
    norm = [s / total * area for s in sizes]
    raw = squarify(norm, float(x), float(y), float(w), float(h))
    laid: list[dict] = []
    half = gutter / 2.0
    for item, (rx, ry, rw, rh) in zip(ordered, raw):
        x0 = int(round(rx + half))
        y0 = int(round(ry + half))
        x1 = int(round(rx + rw - half))
        y1 = int(round(ry + rh - half))
        if x1 - x0 < 4 or y1 - y0 < 4:
            continue
        laid.append({**item, "box": (x0, y0, x1 - x0, y1 - y0)})
    return laid


# ---------------------------------------------------------------------------
# Flags and asset discs
# ---------------------------------------------------------------------------

def ensure_flag(iso: str) -> str | None:
    """Download a flag PNG once and cache it under assets/flags/."""
    iso = iso.lower()
    os.makedirs(FLAG_DIR, exist_ok=True)
    path = os.path.join(FLAG_DIR, f"{iso}.png")
    if os.path.isfile(path) and os.path.getsize(path) > 100:
        return path
    url = FLAG_URL.format(iso=iso)
    req = urllib.request.Request(url, headers={"User-Agent": FLAG_UA, "Accept": "image/png"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
        if len(data) < 100 or not data.startswith(b"\x89PNG"):
            print(f"WARN  flag {iso}: unexpected response", file=sys.stderr)
            return None
        with open(path, "wb") as fh:
            fh.write(data)
        return path
    except Exception as exc:
        print(f"WARN  flag {iso}: {exc}", file=sys.stderr)
        return None


def _circle_mask(diameter: int) -> Image.Image:
    scale = 4
    big = diameter * scale
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    return mask.resize((diameter, diameter), Image.Resampling.LANCZOS)


def _add_soft_ring(icon: Image.Image, rgba: tuple[int, int, int, int] = (255, 255, 255, 90)) -> Image.Image:
    """A faint inner ring so flags stay visible on a same-colour tile."""
    d = icon.size[0]
    scale = 4
    big = max(8, d * scale)
    ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(ring)
    width = max(scale, int(big * 0.024))
    inset = width // 2 + scale
    draw.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), outline=rgba, width=width)
    ring = ring.resize((d, d), Image.Resampling.LANCZOS)
    return Image.alpha_composite(icon, ring)


def circular_flag(path: str, diameter: int) -> Image.Image:
    src = Image.open(path).convert("RGBA")
    sw, sh = src.size
    side = min(sw, sh)
    left = (sw - side) // 2
    top = (sh - side) // 2
    cropped = src.crop((left, top, left + side, top + side))
    face = cropped.resize((diameter, diameter), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", (diameter, diameter), (0, 0, 0, 0))
    out.paste(face, (0, 0))
    out.putalpha(_circle_mask(diameter))
    return _add_soft_ring(out)


def _disc(diameter: int, fill: tuple[int, int, int], rim: tuple[int, int, int]) -> Image.Image:
    scale = 4
    big = max(8, diameter * scale)
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, big - 1, big - 1), fill=rim + (255,))
    inset = max(scale, int(big * 0.06))
    d.ellipse((inset, inset, big - 1 - inset, big - 1 - inset), fill=fill + (255,))
    return img.resize((diameter, diameter), Image.Resampling.LANCZOS)


def asset_disc(kind: str, diameter: int) -> Image.Image:
    """Code-drawn Gold / Silver / Bitcoin disc with a chemical or ₿ mark."""
    if kind == "GOLD":
        fill, rim, fg, label = GOLD_FILL, GOLD_RIM, GOLD_TEXT, "Au"
    elif kind == "SILVER":
        fill, rim, fg, label = SILVER_FILL, SILVER_RIM, SILVER_TEXT, "Ag"
    else:
        fill, rim, fg, label = BTC_FILL, BTC_RIM, BTC_TEXT, "₿"
    disc = _disc(diameter, fill, rim)
    # Draw the label on a 4x canvas so the type stays sharp when downscaled.
    scale = 4
    big = diameter * scale
    overlay = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    # Size the mark to sit comfortably inside the disc.
    target = int(diameter * (0.42 if kind != "BTC" else 0.48) * scale)
    font = load_font(max(8, target), 800)
    # Vertically centre on cap-height so the 'g' in Ag does not drop the mark.
    od.text((big / 2, big / 2), label, font=font, fill=fg + (255,), anchor="mm")
    overlay = overlay.resize((diameter, diameter), Image.Resampling.LANCZOS)
    return Image.alpha_composite(disc, overlay)


# ---------------------------------------------------------------------------
# Tile typography
# ---------------------------------------------------------------------------

def _fit_line(text: str, max_w: float, max_size: int, min_size: int, weight: int):
    """Largest font in [min, max] whose text fits in max_w."""
    chosen = load_font(min_size, weight)
    if max_w <= 1:
        return chosen, min_size
    lo, hi, best_size = min_size, max_size, min_size
    while lo <= hi:
        mid = (lo + hi) // 2
        font = load_font(mid, weight)
        if text_width(font, text) <= max_w:
            chosen, best_size = font, mid
            lo = mid + 1
        else:
            hi = mid - 1
    return chosen, best_size


def _load_icon(tile: dict, code: str, diameter: int, s: int) -> Image.Image | None:
    if diameter < 16 * s:
        return None
    flag = tile.get("flag")
    if flag:
        path = ensure_flag(str(flag))
        if path:
            return circular_flag(path, diameter)
    if code in ("GOLD", "SILVER", "BTC"):
        return asset_disc(code, diameter)
    return None


def plan_tile(tile: dict, s: int) -> dict:
    """Compute flag, fonts and positions for a tile (scale-space pixels).

    Large tiles scale the flag and the % up so they fill the box. Small
    tiles drop the name (and then the flag) rather than drawing type that
    would be illegible after the 2x downscale.
    """
    x, y, w, h = tile["box"]
    name = str(tile["name"]).upper()
    code = str(tile.get("code") or name)
    short = TINY_CODES.get(code, code)
    final = float(tile["pct"])
    sample = format_pct(final if final != 0 else 8.88, final if final != 0 else 8.88)

    pad = max(4 * s, int(min(w, h) * 0.07))
    inner_w = max(1, w - 2 * pad)
    inner_h = max(1, h - 2 * pad)
    plan: dict = {
        "box": (x, y, w, h),
        "final": final,
        "name": name,
        "code": code,
        "short": short,
        "icon": None,
        "icon_xy": None,
        "label": None,
        "label_font": None,
        "label_xy": None,
        "pct_font": None,
        "pct_center": (x + w / 2, y + h / 2),
    }

    min_name = 17 * s  # ~17px after downscale — below this, switch to a short code
    min_pct = 16 * s
    min_icon = 24 * s

    def finish(icon, icon_d, label, label_font, pct_font, gap1, gap2) -> dict:
        label_h = text_height(label_font, label) if label and label_font else 0
        pct_h = text_height(pct_font, sample)
        icon_h = icon_d if icon is not None else 0
        g1 = gap1 if icon is not None else 0
        g2 = gap2 if label else max(2 * s, int(inner_h * 0.04))
        parts = icon_h + g1 + label_h + (g2 if label else 0) + pct_h
        cy = y + (h - parts) / 2
        if icon is not None:
            plan["icon"] = icon
            plan["icon_xy"] = (int(round(x + (w - icon_d) / 2)), int(round(cy)))
            cy += icon_d + g1
        if label and label_font is not None:
            plan["label"] = label
            plan["label_font"] = label_font
            plan["label_xy"] = (x + w / 2, cy)
            cy += label_h + g2
        plan["pct_font"] = pct_font
        plan["pct_center"] = (x + w / 2, cy + pct_h / 2)
        return plan

    def plan_code_pct() -> dict:
        gap = max(2 * s, int(inner_h * 0.06))
        if inner_h >= 28 * s:
            max_code = max(8 * s, min(24 * s, int(inner_h * 0.34), int(inner_w * 0.55)))
            max_pct_s = max(10 * s, min(40 * s, int(inner_h * 0.50), int(inner_w * 0.85)))
            code_font, _ = _fit_line(short, inner_w, max_code, 7 * s, 700)
            pct_font, _ = _fit_line(sample, inner_w, max_pct_s, 8 * s, 800)
            return finish(None, 0, short, code_font, pct_font, 0, gap)
        pct_font, _ = _fit_line(sample, inner_w, max(8 * s, int(inner_h * 0.8)), 7 * s, 800)
        plan["pct_font"] = pct_font
        plan["pct_center"] = (x + w / 2, y + h / 2)
        return plan

    if inner_w < 10 * s or inner_h < 10 * s:
        pct_font, _ = _fit_line(sample, max(4, inner_w), max(8, int(inner_h * 0.7)), 6 * s, 800)
        plan["pct_font"] = pct_font
        return plan

    min_side = min(inner_w, inner_h)
    # Narrow or short tiles cannot carry a flag plus two lines of type.
    if min_side < 58 * s or inner_h < 52 * s or inner_w < 58 * s:
        return plan_code_pct()

    # Prefer the full country name on medium and large tiles. Below
    # ~90px the name is too cramped, so use the short code instead.
    label = name
    if min(w, h) < 90 * s or text_width(load_font(min_name, 750), name) > inner_w:
        label = short

    max_icon = int(min(inner_w * 0.52, inner_h * 0.46))
    icon_d = max_icon
    use_icon = icon_d >= min_icon

    def try_layout(label_text: str, with_icon: bool):
        # Reserve typography first so the flag can never starve the numbers.
        gap2 = max(3 * s, int(inner_h * 0.05))
        # Approximate ink we must keep for a readable label + %.
        reserved = int(min_name * 0.75) + int(min_pct * 0.80) + gap2
        if with_icon:
            gap1 = max(4 * s, int(inner_h * 0.04))
            d = min(icon_d, inner_h - reserved - gap1)
            if d < min_icon:
                return None
            gap1 = max(4 * s, int(d * 0.10))
        else:
            d = 0
            gap1 = 0
        remain = inner_h - d - gap1 - gap2
        if remain < min_pct:
            return None
        # The % is the hero; the name is always smaller than the number.
        max_pct_s = max(min_pct, min(180 * s, int(remain * 0.62)))
        pct_font, pct_sz = _fit_line(sample, inner_w, max_pct_s, 10 * s, 800)
        if pct_sz < min_pct:
            return None
        max_name_s = min(80 * s, int(pct_sz * 0.72))
        if max_name_s < min_name:
            max_name_s = min_name
        lab_font, lab_sz = _fit_line(label_text, inner_w, max_name_s, min_name, 750)
        if lab_sz < min_name:
            return None
        lab_h = text_height(lab_font, label_text)
        pct_h = text_height(pct_font, sample)
        stack = d + gap1 + lab_h + gap2 + pct_h
        if stack > inner_h + 1:
            overflow = int(stack - inner_h)
            if with_icon and d - overflow >= min_icon:
                d = d - overflow
            else:
                return None
        icon = _load_icon(tile, code, d, s) if with_icon else None
        if with_icon and icon is None:
            return None
        return finish(icon, d if icon is not None else 0, label_text, lab_font, pct_font, gap1, gap2)

    # Full layout, then gradually give things up so type stays readable.
    for lab, icn in ((label, True), (short, True), (label, False), (short, False)):
        built = try_layout(lab, icn)
        if built is not None:
            return built
    return plan_code_pct()


def draw_label_centered(draw: ImageDraw.ImageDraw, plan: dict, fill=TEXT) -> None:
    label = plan.get("label")
    font = plan.get("label_font")
    xy = plan.get("label_xy")
    if not label or font is None or xy is None:
        return
    # xy is top-centre of the label box; use cap-height top alignment via 'mt'.
    draw.text((xy[0], xy[1]), label, font=font, fill=fill, anchor="mt")


def draw_pct(draw: ImageDraw.ImageDraw, plan: dict, current: float, fill=TEXT) -> None:
    font = plan.get("pct_font")
    cx, cy = plan["pct_center"]
    if font is None:
        return
    text = format_pct(current, plan["final"])
    draw.text((cx, cy), text, font=font, fill=fill, anchor="mm")


# ---------------------------------------------------------------------------
# Frame layout (header + footer + treemap)
# ---------------------------------------------------------------------------

def chrome_bounds(s: int) -> tuple[int, int, int, int, int]:
    """Return (pad, header_bottom, footer_rule_y, treemap_y, treemap_h) in scale space."""
    pad = 48 * s
    bar_h = 10 * s
    top_gap = 22 * s
    title_size = 36 * s
    title_font = load_font(title_size, 800)
    title_h = text_height(title_font, "GLOBAL MARKETS THIS WEEK")
    below = 18 * s
    header_bottom = bar_h + top_gap + title_h + below
    # Footer matches cardutil.draw_footer with slim paddings.
    handle_size = 26 * s
    icon_size = 26 * s
    bottom_pad = 40 * s
    handle_font = load_font(handle_size, 800)
    footer_content_h = max(icon_size, text_height(handle_font, HANDLE))
    footer_bottom = HEIGHT * s - bottom_pad
    content_y = footer_bottom - footer_content_h
    rule_y = content_y - 16 * s
    treemap_y = header_bottom
    treemap_h = rule_y - 16 * s - treemap_y
    return pad, header_bottom, rule_y, treemap_y, treemap_h


def draw_header(draw: ImageDraw.ImageDraw, s: int, title: str, date_range: str) -> None:
    W, H = WIDTH * s, HEIGHT * s
    # Slim orange cap, same language as the news cards.
    draw.rectangle((0, 0, W, 10 * s), fill=ORANGE)

    pad = 48 * s
    title_font = load_font(36 * s, 800)
    title = title.upper()
    title_h = text_height(title_font, title)
    title_y = 10 * s + 22 * s

    # Short orange accent bar, optically centred on the cap-height.
    bar_w, bar_h = 28 * s, 6 * s
    bar_y = title_y + max(0, (title_h - bar_h) // 2) + 1 * s
    draw.rectangle((pad, bar_y, pad + bar_w, bar_y + bar_h), fill=ORANGE)

    title_x = pad + bar_w + 14 * s
    # Leave room for the date pill on the right.
    pill_font = load_font(20 * s, 700)
    pill_pad_x, pill_pad_y = 14 * s, 7 * s
    pill_w, pill_h = measure_pill(date_range, pill_font, pill_pad_x, pill_pad_y)
    max_title_w = W - pad - pill_w - 24 * s - title_x
    # Shrink the title if the week label needs the space.
    size = 36 * s
    while size > 22 * s and text_width(title_font, title) > max_title_w:
        size -= 2
        title_font = load_font(size, 800)
        title_h = text_height(title_font, title)
    draw_text(draw, (title_x, title_y), title, title_font, WHITE)

    pill_x = W - pad - pill_w
    pill_y = title_y + max(0, (title_h - pill_h) // 2)
    draw_pill(
        draw,
        (pill_x, pill_y),
        date_range,
        pill_font,
        fill=PILL_FILL,
        fg=ORANGE,
        pad_x=pill_pad_x,
        pad_y=pill_pad_y,
        outline=ORANGE,
        outline_w=max(2, 2 * s),
    )


def render_frame(
    plans: list[dict],
    progress: float,
    title: str,
    date_range: str,
) -> Image.Image:
    img, draw, s = new_canvas()
    # Dark stage behind the treemap gutters (cardutil canvas starts as BG).
    draw.rectangle((0, 0, WIDTH * s, HEIGHT * s), fill=GUTTER_BG)
    draw_header(draw, s, title, date_range)
    draw_footer(
        draw,
        img,
        s,
        pad_x=48,
        handle_size=26,
        icon_size=26,
        bottom_pad=40,
    )

    t = ease_out_quad(progress)
    for plan in plans:
        x, y, w, h = plan["box"]
        current = plan["final"] * t
        fill = color_for(current)
        draw.rectangle((x, y, x + w, y + h), fill=fill)
        icon = plan.get("icon")
        xy = plan.get("icon_xy")
        if icon is not None and xy is not None:
            img.paste(icon, xy, icon)
        draw_label_centered(draw, plan)
        draw_pct(draw, plan, current)
    return img


def downscale_frame(img: Image.Image) -> Image.Image:
    if img.size == (OUT_W, OUT_H):
        return img
    return img.resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)


# ---------------------------------------------------------------------------
# Video encode
# ---------------------------------------------------------------------------

def encode_mp4(frames_iter, n_frames: int, out_path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    duration = n_frames / FPS
    cmd = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{OUT_W}x{OUT_H}",
        "-r",
        str(FPS),
        "-i",
        "pipe:0",
        "-f",
        "lavfi",
        "-t",
        f"{duration:.4f}",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-profile:v",
        "high",
        "-pix_fmt",
        "yuv420p",
        "-tag:v",
        "avc1",
        "-crf",
        "18",
        "-r",
        str(FPS),
        "-c:a",
        "aac",
        "-b:a",
        "96k",
        "-ac",
        "2",
        "-shortest",
        "-movflags",
        "+faststart",
        out_path,
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin is not None
    try:
        for i, frame in enumerate(frames_iter):
            if i >= n_frames:
                break
            if frame.size != (OUT_W, OUT_H):
                frame = downscale_frame(frame)
            if frame.mode != "RGB":
                frame = frame.convert("RGB")
            proc.stdin.write(frame.tobytes())
        proc.stdin.close()
        ret = proc.wait()
    except Exception:
        proc.kill()
        proc.wait()
        raise
    if ret != 0 or not os.path.isfile(out_path) or os.path.getsize(out_path) < 1000:
        raise RuntimeError(f"ffmpeg failed to encode {out_path} (exit {ret})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def load_payload(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    items = data.get("items") or []
    if not items:
        raise SystemExit("markets JSON has no items")
    clean = []
    for it in items:
        if "name" not in it or "pct" not in it or "weight" not in it:
            continue
        row = {
            "name": str(it["name"]),
            "code": str(it.get("code") or it["name"]),
            "pct": float(it["pct"]),
            "weight": float(it["weight"]),
        }
        if it.get("flag"):
            row["flag"] = str(it["flag"])
        if row["weight"] <= 0:
            continue
        clean.append(row)
    if len(clean) < 4:
        raise SystemExit("need at least 4 markets to build a heatmap")
    data["items"] = clean
    data["title"] = data.get("title") or "GLOBAL MARKETS THIS WEEK"
    data["date_range"] = data.get("date_range") or ""
    return data


def build_plans(items: list[dict], s: int) -> list[dict]:
    pad, _header_bottom, _rule_y, treemap_y, treemap_h = chrome_bounds(s)
    # A slim inset so tiles do not kiss the left/right edges or the chrome.
    tx, tw = pad // 2, WIDTH * s - pad
    gutter = max(3 * s, 4)
    tiles = layout_tiles(items, tx, treemap_y, tw, treemap_h, gutter)
    # Warm the flag cache first so a missing download does not stall mid-plan.
    for tile in tiles:
        if tile.get("flag"):
            ensure_flag(str(tile["flag"]))
    return [plan_tile(tile, s) for tile in tiles]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", required=True, help="markets.json from fetch_markets.py")
    parser.add_argument("--out", required=True, help="Output MP4 path")
    parser.add_argument("--duration", type=float, default=8.0, help="Length in seconds (default 8)")
    parser.add_argument(
        "--png",
        nargs="?",
        const=True,
        default=None,
        help="Also write the final frame as a PNG (optionally a path)",
    )
    args = parser.parse_args()

    duration = float(args.duration)
    if duration < 2.0:
        raise SystemExit("duration must be at least 2 seconds")
    n_frames = int(round(duration * FPS))
    animate_s = min(4.0, duration * 0.5)
    animate_frames = max(1, int(round(animate_s * FPS)))

    payload = load_payload(args.json)
    from cardutil import SCALE

    s = SCALE
    plans = build_plans(payload["items"], s)
    title = payload["title"]
    date_range = payload["date_range"]

    def frames():
        for i in range(n_frames):
            if i >= animate_frames:
                progress = 1.0
            else:
                t = i / max(1, animate_frames - 1)
                progress = 1.0 - (1.0 - t) ** 2.2  # ease-out: fast start, smooth settle
            raw = render_frame(plans, progress, title, date_range)
            yield downscale_frame(raw)

    # Final-frame PNG first so a failed encode still leaves a still.
    if args.png is not None:
        png_path = args.png if isinstance(args.png, str) else os.path.splitext(args.out)[0] + ".png"
        final = downscale_frame(render_frame(plans, 1.0, title, date_range))
        os.makedirs(os.path.dirname(os.path.abspath(png_path)) or ".", exist_ok=True)
        final.save(png_path, "PNG", optimize=True)
        print(f"Wrote {png_path}")

    print(f"Encoding {n_frames} frames ({duration:.1f}s, animate {animate_s:.1f}s) → {args.out}")
    encode_mp4(frames(), n_frames, args.out)
    size = os.path.getsize(args.out)
    print(f"Wrote {args.out} ({size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
