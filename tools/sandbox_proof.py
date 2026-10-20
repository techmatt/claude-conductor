"""Prove nothing user-facing broke: drive the sandbox with the fake executor, then open the app on it.

    uv run python tools/sandbox_proof.py <sandbox control folder> [--shot <png>]

Runs an in-process daemon on the sandbox's control folder with `tests/fake_claude.py` as
the executor (set in memory; the sandbox's `conductor.toml` is not touched) and memory
thresholds off. It queues an item that commits to the sandbox repo and waits for its
merge, sends `session: start` and waits for `session.md`, applies a request (it rewrites
`proof.md` in the control folder), marks the item reviewed, and stops. Then it opens the
app's window offscreen on the same folder, renders every tab, and saves a screenshot
with `--shot`. Exit 1 on the first step that times out. Refuses to run while the
sandbox's own daemon is up.
"""

from __future__ import annotations

import os
import re
import sys
import tempfile
import time
from pathlib import Path

from conductor import config, state
from conductor.app import actions
from conductor.daemon import Daemon
from conductor.util import stamp

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests" / "fake_claude.py"


def next_number(control: Path) -> int:
    nums = [int(m[1]) for p in control.glob("*/**/*.md") if (m := re.match(r"(\d{3})_", p.name))]
    return max(nums, default=0) + 1


def daemon_steps(control: Path) -> None:
    cfg = config.load(control)
    if state.daemon_info(cfg).alive:
        sys.exit("sandbox_proof: the sandbox's daemon is running; stop it first (stop: daemon)")
    cfg.command = [sys.executable, str(FAKE)]
    cfg.memory_pressure_percent = cfg.memory_pressure_free_gb = 0
    cfg.memory_critical_percent = cfg.memory_critical_free_gb = 0
    repo = next(iter(cfg.repos))
    d = Daemon(cfg, echo=print, notifier=lambda *a, **k: None)

    def until(what: str, cond, timeout: float = 60):
        end = time.time() + timeout
        while time.time() < end:
            d.tick()
            if hit := cond():
                print(f"ok: {what}")
                return hit
            time.sleep(0.1)
        d.release()
        sys.exit(f"sandbox_proof: timed out waiting for {what}")

    d.acquire()
    try:
        d.startup()
        name = f"{next_number(control):03d}_cleanup_proof"
        item = f"{repo}/{name}"
        q = control / "queue" / repo / f"{name}.md"
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(f"---\nrepo: {repo}\n---\nCleanup proof: commit one line, then report.\n"
                     f"FAKE progress {{\"note\": \"proving\", \"eta\": \"1m\"}}\n"
                     f"FAKE commit cleanup-proof.txt {stamp()}\n", encoding="utf-8", newline="\n")
        until(f"{item} finished", lambda: d.log.last("finish", item=item))
        until(f"{item} merged", lambda: d.log.last("merge", item=item))
        sig = actions.session(control, "cleanup proof", by="cleanup")
        until("session.md written", lambda: sig.name in (control / "session.md").read_text(encoding="utf-8")
              if (control / "session.md").exists() else False)
        req = control / "requests" / stamp().replace(":", "")
        req.mkdir(parents=True)
        (req / "proof.md").write_text(f"Last cleanup proof: {stamp()}\n", encoding="utf-8", newline="\n")
        before = d.log.max_id()
        (req / "request.md").write_text("---\ntargets: [proof.md]\nmessage: \"cleanup proof\"\n---\n",
                                        encoding="utf-8", newline="\n")
        until("request committed", lambda: any(e.kind == "commit" for e in d.log.query(since_id=before + 1)))
        actions.reviewed(control, item)
        until(f"{item} reviewed", lambda: d.log.last("reviewed", item=item))
    finally:
        d.release()


def app_steps(control: Path, shot: Path | None) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if os.environ.get("WINDIR") and "QT_QPA_FONTDIR" not in os.environ:     # offscreen finds no fonts otherwise
        os.environ["QT_QPA_FONTDIR"] = str(Path(os.environ["WINDIR"]) / "Fonts")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from conductor.app.window import MainWindow
    app = QApplication.instance() or QApplication([sys.argv[0]])
    with tempfile.TemporaryDirectory() as tmp:
        win = MainWindow([control], settings=QSettings(str(Path(tmp) / "app.ini"), QSettings.Format.IniFormat),
                         tray=False, interval_ms=60_000)
        win.resize(1400, 900)
        win.show()
        win.refresh()
        view = win.views[control.resolve()]
        for name, panel in view.panels.items():
            view.tabs.setCurrentWidget(panel)
            app.processEvents()
            if panel.grab().isNull():
                sys.exit(f"sandbox_proof: the app's {name} tab did not render")
        view.tabs.setCurrentWidget(view.panels["repos"])
        app.processEvents()
        if shot:
            win.grab().save(str(shot))
        print(f"ok: app rendered {', '.join(view.panels)}; state {view.snap.state}")
        win._quitting = True
        win.close()


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        sys.exit(__doc__)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")    # as `conductor run` does: events print `→`
    control = Path(argv[0]).resolve()
    shot = Path(argv[argv.index("--shot") + 1]) if "--shot" in argv else None
    daemon_steps(control)
    app_steps(control, shot)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
