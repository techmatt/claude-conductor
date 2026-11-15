"""Port blocks: each control folder owns a block of SIZE local ports, `ports = "5260-5279"`
in its conductor.toml, and every port the project names (run targets, tools, `[remote] port`,
a command's `--port N`) lies in it. One registry on this machine records which control
folder holds which block, so two projects never claim the same port (docs/control-folder.md,
*Port blocks*).

    ~/.conductor/ports.json    {"5260": {"control": "C:/Code/aicomplete-control", "project": "aicomplete"}, ...}

$CONDUCTOR_PORTS names another registry file; `off` turns the check off (the test suite,
except the tests of this). The registry is a record, not the truth: an entry counts only
while its control folder's conductor.toml still declares that block, so a deleted project
or a moved block frees it with no clean-up step.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import urllib.parse
from pathlib import Path

BASE = 5200          # the first block's first port
SIZE = 20            # ports a block
TOP = 49152          # the OS's ephemeral range starts here (executors' CONDUCTOR_PORT comes from it)
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}
REMOTE_DEFAULT_PORT = 8770       # remote/settings.py's default, so a `[remote]` without `port` names it

_PORT_ARG = re.compile(r"--port[= ](\d+)")


class BlockError(ValueError):
    pass


def registry_path() -> Path | None:
    """The registry file, or None when the check is off."""
    value = os.environ.get("CONDUCTOR_PORTS", "")
    if value.strip().lower() == "off":
        return None
    return Path(value) if value else Path.home() / ".conductor" / "ports.json"


def text(block: range) -> str:
    return f"{block.start}-{block.stop - 1}"


def parse(value) -> range:
    """`"5260-5279"`: a whole block, aligned on BASE + n*SIZE."""
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", value) if isinstance(value, str) else None
    if not m:
        raise BlockError(f'`ports` must be a block like "{text(range(BASE, BASE + SIZE))}"')
    start, last = int(m[1]), int(m[2])
    if start < BASE or (start - BASE) % SIZE or last != start + SIZE - 1 or last >= TOP:
        raise BlockError(f"`ports` {value!r} is not a block: blocks are {SIZE} ports from {BASE} up "
                         f"({text(range(BASE, BASE + SIZE))}, {text(range(BASE + SIZE, BASE + 2 * SIZE))}, ...)")
    return range(start, last + 1)


def local_port(url: str) -> int | None:
    """The port a URL names on this machine; None for another host or no explicit port."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    return port if port and (parts.hostname or "").lower() in LOCAL_HOSTS else None


def command_ports(command) -> list[int]:
    words = command if isinstance(command, str) else " ".join(command)
    return [int(p) for p in _PORT_ARG.findall(words)]


def _declared_block(control: Path) -> int | None:
    """The block start a control folder's conductor.toml declares now; -1 when it can't be read
    (mid-edit: still held), None when it declares none or the folder is gone."""
    try:
        data = tomllib.loads((control / "conductor.toml").read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return -1
    try:
        return parse(data["ports"]).start if "ports" in data else None
    except BlockError:
        return -1


def _read(path: Path) -> dict[str, dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict) and isinstance(v.get("control"), str)} \
        if isinstance(data, dict) else {}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dict(sorted(data.items(), key=lambda kv: int(kv[0]))), indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _same(a: Path, b: Path) -> bool:
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def held() -> list[tuple[range, Path, str]]:
    """Every block whose registered control folder still declares it: (block, control, project)."""
    path = registry_path()
    if path is None:
        return []
    out = []
    for start, entry in _read(path).items():
        try:
            begin = int(start)
        except ValueError:
            continue
        control = Path(entry["control"])
        declared = _declared_block(control)
        if declared == begin or declared == -1:
            out.append((range(begin, begin + SIZE), control, str(entry.get("project") or control.name)))
    return out


def owner(port: int, exclude: Path | None = None) -> tuple[range, Path, str] | None:
    """The live block holding `port`, unless it is `exclude`'s."""
    for block, control, project in held():
        if port in block and not (exclude and _same(control, exclude)):
            return block, control, project
    return None


def next_free(exclude: Path | None = None) -> range:
    taken = {b.start for b, c, _ in held() if not (exclude and _same(c, exclude))}
    start = BASE
    while start in taken:
        start += SIZE
    return range(start, start + SIZE)


def check(control: Path, project: str, block: range | None, declared: list[tuple[str, int]],
          claim: bool = True) -> None:
    """Refuse (BlockError) a port outside `block` or inside another project's, or a block another
    project holds; then, with `claim`, record `block` as this control folder's. A no-op when the
    registry is off."""
    path = registry_path()
    if path is None:
        return
    if block is not None and (other := owner(block.start, exclude=control)):
        raise BlockError(f"`ports` {text(block)} is {other[2]}'s block ({other[1].as_posix()}); "
                         f"the next free block is {text(next_free(control))}")
    for where, port in declared:
        if block is not None and port in block:
            continue
        if other := owner(port, exclude=control):
            raise BlockError(f"{where} names port {port}, in {other[2]}'s block {text(other[0])} "
                             f"({other[1].as_posix()}); this project's block is "
                             f"{text(block) if block else 'not set'}")
        if block is None:
            raise BlockError(f'{where} names port {port}, but this project has no port block: add '
                             f'`ports = "{text(next_free(control))}"` (the next free block) and move its ports into it')
        raise BlockError(f"{where} names port {port}, outside this project's block {text(block)}")
    if claim and block is not None:
        data = _read(path)
        key = str(block.start)
        mine = {"control": control.as_posix(), "project": project}
        stale = [k for k, v in data.items() if k != key and _same(Path(v["control"]), control)]
        if data.get(key) != mine or stale:
            for k in stale:
                del data[k]
            data[key] = mine
            try:
                _write(path, data)
            except OSError:
                pass                    # another writer at the same moment: the next load records it
