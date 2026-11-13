"""Claude API outages (docs/api-retry.md): an executor that exits on an API failure (a
Cloudflare 403, an authentication failure, 5xx, 429, overloaded) is not judged. Its run
waits in `running/` with its clock paused, then the same session resumes in the same
worktree (`--resume`) after each wait in `[defaults] api_retry_minutes` (2, 10, 30); red
only once they are spent, its reason naming the API. aicomplete 211 (2026-11-13) went red
six hours in at 1:48am on a Cloudflare 403 and nothing moved until 7:40am.

`retry: <repo>/<NNN_name>` takes the same resume on demand, for a finished red item whose
worktree and session are kept; it then finishes, merges and reports as usual, and a red
hold of its own is cleared when it lands.
"""

from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import executor, git, items, procs, state, talk
from . import resume as resume_items
from .util import clock, now, parse_stamp, retry_io, stamp, write_text

if TYPE_CHECKING:
    from .daemon import Run

# A transient failure of the API, as Claude Code prints it in its last lines, result or stderr.
# Not a 400 (a request that will fail the same way again).
API_ERROR = re.compile(
    r"API Error:\s*(?:40[138]|429|5\d\d|Connection error|Request timed out|Overloaded)\b[^\"\n]{0,120}"
    r"|Failed to authenticate[^\"\n]{0,160}|Attention Required! \| Cloudflare|overloaded_error|rate_limit_error",
    re.IGNORECASE)
API_REASON = "API error"            # a red reason's start when the retries ran out (`api_only`)
TAIL_BYTES = 16384
RETRY_FLOOR = timedelta(minutes=30)

RESUMED = """\
Conductor: {what}, and this session is resumed now, at {now}{tries}. Your worktree, branch
and commits are as you left them, and any job you launched with `start` kept running.
Check what was in flight when it stopped (a tool call's output, a file being written) before
relying on it, then carry on with the prompt from where you were. Your deadline is now
{deadline}; everything else you were told stands. Post a progress line saying where you resume.

Write your report to exactly this path:
    {report}
"""
AMENDS = """
---

Amendments sent while you were stopped: each amends the prompt and wins where they conflict.
"""


def _tail(path: Path, size: int = TAIL_BYTES) -> str:
    try:
        with open(path, "rb") as fh:
            fh.seek(max(0, os.path.getsize(path) - size))
            return fh.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def api_error(log: Path, stderr: Path | None, result: dict | None) -> str | None:
    """The API failure an executor exited on, or None: looked for in its result (when it
    errored), the last lines of its run log and the end of its stderr."""
    if result is not None and not result.get("is_error"):
        return None
    texts = [str(result.get("result") or "")] if result else []
    texts += [*_tail(log).splitlines()[-5:], _tail(stderr, 4096) if stderr else ""]
    for text in texts:
        if m := API_ERROR.search(text):
            return " ".join(m.group(0).split())[:200]
    return None


def own_cost(rec: dict, result: dict | None) -> float | None:
    """The run's spend from its result's `total_cost_usd`, which counts the whole session: a
    fork's (a resume item's) starts at its original's, so that is taken off (216 reported 211's
    $9.48 as its own). A retry resumes the same session, so its total stays the item's."""
    cost = (result or {}).get("total_cost_usd")
    if cost is None or not rec.get("resume_of"):
        return cost
    return round(max(0.0, float(cost) - float(rec.get("resume_cost") or 0)), 4)


def api_only(reasons) -> bool:
    """A red whose every reason is the API's or a failed launch: nothing the executor did."""
    reasons = [str(r) for r in reasons or []]
    return bool(reasons) and all(r.startswith((API_REASON, "launch failed")) for r in reasons)


