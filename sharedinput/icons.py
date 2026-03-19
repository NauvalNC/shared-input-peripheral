"""Generate tray icons programmatically — no external asset files needed."""

from __future__ import annotations

from PIL import Image, ImageDraw


def _create_base_icon(size: int = 64) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    return img, draw


def create_default_icon(size: int = 64) -> Image.Image:
    """Default icon — white monitor with blue arrow."""
    img, draw = _create_base_icon(size)
    s = size

    # Monitor body
    draw.rounded_rectangle(
        [s * 0.1, s * 0.08, s * 0.9, s * 0.65],
        radius=s * 0.06,
        fill=(60, 60, 60),
        outline=(180, 180, 180),
        width=max(1, s // 32),
    )
    # Screen
    draw.rectangle(
        [s * 0.17, s * 0.14, s * 0.83, s * 0.58],
        fill=(30, 120, 220),
    )
    # Stand
    draw.rectangle([s * 0.38, s * 0.65, s * 0.62, s * 0.75], fill=(120, 120, 120))
    draw.rectangle([s * 0.28, s * 0.75, s * 0.72, s * 0.82], fill=(100, 100, 100))

    # Arrow on screen (pointing right)
    arrow_y = s * 0.36
    draw.polygon([
        (s * 0.30, arrow_y - s * 0.08),
        (s * 0.55, arrow_y),
        (s * 0.30, arrow_y + s * 0.08),
    ], fill=(255, 255, 255))

    return img


def create_active_icon(size: int = 64) -> Image.Image:
    """Active icon — green-tinted, forwarding input to a client."""
    img, draw = _create_base_icon(size)
    s = size

    # Monitor body
    draw.rounded_rectangle(
        [s * 0.1, s * 0.08, s * 0.9, s * 0.65],
        radius=s * 0.06,
        fill=(60, 60, 60),
        outline=(80, 220, 100),
        width=max(2, s // 20),
    )
    # Screen (green tint)
    draw.rectangle(
        [s * 0.17, s * 0.14, s * 0.83, s * 0.58],
        fill=(20, 160, 60),
    )
    # Stand
    draw.rectangle([s * 0.38, s * 0.65, s * 0.62, s * 0.75], fill=(120, 120, 120))
    draw.rectangle([s * 0.28, s * 0.75, s * 0.72, s * 0.82], fill=(100, 100, 100))

    # Arrow on screen
    arrow_y = s * 0.36
    draw.polygon([
        (s * 0.30, arrow_y - s * 0.08),
        (s * 0.55, arrow_y),
        (s * 0.30, arrow_y + s * 0.08),
    ], fill=(255, 255, 255))

    return img


def create_disabled_icon(size: int = 64) -> Image.Image:
    """Disabled icon — grey, no clients connected."""
    img, draw = _create_base_icon(size)
    s = size

    # Monitor body (grey)
    draw.rounded_rectangle(
        [s * 0.1, s * 0.08, s * 0.9, s * 0.65],
        radius=s * 0.06,
        fill=(80, 80, 80),
        outline=(120, 120, 120),
        width=max(1, s // 32),
    )
    # Screen (dark grey)
    draw.rectangle(
        [s * 0.17, s * 0.14, s * 0.83, s * 0.58],
        fill=(60, 60, 60),
    )
    # Stand
    draw.rectangle([s * 0.38, s * 0.65, s * 0.62, s * 0.75], fill=(90, 90, 90))
    draw.rectangle([s * 0.28, s * 0.75, s * 0.72, s * 0.82], fill=(80, 80, 80))

    # X mark on screen
    cx, cy = s * 0.50, s * 0.36
    r = s * 0.10
    w = max(2, s // 20)
    draw.line([(cx - r, cy - r), (cx + r, cy + r)], fill=(160, 160, 160), width=w)
    draw.line([(cx - r, cy + r), (cx + r, cy - r)], fill=(160, 160, 160), width=w)

    return img


def save_icons(directory: str = "assets", size: int = 64) -> None:
    """Save all icon variants to disk."""
    from pathlib import Path

    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)

    create_default_icon(size).save(path / "icon.png")
    create_active_icon(size).save(path / "icon_active.png")
    create_disabled_icon(size).save(path / "icon_disabled.png")

    # Also save .ico for Windows (256x256 PNG-compressed, standard for modern Windows)
    for name, creator in [
        ("icon.ico", create_default_icon),
        ("icon_active.ico", create_active_icon),
        ("icon_disabled.ico", create_disabled_icon),
    ]:
        icon_img = creator(256)
        icon_img.save(path / name, format="ICO")

    # macOS .icns — just save a large PNG (PyInstaller can use it)
    create_default_icon(512).save(path / "icon_512.png")


if __name__ == "__main__":
    save_icons()
    print("Icons saved to assets/")
