"""Generate the app/installer icons (committed .ico files):

  - packaging/zzapsync.ico    — blue tile with 'Z'  («ZZap Sync»)
  - packaging/pricemailer.ico — green tile with '@' («Рассылка прайса»)

Matches the runtime tray glyphs (app/gui/tray.py + app/flavor.tray_letter). Run once:
    .venv\\Scripts\\python packaging\\make_icon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PRIMARY = (30, 64, 175)     # theme.PRIMARY (#1E40AF)
MAIL_GREEN = (21, 128, 61)  # «Рассылка прайса» tile (#15803D)
SIZES = [16, 24, 32, 48, 64, 128, 256]


def _render(size: int, letter: str, color: tuple[int, int, int]) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = max(1, size // 16)
    radius = max(2, size // 5)
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=radius, fill=color)
    # Centered bold glyph; fall back to the default font if Segoe UI isn't found.
    try:
        font = ImageFont.truetype("segoeuib.ttf", int(size * 0.62))
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), letter, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((size - tw) / 2 - box[0], (size - th) / 2 - box[1]), letter,
           font=font, fill=(255, 255, 255, 255))
    return img


def _write(name: str, letter: str, color: tuple[int, int, int]) -> None:
    out = Path(__file__).with_name(name)
    base = _render(256, letter, color)
    base.save(out, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"wrote {out}")


def main() -> None:
    _write("zzapsync.ico", "Z", PRIMARY)
    _write("pricemailer.ico", "@", MAIL_GREEN)


if __name__ == "__main__":
    main()
