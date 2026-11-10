"""An item's conversation and trail, for the item window (`trail.py`). No Qt here.

Split from `model.py` (cleanup 2026-11-09): `Project.trail` builds a `Trail` here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .. import config as config_mod
from .. import git, picks, talk
from ..events import RED_KINDS, Event
from ..executor import RunPaths, stored_log
from ..inbox import outline_rows, slow_text
from ..resume import label as resume_label
from ..util import read_text
from .model import activity_text, ask_how, progress_text, sender
from .streamlog import TEXT_LIMIT, render_stream_json


# ---------------------------------------------------------------- an item's conversation
@dataclass
class Turn:
    """One entry of an item's Conversation: who said it, and what came back under it
    (a tell's reply, a question's answer, an amendment's delivery)."""
    id: int                     # the event it stands for
    ts: str
    who: str                    # matt | executor | conductor: where it sits
    title: str
    text: str = ""
    under: list[tuple[str, str, str]] = field(default_factory=list)    # (label, ts, text)
    note: str = ""              # what it still waits for, or why it never arrived
    tone: str = ""              # "" | wait | warn | red | done | partial
    report: bool = False        # a finish: links to the Report tab
    name: str = ""              # the header's sender, when not who's own (`sender`)


# Shown in Conversation; every other kind (delivered, merge, reaped, enqueue, edited,
# commit, worktree, ...) is plumbing and stays in the item's Events tab. Reds other than
# `red` itself (whose reasons the finish carries) show too.
CONVERSATION_KINDS = {"start", "staged", "tell", "amended", "amend_refused", "ask", "progress",
                      "pick_open", "pick_saved", "pick_failed", "finish"}
QUESTION_ENDS = {"answer": "Matt answered", "default": "default taken (nobody answered)",
                 "late": "late answer, ignored", "question_closed": "closed unanswered"}


def current_outline(events: list[Event]) -> Event | None:
    """The item's latest outline: its newest `progress` event carrying one."""
    return next((e for e in reversed(events) if e.kind == "progress" and outline_rows(e)), None)


def conversation(events: list[Event]) -> list[Turn]:
    """An item's events (oldest first) as a chat thread. Replies sit under their tell:
    a `progress` event tagged `reply_to` is not a turn of its own."""
    replies: dict[int, Event] = {}
    delivered: dict[int, Event] = {}
    undelivered: dict[int, Event] = {}
    ends: dict[str, Event] = {}
    for e in events:
        d = e.detail
        if e.kind == "progress":
            for mid in talk.reply_ids(d):
                replies.setdefault(mid, e)
        elif e.kind == "delivered":
            delivered[d.get("tell") or d.get("amend")] = e
        elif e.kind == "warning" and (d.get("tell") or d.get("amend")):
            undelivered[d.get("tell") or d.get("amend")] = e
        elif e.kind in QUESTION_ENDS and d.get("question"):
            ends.setdefault(d["question"], e)
    talked = {e.id for e in events if e.kind in ("tell", "amended")}
    out: list[Turn] = []
    for e in events:
        d = e.detail
        if e.kind not in CONVERSATION_KINDS and not (e.kind in RED_KINDS and e.kind != "red"):
            continue
        match e.kind:
            case "start":
                if d.get("error"):
                    out.append(Turn(e.id, e.ts, "conductor", "Failed to launch", str(d["error"]), tone="red"))
                else:
                    out.append(Turn(e.id, e.ts, "conductor",
                                    "Resumed its staged session" if d.get("resumed") else "Started",
                                    " · ".join(x for x in (f"on {d['branch']}" if d.get("branch") else "",
                                                           f"port {d['port']}" if d.get("port") else "") if x)))
            case "staged":
                plan = (d.get("plan") or "").strip().splitlines()
                out.append(Turn(e.id, e.ts, "executor", "Staged; waits for the lock", plan[0] if plan else ""))
            case "tell" | "amended":
                amend = e.kind == "amended"
                by = d.get("by")
                who = "conductor" if by == "conductor" else "matt"
                asks = d.get("outline_request")         # the fixed text: the title says it
                what = "Amendment" if amend else "Outline request" if asks else "Message"
                t = Turn(e.id, e.ts, who, f"{what} #{e.id}", "" if asks else str(d.get("text") or ""),
                         name=sender(by))
                if amend:
                    t.title += f", while {d.get('state')}"
                    t.under.append(("delivery", "", str(d.get("delivery") or "")))
                if dv := delivered.get(e.id):
                    t.under.append(("delivered", dv.ts, f"via {dv.detail.get('via')}"
                                    + (f", after {dv.detail['waited_seconds']}s" if dv.detail.get("waited_seconds") is not None else "")))
                if r := replies.get(e.id):
                    t.under.append(("reply", r.ts, progress_text(r)))
                elif w := undelivered.get(e.id):
                    t.note, t.tone = str(w.detail.get("note") or "never delivered"), "warn"
                elif d.get("answers"):
                    t.under.append(("delivered", e.ts, f"as the answer to {d['answers']}"))
                elif dv and dv.detail.get("via") in ("hook", "progress"):
                    t.note, t.tone = "waiting for a reply", "wait"
                elif not dv:
                    t.note, t.tone = ("waiting for its launch" if amend and d.get("state") != "running"
                                      else "waiting for delivery, after its next tool call"), "wait"
                out.append(t)
            case "ask":
                how = ask_how(d)
                text = str(d.get("text") or "")
                if d.get("options"):
                    text += "\noptions: " + " / ".join(map(str, d["options"]))
                t = Turn(e.id, e.ts, "executor", f"Question ({how})", text)
                if end := ends.get(d.get("question")):
                    what = end.detail.get("text") if end.kind != "question_closed" else end.detail.get("reason")
                    label = "closed: a message answered it" if end.detail.get("outcome") == "by_message" else QUESTION_ENDS[end.kind]
                    t.under.append((label, end.ts, str(what or "")))
                else:
                    t.note, t.tone = "waiting for an answer", "wait"
                out.append(t)
            case "progress":
                if (ids := talk.reply_ids(d)) and all(i in talked for i in ids):
                    continue                    # shown under its tell
                title = ("Outline" if d.get("outline") and not d.get("note") else "Progress") + (
                    f" (reply to {', '.join(f'#{i}' for i in ids)})" if ids else "")
                out.append(Turn(e.id, e.ts, "executor", title, progress_text(e)))
            case "pick_open" | "pick_saved" | "pick_failed":
                out.append(Turn(e.id, e.ts, "conductor", "Picker", activity_text(e),
                                tone="red" if e.kind == "pick_failed" else ""))
            case "finish":
                status = str(d.get("status"))
                lines = [f"status: {status}" + (f" ({'; '.join(d['reasons'])})" if d.get("reasons") else "")]
                for key in ("blocks", "carry"):
                    lines.append(f"{key}: {d.get(key) or 'none'}")
                lines.append(f"slow: {slow_text(d) or 'none'}")
                lines += [f"pick: {ref}" for ref in picks.refs(d)]
                if resumed := resume_label(d):
                    lines.append(resumed)
                out.append(Turn(e.id, e.ts, "conductor", f"Finished {status}", "\n".join(lines),
                                tone=status if status in ("done", "partial", "red") else "", report=True))
            case _:
                out.append(Turn(e.id, e.ts, "conductor", e.kind, activity_text(e), tone="red"))
    return out


# ---------------------------------------------------------------- an item's trail
@dataclass
class Trail:
    item: str
    repo: str
    name: str
    where: str                  # queue | running | done | proposed | gone
    item_path: Path | None
    prompt: str
    events: list[Event]
    report: str
    log: str
    stage_log: str


def find_item_file(cfg: config_mod.Config, repo: str, name: str,
                   events: list[Event] = ()) -> tuple[str, Path | None]:
    """Where the item file is now; a cancelled item's copy is found through its
    `cancel` event's `kept`."""
    for where, p in (("running", cfg.path("running", repo, f"{name}.md")),
                     ("queue", cfg.path("queue", repo, f"{name}.md")),
                     ("done", cfg.path("done", repo, f"{name}.md")),
                     ("proposed", cfg.path("proposed", f"{name}.md"))):
        if p.is_file():
            return where, p
    for e in reversed(list(events)):
        if e.kind == "cancel" and e.item == f"{repo}/{name}" and e.detail.get("kept"):
            p = cfg.control / e.detail["kept"]
            if p.is_file():
                return "cancelled", p
    return "gone", None


def _read(path: Path, limit: int = TEXT_LIMIT) -> str:
    try:
        text = read_text(path)
    except (OSError, UnicodeDecodeError):
        return ""
    return text if len(text) <= limit else text[:limit] + f"\n… ({len(text) - limit} more characters)\n"


def build_trail(cfg: config_mod.Config, events: list[Event], item_id: str) -> Trail:
    repo, _, name = item_id.partition("/")
    paths = RunPaths.for_name(cfg, repo, name)
    own = [e for e in events if e.item == item_id]
    where, item_path = find_item_file(cfg, repo, name, own)
    prompt = _read(paths.prompt) if paths.prompt.is_file() else (_read(item_path) if item_path else "")
    return Trail(
        item=item_id, repo=repo, name=name, where=where, item_path=item_path, prompt=prompt,
        events=own,
        report=_read(paths.report),
        log=render_stream_json(p) if (p := stored_log(paths.log)) else "",
        stage_log=render_stream_json(p) if (p := stored_log(paths.stage_log)) else "",
    )


def git_trail(cfg: config_mod.Config, item_id: str, events: list[Event]) -> str:
    """The item's commits with their diffs: from its branch while it exists, else from
    the `merge` event (the rebased commits now on main)."""
    repo, _, name = item_id.partition("/")
    path = cfg.repos.get(repo)
    if path is None or not git.is_repo(path):
        return f"repo {repo!r} is not a git repo here"
    branch = f"conductor/{name}"
    main = cfg.opts(repo).main
    try:
        if git.rev(path, branch):
            base = git.run(path, "merge-base", main, branch, check=False).strip()
            spec = f"{base}..{branch}" if base else branch
            text = f"branch {branch} (still exists), commits since {base[:10] or 'its root'}:\n\n"
            text += git.run(path, "log", "--stat", "-p", "--format=commit %H%nDate: %ad%n%n    %s%n",
                            "--date=local", spec, check=False)
            return _cap(text) if text.strip() else f"branch {branch} has no commits of its own"
        merges = [e for e in events if e.item == item_id and e.kind == "merge"]
        if merges:
            ev = merges[-1]
            sha, n = ev.detail.get("sha"), int(ev.detail.get("commits") or 0)
            if not sha or not git.rev(path, sha):
                return f"merged as {sha}, which is no longer in {path}"
            if n <= 0:
                return f"merged at {sha[:10]} with no commits of its own"
            text = f"merged into {main} at {sha[:10]} ({n} commit(s), rebased; branch deleted):\n\n"
            text += git.run(path, "log", "--stat", "-p", "--format=commit %H%nDate: %ad%n%n    %s%n",
                            "--date=local", "-n", str(n), sha, check=False)
            return _cap(text)
        # No worktree (or its branch is gone and nothing merged): the `commit` events
        # name what the item committed straight onto the branch it ran on.
        shas = [e.detail["sha"] for e in events
                if e.item == item_id and e.kind == "commit" and e.detail.get("sha")]
        if shas:
            there = [s for s in shas if git.rev(path, s)]
            text = f"{len(shas)} commit(s) logged for this item (no branch {branch}, no merge)"
            if missing := [s[:10] for s in shas if s not in there]:
                text += f"; no longer in {path}: {', '.join(missing)}"
            text += ":\n\n"
            if there:
                text += git.run(path, "show", "--stat", "-p", "--format=commit %H%nDate: %ad%n%n    %s%n",
                                "--date=local", *there, check=False)
            return _cap(text)
    except git.GitError as exc:
        return f"git: {exc}"
    return f"no branch {branch}, no merge and no commit events: nothing committed survives"


def _cap(text: str) -> str:
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + "\n… (diff truncated)\n"
