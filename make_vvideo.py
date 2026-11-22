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

Supports 1–10 items. Default total duration is ~14s for 5 items, and
~14–15s for 10 items (about 1.3s each). Silent AAC audio is included
for X compatibility. Output is H.264 yuv420p, 30fps, faststart, under 15MB.
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
    HEIGHT,
    ORANGE,
    WHITE,
    WIDTH,
    draw_brand_row,
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
)
from make_vcard import load_payload

PAD_X = 72
TRANS = 0.25  # quick slide/fade, seconds
FPS = 30
MAX_BYTES = 15 * 1024 * 1024


def timings(n: int, target: float | None) -> tuple[float, float, float, float]:
    """Return (intro_vis, item_vis, outro_vis, trans). Output ≈ sum of vis times."""
    if n <= 5:
        intro, outro, default = 1.5, 1.0, 14.0
    else:
        intro, outro, default = 0.9, 0.8, 14.5
    goal = float(target) if target else default
    item = (goal - intro - outro) / max(n, 1)
    if n <= 5:
        item = min(2.7, max(2.0, item))
    else:
        item = min(1.5, max(1.2, item))
    return intro, item, outro, TRANS


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
            # Filled dim disc — reads more clearly than a thin ring at 1080.
            draw.ellipse(tuple(map(int, box)), fill=DOT_OFF)
        x += 2 * r + gap


def render_intro(path: str, title: str, datetime_str: str) -> str:
    img, draw, s = new_canvas()
    draw_top_bar(draw, s, bar_h=14)
    pad = PAD_X * s

    # Brand near the top, no datetime (datetime sits under the title).
    y = 14 * s + 56 * s
    y = draw_brand_row(draw, s, y, "", pad_x=PAD_X, brand_size=26, date_size=22)

    lead, rest = split_title(title)
    inner_w = WIDTH * s - 2 * pad

    # Vertically centre the main title block in the remaining mid area.
    mid_top = y + 80 * s
    mid_bot = HEIGHT * s - 280 * s

    blocks: list[tuple[str, object, int, tuple[int, int, int]]] = []
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

    # Datetime and handle near the bottom.
    if datetime_str:
        dfont = load_font(28 * s, 650)
        dtext = ellipsize(datetime_str, dfont, inner_w)
        dw = text_width(dfont, dtext)
        dh = text_height(dfont, dtext)
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


def render_item(
    path: str,
    title: str,
    datetime_str: str,
    index: int,
    n: int,
    place: str,
    text: str,
) -> str:
    img, draw, s = new_canvas()
    draw_top_bar(draw, s, bar_h=12)
    y = 12 * s + 32 * s
    y = draw_brand_row(draw, s, y, datetime_str, pad_x=PAD_X, brand_size=24, date_size=20)
    y = y + 18 * s
    pad = PAD_X * s
    draw.rectangle((pad, y, WIDTH * s - pad, y + 2 * s), fill=DIVIDER)
    content_top = y + 2 * s + 48 * s

    # Dots + handle reserve the bottom.
    dots_cy = HEIGHT * s - 168 * s
    handle_y = HEIGHT * s - 110 * s
    content_bottom = dots_cy - 48 * s
    inner_w = WIDTH * s - 2 * pad

    # Number badge
    num = f"{index:02d}"
    badge_font = load_font(44 * s, 800)
    badge_h_pad, badge_v_pad = 22 * s, 12 * s
    nw = text_width(badge_font, num)
    nh = text_height(badge_font, num)
    bw, bh = int(nw + 2 * badge_h_pad), int(nh + 2 * badge_v_pad)
    bx, by = pad, content_top
    draw.rounded_rectangle((int(bx), int(by), int(bx + bw), int(by + bh)), radius=int(12 * s), fill=ORANGE)
    # Dark number on orange for maximum contrast.
    draw_text(
        draw,
        (bx + (bw - nw) / 2, by + (bh - nh) / 2),
        num,
        badge_font,
        BG,
    )

    # Place tag to the right of the badge, vertically centred.
    place_font = load_font(42 * s, 800)
    place_tr = 4.0 * s
    place_max = inner_w - bw - 24 * s
    if text_width(place_font, place, tracking=place_tr) > place_max:
        place_tr = 0
        place = ellipsize(place, place_font, place_max)
    # Use cap-height, not the full string, so a comma descender cannot
    # lift the place tag into the header rule.
    ph = text_height(place_font, "H")
    place_y = by + (bh - ph) / 2
    draw_tracked(draw, (bx + bw + 24 * s, place_y), place, place_font, ORANGE, tracking=place_tr)

    # Headline fills the remaining mid-frame, large and bold.
    head_top = by + bh + 36 * s
    head_max_h = content_bottom - head_top
    # Pick a size whose wrapped block fits the leftover height.
    headline_font = lines = size = None
    for max_size, max_lines, min_size in (
        (86, 5, 52),
        (72, 6, 44),
        (60, 7, 40),
        (52, 8, 36),
    ):
        font, wrapped, sz = fit_wrapped(text, inner_w, max_lines, max_size * s, min_size * s, 800)
        lh = int(round(sz * 1.16))
        block_h = lh * (len(wrapped) - 1) + text_height(font, wrapped[0] if wrapped else "Hg")
        if block_h <= head_max_h:
            headline_font, lines, size = font, wrapped, sz
            break
    if headline_font is None:
        headline_font, lines, size = fit_wrapped(text, inner_w, 8, 40 * s, 34 * s, 800)
    lh = int(round(size * 1.16))
    for i, line in enumerate(lines):
        draw_text(draw, (pad, head_top + i * lh), line, headline_font, WHITE)

    draw_progress_dots(draw, s, n, index - 1, dots_cy)

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
    return save_png(img, path)


