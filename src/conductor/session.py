"""`session.md`: the start digest a `session:` signal writes, so a planner session opens
with one signal and one read. It is the inbox since the previous read mark (conductor's own
reports past the planner's marker among its unreviewed reports), plus `state.md`'s lines a
start raises and each repo's maintenance facts."""

from __future__ import annotations

import re
from pathlib import Path

from . import inbox, parked
from .audits import canonical
from .config import Config
from .events import Event, EventLog
from .state import View
from .util import clock, read_text

# Conductor's own checkout: `src/conductor/session.py` → the repo root. Its `planner-notes.md`
# (git-ignored, the planner's own file) holds the marker for conductor's hand-pasted prompts' reports.
CONDUCTOR_ROOT = Path(__file__).resolve().parents[2]
NOTES = "planner-notes.md"
MARKER = "Last report reviewed by the planner:"
LEDGER = Path("prompts") / "ledger.md"     # the run ledger: the order conductor's reports were written in

# What `state.md`'s *Maintenance* section tracks, in the planner's words.
MAINTENANCE = (("bug_hunt", "bug hunt"), ("general_cleanup", "general cleanup"), ("cleanup", "cleanup"),
               ("test_cleanup", "test cleanup"), ("claude_md_cleanup", "claude.md cleanup"),
               ("disk_cleanup", "disk space cleanup"))
SLOW_WINDOW = 5     # a `slow:` (or `friction:`) part named in two or more of a repo's last five reports
RETRO_EVERY = 6     # non-template items finished since the last workflow retro before one is due


def _report_key(name: str) -> tuple[int, str] | None:
    """Natural order of a prompt name: 12 < 12a < 13."""
    m = re.match(r"(\d+)([a-z]*)(?:_|$)", name)
    return (int(m.group(1)), m.group(2)) if m else None


def ledger_order(root: Path) -> list[str]:
    """The run names of `prompts/ledger.md`'s `<time> done <name> → …` lines, oldest first
    (a rerun counts at its latest); [] without a ledger."""
    try:
        text = read_text(root / LEDGER)
    except (OSError, UnicodeDecodeError):
        return []
    names: dict[str, None] = {}
    for m in re.finditer(r"^\S+ done (\S+)", text, re.M):
        names.pop(m[1], None)
        names[m[1]] = None
    return list(names)


def reports_past_marker(root: Path) -> tuple[str | None, list[tuple[str, Path]]] | None:
    """The planner's marker (in `planner-notes.md`) and every `reports/<name>_report.md` of
    conductor's own repo past it, as (name, path); None without the notes file. A marker
    the ledger names (a prompt or a cleanup pass) orders by the ledger's `done` lines;
    otherwise numbered prompts in natural order. The marker line may start with `- ` or
    `**`; without one the marker is None and nothing is past it, as for an unnumbered
    marker the ledger lacks, since listing every report ever written would bury the planner."""
    notes = root / NOTES
    if not notes.is_file():
        return None
    marker = next((ln.lstrip("-* ").split(":", 1)[1].strip("* ") for ln in read_text(notes).splitlines()
                   if ln.lstrip("-* ").startswith(MARKER)), None)
    if marker is None:
        return None, []
    reports = {p.name.removesuffix("_report.md"): p for p in (root / "reports").glob("*_report.md")}
    if marker in (order := ledger_order(root)):
        return marker, [(name, reports[name]) for name in order[order.index(marker) + 1:] if name in reports]
    if (floor := _report_key(marker)) is None:
        return marker, []
    found = sorted((key, name) for name in reports if (key := _report_key(name)) and key > floor)
    return marker, [(name, reports[name]) for _, name in found]


def reports_head(root: Path, marker: str | None, found: list) -> str:
    """`Past `<MARKER> 12_x` in `<root>/planner-notes.md`:` (or `: none.`)."""
    if marker is None:
        return f"No `{MARKER}` line found in `{root / NOTES}`; nothing listed."
    return f"Past `{MARKER} {marker or '(none)'}` in `{root / NOTES}`" + (":" if found else ": none.")


def missing_handoff(root: Path) -> str:
    return f"- conductor's {NOTES} not found under `{root}`; nothing to list."


