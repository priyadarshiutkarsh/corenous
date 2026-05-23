#!/usr/bin/env bash
# Generate ``assets/Corenous.icns`` from ``assets/corenous-1024.png``.
#
# macOS bundles require an .icns containing several pre-rendered sizes
# (Retina included). We script the whole flow with ``sips`` (built-in
# resizer) and ``iconutil`` (built-in icns packer). Re-run any time the
# source image changes.
set -euo pipefail

cd "$(dirname "$0")/.."

SRC="assets/corenous-1024.png"
SRC_RGBA="assets/corenous-1024-rgba.png"
ISET="assets/Corenous.iconset"
OUT="assets/Corenous.icns"

if [[ ! -f "$SRC" ]]; then
    echo "!! missing source image: $SRC"
    exit 1
fi

# ``iconutil`` rejects PNGs that have no alpha channel. Some upstream
# logos (especially those exported from design tools as "PNG") are
# actually JPEGs with the wrong extension, or 24-bit RGB PNGs without
# alpha. Always pipe through Quartz to guarantee an RGBA PNG before
# building the iconset.
if [[ ! -f "$SRC_RGBA" || "$SRC_RGBA" -ot "$SRC" ]]; then
    echo ">> Re-encoding $SRC as RGBA via Quartz …"
    .venv/bin/python - "$SRC" "$SRC_RGBA" <<'PY'
import sys
from Foundation import NSURL
from Quartz import (
    CGImageSourceCreateWithURL,
    CGImageSourceCreateImageAtIndex,
    CGBitmapContextCreate,
    CGContextDrawImage,
    CGImageGetWidth, CGImageGetHeight,
    CGImageDestinationCreateWithURL,
    CGImageDestinationAddImage,
    CGImageDestinationFinalize,
    CGBitmapContextCreateImage,
    kCGImageAlphaPremultipliedLast,
    CGColorSpaceCreateDeviceRGB,
)
src_path, dst_path = sys.argv[1], sys.argv[2]
src_url = NSURL.fileURLWithPath_(src_path)
src_obj = CGImageSourceCreateWithURL(src_url, None)
img = CGImageSourceCreateImageAtIndex(src_obj, 0, None)
w = CGImageGetWidth(img); h = CGImageGetHeight(img)
ctx = CGBitmapContextCreate(
    None, w, h, 8, 0, CGColorSpaceCreateDeviceRGB(),
    kCGImageAlphaPremultipliedLast,
)
CGContextDrawImage(ctx, ((0, 0), (w, h)), img)
new_img = CGBitmapContextCreateImage(ctx)
dst_url = NSURL.fileURLWithPath_(dst_path)
dest = CGImageDestinationCreateWithURL(dst_url, "public.png", 1, None)
CGImageDestinationAddImage(dest, new_img, None)
CGImageDestinationFinalize(dest)
PY
fi

SRC="$SRC_RGBA"

rm -rf "$ISET"
mkdir -p "$ISET"

# Apple icon sizes: 16, 32, 128, 256, 512 (each @1x and @2x).
declare -a SIZES=(16 32 128 256 512)
for s in "${SIZES[@]}"; do
    s2=$((s * 2))
    sips -s format png -z $s  $s  "$SRC" --out "$ISET/icon_${s}x${s}.png"      >/dev/null
    sips -s format png -z $s2 $s2 "$SRC" --out "$ISET/icon_${s}x${s}@2x.png"   >/dev/null
done

iconutil -c icns "$ISET" -o "$OUT"

# iconutil leaves the iconset around — keep it (useful for debugging or
# rebuilding without the source PNG handy).
echo ">> Wrote $OUT ($(du -h "$OUT" | cut -f1))"
