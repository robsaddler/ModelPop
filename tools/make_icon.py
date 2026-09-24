"""Draw the application icon.

Kept as a script rather than a checked-in mystery binary: the icon is a few
shapes and a palette, and both should be readable and changeable. Run it after
editing and commit the result.

    .venv/Scripts/python.exe tools/make_icon.py

What it draws: a part on a build plate, in the same blue the viewport uses for
a model. Three faces of an isometric cube in three tones, so it reads as solid
rather than as a hexagon, over the plate colour from the viewport's own
palette. Everything is drawn eight times oversized and resampled down, because
the shapes are diagonal and a 16-pixel icon drawn directly looks like gravel.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

# Straight from modelpop.rendering.viewport, so the icon and the thing it
# stands for are recognisably the same object.
BACKGROUND = (23, 26, 31, 255)
TOP = (143, 191, 233, 255)
LEFT = (111, 168, 220, 255)
RIGHT = (66, 112, 156, 255)
PLATE = (136, 153, 166, 255)

SIZES = (16, 24, 32, 48, 64, 128, 256)
OVERSAMPLE = 8
EDGE = 256

HERE = Path(__file__).resolve().parent
TARGET = HERE.parent / "src" / "modelpop" / "ui" / "resources"


def draw(edge: int) -> Image.Image:
    """One square icon, drawn large."""
    image = Image.new("RGBA", (edge, edge), (0, 0, 0, 0))
    pen = ImageDraw.Draw(image)

    radius = edge * 0.22
    pen.rounded_rectangle([(0, 0), (edge - 1, edge - 1)], radius=radius, fill=BACKGROUND)

    # The cube, as three rhombi meeting at the centre of the top face.
    middle = edge / 2
    width = edge * 0.28  # half-width of the cube
    height = edge * 0.16  # how far the top face slopes
    depth = edge * 0.23  # how tall the vertical faces are
    lift = edge * 0.08  # nudge upwards, to leave room for the plate

    top_back = (middle, middle - height - depth / 2 - lift)
    top_right = (middle + width, middle - depth / 2 - lift)
    top_front = (middle, middle + height - depth / 2 - lift)
    top_left = (middle - width, middle - depth / 2 - lift)

    pen.polygon([top_back, top_right, top_front, top_left], fill=TOP)
    pen.polygon(
        [
            top_left,
            top_front,
            (top_front[0], top_front[1] + depth),
            (top_left[0], top_left[1] + depth),
        ],
        fill=LEFT,
    )
    pen.polygon(
        [
            top_front,
            top_right,
            (top_right[0], top_right[1] + depth),
            (top_front[0], top_front[1] + depth),
        ],
        fill=RIGHT,
    )

    # The plate it stands on: a single line, wider than the part.
    plate_y = top_front[1] + depth + edge * 0.10
    thickness = max(1.0, edge * 0.045)
    pen.rounded_rectangle(
        [
            (middle - width * 1.25, plate_y),
            (middle + width * 1.25, plate_y + thickness),
        ],
        radius=thickness / 2,
        fill=PLATE,
    )
    return image


def main() -> int:
    """Write the icon as .ico for Windows and .png for everything else."""
    TARGET.mkdir(parents=True, exist_ok=True)
    large = draw(EDGE * OVERSAMPLE)

    frames = [large.resize((size, size), Image.LANCZOS) for size in SIZES]
    frames[-1].save(TARGET / "modelpop.png")
    frames[-1].save(TARGET / "modelpop.ico", sizes=[(size, size) for size in SIZES])

    print(f"wrote {TARGET / 'modelpop.ico'} at {', '.join(str(s) for s in SIZES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