def report_status(text: str) -> str:
    """A report's `status:` line, from its first five non-blank lines."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return next((ln for ln in lines[:5] if ln.lower().startswith("status:")), "no status line")


def conductor_reports(root: Path | None = None) -> list[str]:
    """Conductor's own reports past the planner's marker, one line each with its status, for
    the inbox's *Unreviewed reports*: [] when none, or with no notes file or marker line
    (`review: all` says which)."""
    past = reports_past_marker(root or CONDUCTOR_ROOT)
    out = []
    for name, p in past[1] if past else []:
        try:
            status = report_status(read_text(p))
        except (OSError, UnicodeDecodeError):
            status = "no status line"
        status = status.split(":", 1)[1].strip() if status.lower().startswith("status:") else status
        out.append(f"- conductor's {name} **{status}** — `{p}`")
    return out


def slow_components(text: str | None) -> list[str]:
    """`tests ~25m, why, fix; build ~20m, …` → `["tests", "build"]`."""
    out = []
    for part in (text or "").split(";"):
        name = re.split(r"\s~|,", part.strip(), maxsplit=1)[0].strip().lower()
        if name:
            out.append(name)
    return out


def maintenance(cfg: Config, log: EventLog, view: View, repo: str) -> list[str]:
    """One repo's maintenance facts, with no judgement: when each template last ran and
    how many other items finished since, `CLAUDE.md`'s size, unused audit notes, and
    `slow:` causes repeated in recent reports."""
    fins = sorted((ev for ev in view.finishes.values() if ev.repo == repo), key=lambda e: e.id)
    fins = [ev for ev in fins if not ev.detail.get("resume_of")]     # questions about work, not work
    plain = [ev for ev in fins if not ev.detail.get("template")]
    live = {it.template: it.name for it in view.queued.get(repo, []) if it.template}
    live |= {canonical(rec.get("template")): rec["name"] for rec in view.running.values()
             if rec.get("repo") == repo and rec.get("template")}
    out = [f"### `{repo}`"]
    for tmpl, words in MAINTENANCE:
        # An item run under a template's old name (`general_audit`) counts as its new one.
        last = next((ev for ev in reversed(fins) if canonical(ev.detail.get("template")) == tmpl), None)
        since = sum(1 for ev in plain if last is None or ev.id > last.id)
        text = (f"{last.item.split('/', 1)[1]}, {clock(last.ts)} ({last.detail.get('status')})"
                if last else "never")
        text += f" · {since} non-template item(s) finished since" if last else f" · {since} non-template item(s) finished"
        if tmpl in live:
            text += f" · in flight: {live[tmpl]}"
        out.append(f"- {words}: {text}")
    claude_md = cfg.repos[repo] / "CLAUDE.md"
    try:
        data = claude_md.read_bytes()
        out.append(f"- `CLAUDE.md`: {len(data) / 1024:.1f} KB, {data.count(b'\n')} lines")
    except OSError:
        out.append("- `CLAUDE.md`: none")
    notes = view.unused_notes(cfg, repo)
    out.append(f"- unused audit notes: {len(notes)}" + (f" ({', '.join(n.item.split('/', 1)[1] for n in notes)})"
                                                         if notes else ""))
    recent = fins[-SLOW_WINDOW:]
    named: dict[str, list[str]] = {}
    for ev in recent:
        for comp in dict.fromkeys(slow_components(inbox.slow_text(ev.detail))):
            named.setdefault(comp, []).append(ev.item.split("/", 1)[1].split("_", 1)[0])
    repeats = [f"{c} ({', '.join(ids)})" for c, ids in named.items() if len(ids) >= 2]
    out.append(f"- `slow:` repeated in the last {len(recent)} report(s): {'; '.join(repeats) or 'none'}")
    named = {}
    for ev in recent:
        for comp in dict.fromkeys(slow_components(friction_text(ev.detail))):
            named.setdefault(comp, []).append(ev.item.split("/", 1)[1].split("_", 1)[0])
    repeats = [f"{c} ({', '.join(ids)})" for c, ids in named.items() if len(ids) >= 2]
    out.append(f"- `friction:` repeated in the last {len(recent)} report(s): {'; '.join(repeats) or 'none'}")
    out.append(workflow_retro(log, view, repo))
    waiting, rules = parked.counts(cfg.control, repo)
    out.append(f"- parked edits waiting: {waiting}" + (f" ({rules} `[rules]`)" if rules else ""))
    return out


def friction_text(detail: dict) -> str | None:
    """A finish's `friction:` worth counting, or None when absent or `none`."""
    text = (detail.get("friction") or "").strip()
    return text if text and text.lower().rstrip(".") != "none" else None


def friction_score(log: EventLog, fin: Event) -> tuple[int, list[str]]:
    """How much an item's run cost beyond its work, and why: its `friction:` parts, watch
    tells to it, questions that timed out to their default, watch `eta` flags (it overran
    its own estimate) and a red outcome."""
    item = fin.item
    parts = []
    if n := len(slow_components(friction_text(fin.detail))):
        parts.append(f"{n} friction")
    counts = {
        "watch tell": sum(1 for e in log.query(kinds=["tell"], item=item) if e.detail.get("by") == "watch"),
        "timed-out question": len(log.query(kinds=["default"], item=item)),
        "overrun": sum(1 for e in log.query(kinds=["watch_flag"], item=item) if e.detail.get("flag") == "eta"),
    }
    parts += [f"{v} {k}{'s' if v > 1 else ''}" for k, v in counts.items() if v]
    if fin.detail.get("status") == "red":
        parts.append("red")
    score = n + sum(counts.values()) + (2 if fin.detail.get("status") == "red" else 0)
    return score, parts


