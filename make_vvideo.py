#!/usr/bin/env python3
"""Generate a vertical 1080x1920 news video for @NBSHARMA3 (Pillow + ffmpeg, no AI).

Usage:
  python3 make_vvideo.py --json video.json --out video.mp4
  python3 make_vvideo.py --json video.json --out video.mp4 --duration 10

JSON shape matches make_vcard.py:
  {
    "title": "BREAKING | TOP 5",
    "datetime": "6:00 AM IST, 22 Nov 2026",
    "items": [{"place": "TOKYO", "text": "Headline in our own words."}]
  }

Supports 1–10 items. Every item sits on a single vertical card in the
same list style as make_vcard.py (dark background, header with title
and date pill, numbered orange badges, orange place tags, heavy white
Inter headlines, @NBSHARMA3 footer). Headlines are never truncated.

Headlines reveal with a typewriter effect: item 1 types character by
character with a blinking cursor, then item 2, and so on, until every
item is complete. The finished card holds for the last 1.5 seconds.
Typing is paced evenly so the last character lands at duration-1.5s.

Default duration, when --duration is omitted, is computed from the
character count at a readable rate. Short requested durations (for
example 7s) still work; typing just runs faster.

Silent AAC audio is included for X compatibility. Output is H.264
yuv420p, 30fps, faststart, under 15MB when possible.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageDraw

from cardutil import (
    BG,
    INK,
    ORANGE,
    PANEL,
    WHITE,
    draw_circle_badge,
    draw_text,
    draw_tracked,
    ellipsize,
    load_font,
    text_height,
    text_width,
    wrap_text,
)
from make_vcard import (
    HEIGHT as CARD_H,
    PAD_X,
    WIDTH as CARD_W,
    draw_footer_block,
    draw_header_band,
    load_payload,
)

OUT_W, OUT_H = 1080, 1920
FPS = 30
HOLD = 1.5  # seconds the completed card stays on screen
MAX_BYTES = 15 * 1024 * 1024
# Comfortable typewriter rate used for the default duration only.
DEFAULT_CPS = 18.0
CURSOR_HZ = 2.0


@dataclass
class RowLayout:
    index: int
    place: str
    place_tr: float
    lines: list[str]
    char_len: int
    box: tuple[int, int, int, int]
    badge_cx: int
    badge_cy: int
    badge_r: int
    text_x: float
    place_y: float
    head_y: float
    block_h: int


@dataclass
class CardLayout:
    title: str
    datetime_str: str
    rows: list[RowLayout]
    head_font: object
    head_size: int
    lh: int
    place_font: object
    badge_font: object
    total_chars: int = 0
    char_starts: list[int] = field(default_factory=list)


def default_duration(total_chars: int, n_items: int) -> float:
    """Readable default when the caller does not pass --duration."""
    floor = 10.0 if n_items <= 5 else 18.0
    return max(floor, total_chars / DEFAULT_CPS + HOLD)


def resolve_timing(duration: float | None, total_chars: int, n_items: int) -> tuple[float, float, int, int]:
    """Return (total_seconds, hold_seconds, typing_frames, hold_frames)."""
    total = float(duration) if duration and duration > 0 else default_duration(total_chars, n_items)
    if total <= HOLD + 0.3:
        hold = max(0.4, total * 0.22)
    else:
        hold = HOLD
    typing = max(0.2, total - hold)
    n_total = max(1, int(round(total * FPS)))
    n_typing = max(1, int(round(typing * FPS)))
    if n_typing >= n_total:
        n_typing = max(1, n_total - max(1, int(round(0.4 * FPS))))
    n_hold = n_total - n_typing
    real_total = n_total / FPS
    real_hold = n_hold / FPS
    return real_total, real_hold, n_typing, n_hold


def fit_headlines(
    items: list[dict[str, str]],
    text_w: float,
    head_room: float,
    head_max: int,
    place_font,
) -> tuple[object, int, int, list[tuple[str, float, list[str], int]]]:
    """Pick a shared headline size so every item's full text fits."""
    hard_min = 36
    chosen: tuple | None = None
    last_trial: list[tuple[str, float, list[str], int]] | None = None
    last_font = None
    last_size = hard_min
    last_lh = int(round(hard_min * 1.16))

    for size in range(int(head_max), hard_min - 1, -2):
        font = load_font(size, 900)
        lh = int(round(size * 1.16))
        trial: list[tuple[str, float, list[str], int]] = []
        ok = True
        for it in items:
            lines = wrap_text(it["text"], font, text_w) or [""]
            first = lines[0] if lines[0] else "Hg"
            block_h = lh * max(0, len(lines) - 1) + text_height(font, first)
            if block_h > head_room:
                ok = False
                break
            place = it["place"]
            tr = 3.0
            if text_width(place_font, place, tracking=tr) > text_w:
                tr = 0
                place = ellipsize(place, place_font, text_w)
            trial.append((place, tr, lines, block_h))
        last_font, last_size, last_lh, last_trial = font, size, lh, trial if ok else last_trial
        if ok:
            chosen = (font, size, lh, trial)
            break

    if chosen:
        return chosen

    # Still too tall at the floor: tighten line-height, then shrink further.
    size = hard_min
    font = last_font or load_font(size, 900)
    for lh_ratio in (1.16, 1.10, 1.06):
        lh = int(round(size * lh_ratio))
        trial = []
        ok = True
        for it in items:
            lines = wrap_text(it["text"], font, text_w) or [""]
            first = lines[0] if lines[0] else "Hg"
            block_h = lh * max(0, len(lines) - 1) + text_height(font, first)
            if block_h > head_room:
                ok = False
                break
            place = ellipsize(it["place"], place_font, text_w)
            trial.append((place, 0.0, lines, block_h))
        if ok:
            return font, size, lh, trial

    # Last resort: keep shrinking until it fits, never truncate the text.
    for size in range(hard_min - 2, 22, -2):
        font = load_font(size, 900)
        lh = int(round(size * 1.08))
        trial = []
        ok = True
        for it in items:
            lines = wrap_text(it["text"], font, text_w) or [""]
            first = lines[0] if lines[0] else "Hg"
            block_h = lh * max(0, len(lines) - 1) + text_height(font, first)
            if block_h > head_room:
                ok = False
                break
            place = ellipsize(it["place"], place_font, text_w)
            trial.append((place, 0.0, lines, block_h))
        if ok:
            return font, size, lh, trial

    # Impossible overflow: still return full lines (may be tight in the panel).
    font = load_font(24, 900)
    lh = int(round(24 * 1.06))
    trial = []
    for it in items:
        lines = wrap_text(it["text"], font, text_w) or [""]
        first = lines[0] if lines[0] else "Hg"
        block_h = lh * max(0, len(lines) - 1) + text_height(font, first)
        place = ellipsize(it["place"], place_font, text_w)
        trial.append((place, 0.0, lines, block_h))
    return font, 24, lh, trial