def render_outro(path: str) -> str:
    img, draw, s = new_canvas()
    draw_top_bar(draw, s, bar_h=14)
    pad = PAD_X * s

    # Centre the sign-off block.
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
    # Small orange accent + brand
    bar_w, bar_h = 28 * s, 6 * s
    brand_x = (WIDTH * s - (bar_w + 14 * s + br_w)) // 2
    bar_y = y + max(0, (br_h - bar_h) // 2) + 1 * s
    draw.rectangle((brand_x, bar_y, brand_x + bar_w, bar_y + bar_h), fill=ORANGE)
    draw_tracked(draw, (brand_x + bar_w + 14 * s, y), BRAND, brand_font, WHITE, tracking=4.0 * s)
    return save_png(img, path)


def encode_video(frames: list[tuple[str, float]], trans: float, out_path: str) -> None:
    """Stitch still frames with xfade (fade on/off, slideleft between items)."""
    n = len(frames)
    if n < 2:
        raise ValueError("Need at least intro + outro")
    vis = [d for _, d in frames]
    # Clip duration must cover the outgoing (and incoming) transition.
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
        # Silent stereo AAC so X/Buffer accept the file as a proper video.
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
            # Do not use settb/setpts here: they make xfade see fps 1/0.
            parts.append(
                f"[{i}:v]scale={WIDTH}:{HEIGHT}:flags=lanczos,"
                f"fps={FPS},format=yuv420p,setsar=1[v{i}]"
            )
        # Intro -> first item: fade; between items: slideleft; last -> outro: fade.
        last = "v0"
        for i in range(n - 1):
            # i==0 is intro->item1 (fade); i==n-2 is last item->outro (fade);
            # everything in between is an item-to-item slide.
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

    # Aim for high quality around 8 Mbps, shrink if the file would exceed 15MB.
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
    # Still over 15MB after the softest encode — leave the smallest file and warn.
    size = os.path.getsize(out_path)
    if size > MAX_BYTES:
        print(
            f"warning: output is {size / (1024*1024):.1f} MB (over 15 MB target)",
            file=sys.stderr,
        )


def render_video(payload: dict, out_path: str, duration: float | None) -> str:
    items = payload["items"]
    n = len(items)
    intro_t, item_t, outro_t, trans = timings(n, duration)
    tmp = tempfile.mkdtemp(prefix="vnews-")
    try:
        frames: list[tuple[str, float]] = []
        intro = os.path.join(tmp, "intro.png")
        render_intro(intro, payload["title"], payload["datetime"])
        frames.append((intro, intro_t))
        for i, it in enumerate(items, 1):
            p = os.path.join(tmp, f"item-{i:02d}.png")
            render_item(p, payload["title"], payload["datetime"], i, n, it["place"], it["text"])
            frames.append((p, item_t))
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
        help="Target total duration in seconds (default: ~14 for 5 items, ~14.5 for 10)",
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
