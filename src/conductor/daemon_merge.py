"""Serial merges into main, the prose-conflict resolver, and concluding an item."""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from . import audits, executor, git, items, procs, talk, worktrees
from .util import file_hash, now, parse_stamp, stamp, write_text

if TYPE_CHECKING:
    from .daemon import Run


class MergeMixin:
    def process_merges(self) -> None:
        """Merge finished items into main, per repo serially in finish order."""
        pending: dict[str, list[Run]] = {}
        for run in self.runs.values():
            if run.rec.get("phase") == "merge":
                pending.setdefault(run.rec["repo"], []).append(run)
        for repo, runs in pending.items():
            for run in sorted(runs, key=lambda r: (r.rec["exited"], r.item_id)):
                if not self._merge(run):
                    break

    def _merge(self, run: Run) -> bool:
        """One merge attempt; False when it holds or a merge executor is resolving a
        prose conflict (later merges in the repo wait too)."""
        rec = run.rec
        wt = Path(rec["worktree"])
        res = rec.get("resolve")
        repo_path = self.cfg.repos[rec["repo"]]
        try:
            before = git.rev(repo_path, self.cfg.opts(rec["repo"]).main)   # pre-merge main
            if res and res["state"] == "running":
                if not self._resolver_exited(run):
                    return False
                if problem := self._judge_resolver(run):
                    self._conflict(run, res["files"], problem, f"prose conflict in {', '.join(res['files'])}: "
                                   f"the merge executor failed ({problem})", resolver=res["log"],
                                   note="rebase aborted: the branch keeps the item's version, main keeps its own; "
                                        "worktree kept")
                    return True
                res["state"] = "resolved"
                rec["resolved"] = sorted(set(rec.get("resolved", [])) | set(res["files"]))
                self._save(run)
            if res:
                m = worktrees.continue_rebase(self.cfg, rec["repo"], wt, rec["branch"], res["files"])
                if not (m.outcome == "hold" and git.rebase_in_progress(wt)):
                    rec.pop("resolve")              # the stopped rebase is over, one way or another
                    self._save(run)
            else:
                m = worktrees.merge(self.cfg, rec["repo"], wt, rec["branch"])
        except git.GitError as exc:
            m = worktrees.Merge("hold", reason=f"git failed: {str(exc)[:300]}")
        if m.outcome == "resolve":
            return self._start_resolver(run, m)
        if m.outcome == "hold":
            reason = f"merge waits: {m.reason}"
            if self.holds.get(rec["item"]) != reason:
                self.holds[rec["item"]] = reason
                self.log.append("hold", item=rec["item"], reason=reason, merge=True)
            return False
        out = rec["outcome"]
        if m.outcome == "conflict":
            self._conflict(run, m.files, m.reason, f"rebase onto {self.cfg.opts(rec['repo']).main} conflicted",
                           note="branch kept; main untouched")
            return True
        # The item's own commits: from pre-merge main, so an alongside neighbour merged since
        # the cut isn't counted; from the cut point when main already held them (a restart).
        since = before if before and before != m.sha else rec["base"]
        commits = git.commits_between(repo_path, since, m.sha) if m.sha else []
        out["merged"] = m.sha
        extra = {"resolved": rec["resolved"]} if rec.get("resolved") else {}
        self.log.append("merge", item=rec["item"], branch=rec["branch"], sha=m.sha, commits=len(commits),
                        note=m.reason, **extra)
        rec["phase"] = "concluding"
        self._save(run)
        self._worktree_op(rec, "removed", "merged")
        self._conclude(run)
        return True

    def _conflict(self, run: Run, files: list[str], error: str, reason: str, note: str, **extra) -> None:
        """The merge failed on content: red, rebase aborted, branch and worktree kept."""
        rec, out = run.rec, run.rec["outcome"]
        worktrees.abort_rebase(Path(rec["worktree"]))
        rec.pop("resolve", None)
        out["status"] = "red"
        out["reasons"] = out["reasons"] + [reason]
        self.log.append("conflict", item=rec["item"], branch=rec["branch"], files=files, error=error, note=note,
                        **extra)
        rec["phase"] = "concluding"
        self._save(run)
        self._worktree_op(rec, "kept", "conflict: branch and worktree kept for inspection")
        self._conclude(run)

    # ---------------------------------------------------------------- merge executors
    def _start_resolver(self, run: Run, m: worktrees.Merge) -> bool:
        """The rebase stopped on prose files only: a short merge executor resolves them in
        the item's worktree. False while it runs; True if it could not start (concluded red)."""
        cfg, rec = self.cfg, run.rec
        wt, repo = Path(rec["worktree"]), rec["repo"]
        paths = executor.RunPaths.for_name(cfg, repo, rec["name"])
        step = rec["resolve_steps"] = rec.get("resolve_steps", 0) + 1     # one per stop of the rebase
        self._guard_check(f"before the merge executor of {rec['item']}")
        # What the executor may change: the conflicted files, nothing else.
        touched = [f for f in git.run(wt, "diff", "HEAD", "--name-only", "-z").split("\0") if f]
        hashes = {f: file_hash(wt / f) for f in touched if f not in m.files}
        stopped = git.run(wt, "log", "-1", "--format=%h %s", "REBASE_HEAD", check=False).strip()
        main_sha = git.rev(cfg.repos[repo], cfg.opts(repo).main)
        upstream = git.commits_between(cfg.repos[repo], rec["base"], main_sha)
        body = executor.body(cfg, items.parse(run.md_path, repo)) if run.md_path.is_file() else ""
        write_text(paths.merge_prompt, executor.build_merge_prompt(cfg, rec["item"], rec, m.files, upstream,
                                                                   stopped, body))
        for stale in (paths.merge_log, paths.merge_stderr):
            stale.unlink(missing_ok=True)
        session = str(uuid.uuid4())
        res = {"state": "running", "step": step, "files": m.files, "head": git.head(wt), "hashes": hashes,
               "touched": touched, "untracked": worktrees.untracked(wt), "session_id": session,
               "log": str(paths.merge_log), "started": stamp(),
               "deadline": stamp(now() + timedelta(minutes=cfg.merge_minutes)), "tamper_before": len(run.tamper),
               "pid": None, "create_time": None}
        rec["resolve"] = res
        self._save(run)
        try:
            run.resolver = procs.spawn(executor.command(cfg, session, None), wt, paths.merge_prompt,
                                       paths.merge_log, paths.merge_stderr)
        except OSError as exc:
            self._conflict(run, m.files, str(exc), f"prose conflict in {', '.join(m.files)}: the merge executor "
                           f"did not start ({exc})", note="rebase aborted; worktree kept")
            return True
        res["pid"], res["create_time"] = run.resolver.pid, procs.create_time(run.resolver.pid)
        self._save(run)
        self.log.append("resolve", item=rec["item"], paths=[cfg.rel(paths.merge_log)], files=m.files, step=step,
                        session_id=session, pid=res["pid"], deadline=res["deadline"], commit=stopped,
                        note=f"prose conflict while rebasing onto {cfg.opts(repo).main}; a merge executor resolves it")
        return False

    def _resolver_exited(self, run: Run) -> bool:
        res = run.rec["resolve"]
        if run.resolver is not None:
            exited = run.resolver.poll() is not None
        else:                                       # adopted after a restart
            exited = not procs.alive(res.get("pid"), res.get("create_time"))
        if not exited and now() > parse_stamp(res["deadline"]):
            procs.kill_tree(res["pid"])
            res["killed"] = True
            self._save(run)
            exited = True
        if exited:
            run.resolver = None
        return exited

    def _judge_resolver(self, run: Run) -> str | None:
        """What is wrong with the merge executor's work, or None: it resolved exactly the
        conflicted files and left the stopped rebase as it found it."""
        rec, res = run.rec, run.rec["resolve"]
        wt = Path(rec["worktree"])
        self._guard_check(f"after the merge executor of {rec['item']}")
        if len(run.tamper) > res.get("tamper_before", 0):
            return "it changed the control folder (reverted)"
        if res.get("killed"):
            return f"it ran past merge_minutes ({self.cfg.merge_minutes:g})"
        result = executor.read_result(Path(res["log"]))
        if result is None or result.get("is_error"):
            return "it left no result" if result is None else f"it errored: {result.get('subtype')}"
        if not wt.is_dir() or not git.rebase_in_progress(wt) or git.head(wt) != res["head"]:
            return "it moved the stopped rebase (ran git)"
        touched = [f for f in git.run(wt, "diff", "HEAD", "--name-only", "-z").split("\0") if f]
        if other := sorted(set(touched) - set(res["touched"])) + sorted(
                f for f, h in res["hashes"].items() if file_hash(wt / f) != h):
            return f"it changed {other[:5]}, which were not in conflict"
        if worktrees.untracked(wt) != res["untracked"]:
            return "it added untracked files"
        for f in res["files"]:
            try:
                lines = (wt / f).read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                return f"{f} is gone"
            if any(ln.startswith(("<<<<<<<", ">>>>>>>")) for ln in lines):
                return f"conflict markers remain in {f}"
        return None

    def _conclude(self, run: Run) -> None:
        cfg, rec = self.cfg, run.rec
        item_id, repo, out = rec["item"], rec["repo"], rec["outcome"]
        done_md = f"done/{repo}/{rec['name']}.md"
        extra = {k: rec[k] for k in ("branch",) if rec.get(k)}
        if out.get("merged"):
            extra["merged"] = out["merged"]
        if rec.get("template"):
            extra["template"] = rec["template"]
        if rec.get("resume_of"):
            extra["resume_of"] = rec["resume_of"]
        report_paths = [cfg.rel(Path(rec["report"])), cfg.rel(Path(rec["log"])), done_md]
        notes = executor.RunPaths.for_name(cfg, repo, rec["name"]).audit
        if audits.writes_notes(rec):
            extra["audit"] = True
            if notes.is_file():
                report_paths.append(cfg.rel(notes))
        if rec.get("template") in audits.USES_NOTES and out["status"] != "red":
            # What this cleanup (or general audit, its own notes) acted on: the next
            # cleanup sees only newer notes. A red one uses none.
            used = list(rec.get("notes") or [])
            if rec["template"] == "general_audit" and notes.is_file():
                used.append(item_id)
            if used:
                self.log.append("audit_used", item=item_id, notes=used,
                                paths=[cfg.rel(audits.note_path(cfg, *n.split("/", 1))) for n in used])
        # `red` before `finish`: a restart that finds the finish logged only moves the
        # item, so the red hold must already be on record.
        self.process_talk()
        for msg in talk.set_aside(cfg.control, item_id):
            if talk.is_amend(msg):
                kept = self._keep_amendment(msg)
                self.log.append("warning", item=item_id, amend=msg.get("id"), paths=[cfg.rel(kept)] if kept else [],
                                note=f"amendment #{msg.get('id')} was never delivered: the executor exited first")
                continue
            self.log.append("warning", item=item_id, tell=msg.get("id"),
                            note=f"message #{msg.get('id')} was never delivered: the executor exited first")
        if out["status"] == "red":
            self.log.append("red", item=item_id, paths=[cfg.rel(Path(rec["report"]))],
                            reasons=out["reasons"], after_red=rec.get("after_red", "stop"))
        self.log.append(
            "finish", item=item_id, paths=report_paths,
            status=out["status"], report_status=out["report_status"], reasons=out["reasons"],
            blocks=out["blocks"], carry=out["carry"], slow=out.get("slow"), pick=out.get("pick"),
            session_id=rec["session_id"], exit_code=out["exit_code"],
            cost_usd=out["cost_usd"], turns=out["turns"],
            needs_planner=rec.get("needs_planner", False), after_red=rec.get("after_red", "stop"), **extra,
        )
        rec["phase"] = "done"
        if run.rec_path.exists():
            self._save(run)
        self._move_to_done(run)
        self.runs.pop(item_id, None)
        self._compact_logs([item_id])
        self._close_questions(item_id)
        qdir = cfg.path("queue", repo)
        still_running = any(r.rec["repo"] == repo for r in self.runs.values())
        if not still_running and not items.item_files(qdir):
            self.log.append("queue_empty", item=item_id, repo=repo, last=item_id)
            # Not this item's own leftover: whatever held it a moment ago still does.
            self._retry_leftovers("queue empty", repo=repo, skip=item_id)