def workflow_retro(log: EventLog, view: View, repo: str) -> str:
    """When a workflow retro (`post-audit`, a self-audit) last ran, how many non-template
    items finished since, and once RETRO_EVERY have, that it is due, naming the item with
    the most friction among them."""
    fins = sorted((ev for ev in view.finishes.values() if ev.repo == repo), key=lambda e: e.id)
    last = next((ev for ev in reversed(fins) if canonical(ev.detail.get("template")) == "self_audit"), None)
    since = [ev for ev in fins if not ev.detail.get("template") and not ev.detail.get("resume_of")
             and (last is None or ev.id > last.id)]
    text = (f"{last.item.split('/', 1)[1]}, {clock(last.ts)}" if last else "never") + \
        f" · {len(since)} non-template item(s) finished since"
    if len(since) >= RETRO_EVERY:
        scored = [(friction_score(log, ev), ev) for ev in since]
        (score, why), worst = max(scored, key=lambda s: (s[0][0], s[1].id))
        text += (f" · **due**: post-audit {worst.item.split('/', 1)[1]} ("
                 + (", ".join(why) if score else "no friction recorded; the latest") + ")")
    return f"- workflow retro: {text}"


def state_sections(text: str) -> dict[str, list[str]]:
    """`state.md`'s `## ` sections by lowercased heading (any `(…)` after the name dropped),
    each body's lines as written."""
    out: dict[str, list[str]] = {}
    body: list[str] | None = None
    for ln in text.splitlines():
        if ln.startswith("## "):
            body = out.setdefault(ln[3:].split("(", 1)[0].strip().lower(), [])
        elif ln.startswith("# "):
            body = None
        elif body is not None:
            body.append(ln)
    return out


def due_items(lines: list[str]) -> list[str]:
    """The top-level bullet items (with their indented continuation lines) saying `due`."""
    items: list[list[str]] = []
    for ln in lines:
        if ln.startswith(("- ", "* ")):
            items.append([ln])
        elif items and ln.startswith((" ", "\t")) and ln.strip():
            items[-1].append(ln)
        else:
            items.append([])            # a blank or paragraph line ends the item
    return [ln for it in items if it and re.search(r"\bdue\b", " ".join(it), re.I) for ln in it]


def from_state(control: Path) -> list[str]:
    """`state.md`'s lines a session start raises, copied as written: *Waiting on Matt* and
    *Open questions* whole, and the *Pending fixes* and *Maintenance* items marked due."""
    try:
        sections = state_sections(read_text(control / "state.md"))
    except (OSError, UnicodeDecodeError):
        return ["## From state.md", "", "No `state.md` readable."]
    out = ["## From state.md"]
    for name, due in (("Waiting on Matt", False), ("Open questions", False),
                      ("Pending fixes", True), ("Maintenance", True)):
        out += ["", f"### {name}" + (", due" if due else "")]
        if (body := sections.get(name.lower())) is None:
            out.append(f"No *{name}* section.")
            continue
        lines = due_items(body) if due else body
        while lines and not lines[0].strip():
            lines = lines[1:]
        while lines and not lines[-1].strip():
            lines = lines[:-1]
        out += lines or ["none marked due" if due else "empty"]
    return out


HABITS_NOTE = "The planner's own rules, from `state.md`: follow them; never list them to Matt."


def planner_habits(control: Path) -> list[str]:
    """`state.md`'s *Planner habits* whole, under its own heading so the planner follows
    them without relaying them as fixes; nothing when the section is absent or empty."""
    try:
        body = state_sections(read_text(control / "state.md")).get("planner habits") or []
    except (OSError, UnicodeDecodeError):
        return []
    lines = "\n".join(body).strip("\n").splitlines()
    return ["## Planner habits", "", HABITS_NOTE, "", *lines] if any(ln.strip() for ln in lines) else []


def render(cfg: Config, log: EventLog, view: View, cp: Event, rel: str, top: int,
           root: Path | None = None) -> str:
    """The digest: the inbox since the previous read mark, headed by the session's
    checkpoint, then `state.md`'s due lines and the maintenance facts. The project's
    `[links]` come first, above everything else."""
    links = "".join(f"- {name}: {url}\n" for name, url in cfg.links.items())
    head = (f"# Session start — {cfg.project}\n\n"
            + (f"## Links\n\n{links}\n" if links else "")
            + f"Written for `{rel}` at {clock(cp.ts)}: {inbox.checkpoint_name(cp)}. "
            f"The read mark moves to #{top} as this is written; no `read:` is needed.")
    body = inbox.render(cfg, log, view, heading=head, root=root)     # conductor's reports sit in its *Unreviewed reports*
    habits = planner_habits(cfg.control)
    out = [body.rstrip(), "", *from_state(cfg.control), *([""] + habits if habits else []), "", "## Maintenance facts"]
    for repo in cfg.repos:
        out += maintenance(cfg, log, view, repo)
    return "\n".join(out) + "\n"
