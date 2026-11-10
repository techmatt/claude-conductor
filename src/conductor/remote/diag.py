"""Remote diagnostics: what a planner reads when Matt says the remote looks wrong, with no
Claude Code on the worker's machine (`docs/remote-diag.md`).

- **Steps** (`.conductor/remote/steps.log`, JSON lines): every preparing step, from the
  broker (a gate handed out and its answer, a worker's first shard of a job) and from
  `farm-seeds` (the wasm build, the Node wasm reference, the native fallback). A step is a
  `start` line and an `end` line with the same `id`; a stall is a start with no end.
- **Page logs** (`.conductor/remote/workers/<name>-<id>.log`): each browser tab's own rolling
  log, uploaded with its hellos, on the broker's clock; the newest `PAGE_LINES` kept.
- **The digest** (`remote-diag.md` at the control folder's root): each worker's state, any
  step past its deadline or failed with its log lines, and the last events per worker, page
  and broker interleaved. The broker's loop rewrites it when its text changes.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

STEPS = "steps.log"
STEPS_CAP = 512_000              # steps.log rolls over to steps.log.1 past this
WORKERS = "workers"              # page logs, one file a worker
PAGE_LINES = 500                 # a worker's page log keeps this many lines
PAGE_KEEP_DAYS = 7
PER_UPLOAD = 200                 # lines taken from one hello
DIGEST = "remote-diag.md"        # at the control folder's root, like inbox.md
EVENTS_SHOWN = 30                # per worker in the digest
# Each step's deadline (seconds): past it the step is an error, killed and tried once more.
DEADLINES = {"build": 20 * 60.0, "reference": 120.0, "gate": 60.0}
NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
_lock = threading.Lock()


def _hm(t: float, secs: bool = True) -> str:
    """Local 12-hour clock time: `10:25:35pm`."""
    s = time.strftime("%I:%M:%S%p" if secs else "%I:%M%p", time.localtime(t)).lower()
    return s[1:] if s.startswith("0") else s


# ------------------------------------------------------------------ steps
def step(state: Path, name: str, phase: str, sid: str, at: float | None = None, **fields) -> None:
    """One step line: `phase` is start, end or note (a moment, no duration); an end carries
    `result` (ok, failed, timeout, fallback) and, for a child process, `exit` and `stderr`
    (its tail). Never raises."""
    rec = {"at": round(time.time() if at is None else at, 3), "step": name, "phase": phase, "id": sid,
           **{k: v for k, v in fields.items() if v not in (None, "")}}
    path = state / STEPS
    try:
        with _lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > STEPS_CAP:
                os.replace(path, path.with_name(STEPS + ".1"))
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def read_steps(state: Path, since: float = 0.0) -> list[dict]:
    out = []
    for p in (state / (STEPS + ".1"), state / STEPS):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                rec = json.loads(ln)
            except ValueError:
                continue
            if isinstance(rec, dict) and rec.get("at", 0) >= since:
                out.append(rec)
    return out


def pair(steps: list[dict]) -> list[dict]:
    """Starts joined to their ends, oldest first: `{start, end|None, ...the start's fields}`."""
    runs: dict[str, dict] = {}
    order = []
    for s in steps:
        if s.get("phase") == "start":
            runs[s["id"]] = {**s, "start": s["at"], "end": None}
            order.append(s["id"])
        elif s.get("phase") == "end" and s.get("id") in runs:
            runs[s["id"]] |= {"end": s["at"], **{k: v for k, v in s.items() if k not in ("at", "phase")}}
        elif s.get("phase") == "note":
            runs[s["id"]] = {**s, "start": s["at"], "end": s["at"]}
            order.append(s["id"])
    return [runs[i] for i in order]


def tail(text: str, n: int = 400) -> str:
    """A child process's stderr tail: its last lines, at most `n` characters."""
    lines = [x.rstrip() for x in (text or "").strip().splitlines() if x.strip()]
    return "\n".join(lines[-6:])[-n:]


