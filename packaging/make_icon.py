"""Generate packaging/zzapsync.ico — the app/installer icon (a blue 'Z' tile).

Matches the runtime tray glyph (app/gui/tray.py). Run once; the .ico is committed:
    .venv\\Scripts\\python packaging\\make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PRIMARY = (30, 64, 175)   # theme.PRIMARY (#1E40AF)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    radius = max(2, size // 5)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=PRIMARY)
    # Centered bold "Z"; fall back to the default font if Segoe UI isn't found.
    try:
        font = ImageFont.truetype("segoeuib.ttf", int(size * 0.62))
    except OSError:
        font = ImageFont.load_default()
    text = "Z"
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((size - tw) / 2 - box[0], (size - th) / 2 - box[1]), text,
           font=font, fill=(255, 255, 255, 255))
    return img


def main() -> None:
    out = Path(__file__).with_name("zzapsync.ico")
    base = _render(256)
    base.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
