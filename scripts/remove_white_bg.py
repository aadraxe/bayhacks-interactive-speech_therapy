"""Remove near-white backgrounds from character PNGs with soft edge alpha."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
IMG = ROOT / "static" / "img"


def remove_white_bg(src: Path, dst: Path, threshold: int = 245, softness: int = 28) -> None:
    im = Image.open(src).convert("RGBA")
    pixels = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b, a = pixels[x, y]
            # Near-white + low color variance = background
            mn = min(r, g, b)
            mx = max(r, g, b)
            if mx - mn > 18:
                continue  # tinted pixel (fur/ears) — keep
            if mn >= threshold:
                pixels[x, y] = (r, g, b, 0)
            elif mn >= threshold - softness:
                # Soft feather near the edge
                t = (mn - (threshold - softness)) / softness
                pixels[x, y] = (r, g, b, max(0, min(255, int(a * (1 - t)))))
    # Trim empty margins a little
    bbox = im.getbbox()
    if bbox:
        pad = 8
        left = max(0, bbox[0] - pad)
        top = max(0, bbox[1] - pad)
        right = min(w, bbox[2] + pad)
        bottom = min(h, bbox[3] + pad)
        im = im.crop((left, top, right, bottom))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "PNG")
    print(f"wrote {dst} ({im.size[0]}x{im.size[1]})")


def main() -> int:
    bunny = IMG / "buddy-bunny.png"
    out = IMG / "buddy-bunny.png"
    remove_white_bg(bunny, out)
    return 0


if __name__ == "__main__":
    # Prefer rembg when available
    src = IMG / "buddy-bunny.png"
    try:
        from rembg import remove

        data = remove(src.read_bytes())
        out = IMG / "buddy-bunny.png"
        out.write_bytes(data)
        print(f"rembg wrote {out}")
        sys.exit(0)
    except Exception as exc:
        print(f"rembg unavailable ({exc}); using soft white-key fallback")
        sys.exit(main())
