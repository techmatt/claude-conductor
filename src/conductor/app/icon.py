"""The app's icon: a tetrahedron's four nodes and six links around a gold hub, in navy.

`icon.svg` is the master. `svg()` is the same drawing with the hub's colour as a
parameter (the tray turns it red, or pulses it) and a bolder variant for 24 px and
below, where the master's thin links and small nodes stop reading. `conductor.ico`
(16–256 px) is built from it by `tools/build_icon.py` and committed beside it.
"""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QIcon, QImage, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

MASTER = Path(__file__).with_name("icon.svg")
ICO = Path(__file__).with_name("conductor.ico")
ICO_SIZES = (16, 20, 24, 32, 48, 256)
SMALL = 24                  # this size and below draw the bolder variant
HUB = {"gold": "#f4b942", "dim": "#8a6a22", "red": "#e5484d", "slate": "#8da9c4"}

# (link width, node radius, hub radius): the master's, and the bolder small one.
_WEIGHTS = {False: ("2.6", "3.6", "7"), True: ("4.4", "5.4", "9.5")}
_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <rect width="64" height="64" rx="14" fill="#0b2545"/>
  <g stroke="#8da9c4" stroke-width="{link}" stroke-linecap="round">
    <line x1="32" y1="10" x2="11" y2="50"/><line x1="32" y1="10" x2="53" y2="50"/>
    <line x1="11" y1="50" x2="53" y2="50"/><line x1="32" y1="10" x2="32" y2="36"/>
    <line x1="11" y1="50" x2="32" y2="36"/><line x1="53" y1="50" x2="32" y2="36"/>
  </g>
  <g fill="#eef4ed"><circle cx="32" cy="10" r="{node}"/><circle cx="11" cy="50" r="{node}"/><circle cx="53" cy="50" r="{node}"/></g>
  <circle cx="32" cy="36" r="{hub}" fill="{fill}"/>
</svg>
"""


def svg(hub: str = "gold", small: bool = False) -> str:
    """The icon's SVG; `svg()` is exactly `icon.svg`."""
    link, node, hub_r = _WEIGHTS[small]
    return _SVG.format(link=link, node=node, hub=hub_r, fill=HUB.get(hub, hub))


def image(size: int, hub: str = "gold") -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    QSvgRenderer(QByteArray(svg(hub, size <= SMALL).encode("utf-8"))).render(p, QRectF(0, 0, size, size))
    p.end()
    return img


_cache: dict[str, QIcon] = {}


def icon(hub: str = "gold") -> QIcon:
    """Every size drawn for its own size (the bold variant at 24 px and below), cached
    per hub colour: a refresh asks for the same few icons over and over."""
    if hub not in _cache:
        ic = QIcon()
        for size in (*ICO_SIZES, 64, 128):
            ic.addPixmap(QPixmap.fromImage(image(size, hub)))
        _cache[hub] = ic
    return _cache[hub]


def app_icon() -> QIcon:
    """The window's and the taskbar's: the committed `.ico`, else drawn."""
    return QIcon(str(ICO)) if ICO.is_file() else icon()


def png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(buf.data())


def ico_bytes(sizes=ICO_SIZES) -> bytes:
    """A Windows `.ico` holding one PNG-compressed image per size (Vista and later)."""
    images = [png(image(s)) for s in sizes]
    out = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    for s, data in zip(sizes, images):
        out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    return out + b"".join(images)