# ------------------------------------------------------------------ page logs
def _slug(name: str) -> str:
    return NAME_RE.sub("-", name).strip("-")[:40] or "worker"


def page_file(state: Path, wid: str, name: str) -> Path:
    return state / WORKERS / f"{_slug(name)}-{wid}.log"


def page_lines(state: Path, wid: str, name: str, lines, page_clock, now: float | None = None) -> int:
    """A tab's new log lines (`[{t: page ms, s: source, m: text}]`), put on the broker's clock
    (`page_clock`: the page's ms when it sent them) and appended to its file, which keeps the
    newest PAGE_LINES. A renamed worker's file is renamed. Returns the lines taken."""
    if not isinstance(lines, list) or not lines:
        return 0
    now = time.time() if now is None else now
    try:
        skew = now - float(page_clock) / 1000
    except (TypeError, ValueError):
        skew = 0.0
    new = []
    for x in lines[:PER_UPLOAD]:
        if not isinstance(x, dict):
            continue
        try:
            t = float(x.get("t", 0)) / 1000 + skew
        except (TypeError, ValueError):
            t = now
        src = str(x.get("s", "page"))[:12].replace(" ", "_") or "page"
        text = " ".join(str(x.get("m", "")).split())[:400]
        new.append(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(t))}.{int(t * 1000) % 1000:03d} {src} {text}")
    path = page_file(state, wid, name)
    with _lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            for old in path.parent.glob(f"*-{wid}.log"):     # renamed: its file follows the name
                if old == path:
                    continue
                if path.exists():
                    old.unlink()
                else:
                    os.replace(old, path)
            have = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            keep = (have + new)[-PAGE_LINES:]
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(keep) + "\n", encoding="utf-8", newline="\n")
            os.replace(tmp, path)
        except OSError:
            return 0
    return len(new)


def read_page(state: Path, wid: str) -> list[tuple[float, str, str]]:
    """A worker's page log as (time, source, text), oldest first."""
    out = []
    for path in (state / WORKERS).glob(f"*-{wid}.log"):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ln in text.splitlines():
            try:
                stamp, src, msg = ln[:23], *ln[24:].split(" ", 1)
                t = time.mktime(time.strptime(stamp[:19], "%Y-%m-%d %H:%M:%S")) + int(stamp[20:23]) / 1000
            except (ValueError, TypeError):
                continue
            out.append((t, src, msg))
    return out


def sweep(state: Path, now: float | None = None) -> None:
    """Page logs untouched for PAGE_KEEP_DAYS go."""
    cutoff = (time.time() if now is None else now) - PAGE_KEEP_DAYS * 86400
    for p in (state / WORKERS).glob("*.log") if (state / WORKERS).is_dir() else []:
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


# ------------------------------------------------------------------ the digest
def _step_text(r: dict, now: float) -> str:
    what = {"build": "wasm build", "reference": "Node wasm reference", "gate": "fingerprint gate",
            "handout": "handout", "fallback": "native fallback", "cores": "core count"}.get(r["step"], r["step"])
    key = f" {r['key'][:7]}" if r.get("key") else ""
    who = f" for {r.get('label') or r['worker']}" if r.get("worker") else ""
    head = f"{_hm(r['start'])} {what}{key}{who}"
    if r.get("try", 1) > 1:
        head += f" (try {r['try']})"
    if r["end"] is None:          # no running count: the digest changes only when something happens
        limit = DEADLINES.get(r["step"])
        return head + ": started, no end" + (f", past its {limit:.0f} s deadline" if limit and now - r["start"] > limit else "")
    secs = r["end"] - r["start"]
    res = r.get("result", "ok")
    tail_ = (f"; exit {r['exit']}" if "exit" in r else "") + (f": {r['detail']}" if r.get("detail") else "")
    return head + (f": {res}" if r["step"] in ("handout", "fallback", "cores") else f": {res} after {secs:.1f} s") + tail_


