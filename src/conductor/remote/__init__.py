"""Remote workers (opt-in, removable; docs/remote-workers.md).

The rest of conductor reaches this package through three hooks only, each a no-op unless
the project's `conductor.toml` has a `[remote]` section, and none able to raise:

- `start_broker`, from `Daemon.startup`: the broker process (`broker.py`), and a request
  pinning a generated token into `[remote]` (`pin_token`);
- `keep_broker`, from `Daemon.tick`: a broker that died is started again (checked every
  `KEEP_SECONDS`);
- `item_launch`, from `_start_exec` for an item `wants` says farms (`remote: true`, or no
  `remote:` under `[remote] default = true`): the `farm-seeds` command on its PATH, and one
  paragraph naming it at the end of its prompt.

The app imports `app.py` (its chip and Workers tab) if it is there, and shows nothing for a
project without `[remote]`. Deleting this folder, those hooks, that import (and
`remote` in `items.FIELDS`) and the `[remote]` sections removes the feature.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

KEEP_SECONDS = 10.0
_watch: dict[str, tuple[float, bool]] = {}     # control -> (next check, the broker was up at the last)

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
        _watch[str(control)] = (time.monotonic() + KEEP_SECONDS, True)
        if (r := _start_broker(control, warn)[1]) is not None:
            pin_token(r)
    except Exception as exc:                     # noqa: BLE001 - the daemon starts regardless
        warn(f"remote workers: the broker did not start ({exc!r}); remote: true items run locally")


def keep_broker(control: Path, warn) -> None:
    """Each daemon tick, at most every KEEP_SECONDS: a broker that is gone (killed, crashed)
    is started again; one warning when it had been up, none while it never comes up."""
    try:
        due, was_up = _watch.get(str(control), (0.0, True))
        if time.monotonic() < due:
            return
        did = _start_broker(control, lambda note: None)[0]
        _watch[str(control)] = (time.monotonic() + KEEP_SECONDS, did != "started")
        if did == "started" and was_up:
            warn("remote workers: the broker was not running (killed or crashed); started it again")
    except Exception:                            # noqa: BLE001 - never in the daemon's way
        pass


def _start_broker(control: Path, warn) -> tuple[str, object]:
    """("none" | "kept" | "started", the settings or None)."""
    from . import settings
    try:
        r = settings.load(control)
    except settings.RemoteError as exc:
        warn(f"remote workers off: {exc}")
        return "none", None
    if r is None:
        return "none", None
    import psutil

    from .. import procs
    info_path = r.state / "broker.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
        p = psutil.Process(int(info["pid"]))
        if "conductor.remote.broker" in " ".join(p.cmdline()):
            if info.get("digest") == r.digest():
                return "kept", r
            p.kill()                             # started under other settings
            p.wait(5)
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        pass
    info_path.unlink(missing_ok=True)
    r.state.mkdir(parents=True, exist_ok=True)
    new = procs.spawn_detached([sys.executable, "-P", "-m", "conductor.remote.broker", str(r.control)], r.control,
                               r.state / "broker.log")
    # Its pid at once, so the next check sees it starting rather than starting another.
    info_path.write_text(json.dumps({"pid": new.pid, "digest": r.digest(), "starting": True}) + "\n",
                         encoding="utf-8", newline="\n")
    return "started", r


def pin_token(r) -> None:
    """A `[remote]` without `token` gets the one in effect written into it, through a request
    the daemon applies and commits (the control folder's normal route), so clearing
    `.conductor/remote/` never changes the worker URL. One request at a time; a failed one
    leaves its receipt and the token file still serves."""
    from . import settings
    if r.token:
        return
    from ..util import stamp
    requests = r.control / "requests"
    if any(settings.TOKEN_REQUEST in d.name for d in requests.glob("*") if d.is_dir()):
        return                                   # one waiting or failed already: its receipt says why
    text = (r.control / settings.CONFIG_NAME).read_text(encoding="utf-8-sig")
    header = re.findall(r"^\[remote\][^\r\n]*$", text, flags=re.M)
    if len(header) != 1:
        return
    d = requests / (re.sub(r"[^0-9T]", "", stamp()) + "-" + settings.TOKEN_REQUEST)
    d.mkdir(parents=True, exist_ok=True)
    (d / "request.md").write_text(
        f"---\ntargets: [{settings.CONFIG_NAME}]\nmessage: \"remote: pin the worker page's token, so its URL never "
        f"changes\"\n---\n<<<<<<< {settings.CONFIG_NAME}\n{header[0]}\n=======\n{header[0]}\n"
        f"token = \"{r.secret()}\"\n>>>>>>>\n", encoding="utf-8", newline="\n")


def wants(control: Path, item) -> bool:
    """Whether an item gets `farm-seeds`: its `remote:` when it says, else `[remote] default`
    for an item that is neither readonly nor a template. Never raises."""
    if item.remote is not None:
        return bool(item.remote)
    if item.readonly or item.template:
        return False
    try:
        from . import settings
        r = settings.load(control)
        return bool(r and r.default)
    except Exception:                            # noqa: BLE001 - a bad [remote] warns at daemon start
        return False


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