def build_layout(title: str, datetime_str: str, items: list[dict[str, str]]) -> CardLayout:
    """Measure the one-card list layout at the 2160x3840 card resolution."""
    n = len(items)
    # Probe header / footer on a throwaway canvas so we get exact band heights.
    probe = Image.new("RGB", (CARD_W, CARD_H), BG)
    pdraw = ImageDraw.Draw(probe)
    header_bottom = draw_header_band(pdraw, title, datetime_str)
    rule_y = draw_footer_block(probe, pdraw)

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
    panel_w = CARD_W - 2 * pad
    badge_r = 64 if n >= 8 else (72 if n >= 5 else 80)
    badge_left = 28
    text_x_off = badge_left + badge_r * 2 + 36
    text_w = panel_w - text_x_off - 36

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
    head_room = panel_h - 2 * inner_v - place_h - place_gap

    head_font, head_size, lh, prepared = fit_headlines(
        items, text_w, head_room, head_max, place_font
    )
    badge_font = load_font(52 if badge_r < 70 else 58, 900)

    rows: list[RowLayout] = []
    char_starts: list[int] = []
    total = 0
    radius = 28
    for i, (place, tr, lines, block_h) in enumerate(prepared):
        slot_top = content_top + i * slot
        py = int(round(slot_top))
        ph = int(round(panel_h))
        box = (pad, py, pad + panel_w, py + ph)

        text_block_h = place_h + place_gap + block_h
        group_h = max(badge_r * 2, text_block_h)
        group_top = py + (ph - group_h) / 2
        inset = 26
        if group_top < py + inset:
            group_top = py + inset

        cx = pad + badge_left + badge_r
        cy = int(round(group_top + group_h / 2))
        cy = min(max(cy, py + badge_r + 10), py + ph - badge_r - 10)

        text_top = group_top + (group_h - text_block_h) / 2
        tx = pad + text_x_off
        hy = text_top + place_h + place_gap
        char_len = sum(len(line) for line in lines)
        rows.append(
            RowLayout(
                index=i + 1,
                place=place,
                place_tr=tr,
                lines=lines,
                char_len=char_len,
                box=box,
                badge_cx=int(cx),
                badge_cy=int(cy),
                badge_r=int(badge_r),
                text_x=tx,
                place_y=text_top,
                head_y=hy,
                block_h=block_h,
            )
        )
        char_starts.append(total)
        total += char_len

    return CardLayout(
        title=title,
        datetime_str=datetime_str,
        rows=rows,
        head_font=head_font,
        head_size=head_size,
        lh=lh,
        place_font=place_font,
        badge_font=badge_font,
        total_chars=total,
        char_starts=char_starts,
    )


