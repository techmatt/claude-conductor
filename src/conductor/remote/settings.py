"""The `[remote]` section of `conductor.toml`, read here only: `config.py` never sees it
(an unknown top-level table is ignored there), so deleting this module leaves config alone."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shlex
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_NAME = "conductor.toml"
STATE = (".conductor", "remote")        # everything this module keeps, under the control folder
COMMAND = "farm-seeds"                  # the one command a `remote: true` item gets on its PATH
CONTROL_VAR = "CONDUCTOR_REMOTE_CONTROL"
ITEM_VAR = "CONDUCTOR_REMOTE_ITEM"      # the item's id, for the worker page
ACCENT = "#7c9cff"                      # the worker page's accent colour unless `accent` sets one
ACCENT_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
NOW_LINE = (".conductor", "now", "remote.line")   # the inbox's *Now* line while a worker is connected

KEYS = {"dir","native", "native_build", "build", "bundle_js", "fingerprint", "merge", "port", "bind",
        "token", "shard_seeds", "seed_seconds", "local_below_seconds", "lease_seconds", "fresh_seconds",
        "main_slots", "accent"}


class RemoteError(ValueError):
    pass


@dataclass
class Remote:
    control: Path
    native: list[str]                      # argv; `{first}` `{count}` `{out}` filled, a `{args}` element expanded
    fingerprint: list[str]                 # argv whose stdout (stripped) a worker must reproduce
    build: list[str] = field(default_factory=list)          # the wasm bundle build; `{bundle}` is its out dir
    native_build: list[str] = field(default_factory=list)   # run once before native shards (e.g. cargo build)
    bundle_js: str = "bundle.js"           # the bundle's ES module, exporting init (default), run, fingerprint
    dir: str = ""                        # the commands' folder inside the repo
    merge: str = "concat"                  # shard outputs, concatenated in seed order
    port: int = 8770
    bind: str = "0.0.0.0"
    token: str | None = None               # None: generated once, kept in .conductor/remote/token
    shard_seeds: int = 25
    seed_seconds: float = 0.05             # estimated native seconds per seed, for the size check
    local_below_seconds: float = 10.0      # a job estimated under this stays local
    lease_seconds: float = 60.0            # a shard not back in this long is leased again
    fresh_seconds: float = 10.0            # a worker not heard from in this long is gone
    main_slots: int = 1                    # shards the main box runs at once
    accent: str = ACCENT                   # the worker page's accent colour (#rgb or #rrggbb)
    project: str = ""                      # conductor.toml's `project` (else the control folder's name)

    @property
    def state(self) -> Path:
        return self.control.joinpath(*STATE)

    def digest(self) -> str:
        """Settings identity: a broker started under other settings is replaced."""
        d = asdict(self)
        d["control"] = str(self.control)
        return hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]

    def secret(self) -> str:
        if self.token:
            return self.token
        path = self.state / "token"
        try:
            return path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            token = secrets.token_urlsafe(18)
            path.write_text(token + "\n", encoding="utf-8", newline="\n")
            return token


def _argv(where: str, value, required: bool) -> list[str]:
    if value is None and not required:
        return []
    if isinstance(value, str) and value.strip():
        try:
            return shlex.split(value)      # a list is safer for Windows paths
        except ValueError as exc:
            raise RemoteError(f"{CONFIG_NAME}: remote.{where}: {exc}") from None
    if isinstance(value, list) and value and all(isinstance(x, str) and x for x in value):
        return list(value)
    raise RemoteError(f"{CONFIG_NAME}: remote.{where} must be a command (a list of strings)")


def _toml(control: Path) -> dict:
    try:
        return tomllib.loads((Path(control) / CONFIG_NAME).read_text(encoding="utf-8-sig"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def raw_section(control: Path) -> dict | None:
    """The `[remote]` table, or None when absent (or the file unreadable: never in the daemon's way)."""
    table = _toml(control).get("remote")
    return table if isinstance(table, dict) else None


def load(control: Path) -> Remote | None:
    """The project's remote settings, or None when it has no `[remote]` section."""
    control = Path(control).resolve()
    data = _toml(control)
    table = data.get("remote")
    if not isinstance(table, dict):
        return None
    if unknown := set(table) - KEYS:
        raise RemoteError(f"{CONFIG_NAME}: remote: unknown keys {sorted(unknown)}")
    r = Remote(control=control, native=_argv("native", table.get("native"), True),
               fingerprint=_argv("fingerprint", table.get("fingerprint"), True),
               build=_argv("build", table.get("build"), False),
               native_build=_argv("native_build", table.get("native_build"), False))
    project = data.get("project")
    r.project = project if isinstance(project, str) and project else control.name.removesuffix("-control")
    for key in ("dir", "merge", "bind", "token", "bundle_js", "accent"):
        if key in table:
            if not isinstance(table[key], str):
                raise RemoteError(f"{CONFIG_NAME}: remote.{key} must be a string")
            setattr(r, key, table[key])
    if Path(r.dir).is_absolute() or ".." in Path(r.dir).parts:
        raise RemoteError(f"{CONFIG_NAME}: remote.dir must be a folder inside the repo")
    if not ACCENT_RE.match(r.accent):
        raise RemoteError(f"{CONFIG_NAME}: remote.accent must be a colour like \"#7c9cff\"")
    if r.merge != "concat":
        raise RemoteError(f"{CONFIG_NAME}: remote.merge: only \"concat\" (by seed) is supported")
    for key in ("port", "shard_seeds", "main_slots"):
        if key in table:
            if (not isinstance(table[key], int) or isinstance(table[key], bool)
                    or table[key] < (0 if key == "port" else 1)):
                raise RemoteError(f"{CONFIG_NAME}: remote.{key} must be a whole number")
            setattr(r, key, table[key])
    for key in ("seed_seconds", "local_below_seconds", "lease_seconds", "fresh_seconds"):
        if key in table:
            try:
                value = float(table[key])
                if value < 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise RemoteError(f"{CONFIG_NAME}: remote.{key} must be a number of seconds") from None
            setattr(r, key, value)
    if "{out}" not in r.native:
        raise RemoteError(f"{CONFIG_NAME}: remote.native must hold `{{out}}` (and `{{first}}`, `{{count}}`)")
    if r.build and "{bundle}" not in " ".join(r.build):
        raise RemoteError(f"{CONFIG_NAME}: remote.build must write to `{{bundle}}`")
    return r


def fill(argv: list[str], **values: str) -> list[str]:
    """`{name}` placeholders filled; an element that is exactly `{args}` expands to the args list."""
    args = values.pop("args", [])
    out: list[str] = []
    for part in argv:
        if part == "{args}":
            out.extend(args)
            continue
        for k, v in values.items():
            part = part.replace("{" + k + "}", v)
        out.append(part)
    return out
