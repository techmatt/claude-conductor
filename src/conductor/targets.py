"""Run targets: a project's long-running servers (`[run.<name>]` in conductor.toml),
started on request and opened in the default browser.

    .conductor/run-<name>.json    the process conductor started: pid, create_time, started
    .conductor/run-<name>.log     its stdout and stderr, appended

One process per target. A run request opens the URL at once when the target already
answers; otherwise it starts the process (unless the recorded one is still alive and
just not answering yet), and `poll` opens the URL once the ready URL answers — or logs
`run_failed` with the log's tail when the process dies or `ready_seconds` pass.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from . import procs
from .config import Config, RunTarget
from .util import stamp, write_text

LOG_TAIL_LINES = 20
PROBE_SECONDS = 1.0


def state_path(cfg: Config, name: str) -> Path:
    return cfg.state_dir / f"run-{name}.json"


def log_path(cfg: Config, name: str) -> Path:
    return cfg.state_dir / f"run-{name}.log"


def answers(url: str, timeout: float = PROBE_SECONDS) -> bool:
    """Does anything answer HTTP at `url`? Any response counts, an error status too."""
    try:
        with urllib.request.urlopen(url, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def process(cfg: Config, name: str) -> dict | None:
    """The recorded process of `name`, while it is alive."""
    try:
        rec = json.loads(state_path(cfg, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(rec, dict) and procs.alive(rec.get("pid"), rec.get("create_time")):
        return rec
    return None


def status(cfg: Config, t: RunTarget, timeout: float = PROBE_SECONDS) -> str:
    """`up` (answers), `starting` (our process is alive, not answering yet) or `down`."""
    if answers(t.ready, timeout):
        return "up"
    return "starting" if process(cfg, t.name) else "down"


def log_tail(cfg: Config, name: str, lines: int = LOG_TAIL_LINES) -> str:
    try:
        text = log_path(cfg, name).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.rstrip().splitlines()[-lines:])


@dataclass
class Pending:
    deadline: float
    started: float
    by: str | None


class Runner:
    """Carries out run requests. The daemon calls `request` from a signal and `poll`
    every tick, so it never blocks waiting; the CLI calls `poll` until nothing is pending."""

    def __init__(self, cfg: Config, log, opener=None):
        """`opener(url)` opens a URL; default: the default browser (`webbrowser.open`)."""
        self.cfg, self.log, self.opener = cfg, log, opener
        self.pending: dict[str, Pending] = {}

    def target(self, name: str) -> RunTarget:
        if name not in self.cfg.targets:
            known = ", ".join(sorted(self.cfg.targets)) or "none"
            raise ValueError(f"run: no target {name!r} in conductor.toml (targets: {known})")
        return self.cfg.targets[name]

    def _by(self, by: str | None) -> dict:
        return {"by": by} if by else {}

    def request(self, name: str, by: str | None = None) -> None:
        t = self.target(name)
        if answers(t.ready):
            self.pending.pop(name, None)
            self._open(t, by)
            return
        if name in self.pending:
            return                          # already starting: the open follows when ready
        now = time.monotonic()
        if rec := process(self.cfg, name):
            note = f"pid {rec['pid']} is alive but not answering yet; waiting for it"
            self.log.append("run_start", paths=[self.cfg.rel(log_path(self.cfg, name))], target=name,
                            pid=rec["pid"], reused=True, note=note, **self._by(by))
        else:
            command = t.command if isinstance(t.command, str) else [procs.resolve(t.command[0]), *t.command[1:]]
            log = log_path(self.cfg, name)
            try:
                p = procs.spawn_detached(command, t.cwd, log)
            except OSError as exc:
                self.log.append("run_failed", target=name, error=f"could not start: {exc}", **self._by(by))
                return
            write_text(state_path(self.cfg, name), json.dumps({
                "pid": p.pid, "create_time": procs.create_time(p.pid), "started": stamp(),
                "command": t.command, "cwd": str(t.cwd)}))
            self.log.append("run_start", paths=[self.cfg.rel(log)], target=name, pid=p.pid,
                            command=t.command, cwd=str(t.cwd), **self._by(by))
        self.pending[name] = Pending(now + t.ready_seconds, now, by)

    def stop(self, name: str, by: str | None = None) -> None:
        self.target(name)
        self.pending.pop(name, None)
        rec = process(self.cfg, name)
        if rec:
            procs.kill_tree(rec["pid"])
        state_path(self.cfg, name).unlink(missing_ok=True)
        self.log.append("run_stop", target=name, pid=rec["pid"] if rec else None,
                        note="" if rec else "it was not running (nothing conductor started is alive)",
                        **self._by(by))

    def poll(self) -> None:
        for name, p in list(self.pending.items()):
            t = self.cfg.targets.get(name)
            if t is None:                   # dropped from conductor.toml meanwhile
                self.pending.pop(name)
                continue
            if answers(t.ready):
                self.pending.pop(name)
                waited = round(time.monotonic() - p.started, 1)
                self.log.append("run_ready", target=name, waited_seconds=waited, **self._by(p.by))
                self._open(t, p.by)
            elif process(self.cfg, name) is None:
                self.pending.pop(name)
                self._failed(t, p.by, f"the process exited before {t.ready} answered")
            elif time.monotonic() > p.deadline:
                self.pending.pop(name)
                self._failed(t, p.by, f"{t.ready} did not answer within ready_seconds ({t.ready_seconds:g}); "
                                      "the process is left running")

    def _failed(self, t: RunTarget, by: str | None, error: str) -> None:
        self.log.append("run_failed", paths=[self.cfg.rel(log_path(self.cfg, t.name))], target=t.name,
                        error=error, log_tail=log_tail(self.cfg, t.name), **self._by(by))

    def _open(self, t: RunTarget, by: str | None) -> None:
        try:
            ok = (self.opener or webbrowser.open)(t.url)
        except Exception as exc:            # noqa: BLE001 - a browser problem is an event, not a crash
            self.log.append("run_failed", target=t.name, error=f"opening {t.url} failed: {exc!r}", **self._by(by))
            return
        self.log.append("run_open", target=t.name, url=t.url, opened=ok is not False, **self._by(by))
