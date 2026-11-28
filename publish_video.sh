#!/usr/bin/env bash
# Usage: ./publish_video.sh videos/NAME.mp4 [commit message]
# Validates the MP4, commits+pushes it, then downloads it back from jsDelivr
# and raw.githubusercontent and checks the bytes match. Prints the URL to give Buffer.
# Exit non-zero (and DO NOT post) on any failure.
set -euo pipefail
cd "$(dirname "$0")"
F="$1"; MSG="${2:-Add $F}"
[ -f "$F" ] || { echo "missing $F"; exit 1; }
# 1. file must be fully written and decodable (moov atom present, correct frames)
ffprobe -v error -show_entries format=duration,size -of default=nw=1 "$F" >/dev/null
ffmpeg -v error -i "$F" -f null - || { echo "video not decodable"; exit 1; }
V=$(ffprobe -v error -select_streams v:0 -show_entries stream=codec_name,profile,pix_fmt,width,height -of default=nw=1 "$F" | sort | tr '\n' ' ')
echo "$V" | grep -q "codec_name=h264" && echo "$V" | grep -q "pix_fmt=yuv420p" && echo "$V" | grep -q "profile=High" || { echo "bad codec: $V"; exit 1; }
ffprobe -v error -select_streams a:0 -show_entries stream=codec_name -of csv=p=0 "$F" | grep -q aac || { echo "no AAC track"; exit 1; }
LOCAL=$(md5sum "$F" | cut -d' ' -f1); SIZE=$(stat -c %s "$F")
# 2. commit + push (only after render process has fully exited)
git add "$F"; git add cards 2>/dev/null || true
git commit -m "$MSG" || true
git push origin HEAD:main
SHA=$(git rev-parse HEAD)
# 3. verify served bytes. Commit-pinned jsDelivr URL avoids branch cache staleness.
URL="https://cdn.jsdelivr.net/gh/aboqindia/newscards@${SHA}/${F}"
RAW="https://raw.githubusercontent.com/aboqindia/newscards/${SHA}/${F}"
for u in "$URL" "$RAW"; do
  ok=0
  for i in 1 2 3 4 5 6; do
    curl -sL "$u" -o /tmp/_verify.mp4 || true
    if [ "$(md5sum /tmp/_verify.mp4 | cut -d' ' -f1)" = "$LOCAL" ]; then ok=1; break; fi
    sleep 10
  done
  [ $ok = 1 ] || { echo "SERVED FILE DIFFERS from local ($u). Do not post."; exit 1; }
done
ffprobe -v error -show_entries stream=codec_name /tmp/_verify.mp4 >/dev/null
echo "OK size=$SIZE md5=$LOCAL"
echo "VIDEO_URL=$URL"
