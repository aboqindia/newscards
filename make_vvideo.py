#!/usr/bin/env python3
"""Generate a vertical 1080x1920 news video for @NBSHARMA3 (Pillow + ffmpeg, no AI).

Usage:
  python3 make_vvideo.py --json video.json --out video.mp4
  python3 make_vvideo.py --json video.json --out video.mp4 --duration 14

JSON shape matches make_vcard.py:
  {
    "title": "BREAKING | TOP 5",
    "datetime": "6:00 AM IST, 22 Nov 2026",
    "items": [{"place": "TOKYO", "text": "Headline in our own words."}]
  }

Supports 1–10 items. Items are shown two per screen so the frame stays
full and every headline is drawn in full (wrapped, never ellipsized).
A leftover final item uses a larger hero layout.

  5 items  -> screens of 2 + 2 + 1, default ~14s
  10 items -> five screens of 2, default ~15s

Silent AAC audio is included for X compatibility. Output is H.264
yuv420p, 30fps, faststart, under 15MB.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw

from cardutil import (
    BG,
    BRAND,
    DIVIDER,
    DOT_OFF,
    HANDLE,
    INK,
    HEIGHT,
    ORANGE,
    PANEL,
    WHITE,
    WIDTH,
    draw_brand_row,
    draw_circle_badge,
    draw_text,
    draw_top_bar,
    draw_tracked,
    ellipsize,
    fit_wrapped,
    load_font,
    make_x_icon,
    new_canvas,
    save_png,
    text_height,
    text_width,
    wrap_text,
)
from make_vcard import load_payload

PAD_X = 72
TRANS = 0.22  # quick slide / fade, seconds
FPS = 30
MAX_BYTES = 15 * 1024 * 1024


def chunk_items(items: list[dict[str, str]]) -> list[list[tuple[int, dict[str, str]]]]:
    """Group items into screens of 2, with a possible leftover hero of 1."""
    numbered = [(i, it) for i, it in enumerate(items, 1)]
    chunks: list[list[tuple[int, dict[str, str]]]] = []
    i = 0
    n = len(numbered)
    while i < n:
        if i + 1 == n:
            chunks.append(numbered[i : i + 1])
            i += 1
        else:
            chunks.append(numbered[i : i + 2])
            i += 2
    return chunks


def timings(
    n_screens: int, n_items: int, target: float | None
) -> tuple[float, float, float, float]:
    """Return (intro_vis, screen_vis, outro_vis, trans).

    Visible times are split so they sum to the target duration.
    """
    if n_items <= 5:
        intro, outro, default = 1.15, 0.85, 14.0
    else:
        intro, outro, default = 0.80, 0.70, 15.0
    goal = float(target) if target else default
    min_screen = 1.8 if n_items <= 5 else 1.6
    if goal - intro - outro < n_screens * min_screen:
        intro = max(0.6, intro * 0.7)
        outro = max(0.5, outro * 0.7)
    screen = (goal - intro - outro) / max(n_screens, 1)
    screen = max(screen, 1.4)
    return intro, screen, outro, TRANS


def split_title(title: str) -> tuple[str, str]:
    """Split 'BREAKING | TOP 5' into an orange lead and a white rest."""
    title = " ".join(title.split())
    if "|" in title:
        left, _, right = title.partition("|")
        return left.strip(), right.strip()
    parts = title.split(" ", 1)
    if parts and parts[0].upper() == "BREAKING":
        return parts[0].upper(), parts[1].strip() if len(parts) > 1 else ""
    return "", title


def fit_full(
    text: str,
    max_width: float,
    max_height: float,
    max_size: int,
    min_size: int,
    weight: int = 900,
    lh_ratio: float = 1.14,
) -> tuple[object, list[str], int, int]:
    """Wrap the full headline and shrink the font until the block fits.

    Never ellipsizes or drops lines. If even min_size overflows, the font
    keeps shrinking down to a hard readable floor.
    """
    text = " ".join(text.split())
    hard_min = max(28, min(min_size, 36))
    last = None
    for size in range(int(max_size), hard_min - 1, -2):
        font = load_font(size, weight)
        lines = wrap_text(text, font, max_width) or [""]
        lh = int(round(size * lh_ratio))
        first = lines[0] if lines[0] else "Hg"
        h = lh * max(0, len(lines) - 1) + text_height(font, first)
        last = (font, lines, size, lh, h)
        if h <= max_height:
            return font, lines, size, lh
    # Still too tall at the floor — tighten line height once, still no crop.
    font, lines, size, lh, h = last
    tight = 1.08
    lh = int(round(size * tight))
    first = lines[0] if lines[0] else "Hg"
    return font, lines, size, lh


def draw_progress_dots(
    draw: ImageDraw.ImageDraw,
    s: int,
    n: int,
    current: int,
    cy: int,
) -> None:
    if n <= 0:
        return
    r = 8 * s
    gap = 16 * s
    total = n * (2 * r) + (n - 1) * gap
    x = (WIDTH * s - total) // 2
    for i in range(n):
        box = (x, cy - r, x + 2 * r, cy + 2 * r - r)
        if i == current:
            draw.ellipse(tuple(map(int, box)), fill=ORANGE)
        else:
            draw.ellipse(tuple(map(int, box)), fill=DOT_OFF)
        x += 2 * r + gap


def draw_kicker(draw: ImageDraw.ImageDraw, s: int, y: int, title: str, pad: int) -> int:
    """One-line 'BREAKING  ·  TOP 5' under the brand row. Returns bottom y."""
    lead, rest = split_title(title)
    font = load_font(26 * s, 800)
    h = text_height(font, "H")
    x = pad
    if lead:
        draw_text(draw, (x, y), lead.upper(), font, ORANGE)
        x += text_width(font, lead.upper())
        if rest:
            dot = "  ·  "
            draw_text(draw, (x, y), dot, font, DIVIDER)
            x += text_width(font, dot)
    if rest:
        draw_text(draw, (x, y), rest.upper(), font, WHITE)
    elif not lead:
        draw_text(draw, (x, y), title.upper(), font, WHITE)
    return y + h


def draw_screen_footer(
    img: Image.Image,
    draw: ImageDraw.ImageDraw,
    s: int,
    n_screens: int,
    current: int,
) -> int:
    """Progress dots + centred handle. Returns the y content must stay above."""
    dots_cy = HEIGHT * s - 158 * s
    handle_y = HEIGHT * s - 104 * s
    draw_progress_dots(draw, s, n_screens, current, dots_cy)

    hfont = load_font(28 * s, 800)
    hw = text_width(hfont, HANDLE)
    icon_px = 28 * s
    icon = make_x_icon(icon_px, WHITE)
    group_w = icon_px + 12 * s + hw
    gx = (WIDTH * s - group_w) // 2
    mid = handle_y + max(icon_px, text_height(hfont, HANDLE)) / 2
    img.paste(icon, (int(gx), int(round(mid - icon_px / 2))), icon)
    draw_text(
        draw,
        (gx + icon_px + 12 * s, mid - text_height(hfont, HANDLE) / 2),
        HANDLE,
        hfont,
        WHITE,
    )
    return dots_cy - 36 * s


def draw_screen_header(
    draw: ImageDraw.ImageDraw,
    s: int,
    title: str,
    datetime_str: str,
) -> int:
    """Top bar, brand, datetime, kicker. Returns content top y."""
    draw_top_bar(draw, s, bar_h=12)
    pad = PAD_X * s
    y = 12 * s + 26 * s
    y = draw_brand_row(draw, s, y, datetime_str, pad_x=PAD_X, brand_size=22, date_size=20)
    y = y + 16 * s
    y = draw_kicker(draw, s, y, title, pad)
    y = y + 16 * s
    draw.rectangle((pad, y, WIDTH * s - pad, y + 2 * s), fill=DIVIDER)
    return y + 2 * s + 24 * s


def _fit_place(place: str, font, max_width: float, tracking: float) -> tuple[str, float]:
    if text_width(font, place, tracking=tracking) > max_width:
        tracking = 0
        place = ellipsize(place, font, max_width)
    return place, tracking


def draw_item_block(
    draw: ImageDraw.ImageDraw,
    s: int,
    box: tuple[int, int, int, int],
    index: int,
    place: str,
    text: str,
    *,
    hero: bool = False,
) -> None:
    """Draw one news item inside a rounded panel, headline in full."""
    x0, y0, x1, y1 = box
    panel_w = x1 - x0
    panel_h = y1 - y0
    radius = 22 * s
    draw.rounded_rectangle(box, radius=radius, fill=PANEL)
    # Orange leading edge, inset so it sits inside the rounded corner.
    edge_w = 8 * s
    draw.rectangle((x0, y0 + radius // 2, x0 + edge_w, y1 - radius // 2), fill=ORANGE)

    inset = 28 * s
    inner_x = x0 + inset
    inner_w = panel_w - 2 * inset
    inner_top = y0 + inset
    inner_bot = y1 - inset
    inner_h = inner_bot - inner_top

    if hero:
        badge_r = 48 * s
        badge_font = load_font(44 * s, 900)
        place_size = 36 * s
        head_max, head_min = 88 * s, 48 * s
        badge_gap = 28 * s
        place_gap = 28 * s
    else:
        badge_r = 38 * s
        badge_font = load_font(34 * s, 900)
        place_size = 30 * s
        head_max, head_min = 62 * s, 40 * s
        badge_gap = 22 * s
        place_gap = 18 * s

    place_font = load_font(place_size, 800)
    place_tr = 3.0 * s
    place_max = inner_w - (badge_r * 2 + badge_gap)
    place, place_tr = _fit_place(place, place_font, place_max, place_tr)
    place_h = text_height(place_font, "H")

    badge_d = badge_r * 2
    row_h = max(badge_d, place_h)
    # Headline gets everything below the badge/place row.
    head_top_off = row_h + place_gap
    head_max_h = inner_h - head_top_off
    # Leave at least a slim band for type; fit_full will shrink if needed.
    if head_max_h < 80 * s:
        head_max_h = max(60 * s, inner_h - row_h - 12 * s)

    font, lines, size, lh = fit_full(
        text, inner_w, head_max_h, head_max, head_min, 900, 1.14
    )
    first = lines[0] if lines and lines[0] else "Hg"
    head_h = lh * max(0, len(lines) - 1) + text_height(font, first)
    block_h = head_top_off + head_h

    # Vertically centre the badge + place + headline group in the panel.
    group_top = inner_top + max(0, int((inner_h - block_h) * 0.45))
    if group_top + block_h > inner_bot:
        group_top = max(inner_top, inner_bot - block_h)

    cx = inner_x + badge_r
    cy = int(round(group_top + row_h / 2))
    # Keep the circle fully inside the panel.
    cy = min(max(cy, y0 + badge_r + 10 * s), y1 - badge_r - 10 * s)
    draw_circle_badge(
        draw, int(cx), int(cy), int(badge_r), f"{index:02d}", badge_font, fill=ORANGE, fg=INK
    )

    place_y = group_top + (row_h - place_h) / 2
    draw_tracked(
        draw,
        (inner_x + badge_d + badge_gap, place_y),
        place,
        place_font,
        ORANGE,
        tracking=place_tr,
    )

    hy = group_top + head_top_off
    for i, line in enumerate(lines):
        draw_text(draw, (inner_x, hy + i * lh), line, font, WHITE)


def render_intro(path: str, title: str, datetime_str: str) -> str:
    img, draw, s = new_canvas()
    draw_top_bar(draw, s, bar_h=14)
    pad = PAD_X * s

    y = 14 * s + 56 * s
    y = draw_brand_row(draw, s, y, "", pad_x=PAD_X, brand_size=26, date_size=22)

    lead, rest = split_title(title)
    inner_w = WIDTH * s - 2 * pad

    mid_top = y + 80 * s
    mid_bot = HEIGHT * s - 280 * s

    blocks: list[tuple] = []
    if lead:
        font, lines, size = fit_wrapped(lead.upper(), inner_w, 2, 110 * s, 64 * s, 800)
        lh = int(round(size * 1.12))
        h = lh * (len(lines) - 1) + text_height(font, lines[0])
        blocks.append((lines, font, lh, h, ORANGE))
    if rest:
        font, lines, size = fit_wrapped(rest.upper(), inner_w, 3, 96 * s, 52 * s, 800)
        lh = int(round(size * 1.12))
        h = lh * (len(lines) - 1) + text_height(font, lines[0])
        blocks.append((lines, font, lh, h, WHITE))

    gap = 18 * s
    block_h = sum(b[3] for b in blocks) + gap * max(0, len(blocks) - 1)
    start = mid_top + max(0, int((mid_bot - mid_top - block_h) * 0.45))
    yy = start
    for lines, font, lh, h, color in blocks:
        for i, line in enumerate(lines):
            draw_text(draw, (pad, yy + i * lh), line, font, color)
        yy += h + gap

    if datetime_str:
        dfont = load_font(28 * s, 650)
        dtext = ellipsize(datetime_str, dfont, inner_w)
        dw = text_width(dfont, dtext)
        draw_text(draw, ((WIDTH * s - dw) // 2, HEIGHT * s - 200 * s), dtext, dfont, WHITE)

    hfont = load_font(30 * s, 800)
    hw = text_width(hfont, HANDLE)
    icon_px = 30 * s
    icon = make_x_icon(icon_px, WHITE)
    group_w = icon_px + 12 * s + hw
    gx = (WIDTH * s - group_w) // 2
    gy = HEIGHT * s - 130 * s
    mid = gy + max(icon_px, text_height(hfont, HANDLE)) / 2
    img.paste(icon, (int(gx), int(round(mid - icon_px / 2))), icon)
    draw_text(draw, (gx + icon_px + 12 * s, mid - text_height(hfont, HANDLE) / 2), HANDLE, hfont, WHITE)
    return save_png(img, path)


def render_pair(
    path: str,
    title: str,
    datetime_str: str,
    pair: list[tuple[int, dict[str, str]]],
    screen_i: int,
    n_screens: int,
) -> str:
    img, draw, s = new_canvas()
    content_top = draw_screen_header(draw, s, title, datetime_str)
    content_bot = draw_screen_footer(img, draw, s, n_screens, screen_i)
    pad = PAD_X * s
    gap = 20 * s
    available = content_bot - content_top
    panel_h = int((available - gap) // 2)
    x0, x1 = pad, WIDTH * s - pad

    y = content_top
    for index, item in pair:
        box = (int(x0), int(y), int(x1), int(y + panel_h))
        draw_item_block(draw, s, box, index, item["place"], item["text"], hero=False)
        y += panel_h + gap
    return save_png(img, path)


def render_hero_item(
    path: str,
    title: str,
    datetime_str: str,
    index: int,
    item: dict[str, str],
    screen_i: int,
    n_screens: int,
) -> str:
    img, draw, s = new_canvas()
    content_top = draw_screen_header(draw, s, title, datetime_str)
    content_bot = draw_screen_footer(img, draw, s, n_screens, screen_i)
    pad = PAD_X * s
    box = (int(pad), int(content_top), int(WIDTH * s - pad), int(content_bot))
    draw_item_block(draw, s, box, index, item["place"], item["text"], hero=True)
    return save_png(img, path)


def render_outro(path: str) -> str:
    img, draw, s = new_canvas()
    draw_top_bar(draw, s, bar_h=14)

    icon_px = 72 * s
    icon = make_x_icon(icon_px, WHITE)
    handle_font = load_font(56 * s, 800)
    follow_font = load_font(36 * s, 700)
    brand_font = load_font(26 * s, 800)
    follow = "Follow for more"
    hw = text_width(handle_font, HANDLE)
    hh = text_height(handle_font, HANDLE)
    fw = text_width(follow_font, follow)
    fh = text_height(follow_font, follow)
    br_w = text_width(brand_font, BRAND, tracking=4.0 * s)
    br_h = text_height(brand_font, BRAND)

    gap1, gap2, gap3 = 36 * s, 28 * s, 48 * s
    block_h = icon_px + gap1 + hh + gap2 + fh + gap3 + br_h
    y = (HEIGHT * s - block_h) // 2 - 20 * s

    img.paste(icon, (int((WIDTH * s - icon_px) // 2), int(y)), icon)
    y += icon_px + gap1
    draw_text(draw, ((WIDTH * s - hw) // 2, y), HANDLE, handle_font, WHITE)
    y += hh + gap2
    draw_text(draw, ((WIDTH * s - fw) // 2, y), follow, follow_font, ORANGE)
    y += fh + gap3
    bar_w, bar_h = 28 * s, 6 * s
    brand_x = (WIDTH * s - (bar_w + 14 * s + br_w)) // 2
    bar_y = y + max(0, (br_h - bar_h) // 2) + 1 * s
    draw.rectangle((brand_x, bar_y, brand_x + bar_w, bar_y + bar_h), fill=ORANGE)
    draw_tracked(draw, (brand_x + bar_w + 14 * s, y), BRAND, brand_font, WHITE, tracking=4.0 * s)
    return save_png(img, path)


def encode_video(frames: list[tuple[str, float]], trans: float, out_path: str) -> None:
    """Stitch still frames with xfade (fade on/off, slideleft between screens)."""
    n = len(frames)
    if n < 2:
        raise ValueError("Need at least intro + outro")
    vis = [d for _, d in frames]
    clip_durs = [vis[i] + (trans if i < n - 1 else 0.0) for i in range(n)]
    offsets = []
    acc = 0.0
    for i in range(n - 1):
        acc += vis[i]
        offsets.append(acc)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    total_vis = sum(vis)

    def run(maxrate: str, bufsize: str, crf: int) -> None:
        cmd: list[str] = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
        for (png, _), dur in zip(frames, clip_durs):
            cmd += [
                "-framerate",
                str(FPS),
                "-loop",
                "1",
                "-t",
                f"{dur:.3f}",
                "-i",
                png,
            ]
        cmd += [
            "-f",
            "lavfi",
            "-t",
            f"{total_vis:.3f}",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
        ]

        parts: list[str] = []
        for i in range(n):
            parts.append(
                f"[{i}:v]scale={WIDTH}:{HEIGHT}:flags=lanczos,"
                f"fps={FPS},format=yuv420p,setsar=1[v{i}]"
            )
        last = "v0"
        for i in range(n - 1):
            # intro -> first screen and last screen -> outro fade;
            # screen-to-screen uses a fast slide.
            if i == 0 or i == n - 2:
                transition = "fade"
            else:
                transition = "slideleft"
            out = "vout" if i == n - 2 else f"x{i}"
            parts.append(
                f"[{last}][v{i+1}]xfade=transition={transition}:"
                f"duration={trans:.3f}:offset={offsets[i]:.3f}[{out}]"
            )
            last = out

        filter_complex = ";".join(parts)
        cmd += [
            "-filter_complex",
            filter_complex,
            "-map",
            "[vout]",
            "-map",
            f"{n}:a",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            "-profile:v",
            "high",
            "-level",
            "4.1",
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "avc1",
            "-crf",
            str(crf),
            "-maxrate",
            maxrate,
            "-bufsize",
            bufsize,
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
        subprocess.run(cmd, check=True)

    attempts = [
        ("8M", "16M", 18),
        ("6M", "12M", 20),
        ("4M", "8M", 22),
    ]
    last_err: Exception | None = None
    for maxrate, bufsize, crf in attempts:
        try:
            run(maxrate, bufsize, crf)
        except subprocess.CalledProcessError as exc:
            last_err = exc
            continue
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            if os.path.getsize(out_path) <= MAX_BYTES:
                return
        else:
            last_err = last_err or RuntimeError("ffmpeg produced an empty file")
    if last_err and (not os.path.isfile(out_path) or os.path.getsize(out_path) == 0):
        raise last_err
    size = os.path.getsize(out_path)
    if size > MAX_BYTES:
        print(
            f"warning: output is {size / (1024*1024):.1f} MB (over 15 MB target)",
            file=sys.stderr,
        )


def render_video(payload: dict, out_path: str, duration: float | None) -> str:
    items = payload["items"]
    chunks = chunk_items(items)
    n_screens = len(chunks)
    intro_t, screen_t, outro_t, trans = timings(n_screens, len(items), duration)
    tmp = tempfile.mkdtemp(prefix="vnews-")
    try:
        frames: list[tuple[str, float]] = []
        intro = os.path.join(tmp, "intro.png")
        render_intro(intro, payload["title"], payload["datetime"])
        frames.append((intro, intro_t))
        for si, chunk in enumerate(chunks):
            p = os.path.join(tmp, f"screen-{si+1:02d}.png")
            if len(chunk) == 1:
                index, item = chunk[0]
                render_hero_item(
                    p, payload["title"], payload["datetime"], index, item, si, n_screens
                )
            else:
                render_pair(
                    p, payload["title"], payload["datetime"], chunk, si, n_screens
                )
            frames.append((p, screen_t))
        outro = os.path.join(tmp, "outro.png")
        render_outro(outro)
        frames.append((outro, outro_t))
        encode_video(frames, trans, out_path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a 1080x1920 vertical news video for @NBSHARMA3")
    p.add_argument("--json", required=True, help="Path to JSON with title, datetime, items")
    p.add_argument("--out", required=True, help="Output MP4 path")
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Target total duration in seconds (default: ~14 for 5 items, ~15 for 10)",
    )
    return p.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    payload = load_payload(args.json)
    path = render_video(payload, args.out, args.duration)
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