def render_base(layout: CardLayout) -> Image.Image:
    """Card chrome: background, header, footer, panels, badges, place tags."""
    img = Image.new("RGB", (CARD_W, CARD_H), BG)
    draw = ImageDraw.Draw(img)
    draw_header_band(draw, layout.title, layout.datetime_str)
    draw_footer_block(img, draw)

    radius = 28
    for row in layout.rows:
        draw.rounded_rectangle(row.box, radius=radius, fill=PANEL)
        draw_circle_badge(
            draw,
            row.badge_cx,
            row.badge_cy,
            row.badge_r,
            f"{row.index:02d}",
            layout.badge_font,
            fill=ORANGE,
            fg=INK,
        )
        draw_tracked(
            draw,
            (row.text_x, row.place_y),
            row.place,
            layout.place_font,
            ORANGE,
            tracking=row.place_tr,
        )
    return img


def prefix_lines(lines: list[str], n: int) -> tuple[list[str], int, str]:
    """Visible wrapped lines for the first n visual characters, plus cursor site."""
    if n <= 0:
        return [], 0, ""
    visible: list[str] = []
    left = n
    cursor_line = 0
    cursor_prefix = ""
    for i, line in enumerate(lines):
        if left <= 0:
            break
        take = min(len(line), left)
        visible.append(line[:take])
        cursor_line = i
        cursor_prefix = line[:take]
        left -= take
    return visible, cursor_line, cursor_prefix


def chars_for_frame(frame_i: int, n_typing: int, total_chars: int, in_hold: bool) -> int:
    if total_chars <= 0 or in_hold:
        return total_chars
    # Frame 0 starts typing; the last typing frame shows every character.
    return int(round((frame_i + 1) / n_typing * total_chars))


def draw_headlines(
    img: Image.Image,
    layout: CardLayout,
    n_chars: int,
    cursor_on: bool,
) -> None:
    draw = ImageDraw.Draw(img)
    cap_h = text_height(layout.head_font, "H")
    cursor_w = max(8, int(round(layout.head_size * 0.08)))
    for row, start in zip(layout.rows, layout.char_starts):
        end = start + row.char_len
        if n_chars <= start:
            # Not yet started: optional cursor on the first unstarted row.
            if cursor_on and n_chars == start:
                x = row.text_x
                y = row.head_y
                draw.rectangle((x, y, x + cursor_w, y + cap_h), fill=ORANGE)
            continue
        shown = min(row.char_len, n_chars - start)
        lines, cur_i, cur_pref = prefix_lines(row.lines, shown)
        for li, line in enumerate(lines):
            if line:
                draw_text(
                    draw,
                    (row.text_x, row.head_y + li * layout.lh),
                    line,
                    layout.head_font,
                    WHITE,
                )
        # Cursor lives on the in-progress item, including the first empty frame
        # of that item. Once the item is complete and we have moved on (or into
        # the hold), the cursor is hidden so the finished card matches a still.
        if cursor_on and start <= n_chars < end:
            if shown == 0:
                cx, cy = row.text_x, row.head_y
            else:
                cx = row.text_x + text_width(layout.head_font, cur_pref)
                cy = row.head_y + cur_i * layout.lh
            # A 3px gap after the last glyph keeps the bar from overlapping it.
            cx = cx + (3 if cur_pref else 0)
            draw.rectangle((cx, cy, cx + cursor_w, cy + cap_h), fill=ORANGE)
        # If this is the last item and it just completed during typing (not hold),
        # keep a brief end-of-line cursor until the hold starts — handled by
        # n_chars == total and not in_hold below.
    if cursor_on and n_chars >= layout.total_chars and layout.rows:
        # All characters are on screen but we are still in the typing window
        # (the last typing frame). Park the cursor at the end of the last item.
        row = layout.rows[-1]
        last = row.lines[-1] if row.lines else ""
        cx = row.text_x + text_width(layout.head_font, last) + (3 if last else 0)
        cy = row.head_y + max(0, len(row.lines) - 1) * layout.lh
        draw.rectangle((cx, cy, cx + cursor_w, cy + cap_h), fill=ORANGE)


