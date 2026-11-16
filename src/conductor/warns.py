"""A report's `warnings:` line read for the digests (`docs/warnings.md`): its parts by kind,
the kinds repeated in a repo's recent reports, and the *Warnings* section's lines with the
standing (115) and unchanged-from-main (121) folds. Split from `inbox` at 121."""

from __future__ import annotations

import re

from .events import Event
from .state import View
from .util import clock


def warnings_text(detail: dict) -> str | None:
    """A finish's `warnings:` worth showing, or None when `none` or absent. A part saying
    the repo has no *Warning checks* section is dropped: that is fine, not a warning (113)."""
    text = str(detail.get("warnings") or "").strip()
    if not text or text.lower().rstrip(".") == "none":
        return None
    kept = [p.strip() for p in _split_parts(text) if p.strip() and not NO_CHECKS_RE.search(p)]
    return "; ".join(kept) or None


def _split_parts(text: str) -> list[str]:
    """Split on `;` outside parentheses: helium-hustle 019's "(pre-existing; this item
    changes no rules)" is one part, not a stray `other` (121)."""
    parts, depth, cur = [], 0, []
    for ch in text:
        depth += (ch == "(") - (ch == ")" and depth > 0)
        if ch == ";" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    return parts + ["".join(cur)]


# "docs: CLAUDE.md has no *Warning checks* section to run" and the like.
NO_CHECKS_RE = re.compile(r"\b(no|without|lacks?|missing)\b[^;]*warning[ _]checks", re.IGNORECASE)


# A warning its executor calls unchanged from main (121): the preamble's `unchanged from
# main:` marker, or the paraphrases helium-hustle 012-020 wrote before it ("the same as on
# main", "pre-existing", "unchanged by this item", "changes no rules").
UNCHANGED_RE = re.compile(r"\bunchanged (from|on|since|vs\.?) main\b|\bsame as (on )?main\b|\bpre-?existing\b"
                          r"|\bunchanged by this\b|\bchanges no (rules|code)\b|\bpredate", re.IGNORECASE)


def unchanged(text: str) -> bool:
    return bool(UNCHANGED_RE.search(text))


WARNING_KIND_RE = re.compile(r"^\**`?([A-Za-z][\w-]{0,23})`?\**\s*:\s*(.+)$")


def warning_parts(text: str | None) -> list[tuple[str, str]]:
    """`design: capstone cheap; perf: 40s turn` → [("design", "capstone cheap"), ("perf", "40s turn")].
    A part with no `<kind>:` lead is kind `other`."""
    out = []
    for part in _split_parts(text or ""):
        if not (part := part.strip()):
            continue
        m = WARNING_KIND_RE.match(part)
        out.append((m[1].lower(), m[2].strip()) if m else ("other", part))
    return out


def _num(item: str) -> str:
    return item.split("/", 1)[1].split("_", 1)[0]


def warning_repeats(view: View, repo: str, window: int = 5) -> dict[str, list[str]]:
    """Kind → the item numbers naming it, among `repo`'s last `window` reports (resume
    items aside); only kinds named in two or more."""
    fins = sorted((ev for ev in view.finishes.values() if ev.repo == repo and not ev.detail.get("resume_of")),
                  key=lambda e: e.id)[-window:]
    named: dict[str, list[str]] = {}
    for ev in fins:
        for kind in dict.fromkeys(k for k, _ in warning_parts(warnings_text(ev.detail))):
            named.setdefault(kind, []).append(_num(ev.item))
    return {k: ids for k, ids in named.items() if len(ids) >= 2}


def _part_key(kind: str, text: str) -> tuple[str, str]:
    return kind, " ".join(text.lower().split()).rstrip(".")


def last_reviewed(view: View, repo: str, before: int) -> Event | None:
    """`repo`'s newest reviewed report finished before event `before` (watches and resume
    items aside): the baseline a standing warning is unchanged since."""
    revs = [ev for i, ev in view.finishes.items() if ev.repo == repo and ev.id < before and view.reviewed(i)
            and not view.is_watch(i) and not ev.detail.get("resume_of")]
    return max(revs, key=lambda e: e.id, default=None)


def warning_lines(view: View, finishes: list[Event]) -> list[str]:
    """The *Warnings* section: each finish among `finishes` whose report raised any, kind by
    kind, then the kinds among them repeated in their repo's recent reports. A warning
    unchanged since its repo's last reviewed report is folded into one *standing* line per
    repo, so the new ones stand out (115). One its executor calls unchanged from main
    (`unchanged`) leaves its item's line too: the repeat line names it (121)."""
    from .session import SLOW_WINDOW      # session imports this module
    out, kinds, standing, same = [], {}, {}, {}
    for ev in finishes:
        if not (parts := warning_parts(warnings_text(ev.detail))):
            continue
        base = last_reviewed(view, ev.repo, ev.id)
        seen = {_part_key(k, t) for k, t in warning_parts(warnings_text(base.detail))} if base else set()
        for k, t in parts:
            if _part_key(k, t) not in seen and unchanged(t):
                nums = same.setdefault(ev.repo, {}).setdefault(k, [])
                nums += [_num(ev.item)] * (_num(ev.item) not in nums)
        new = [(k, t) for k, t in parts if _part_key(k, t) not in seen and not unchanged(t)]
        for k, t in parts:
            if _part_key(k, t) in seen:
                stand = standing.setdefault(ev.repo, {"base": base, "parts": {}, "on": []})
                stand["parts"].setdefault(_part_key(k, t), (k, t))
                if _num(ev.item) not in stand["on"]:
                    stand["on"].append(_num(ev.item))
        if new:
            out.append(f"- {ev.item} ({ev.detail.get('status')}, finished {clock(ev.ts)}, #{ev.id}): "
                       + "; ".join(f"**{k}**: {t}" for k, t in new))
            kinds.setdefault(ev.repo, set()).update(k for k, _ in new)
    for repo, s in standing.items():
        out.append(f"- standing in `{repo}`, unchanged since {_num(s['base'].item)} (reviewed), again on "
                   f"{', '.join(s['on'])}: " + "; ".join(f"**{k}**: {t}" for k, t in s["parts"].values()))
    for repo in dict.fromkeys([*kinds, *same]):
        reps, shown, unch = warning_repeats(view, repo, SLOW_WINDOW), kinds.get(repo, set()), same.get(repo, {})
        segs = [f"{k} ×{len(ids)} ({', '.join(ids)})" for k, ids in reps.items() if k in shown]
        segs += [(f"{k} ×{len(reps[k])} ({', '.join(reps[k])})" if k in reps and k not in shown else k)
                 + f", unchanged from main on {', '.join(nums)}" for k, nums in unch.items()]
        if segs:
            out.append(f"- repeated in `{repo}`'s last {SLOW_WINDOW} report(s): " + "; ".join(segs))
    return out
