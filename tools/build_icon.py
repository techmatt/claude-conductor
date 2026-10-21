"""Build the app's `.ico` from its SVG master.

    uv run python tools/build_icon.py [--png <dir>]

Writes `src/conductor/app/conductor.ico` (16, 20, 24, 32, 48 and 256 px, each drawn at
its own size, the bolder variant at 24 px and below: `conductor.app.icon`). Run it after
any change to `icon.svg` or the icon's weights, and commit the result. `--png` also
saves each size as a PNG there, to look at.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from conductor.app import icon  # noqa: E402


def main(argv: list[str]) -> int:
    app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])   # noqa: F841 - QImage needs one
    icon.ICO.write_bytes(icon.ico_bytes())
    print(f"wrote {icon.ICO} ({icon.ICO.stat().st_size} bytes; {', '.join(map(str, icon.ICO_SIZES))} px)")
    if "--png" in argv:
        out = Path(argv[argv.index("--png") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for size in icon.ICO_SIZES:
            icon.image(size).save(str(out / f"icon_{size}.png"))
        print(f"pngs in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
