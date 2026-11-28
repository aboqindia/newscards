# Weekly heatmap video pipeline (Buffer-safe)
1. `python3 fetch_markets.py --out /tmp/markets.json`
2. `python3 make_heatmap.py --json /tmp/markets.json --out videos/YYYYMMDD-heatmap.mp4 --png cards/YYYYMMDD-heatmap.png`
   - WAIT until it prints "Wrote ...mp4" (process fully exited). Never commit while it is still running.
3. `./publish_video.sh videos/YYYYMMDD-heatmap.mp4 "Weekly heatmap YYYYMMDD"`
   - validates (ffmpeg full decode, H.264 High yuv420p, AAC), commits, pushes, re-downloads from jsDelivr + raw.githubusercontent and compares md5.
   - prints `VIDEO_URL=` (commit-pinned jsDelivr URL). Use ONLY this URL for Buffer.
   - If it exits non-zero: do NOT post an image fallback silently; retry once, otherwise tell the user.
4. Buffer create_post: mode shareNow, text hook + hashtags, assets=[{video:{url:VIDEO_URL}}]. If Buffer errors, report it.
Root cause 28 Nov 2026: commit 57fd13a held a truncated MP4 (1,572,912 B, no moov atom; real file 1,685,739 B) -> jsDelivr served HTTP 200 but Buffer could not read it.

Branding: official circular Aboq India coin (assets/aboq-logo-round.png, cropped from the user-supplied gold-coin logo) sits as a top-left brand bug, 128px at 1080 wide, with a short fade-in and a soft drop shadow. The footer keeps @NBSHARMA3 and an ivory 'Aboqindia.com' text badge; do not invent or swap the coin logo.
