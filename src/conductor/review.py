"""`review.md`: the bundle a `review:` signal writes, so a planner reviews in one read.
The unreviewed-reports summary, then each report inlined whole (with a red item's reasons
and the paths of its amendments, audit notes and setup log), then, for `review: all`, conductor's own reports past
the planner's marker (`planner-notes.md`). It marks nothing reviewed: that stays `reviewed:`."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import inbox, picks, questions, session
from .config import Config
from .events import Event, EventLog
from .executor import RunPaths
from .resume import label as resume_label
from .state import View
from .util import clock, now, read_text


@dataclass
class Bundle:
    text: str
    items: list[str] = field(default_factory=list)          # the project items inlined
    conductor: list[str] = field(default_factory=list)      # conductor's own reports inlined


def _inline(path: Path) -> list[str]:
    try:
        return ["", read_text(path).strip()]
    except FileNotFoundError:
        return [f"- the report file is missing: `{path}`"]
    except (OSError, UnicodeDecodeError) as exc:
        return [f"- the report file could not be read: {exc!r}"]


def _item_section(cfg: Config, view: View, ev: Event, log: EventLog | None = None) -> list[str]:
    repo, name = ev.item.split("/", 1)
    paths = RunPaths.for_name(cfg, repo, name)
    reviewed = f" · reviewed #{r.id}" if (r := view.reviews.get(ev.item)) and view.reviewed(ev.item) else ""
    if chg := view.changed_since_review(ev.item):
        reviewed = f" · **{inbox.changed_text(chg)}**"
    out = ["", f"## {ev.item} — **{ev.detail.get('status')}**, finished {clock(ev.ts)} (#{ev.id}){reviewed}",
           f"- report: `{cfg.rel(paths.report)}`"]
    if ev.detail.get("status") == "red" and ev.detail.get("reasons"):      # often not in the report
        out.append(f"- red: {'; '.join(ev.detail['reasons'])}")
    if orig := ev.detail.get("resume_of"):
        o_repo, _, o_name = orig.partition("/")
        out.append(f"- {resume_label(ev.detail)}: its report `{cfg.rel(RunPaths.for_name(cfg, o_repo, o_name).report)}`")
    for label, p in (("amendments", paths.amend), ("audit notes", paths.audit), ("setup log", paths.setup)):
        if p.is_file():
            out.append(f"- {label}: `{cfg.rel(p)}`")
    if log and (launched := inbox.launch_defaults(log, ev.item)) and not view.reviewed(ev.item):
        st, qs = launched                   # 145: here, beside the report, not as open rulings
        out.append(f"- launched {clock(st.ts)} on the defaults of {len(qs)} staging question{'s' * (len(qs) != 1)} "
                   "(reviewing this report settles them; a ruling against one is a follow-up):")
        out += questions.default_lines(qs)
    return out + _inline(paths.report)


def render(cfg: Config, view: View, rel: str, item_id: str | None = None, root: Path | None = None,
           log: EventLog | None = None) -> Bundle:
    """The bundle for `review: all` (`item_id` None) or `review: <item>` (one finished item).
    With `log`, staging questions still open as rulings follow the warnings (120); a finished
    item's launch defaults go in its own section instead (145)."""
    root = root or session.CONDUCTOR_ROOT
    finishes = [view.finishes[item_id]] if item_id else view.unreviewed()
    what = item_id or "all"
    rulings = inbox.staged_questions(log, view, {ev.item for ev in finishes}) if log else []
    out = [f"# Review — {cfg.project}", "",
           f"Written for `{rel}` at {clock(now())}: `review: {what}`. Nothing is marked reviewed: "
           "`reviewed:` comes only from processing a report read in full, as process X / process all does.",
           f"Now: {inbox.model_line()}.",
           *(["", "## Warnings", *warned] if (warned := inbox.warning_lines(view, finishes)) else []),
           *(["", "## Staging questions: open rulings", *rulings] if rulings else []),
           "", "## Unreviewed reports" if not item_id else "## Report",
           *(inbox.unreviewed_lines(view, finishes, picks.saved_at(cfg, view), cfg) or ["- none"])]
    bundle = Bundle("", items=[ev.item for ev in finishes])
    for ev in finishes:
        out += _item_section(cfg, view, ev, log)
    if not item_id:
        out += ["", "## Conductor reports"]
        if (past := session.reports_past_marker(root)) is None:
            out.append(session.missing_handoff(root))
        else:
            marker, found = past
            out.append(session.reports_head(root, marker, found))
            for name, p in found:
                bundle.conductor.append(name)
                out += ["", f"### {name}", f"- report: `{p}`", *_inline(p)]
    bundle.text = "\n".join(out) + "\n"
    return bundle