class ApiMixin:
    def _api_wait(self, run: Run) -> bool:
        """At an exit: True when the run now waits to retry (or already did), so it is not
        judged. When the waits are spent, `api_exhausted` makes `finish` red it on the API."""
        rec = run.rec
        if rec.get("api_wait"):
            return True                             # a restart found it waiting: it still is
        if run.tamper or rec.get("api_exhausted") or rec.get("phase", "exec") != "exec":
            return False
        err = api_error(Path(rec["log"]), Path(rec["stderr"]) if rec.get("stderr") else None,
                        executor.read_result(Path(rec["log"])))
        if not err:
            return False
        waits, n = self.cfg.api_retry_minutes, rec.get("api_tries", 0)
        if n >= len(waits) or rec.get("template") == "watch" or not run.md_path.exists():
            rec["api_exhausted"] = f"{API_REASON} ({err}); " + (
                f"{n} retr{'y' if n == 1 else 'ies'} spent" if n else "not retried")
            return False
        at = now() + timedelta(minutes=waits[n])
        rec.update(api_tries=n + 1, api_wait={"try": n + 1, "at": stamp(at), "since": stamp(), "error": err})
        self._save(run)
        self.log.append("api_retry", item=rec["item"], state="waiting", attempt=n + 1, of=len(waits),
                        at=stamp(at), error=err, session_id=rec["session_id"],
                        note=f"the API dropped it ({err}); its session resumes at {clock(at)}, "
                             f"try {n + 1} of {len(waits)}, its clock paused")
        return True

    def _api_tick(self, run: Run) -> None:
        """Each tick for a waiting run: resume it once its time comes."""
        if now() >= parse_stamp(run.rec["api_wait"]["at"]):
            self._api_resume(run)

    def _api_resume(self, run: Run, by: str | None = None) -> None:
        """The same session, in the same worktree, told what happened; its deadline moves
        by the time it was stopped. `by`: a `retry:` signal's sender."""
        cfg, rec = self.cfg, run.rec
        wait = rec.pop("api_wait", None) or {}
        since = parse_stamp(wait.get("since") or rec.get("exited") or stamp())
        wt = Path(rec["worktree"]) if rec.get("worktree") else None
        problem = None
        if resume_items.session_file(rec["session_id"]) is None:
            problem = "its session has no transcript in Claude Code's projects folder"
        elif wt and (not wt.is_dir() or git.symbolic_head(wt) != f"refs/heads/{rec['branch']}"):
            problem = f"its worktree is gone or off {rec['branch']}"
        if problem and not by:
            rec["api_exhausted"] = f"{API_REASON} ({wait.get('error')}); could not resume: {problem}"
            self.finish(run)
            return
        if problem:
            raise ValueError(f"retry: {rec['item']} cannot resume: {problem}")
        item = items.parse(run.md_path, rec["repo"])
        paths = executor.RunPaths.for_item(cfg, item)
        n = rec.get("api_tries", 0) if not by else rec.setdefault("retried", 0) + 1
        # The time it had left when it stopped, from now (at least RETRY_FLOOR: a `retry:` of
        # an item killed at max_hours would otherwise be killed again at once).
        deadline = now() + max(parse_stamp(rec["deadline"]) - since, RETRY_FLOOR)
        what = (f"`retry:` by {by} resumes this item, which ended red at {clock(since)}"
                f" ({'; '.join(rec.get('retry_reasons') or [])})" if by
                else f"the Claude API dropped this session at {clock(since)} ({wait.get('error')})")
        tries = "" if by else f" (try {n} of {len(cfg.api_retry_minutes)})"
        text = RESUMED.format(what=what, now=clock(now()), tries=tries, deadline=clock(deadline),
                              report=paths.report.resolve())
        amends = [m for m in talk.take(cfg.control, item.id, "resume") if talk.is_amend(m)]
        text += (AMENDS + "".join(executor._amend(executor.RESUME_AMEND, m) for m in amends) if amends else "")
        prompt = paths.prompt.with_name(paths.prompt.name.replace(
            ".prompt.md", f".retry{n}.md" if by else f".api{n}.md"))
        write_text(prompt, text)
        write_text(paths.mcp, executor.mcp_config(cfg, item, rec["session_id"], rec.get("worktree") or cfg.repos[item.repo]))
        write_text(paths.settings, executor.hook_settings(cfg, item))
        env = {executor.PORT_VAR: str(rec.get("port", ""))}
        if rec.get("remote"):
            from . import remote
            env |= remote.item_launch(cfg.control, prompt, item.id)
        try:
            run.popen = procs.spawn(executor.command(cfg, rec["session_id"], paths.mcp, resume=True,
                                                     settings=paths.settings),
                                    wt or cfg.repos[item.repo], prompt, paths.log, paths.stderr, env=env)
        except OSError as exc:
            if by:
                raise ValueError(f"retry: {rec['item']}: resuming its session failed: {exc}") from exc
            rec["api_exhausted"] = f"{API_REASON} ({wait.get('error')}); resuming its session failed: {exc}"
            self.finish(run)
            return
        run.proc = None
        rec.update(pid=run.popen.pid, create_time=procs.create_time(run.popen.pid), deadline=stamp(deadline),
                   api_resumed=stamp())
        if by:
            rec["retried"] = n
        self._save(run)
        signal = [rec.pop("retry_signal")] if rec.get("retry_signal") else []
        self.log.append("api_retry", item=item.id, paths=[cfg.rel(prompt), *signal], state="retry" if by else "resumed",
                        attempt=n, pid=run.popen.pid, session_id=rec["session_id"], deadline=rec["deadline"],
                        **({"by": by} if by else {}),
                        note=(f"resumed by `retry:` ({by})" if by else f"its session resumed, try {n}")
                             + f"; deadline now {clock(deadline)}")

    # ---------------------------------------------------------------- retry: <item>
    def _retry(self, ref: str, rel: str) -> None:
        """`retry: <item>`: resume a finished red item's kept session in its kept worktree."""
        from .daemon import Run
        item_id, view = self._finished_item("retry", ref)
        repo, name = item_id.split("/", 1)
        fin = view.finishes[item_id]
        if fin.detail.get("status") != "red":
            raise ValueError(f"retry: {item_id} finished {fin.detail.get('status')}, not red")
        if item_id in self.runs or item_id in self.landings:
            raise ValueError(f"retry: {item_id} is already running or landing")
        if (prior := self.log.last("land", item=item_id)) and prior.detail.get("state") == "landed":
            raise ValueError(f"retry: {item_id} already landed")
        rec_path, md = self.cfg.path("done", repo, f"{name}.run.json"), self.cfg.path("done", repo, f"{name}.md")
        rec = state.read_run(rec_path) if rec_path.is_file() else {}
        wt = Path(rec["worktree"]) if rec.get("worktree") else None
        if not wt or rec.get("readonly") or not wt.is_dir() or not md.is_file():
            raise ValueError(f"retry: {item_id} has no kept worktree to resume in")
        if busy := [r.item_id for r in self.runs.values() if r.rec["repo"] == repo
                    and not r.rec.get("readonly") and not r.rec.get("alongside")]:
            raise ValueError(f"retry: {busy[0]} is running in {repo}; retry once it finishes")
        if rec.get("port") is None or any(r.rec.get("port") == rec["port"] for r in self.runs.values()):
            rec["port"] = self._free_port()
        rec["retry_reasons"] = (rec.get("outcome") or {}).get("reasons") or fin.detail.get("reasons") or []
        for key in ("outcome", "api_wait", "api_exhausted", "resolve"):
            rec.pop(key, None)
        rec.update(phase="exec", api_tries=0)
        rdir = self.cfg.path("running", repo)
        rdir.mkdir(parents=True, exist_ok=True)
        run = Run(rec, rdir / rec_path.name, rdir / md.name)
        retry_io(os.replace, md, run.md_path)
        retry_io(os.replace, rec_path, run.rec_path)
        self.runs[item_id] = run
        rec["retry_signal"] = rel
        try:
            self._api_resume(run, by=self._by or "unknown")
        except ValueError:
            self.runs.pop(item_id, None)
            retry_io(os.replace, run.md_path, md)
            retry_io(os.replace, run.rec_path, rec_path)
            raise

    def _retry_cleared(self, run: Run) -> None:
        """A retried item that finished without a red: the red hold it left is lifted."""
        rec = run.rec
        if not rec.get("retried") or rec["outcome"]["status"] == "red":
            return
        if (ev := state.scan(self.cfg, self.log).red_hold.get(rec["repo"])) and ev.item == rec["item"]:
            self.log.append("clear", repo=rec["repo"], by="conductor",
                            note=f"{rec['item']}, whose red held the queue, finished {rec['outcome']['status']} "
                                 "after `retry:`")
