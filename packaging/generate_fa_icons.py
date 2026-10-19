#!/usr/bin/env python3
"""Generate Tk-compatible button assets from Font Awesome Free Solid."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


PROJECT_DIR = Path(__file__).resolve().parents[1]
FONT_PATH = PROJECT_DIR / "assets" / "fonts" / "fa-solid-900.ttf"
OUTPUT_DIR = PROJECT_DIR / "assets" / "icons" / "fa"
ICON_CODES = {
    "plus": "\uf067",
    "folder": "\uf07b",
    "save": "\uf0c7",
    "import": "\uf56f",
    "pin": "\uf08d",
    "play": "\uf04b",
    "stop": "\uf04d",
    "monitor": "\uf108",
    "terminal": "\uf120",
    "edit": "\uf044",
    "trash": "\uf2ed",
}
COLORS = {"dark": "#243954", "white": "#ffffff", "muted": "#a8b4c1"}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    # Render from the vector font at 4x, then downsample with Lanczos to the
    # unchanged 18px Tk display size. This improves edge antialiasing.
    font = ImageFont.truetype(str(FONT_PATH), 52)
    for name, code in ICON_CODES.items():
        for variant, color in COLORS.items():
            source = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
            ImageDraw.Draw(source).text((36, 36), code, font=font, fill=color, anchor="mm")
            image = source.resize((18, 18), Image.Resampling.LANCZOS)
            image.save(OUTPUT_DIR / f"{name}-{variant}.png")
            # Rasterize each DPI size from the font, not from the 18px bitmap.
            for size in (24, 30, 36, 42, 48, 54, 60, 72):
                large = Image.new("RGBA", (size * 4, size * 4), (0, 0, 0, 0))
                face = ImageFont.truetype(str(FONT_PATH), round(size * 52 / 18))
                ImageDraw.Draw(large).text((size * 2, size * 2), code, font=face, fill=color, anchor="mm")
                large.resize((size, size), Image.Resampling.LANCZOS).save(OUTPUT_DIR / f"{name}-{variant}-{size}.png")


if __name__ == "__main__":
    main()