def downscale_frame(img: Image.Image) -> Image.Image:
    if img.size == (OUT_W, OUT_H):
        return img
    return img.resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)


def encode_rgb_frames(frames_iter, n_frames: int, out_path: str) -> None:
    """Pipe 1080x1920 RGB frames to H.264 30fps with silent AAC, faststart."""
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    duration = n_frames / FPS

    def run(maxrate: str, bufsize: str, crf: int, source_dir: str | None) -> None:
        cmd: list[str]
        if source_dir is None:
            # First attempt: raw pipe. Later retries reread a cached PNG sequence
            # if we had to spill to disk; otherwise the caller passes frames again.
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
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-framerate",
                str(FPS),
                "-i",
                os.path.join(source_dir, "f%05d.png"),
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
        return cmd

    attempts = [
        ("8M", "16M", 18),
        ("6M", "12M", 20),
        ("4M", "8M", 22),
    ]

    # Spill frames to a PNG sequence so bitrate retries do not require a
    # second full render. compress_level=1 keeps this reasonably fast.
    tmp = tempfile.mkdtemp(prefix="vnews-frames-")
    last_err: Exception | None = None
    try:
        for i, frame in enumerate(frames_iter):
            if i >= n_frames:
                break
            path = os.path.join(tmp, f"f{i:05d}.png")
            frame.save(path, "PNG", compress_level=1)
        written = len([n for n in os.listdir(tmp) if n.endswith(".png")])
        if written != n_frames:
            raise RuntimeError(f"expected {n_frames} frames, wrote {written}")

        for maxrate, bufsize, crf in attempts:
            cmd = run(maxrate, bufsize, crf, tmp)
            try:
                subprocess.run(cmd, check=True)
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
                f"warning: output is {size / (1024 * 1024):.1f} MB (over 15 MB target)",
                file=sys.stderr,
            )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def iter_frames(base: Image.Image, layout: CardLayout, n_typing: int, n_hold: int, previews: dict[str, str] | None = None):
    """Yield 1080x1920 RGB frames for the typewriter animation."""
    n_total = n_typing + n_hold
    # Preview targets: early partial (~20% of typing), mid typing, final hold.
    preview_at: dict[int, str] = {}
    if previews:
        preview_at[max(0, min(n_typing - 1, int(n_typing * 0.18)))] = "partial"
        preview_at[max(0, min(n_typing - 1, int(n_typing * 0.52)))] = "mid"
        preview_at[n_total - 1] = "final"

    for f in range(n_total):
        in_hold = f >= n_typing
        n_chars = chars_for_frame(f, n_typing, layout.total_chars, in_hold)
        # Blink only while typing. Hold is a clean finished card.
        t = f / FPS
        cursor_on = (not in_hold) and (int(t * CURSOR_HZ * 2) % 2 == 0)
        frame = base.copy()
        draw_headlines(frame, layout, n_chars, cursor_on)
        out = downscale_frame(frame)
        key = preview_at.get(f)
        if key and previews and previews.get(key):
            os.makedirs(os.path.dirname(os.path.abspath(previews[key])) or ".", exist_ok=True)
            out.save(previews[key], "PNG", optimize=True)
        yield out


def render_video(
    payload: dict,
    out_path: str,
    duration: float | None,
    previews: dict[str, str] | None = None,
) -> str:
    items = payload["items"]
    layout = build_layout(payload["title"], payload["datetime"], items)
    total, hold, n_typing, n_hold = resolve_timing(duration, layout.total_chars, len(items))
    base = render_base(layout)
    n_frames = n_typing + n_hold
    encode_rgb_frames(
        iter_frames(base, layout, n_typing, n_hold, previews=previews),
        n_frames,
        out_path,
    )
    return out_path


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Render a 1080x1920 vertical news video for @NBSHARMA3")
    p.add_argument("--json", required=True, help="Path to JSON with title, datetime, items")
    p.add_argument("--out", required=True, help="Output MP4 path")
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Target total duration in seconds (default: character-based, about 18 cps + 1.5s hold)",
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