def trouble(runs: list[dict], now: float) -> list[dict]:
    """Steps past their deadline with no end, or ended failed / timed out / fallen back."""
    return [r for r in runs if (r["end"] is None and now - r["start"] > DEADLINES.get(r["step"], 1e9))
            or r.get("result") in ("failed", "timeout", "fallback")]


def render(project: str, panel: dict, state: Path, now: float | None = None) -> str:
    """The digest's text from the broker's panel (`Board.panel`), the steps log and the page
    logs. Times are clock times, so the text changes only when something happens."""
    now = time.time() if now is None else now
    runs = pair(read_steps(state, now - 6 * 3600))
    rows = panel.get("workers") or []
    names = {w["id"]: w.get("label") or w["id"] for w in rows}
    for r in runs:
        if r.get("worker"):
            r["label"] = names.get(r["worker"], r["worker"])
    out = [f"# Remote diagnostics: {project}", "",
           "Written by the broker as things change (`docs/remote-diag.md`). Full logs: "
           "`.conductor/remote/steps.log`, `.conductor/remote/workers/`.", "", "## Workers", ""]
    for w in rows:
        if w["kind"] != "main" and not w.get("fresh") and not any(r.get("worker") == w["id"] for r in runs):
            continue
        detail = f" · {w['detail']}" if w.get("detail") else ""
        since = f" (since {_hm(w['since'])})" if w.get("since") else ""
        kind = "" if w["kind"] == "main" else f" ({w['id']}, {w['kind']}, {w['cores']} cores)"
        out.append(f"- {w['label']}{kind}: {w['state']}{detail}{since}")
        if w.get("chosen"):           # a browser's count, chosen by the broker (`cores.py`)
            cap = f", capped at {w['cap']} on its page" if w.get("cap") and w["cap"] < w["chosen"] else ""
            out.append(f"    cores: {w['chosen']} of {w['hw']} chosen by the broker{cap}; last change "
                       f"{_hm(w['why_at'])}: {w['why']}")
    bad = trouble(runs, now)
    out += ["", "## Past deadline or failed", ""]
    if not bad:
        out.append("None in the last 6 hours.")
    for r in bad[-12:]:
        out.append(f"- {_step_text(r, now)}")
        if r.get("stderr"):
            out += [f"    {x}" for x in r["stderr"].splitlines()[-4:]]
        if r.get("worker"):
            page = [p for p in read_page(state, r["worker"]) if p[0] <= (r["end"] or now) + 5][-5:]
            out += [f"    {_hm(t)} {src} {msg}" for t, src, msg in page]
    prep = [r for r in runs if r["step"] in ("build", "reference", "fallback")][-8:]
    out += ["", "## Bundle steps (main box)", ""]
    out += [f"- {_step_text(r, now)}" for r in prep] or ["None in the last 6 hours."]
    for w in rows:
        if w["kind"] == "main":
            continue
        events = [(r["start"], "broker", _step_text(r, now)) for r in runs if r.get("worker") == w["id"]]
        events += read_page(state, w["id"])
        if not events:
            continue
        events.sort(key=lambda e: e[0])
        out += ["", f"## {w['label']} ({w['id']}): last {min(EVENTS_SHOWN, len(events))} events", ""]
        out += [f"    {_hm(t)} {src:<7} {msg}" for t, src, msg in events[-EVENTS_SHOWN:]]
    return "\n".join(out) + "\n"


def write_digest(control: Path, text: str) -> bool:
    """`remote-diag.md`, rewritten only when its text changed. Never raises."""
    path = control / DIGEST
    try:
        if path.exists() and path.read_text(encoding="utf-8") == text:
            return False
        tmp = path.with_name(DIGEST + ".tmp")
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
        return True
    except OSError:
        return False
