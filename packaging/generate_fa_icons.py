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
    font = ImageFont.truetype(str(FONT_PATH), 10)
    for name, code in ICON_CODES.items():
        for variant, color in COLORS.items():
            image = Image.new("RGBA", (14, 14), (0, 0, 0, 0))
            ImageDraw.Draw(image).text((7, 7), code, font=font, fill=color, anchor="mm")
            image.save(OUTPUT_DIR / f"{name}-{variant}.png")


if __name__ == "__main__":
    main()
