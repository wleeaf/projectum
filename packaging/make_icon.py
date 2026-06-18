"""Generate Projectum's app icon — an italic serif **P** — in every format the
app and its packaging need, from a single source. Re-run after a design tweak:

    .venv/bin/python packaging/make_icon.py

The glyph is baked to a font-independent vector path (via Qt), so the SVG
scales crisply and every raster rendition is pixel-identical to it. Produces:

    projectum/assets/icon.svg          canonical window icon (loaded at runtime)
    packaging/appimage/projectum.png   256px AppImage icon
    packaging/windows/projectum.ico    PyInstaller --icon (Windows)
    packaging/macos/projectum.icns     PyInstaller --icon (macOS)
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor, QFont, QGuiApplication, QImage, QPainter, QPainterPath, QTransform,
)
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# Glyph styling: a high-contrast display serif, heavy + italic — matches the
# website favicon's character but designed for large display sizes.
GLYPH_FONT = "Noto Serif Display"
GLYPH_STYLE = "Black Italic"
CANVAS = 256
TARGET_H = 156.0          # glyph cap-to-baseline height inside the canvas
OPTICAL_DX = -4.0         # nudge the italic glyph left so it reads centered


def _build_glyph_path() -> QPainterPath:
    font = QFont(GLYPH_FONT)
    font.setStyleName(GLYPH_STYLE)
    font.setPointSizeF(220)
    raw = QPainterPath()
    raw.addText(0.0, 0.0, font, "P")
    br = raw.boundingRect()
    scale = TARGET_H / br.height()
    t = QTransform()
    t.translate(
        CANVAS / 2 - br.center().x() * scale + OPTICAL_DX,
        CANVAS / 2 - br.center().y() * scale,
    )
    t.scale(scale, scale)
    return t.map(raw)


def _path_to_svg_d(p: QPainterPath) -> str:
    cmds: list[str] = []
    i, n = 0, p.elementCount()
    started = False
    while i < n:
        e = p.elementAt(i)
        if e.isMoveTo():
            if started:
                cmds.append("Z")
            cmds.append(f"M{e.x:.2f} {e.y:.2f}")
            started = True
            i += 1
        elif e.isLineTo():
            cmds.append(f"L{e.x:.2f} {e.y:.2f}")
            i += 1
        elif e.isCurveTo():
            c2 = p.elementAt(i + 1)
            end = p.elementAt(i + 2)
            cmds.append(
                f"C{e.x:.2f} {e.y:.2f} {c2.x:.2f} {c2.y:.2f} {end.x:.2f} {end.y:.2f}"
            )
            i += 3
        else:
            i += 1
    if started:
        cmds.append("Z")
    return " ".join(cmds)


def _svg(glyph_d: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#22243a"/>
      <stop offset="1" stop-color="#0e0f17"/>
    </linearGradient>
    <linearGradient id="p" x1="0.1" y1="0.05" x2="0.9" y2="0.95">
      <stop offset="0" stop-color="#c4a7ff"/>
      <stop offset="0.55" stop-color="#dcb1f0"/>
      <stop offset="1" stop-color="#f5b8e2"/>
    </linearGradient>
    <linearGradient id="border" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#3a3a55" stop-opacity="0.85"/>
      <stop offset="1" stop-color="#181927" stop-opacity="0.35"/>
    </linearGradient>
    <linearGradient id="shine" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.06"/>
      <stop offset="0.5" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
  </defs>

  <rect x="6" y="6" width="244" height="244" rx="56" fill="url(#bg)"/>
  <rect x="6" y="6" width="244" height="244" rx="56" fill="url(#shine)"/>
  <rect x="6.75" y="6.75" width="242.5" height="242.5" rx="55.25"
        fill="none" stroke="url(#border)" stroke-width="1.5"/>

  <path d="{glyph_d}" fill="url(#p)" fill-rule="evenodd"/>
</svg>
"""


def _render(svg: str, size: int) -> QImage:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(p)
    p.end()
    return img


def main() -> None:
    QGuiApplication([])
    svg = _svg(_path_to_svg_d(_build_glyph_path()))

    svg_path = ROOT / "projectum" / "assets" / "icon.svg"
    svg_path.write_text(svg, encoding="utf-8")
    print(f"wrote {svg_path.relative_to(ROOT)}")

    targets = {
        ROOT / "packaging" / "appimage" / "projectum.png": ("PNG", 256),
        ROOT / "packaging" / "windows" / "projectum.ico": ("ICO", 256),
        ROOT / "packaging" / "macos" / "projectum.icns": ("ICNS", 512),
    }
    for out, (fmt, size) in targets.items():
        out.parent.mkdir(parents=True, exist_ok=True)
        if not _render(svg, size).save(str(out), fmt):
            raise SystemExit(f"failed to write {out}")
        print(f"wrote {out.relative_to(ROOT)}  ({fmt} {size}px)")


if __name__ == "__main__":
    main()
