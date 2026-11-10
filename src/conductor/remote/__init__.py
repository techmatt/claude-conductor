"""Remote workers (opt-in, removable; docs/remote-workers.md).

The rest of conductor reaches this package through two hooks only, each a no-op unless
the project's `conductor.toml` has a `[remote]` section, and neither able to raise:

- `start_broker`, from `Daemon.startup`: the broker process (`broker.py`);
- `item_launch`, from `_start_exec` for an item with `remote: true`: the `farm-seeds`
  command on its PATH, and one paragraph naming it at the end of its prompt.

The app imports `app.py` (its chip and Workers tab) if it is there, and shows nothing for a
project without `[remote]`. Deleting this folder, those two hooks, that import (and
`remote` in `items.FIELDS`) and the `[remote]` sections removes the feature.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PARAGRAPH = """
## Remote workers

`farm-seeds <first seed> <count> <out file> [args…]` is on your PATH for this item. It runs this
project's seeded job ({native}, in `{folder}`) for that seed range and leaves at `<out file>`
exactly what that command would, spread over any remote workers that are connected (locally when
none is). Run seeded batches through it, not the native command. Its last stderr line says where
the shards ran.
"""


def start_broker(control: Path, warn) -> None:
    """At daemon start: the broker for a project with `[remote]`, unless one under the same
    settings is already serving (a daemon restart keeps it). `warn(note)` logs a problem;
    nothing here can stop the daemon starting."""
    try:
        _start_broker(control, warn)
    except Exception as exc:                     # noqa: BLE001 - the daemon starts regardless
        warn(f"remote workers: the broker did not start ({exc!r}); remote: true items run locally")


def _start_broker(control: Path, warn) -> None:
    from . import settings
    try:
        r = settings.load(control)
    except settings.RemoteError as exc:
        warn(f"remote workers off: {exc}")
        return
    if r is None:
        return
    import psutil

    from .. import procs
    info_path = r.state / "broker.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        p = psutil.Process(int(info["pid"]))
        if "conductor.remote.broker" in " ".join(p.cmdline()):
            if info.get("digest") == r.digest():
                return
            p.kill()                             # started under other settings
            p.wait(5)
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    info_path.unlink(missing_ok=True)
    procs.spawn_detached([sys.executable, "-P", "-m", "conductor.remote.broker", str(r.control)], r.control,
                         r.state / "broker.log")


def item_launch(control: Path, prompt: Path, item: str = "") -> dict[str, str]:
    """For a `remote: true` item: its extra environment (the `farm-seeds` folder first on
    PATH, and its id for the worker page), with the paragraph added to its prompt. Nothing
    at all without `[remote]`, and nothing on any error: the item then runs as an ordinary one."""
    try:
        return _item_launch(control, prompt, item)
    except Exception:                            # noqa: BLE001 - never in the way of a launch
        return {}


def _item_launch(control: Path, prompt: Path, item: str) -> dict[str, str]:
    from . import settings
    try:
        r = settings.load(control)
    except settings.RemoteError:
        return {}                                # the daemon's start already warned
    if r is None:
        return {}
    from .. import procs
    bin_dir = r.state / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = Path(sys.executable).as_posix()
    # -P: the item's folder never shadows a module farm-seeds imports.
    (bin_dir / settings.COMMAND).write_text(f'#!/bin/sh\nexec "{py}" -P -m conductor.remote.client "$@"\n',
                                            encoding="utf-8", newline="\n")
    (bin_dir / f"{settings.COMMAND}.cmd").write_text(
        f'@"{Path(sys.executable)}" -P -m conductor.remote.client %*\r\n', encoding="utf-8", newline="")
    os.chmod(bin_dir / settings.COMMAND, 0o755)
    env = procs.child_env()
    path_key = next((k for k in env if k.upper() == "PATH"), "PATH")
    extra = {path_key: os.pathsep.join([str(bin_dir), env.get(path_key, "")]), settings.CONTROL_VAR: str(r.control),
             settings.ITEM_VAR: item}
    with open(prompt, "a", encoding="utf-8", newline="\n") as f:
        f.write(PARAGRAPH.format(native=f"`{' '.join(r.native)}`", folder=r.dir or "."))
    return extra
