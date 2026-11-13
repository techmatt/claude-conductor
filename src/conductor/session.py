"""`session.md`: the start digest a `session:` signal writes, so a planner session opens
with one signal and one read. It is the inbox since the previous read mark (conductor's own
reports past the planner's marker among its unreviewed reports), plus `state.md`'s lines a
start raises and each repo's maintenance facts."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import git, globs, inbox, nightshift, parked, resume
from .audits import canonical
from .daemon_api import api_only
from .config import Config
from .events import Event, EventLog
from .state import View
from .util import clock, parse_stamp, read_text, write_text

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
RETRO_FLOOR = 12    # non-template items finished since the last workflow retro before one is due regardless
RETRO_OVERRUN = 1.5     # a run this many times its first ETA (and `RETRO_OVERRUN_MINUTES` over) makes one due
RETRO_OVERRUN_MINUTES = 10
STATE_TRIM_KB = 25      # `state.md` past this: **trim state** is due (planner.md, *state.md is status*)
SIZES = "doc_sizes.json"    # in `.conductor/`: each digest's doc sizes, for the next one's growth


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


def maintenance(cfg: Config, log: EventLog, view: View, repo: str,
                docs: tuple[dict, dict | None] | None = None) -> list[str]:
    """One repo's maintenance facts, with no judgement: when each template last ran and
    how many other items finished since, `CLAUDE.md`'s size, each tracked doc over
    `doc_budget_kb` (from `docs`, `doc_growth`'s pair), unused audit notes, and `slow:`
    causes repeated in recent reports."""
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
    if docs is not None:
        out += [ln for key in sorted(k for k in docs[0] if k.startswith(f"{repo}:"))
                if (ln := size_line(key, *docs, cfg.doc_budget_kb, "doc trim"))]
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
    warned = inbox.warning_repeats(view, repo, SLOW_WINDOW)
    out.append(f"- `warnings:` kinds repeated in the last {len(recent)} report(s): "
               + ("; ".join(f"{k} ({', '.join(ids)})" for k, ids in warned.items()) or "none"))
    out.append(workflow_retro(log, view, repo, cfg.auto_retros))
    waiting, rules = parked.counts(cfg.control, repo)
    out.append(f"- parked edits waiting: {waiting}" + (f" ({rules} `[rules]`)" if rules else ""))
    return out


def is_doc(rel: str) -> bool:
    """A repo's own docs: `.md` at its root or under `docs/`, or a `README.md` or `CLAUDE.md`
    anywhere. Reports under a results folder are output (aicomplete has ~40 past 15 KB)."""
    parts = rel.split("/")
    return parts[-1].lower().endswith(".md") and (
        len(parts) == 1 or parts[0] == "docs" or parts[-1] in ("README.md", "CLAUDE.md"))


GENERATED_RE = re.compile(rb"\bgenerated\b[^\n]{0,80}\b(by|from|with)\b|\bdo not edit\b", re.IGNORECASE)
GENERATED_HEAD = 1024       # a generated doc says so near its top


def is_generated(path: Path) -> bool:
    """A doc that says near its top it is generated ("Generated by `cards_doc.py` ... do
    not edit"): its size is its source's, so no trim is due (aicomplete 133, 84)."""
    try:
        with open(path, "rb") as fh:
            return bool(GENERATED_RE.search(fh.read(GENERATED_HEAD)))
    except OSError:
        return False


def doc_sizes(cfg: Config) -> dict[str, int]:
    """Bytes of the control `state.md` (key `state.md`) and of each repo's tracked docs
    (`is_doc`, not `is_generated`, not matching its `doc_budget_exempt` globs: aicomplete's
    adversary cards say "written by" instead, 86) in its main checkout (key `<repo>:<path>`)."""
    out = {}
    if (state_md := cfg.control / "state.md").is_file():
        out["state.md"] = state_md.stat().st_size
    for repo, root in cfg.repos.items():
        try:
            paths = git.ls_files(root)
        except (OSError, git.GitError):
            continue
        exempt = cfg.opts(repo).doc_budget_exempt
        for rel in paths:
            if any(globs.matches(rel, g) for g in exempt):
                continue
            if is_doc(rel) and (p := root / rel).is_file() and not is_generated(p):
                out[f"{repo}:{rel}"] = p.stat().st_size
    return out


def doc_growth(cfg: Config) -> tuple[dict[str, int], dict[str, int] | None]:
    """This digest's doc sizes and the last digest's (None before the first), saving these
    for the next."""
    path = cfg.state_dir / SIZES
    try:
        prev = json.loads(read_text(path))
    except (OSError, ValueError):
        prev = None
    sizes = doc_sizes(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text(path, json.dumps(sizes, indent=0, sort_keys=True) + "\n")
    return sizes, prev


def size_line(key: str, sizes: dict[str, int], prev: dict[str, int] | None, budget_kb: float,
              trim: str) -> str | None:
    """`- **due** <trim>: `<file>` 61.0 KB, budget 25 KB, +2.0 KB since the last digest`,
    or None when the file is absent or within its budget."""
    if (size := sizes.get(key)) is None or size <= budget_kb * 1024:
        return None
    name = key.split(":", 1)[-1]
    if prev is None:
        growth = "no earlier digest to compare"
    elif key not in prev:
        growth = "new since the last digest"
    else:
        growth = f"{(size - prev[key]) / 1024:+.1f} KB since the last digest"
    return f"- **due** {trim}: `{name}` {size / 1024:.1f} KB, budget {budget_kb:g} KB, {growth}"


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


def overrun(log: EventLog, fin: Event) -> float | None:
    """How many times its first ETA an item ran (from its start), once that is
    RETRO_OVERRUN or more and over by RETRO_OVERRUN_MINUTES; else None."""
    start = log.last("start", item=fin.item)
    first = next((e for e in log.query(kinds=["progress"], item=fin.item, since_id=start.id if start else None)
                  if e.detail.get("eta") or e.detail.get("outline")), None)
    if first is None or start is None:
        return None
    rows = first.detail.get("outline") or []
    text = first.detail.get("eta") or (rows[-1].get("eta") if isinstance(rows[-1], dict) else None)
    due = nightshift.parse_eta(text, parse_stamp(first.ts))
    if due is None:
        return None
    began = parse_stamp(start.ts)
    planned, ran = (due - began).total_seconds(), (parse_stamp(fin.ts) - began).total_seconds()
    if planned <= 0 or ran < planned * RETRO_OVERRUN or ran - planned < RETRO_OVERRUN_MINUTES * 60:
        return None
    return ran / planned


def retro_trigger(log: EventLog, fin: Event) -> str | None:
    """Why this item alone makes a workflow retro due, or None. A red that is only the API's
    or a failed launch is none: there is no workflow to look back on (aicomplete 216)."""
    if fin.detail.get("status") == "red" and api_only(fin.detail.get("reasons")):
        return None
    if fin.detail.get("status") in ("partial", "red"):
        return str(fin.detail["status"])
    return f"ran {times:.1f}× its first ETA" if (times := overrun(log, fin)) else None


@dataclass
class Retro:
    """A repo's workflow retros (docs/retros.md): the latest self-audit that ran, the one
    queued or running, the non-template items finished since the newer of them was queued,
    and the item a retro is due on now with why (None when none is)."""
    last: Event | None
    pending: tuple[str, str] | None         # (item, "queued" | "running")
    since: list[Event]
    due: Event | None
    why: list[str]


def _resumable(fin: Event) -> bool:
    """A finish a self-audit can fork: it launched and its session's transcript is still in
    Claude Code's projects folder (`resume.prepare` fails the launch red otherwise: an item
    older than Claude Code's pruning, or a sandbox's fake session)."""
    sid = fin.detail.get("session_id")
    return bool(sid) and not any(str(r).startswith("launch failed") for r in fin.detail.get("reasons") or []) \
        and resume.session_file(str(sid)) is not None


def retros(log: EventLog, view: View, repo: str) -> Retro:
    """Where the repo's workflow retros stand. Items count from the newest self-audit's
    queueing (its `retro` or `enqueue` event; its finish when it has neither), so what
    finishes while one runs counts toward the next. One is due when any counted item went
    `partial` or `red` or overran (`retro_trigger`), or at RETRO_FLOOR of them, on the item
    with the most friction among those that triggered it (all of them, at the floor). Never
    on an item a self-audit already covers, or one with no session to fork."""
    audits: list[tuple[int, str, str, Event | None]] = []     # (marker, item, state, finish)
    for ev in view.finishes.values():
        if ev.repo == repo and canonical(ev.detail.get("template")) == "self_audit":
            audits.append((0, ev.item, "ran", ev))
    audits += [(0, i, "running", None) for i, rec in view.running.items()
               if rec.get("repo") == repo and rec.get("template") == "self_audit"]
    audits += [(0, it.id, "queued", None) for it in view.queued.get(repo, []) if it.template == "self_audit"]
    covered = {ev.detail.get("resume_of") for ev in view.finishes.values() if ev.detail.get("template") == "self_audit"}
    covered |= {rec.get("resume_of") for rec in view.running.values() if rec.get("template") == "self_audit"}
    covered |= {it.resume_of for q in view.queued.values() for it in q if it.template == "self_audit"}
    marked = []
    for _, item, how, fin in audits:
        first = next((e for e in log.query(kinds=["retro", "enqueue"], item=item) if e.item == item), None)
        marked.append((first.id if first else fin.id if fin else 1 << 62, item, how, fin))
    ran = [m for m in marked if m[2] == "ran"]
    last = max(ran, key=lambda m: m[3].id)[3] if ran else None
    pend = max((m for m in marked if m[2] != "ran"), default=None, key=lambda m: m[0])
    floor = max((m[0] for m in marked), default=0)
    since = sorted((ev for ev in view.finishes.values() if ev.repo == repo and ev.id > floor
                    and not ev.detail.get("template") and not ev.detail.get("resume_of")), key=lambda e: e.id)
    triggers = {ev.id: why for ev in since if (why := retro_trigger(log, ev))}
    due, parts = None, []
    if triggers or len(since) >= RETRO_FLOOR:
        pool = [ev for ev in since if (ev.id in triggers or not triggers) and ev.item not in covered and _resumable(ev)
                and not (ev.detail.get("status") == "red" and api_only(ev.detail.get("reasons")))]
        if pool:
            (_, why), due = max(((friction_score(log, ev), ev) for ev in pool), key=lambda s: (s[0][0], s[1].id))
            trigger = triggers.get(due.id)
            why = [w for w in why if w != trigger]
            parts = ([trigger] if trigger else []) + why or ["no friction recorded; the latest"]
    return Retro(last, (pend[1], pend[2]) if pend else None, since, due, parts)


def workflow_retro(log: EventLog, view: View, repo: str, auto: bool = False) -> str:
    """The digest's line: the last retro, the one queued or running, the items counted
    since, and the one due. With `auto` (`[defaults] auto_retros`) conductor queues a due
    one itself, so it reads as queued or ran; **due** (the planner offers it) only without."""
    r = retros(log, view, repo)
    text = (f"{r.last.item.split('/', 1)[1]}, {clock(r.last.ts)}" if r.last else "never")
    if r.pending:
        text += f" · {r.pending[1]}: {r.pending[0].split('/', 1)[1]}"
    text += f" · {len(r.since)} non-template item(s) finished since"
    if r.due:
        what = f"post-audit {r.due.item.split('/', 1)[1]} ({', '.join(r.why)})"
        if not auto:
            text += f" · **due**: {what}"
        elif r.pending:
            text += f" · next: {what}, queued once {r.pending[0].split('/', 1)[1]} ends"
        elif repo in view.paused:
            text += f" · next: {what}, queued when `{repo}` resumes"
        else:
            text += f" · next: {what}, conductor queues it"
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


DATED = re.compile(r"\b20\d\d-\d\d-\d\d\b|\b\d{1,2}/\d{1,2}\b|\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w* \d{1,2}\b"
                   r"|\(\d{1,2}(:\d\d)?\s?[ap]m\)|#\d{3,}")
FOLD_SHOW = 3       # lines a fold hint names


def fold_candidates(text: str) -> list[str]:
    """`state.md`'s top-level bullets that carry two or more indented sub-bullets, biggest
    sub-bullet text first, at most `FOLD_SHOW`: `*Section*: "line start…" (N sub-bullets,
    M dated, 2.1 KB)`. What a planner day appends (aicomplete, 2026-11-11: 30 → 41 KB of
    dated sub-bullets) and what folding each into its line, as it now stands, removes."""
    found = []
    for name, body in state_sections(text).items():
        parent, subs = None, []
        for ln in [*body, "- "]:            # a sentinel bullet closes the last one
            if ln.startswith(("- ", "* ")):
                if parent and len(subs) >= 2:
                    size = sum(len(s.encode("utf-8")) + 1 for s in subs)
                    head = parent[2:].strip()
                    head = head if len(head) <= 50 else head[:50].rstrip() + "…"
                    dated = sum(1 for s in subs if DATED.search(s))
                    found.append((size, f'*{name.capitalize()}*: "{head}" ({len(subs)} sub-bullets, '
                                        f'{dated} dated, {size / 1024:.1f} KB)'))
                parent, subs = ln, []
            elif parent and ln[:1].isspace() and ln.lstrip().startswith(("- ", "* ")):
                subs.append(ln)
            elif parent and ln[:1].isspace() and ln.strip() and subs:
                subs[-1] += "\n" + ln        # a sub-bullet's continuation counts with it
    return [line for _, line in sorted(found, key=lambda f: -f[0])[:FOLD_SHOW]]


def state_size(control: Path, targets: list[str]) -> dict:
    """For a request that wrote `state.md` and left it past `STATE_TRIM_KB`: its size and the
    lines to fold (`fold_candidates`), for the request event and its receipt; else {}."""
    if "state.md" not in targets:
        return {}
    try:
        text = read_text(control / "state.md")
    except (OSError, UnicodeDecodeError):
        return {}
    size = len(text.encode("utf-8"))
    if size <= STATE_TRIM_KB * 1024:
        return {}
    return {"state_kb": round(size / 1024, 1), "fold": fold_candidates(text)}


def state_warning(detail: dict) -> str | None:
    """A request receipt's warning for `state_size`'s fields: fold before it grows."""
    if not detail.get("state_kb"):
        return None
    fold = detail.get("fold") or []
    return (f"state.md is {detail['state_kb']:g} KB, past its {STATE_TRIM_KB} KB budget: "
            + (f"fold these lines' sub-bullets into the line as it now stands (fold state): {'; '.join(fold)}"
               if fold else "trim state is due"))


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


def next_number(log: EventLog) -> int:
    """The number the next planner session takes: one more than the `session:` checkpoints
    logged so far (`session` in their detail), so it continues from history (112)."""
    return 1 + sum(1 for ev in log.query(kinds=["checkpoint"]) if ev.detail.get("session"))


def title(cfg: Config, cp: Event) -> str:
    """`AIcomplete session 12`: the planner's first line and claude.ai's chat title."""
    return f"{cfg.name} session {cp.detail.get('number', '?')}"


def render(cfg: Config, log: EventLog, view: View, cp: Event, rel: str, top: int,
           root: Path | None = None) -> str:
    """The digest: the inbox since the previous read mark, headed by the session's
    checkpoint, then `state.md`'s due lines and the maintenance facts. The project's
    `[links]` come first, above everything else. Saves this digest's doc sizes for the next."""
    docs = doc_growth(cfg)
    links = "".join(f"- {name}: {url}\n" for name, url in cfg.links.items())
    # The planner's first line after Start's calls copies the heading, so claude.ai titles the chat by it (112).
    head = (f"# {title(cfg, cp)}\n\n"
            + (f"## Links\n\n{links}\n" if links else "")
            + f"Written for `{rel}` at {clock(cp.ts)}: {inbox.checkpoint_name(cp)}. "
            f"The read mark moves to #{top} as this is written; no `read:` is needed.\n\n"
            # Start step 6's access request copies these: guessed, one missing path fails it (84).
            f"Repos: {', '.join(f'`{p}`' for p in cfg.repos.values()) or 'none'}"
            # Until a repo has a remote, every digest says so (91); nothing about push or sync.
            + "".join(f"\n\n**{git.NO_REMOTE.format(repo=r)}**" for r in git.remote_missing(cfg.repos, ttl=0)))
    body = inbox.render(cfg, log, view, heading=head, root=root)     # conductor's reports sit in its *Unreviewed reports*
    habits = planner_habits(cfg.control)
    out = [body.rstrip(), "", *from_state(cfg.control), *([""] + habits if habits else []), "", "## Maintenance facts"]
    if state_line := size_line("state.md", *docs, STATE_TRIM_KB, "trim state"):
        out.append(state_line)
        try:
            out += [f"  - fold: {f}" for f in fold_candidates(read_text(cfg.control / "state.md"))]
        except (OSError, UnicodeDecodeError):
            pass
    for repo in cfg.repos:
        out += maintenance(cfg, log, view, repo, docs)
    return "\n".join(out) + "\n"
