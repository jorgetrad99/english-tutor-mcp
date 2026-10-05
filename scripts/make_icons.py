"""Render the PWA icons and iOS splash screens from the brand mark (a practice trail).

Run: uv run --with pillow python scripts/make_icons.py
Writes src/tutor/web/static/icons/*.png; the PNGs are committed. Re-run only when the mark
changes.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "src" / "tutor" / "web" / "static" / "icons"
INK = (17, 64, 44)
PAPER = (227, 244, 234)
LEAF = (47, 158, 102)
CORAL = (255, 111, 79)
SUPERSAMPLE = 4
TRAIL = ((0.10, 0.72), (0.28, 0.50), (0.44, 0.46), (0.58, 0.62), (0.74, 0.40), (0.90, 0.26))
SPLASHES = ((750, 1334), (1170, 2532), (1179, 2556), (1290, 2796))


def _mark(draw: ImageDraw.ImageDraw, x0: float, y0: float, size: float) -> None:
    points = [(x0 + px * size, y0 + py * size) for px, py in TRAIL]
    draw.line(points, fill=LEAF, width=max(2, round(size * 0.08)), joint="curve")
    dot = size * 0.045
    for x, y in points[:-1]:
        draw.ellipse((x - dot, y - dot, x + dot, y + dot), fill=PAPER)
    end_x, end_y = points[-1]
    big = size * 0.09
    draw.ellipse((end_x - big, end_y - big, end_x + big, end_y + big), fill=CORAL)


def icon_image(size: int, *, opaque: bool, rounded: bool, inset_ratio: float) -> Image.Image:
    """Draw at 4x and downsample, which gives smooth edges without a vector renderer."""
    s = size * SUPERSAMPLE
    img = Image.new("RGBA", (s, s), (*INK, 255) if opaque else (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if rounded:
        draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=round(s * 0.22), fill=INK)
    inset = s * inset_ratio
    _mark(draw, inset, inset, s - 2 * inset)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for size in (192, 512):
        icon = icon_image(size, opaque=False, rounded=True, inset_ratio=0.12)
        icon.save(OUT / f"icon-{size}.png", optimize=True)
    # Maskable: full bleed, mark inside the central 80% safe zone.
    maskable = icon_image(512, opaque=True, rounded=False, inset_ratio=0.2)
    maskable.save(OUT / "icon-maskable-512.png", optimize=True)
    # iOS rounds the corners itself and turns transparency black, so this one is opaque.
    apple = icon_image(180, opaque=True, rounded=False, inset_ratio=0.14).convert("RGB")
    apple.save(OUT / "apple-touch-icon.png", optimize=True)
    for width, height in SPLASHES:
        canvas = Image.new("RGB", (width, height), PAPER)
        tile = round(min(width, height) * 0.32)
        logo = icon_image(tile, opaque=False, rounded=True, inset_ratio=0.12)
        canvas.paste(logo, ((width - tile) // 2, (height - tile) // 2), logo)
        canvas.save(OUT / f"splash-{width}x{height}.png", optimize=True)


if __name__ == "__main__":
    main()
