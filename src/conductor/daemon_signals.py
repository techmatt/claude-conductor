"""The daemon's signal files: parsing, the verbs, and reorder's rename journal."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from typing import TYPE_CHECKING

from . import audits, desktop, executor, items, nightshift, procs, state, talk
from .daemon_push import CONTROL
from . import review as review_mod
from . import session as session_mod
from .events import Event, live_invalids
from .util import clock, read_text, retry_io, write_text

if TYPE_CHECKING:
    from .daemon import Run


SIGNAL_SETTLE_SECONDS = 1.0     # a signal or amend file younger than this may still be being written
# The planner's bridge writes a signal whole in one call, so a `planner-*` signal is taken once
# it reads and is this old: its receipt lands as fast as a request's (docs/receipts.md).
PLANNER_SIGNAL_SETTLE_SECONDS = 0.25


def signal_settle(name: str) -> float:
    return PLANNER_SIGNAL_SETTLE_SECONDS if name.startswith("planner-") else SIGNAL_SETTLE_SECONDS
AUDIT_LATE = "From conductor: this item now keeps audit notes, for the rest of its run.\n\n"   # `audit:`


def _strip_comment(value: str) -> str:
    """`42   # note` → `42`; `#42` stays (a `#` only starts a comment after whitespace)."""
    return re.split(r"\s+#", value.strip(), maxsplit=1)[0].strip()


def _signal_by(text: str) -> str | None:
    """The `by:` of a signal file (the first one), so even an invalid file is attributed."""
    for ln in text.splitlines():
        key, sep, value = ln.strip().partition(":")
        if sep and key.strip().lower() == "by" and _strip_comment(value):
            return _strip_comment(value)
    return None


def review_paths(cfg, item_id: str) -> dict[str, Path]:
    """What a review covers: the report and the audit notes beside it."""
    from .executor import RunPaths
    p = RunPaths.for_name(cfg, *item_id.split("/", 1))
    return {"report": p.report, "audit": p.audit}


def report_hashes(cfg, item_id: str) -> dict[str, str | None]:
    """{"report": sha256 or None, "audit": ...}: None for a file that does not exist. A file
    that exists but can't be read raises OSError: unreadable is not the same as gone."""
    out: dict[str, str | None] = {}
    for k, p in review_paths(cfg, item_id).items():
        try:
            out[k] = hashlib.sha256(p.read_bytes()).hexdigest()
        except FileNotFoundError:
            out[k] = None
    return out


def _stat(path: Path) -> tuple | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns


class SignalsMixin:
    def process_signals(self) -> None:
        sdir = self.cfg.path("signals")
        if not sdir.is_dir():
            return
        for f in sorted(p for p in sdir.iterdir() if p.is_file()):
            if f.suffix in (".bad", ".tmp") or time.time() - f.stat().st_mtime < signal_settle(f.name):
                continue
            try:
                f.read_bytes()                          # its writer still holds it: a later tick
            except OSError:
                continue
            rel = f"signals/{f.name}"
            by, applied = None, []
            with self._receipt(rel, always=True):       # every event its lines log, or why it was refused
                try:
                    text = read_text(f)
                    by = _signal_by(text)
                    lines = self._parse_signal(text)    # every line checked before any applies
                    with self._attributed(by):
                        for key, value in lines:
                            # Journalled before each line: a crash from here until the file
                            # is gone must never apply its lines a second time.
                            write_text(self._signal_journal, json.dumps({"rel": rel, "applied": applied}))
                            self._apply_signal(key, value, rel)
                            applied.append(f"{key}: {value}")
                    retry_io(f.unlink)
                except (ValueError, UnicodeDecodeError, OSError) as exc:
                    with self._attributed(by):
                        extra = {"applied": applied} if applied else {}
                        self.log.append("invalid", paths=[rel], path=rel, error=str(exc), **extra)
                    try:
                        os.replace(f, f.with_name(f.name + ".bad"))
                    except OSError:
                        pass
            self._signal_journal.unlink(missing_ok=True)

    @property
    def _signal_journal(self) -> Path:
        return self.cfg.state_dir / "signal.json"

    def _recover_signal(self) -> None:
        """A signal file the daemon was applying when it died: some of its lines may
        have applied, so it is set aside as `.bad` (never applied again)."""
        if not self._signal_journal.is_file():
            return
        try:
            j = json.loads(self._signal_journal.read_text(encoding="utf-8"))
            sig = self.cfg.path(*j["rel"].split("/"))
            if sig.is_file():
                os.replace(sig, sig.with_name(sig.name + ".bad"))
                self.log.append("warning", paths=[j["rel"]], applied=j.get("applied", []),
                                note=f"{j['rel']} was interrupted part-way by a crash; set aside as .bad. Lines "
                                     f"applied before the line that was running: {j.get('applied') or 'none'}; "
                                     "that line and the ones after it may not have applied")
        except (ValueError, KeyError, TypeError, OSError) as exc:
            self.log.append("warning", note=f"could not read {self.cfg.rel(self._signal_journal)}: {exc!r}")
        self._signal_journal.unlink(missing_ok=True)

    @contextmanager
    def _attributed(self, by: str | None):
        """Every event appended inside carries `detail.by` (side effects included)."""
        if not by:
            yield
            return
        plain = self.log.append

        def append(kind, item=None, repo=None, paths=(), **detail):
            detail.setdefault("by", by)
            return plain(kind, item, repo, paths, **detail)

        self.log.append, self._by = append, by
        try:
            yield
        finally:
            del self.log.append         # back to the class method
            self._by = None

    def _parse_signal(self, text: str) -> list[tuple[str, str]]:
        """`key: value` lines (blank lines, `#` lines and trailing ` # comments` ignored),
        syntax-checked as a whole; `by:` is taken out. What a verb acts on is checked when
        it applies."""
        out: list[tuple[str, str]] = []
        for n, ln in enumerate(text.splitlines(), 1):
            s = ln.strip()
            if not s or s.startswith("#"):
                continue
            key, sep, value = s.partition(":")
            key = key.strip().lower()
            # A message is free text: a ` # ` in it is part of it, not a comment.
            # So is `ack:`'s, whose `#<event>` ids would otherwise read as one.
            value = value.strip() if key in ("tell", "checkpoint", "session", "notify", "ack") else _strip_comment(value)
            if not sep or not key:
                raise ValueError(f"line {n}: expected `key: value`, got {s!r}")
            if key == "by":
                continue
            if key == "read":
                if value.lower() not in ("latest", "all") and not value.lstrip("#").isdigit():
                    raise ValueError(f"line {n}: read: expects an event id or `latest`, got {value!r}")
            elif key in ("clear", "push", "pause", "resume"):
                if value not in self.cfg.repos and not (key == "push" and value == "control"):
                    raise ValueError(f"line {n}: {key}: {value!r} is not a repo")
            elif key in ("approve", "cancel", "reviewed", "outline", "review", "session_opened", "audit", "land"):
                if not value or len(value.split()) != 1:
                    raise ValueError(f"line {n}: {key}: expects one item, got {value!r}")
            elif key == "reorder":
                parts = value.split()
                if len(parts) < 2 or (parts[0] != "proposed" and parts[0] not in self.cfg.repos):
                    raise ValueError(f"line {n}: reorder: expects `<repo|proposed> <item> <item> ...`, got {value!r}")
            elif key == "tell":
                if len(value.split(None, 1)) != 2:
                    raise ValueError(f"line {n}: tell: expects `<item> <message>`, got {value!r}")
            elif key == "run":
                parts = value.split()
                if not 1 <= len(parts) <= 2 or (len(parts) == 2 and parts[1].lower() != "stop"):
                    raise ValueError(f"line {n}: run: expects `<target>` or `<target> stop`, got {value!r}")
            elif key in ("checkpoint", "session"):
                pass                                    # any label, or none
            elif key == "pick":
                repo, sep, path = value.partition("/")
                if len(value.split()) != 1 or not sep or not path or repo not in self.cfg.repos:
                    raise ValueError(f"line {n}: pick: expects `<repo>/<manifest path>`, got {value!r}")
            elif key == "open":
                if len(value.split(None, 1)) != 2 or value.split()[0].lower() not in desktop.VERBS:
                    raise ValueError(f"line {n}: open: expects `<{'|'.join(desktop.VERBS)}> <path or URL>`, "
                                     f"got {value!r}")
            elif key == "notify":
                if not value:
                    raise ValueError(f"line {n}: notify: expects a line of text")
            elif key == "stop":
                if value.lower() != "daemon":
                    raise ValueError(f"line {n}: stop: only `stop: daemon` is known, got {value!r}")
            elif key == "watch":
                if value.lower() != "now":
                    raise ValueError(f"line {n}: watch: only `watch: now` is known, got {value!r}")
            elif key == "ack":
                what, *ids = value.split() or [""]
                if what.lower() != "invalid" or not all(i.lstrip("#").isdigit() for i in ids):
                    raise ValueError(f"line {n}: ack: expects `invalid` or `invalid #<event> ...`, got {value!r}")
            elif key == "autopilot":
                value = {"on": "always"}.get(value.lower(), value.lower())
                if value not in nightshift.AUTOPILOT:
                    raise ValueError(f"line {n}: autopilot: expects `off`, `night` or `always`, got {value!r}")
            else:
                raise ValueError(f"line {n}: unknown signal key {key!r}")
            out.append((key, value))
        if not out:
            raise ValueError("empty signal")
        return out

    def _apply_signal(self, key: str, value: str, rel: str) -> None:
        """One verb. Each checks everything it needs before it changes anything."""
        if key == "read":
            top = self.log.max_id()
            through = top if value.lower() in ("latest", "all") else int(value.lstrip("#"))
            through = max(min(through, top), self.log.read_mark())
            self.log.append("read", paths=[rel], through=through)
        elif key == "clear":
            self.log.append("clear", repo=value, paths=[rel])
        elif key == "pause":
            self._pause(value, rel)                     # daemon_messages
        elif key == "resume":
            self._resume(value, rel)
        elif key == "push":
            # its `push` or `push_failed` follows (daemon_push); `control` is the control folder
            self.want_push(CONTROL if value == "control" and value not in self.cfg.repos else value, "signal")
        elif key == "approve":
            self._approve(value, rel)
        elif key == "cancel":
            self._cancel(value, rel)
        elif key == "reviewed":
            self._reviewed(value, rel)
        elif key == "session_opened":
            self._session_opened(value, rel)
        elif key == "review":
            self._review(value, rel)
        elif key == "tell":
            self._tell(value, rel)
        elif key == "outline":
            self._tell(f"{value} {talk.OUTLINE_REQUEST}", rel, outline_request=True)
        elif key == "land":
            self._land(value, rel)                      # daemon_merge
        elif key == "audit":
            self._audit(value, rel)
        elif key == "checkpoint":
            self._checkpoint(value, rel)
        elif key == "session":
            self._session(value, rel)
        elif key == "run":
            name, *stop = value.split()
            (self.runner.stop if stop else self.runner.request)(name, self._by)
        elif key == "reorder":
            where, *refs = value.split()
            self._reorder(where, refs, rel)
        elif key == "pick":
            self.picker.open(value, self._by, rel)
        elif key == "open":
            # A refusal is an `open_refused` event, not a rejected file: later lines still apply.
            verb, target = value.split(None, 1)
            self.opener.open(verb.lower(), target, self._by, rel)
        elif key == "notify":
            self.log.append("notify", paths=[rel], text=value, by=self._by or "unknown")
        elif key == "stop":
            self.stop_requested = {"paths": [rel], **({"by": self._by} if self._by else {})}
        elif key == "watch":
            # Asked for: the planner rule and memory don't hold it back (daemon_watch).
            if why := self._launch_watch(f"asked for by {self._by or 'unknown'} (`watch: now`)", [], force=True):
                raise ValueError(f"watch: {why}")
        elif key == "ack":
            self._ack([int(i.lstrip("#")) for i in value.split()[1:]], rel)
        elif key == "autopilot":
            self.log.append("autopilot", paths=[rel], mode=value, was=nightshift.autopilot(self.log),
                            by=self._by or "unknown")

    def _one(self, found: list[Path], verb: str, ref: str, where: str) -> Path:
        if not found:
            raise ValueError(f"{verb}: no item {ref!r} in {where}")
        if len(found) > 1:
            raise ValueError(f"{verb}: {ref!r} is ambiguous in {where}: {[p.stem for p in found]}")
        return found[0]

    def _running_file(self, repo: str, ref: str) -> Path | None:
        """`running/<repo>/NNN_name.md` named by `ref` while its run record exists."""
        for p in items.find(self.cfg.path("running", repo), ref):
            if p.with_name(p.stem + ".run.json").exists():
                return p
        return None

    def _approve(self, ref: str, rel: str) -> None:
        src = self._one(items.find(self.cfg.path("proposed"), ref), "approve", ref, "proposed/")
        repo = items.front_repo(src)
        if repo not in self.cfg.repos:
            raise ValueError(f"approve: {src.name}: `repo: {repo}` is not a configured repo")
        item = items.parse(src, repo)
        if item.errors:
            raise ValueError(f"approve: {src.name}: {'; '.join(item.errors)}")
        dest = self.cfg.path("queue", repo, src.name)
        if dest.exists() or item.id in state.scan(self.cfg, self.log).known():
            raise ValueError(f"approve: {item.id} already exists (queued, running or done); "
                             "give the proposal a new number")
        dest.parent.mkdir(parents=True, exist_ok=True)
        retry_io(os.rename, src, dest)     # refuses to overwrite on Windows; checked above anyway
        self._forget(item.id)
        self.log.append("approve", item=item.id, repo=repo, paths=[rel, self.cfg.rel(dest)],
                        **{"from": f"proposed/{src.name}"})

    def _cancel(self, ref: str, rel: str) -> None:
        prefix, slash, name_ref = ref.rpartition("/")
        if not slash or prefix == "proposed":
            found = items.find(self.cfg.path("proposed"), name_ref)
            if found or prefix == "proposed":
                src = self._one(found, "cancel", name_ref, "proposed/")
                repo = items.front_repo(src)
                self._cancel_file(src, "proposed", f"proposed/{src.stem}",
                                  repo if repo in self.cfg.repos else "proposed", rel)
                return
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"cancel: {prefix!r} is not a repo")
        repos = [prefix] if slash else list(self.cfg.repos)
        hits = [(r, p) for r in repos for p in items.find(self.cfg.path("queue", r), name_ref)]
        if not hits:
            for r in repos:
                if p := self._running_file(r, name_ref):
                    raise ValueError(f"cancel: {r}/{p.stem} is running; cancel never kills an executor")
            raise ValueError(f"cancel: no queued or proposed item {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"cancel: {ref!r} is ambiguous: {[f'{r}/{p.stem}' for r, p in hits]}")
        repo, src = hits[0]
        item_id = f"{repo}/{src.stem}"
        self._drop_stage(repo, src.stem, "the item was cancelled")
        self._cancel_file(src, "queue", item_id, repo, rel)
        self._forget(item_id)

    def _finished_item(self, verb: str, ref: str) -> tuple[str, state.View]:
        """The one finished item `ref` names (`<repo>/<NNN_name>`, `NNN`, ...), and the view."""
        prefix, slash, name_ref = ref.rpartition("/")
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"{verb}: {prefix!r} is not a repo")
        view = state.scan(self.cfg, self.log)
        repos = [prefix] if slash else list(self.cfg.repos)
        def named(ids) -> list[str]:
            return [i for i in ids if i.split("/", 1)[0] in repos and items.number_matches(i.split("/", 1)[1], name_ref)]

        hits = named(view.finishes)
        if not hits:
            if live := named([it.id for q in view.queued.values() for it in q] + list(view.running)):
                raise ValueError(f"{verb}: {live[0]} has not finished; only a finished item's report "
                                 "can be reviewed")
            raise ValueError(f"{verb}: no finished item {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"{verb}: {ref!r} is ambiguous: {sorted(hits)}")
        return hits[0], view

    def _reviewed(self, ref: str, rel: str) -> None:
        """Mark one finished item's report reviewed. Reviewed state is the event log's:
        an item is reviewed once a `reviewed` event names it, until its report changes
        (`report_changed`); the event keeps the hashes the change is checked against."""
        item_id, view = self._finished_item("reviewed", ref)
        if view.reviewed(item_id):
            prior = view.reviews.get(item_id)
            when = f"#{prior.id}, {clock(prior.ts)}" if prior else "a watch's report needs no review"
            self.log.append("warning", item=item_id, paths=[rel],
                            note=f"{item_id} was already reviewed ({when}); nothing changed")
            return
        fin = view.finishes[item_id]
        self.log.append("reviewed", item=item_id, paths=[rel, *fin.paths[:1]], finish=fin.id,
                        status=fin.detail.get("status"), by=self._by or "unknown",
                        hashes=retry_io(report_hashes, self.cfg, item_id))

    def check_reviewed_reports(self, view: state.View) -> None:
        """Log `report_changed` when a reviewed report or its audit notes no longer match the
        hashes its `reviewed` (or last `report_changed`) recorded: by hand, after review (a
        session resumed through Terminal can write to it). Files are hashed only when their
        size or mtime moved, so the daemon's scan stays cheap; the first call (at start) hashes all."""
        for item_id, rev in view.reviews.items():
            chg = view.changed.get(item_id)
            base = chg if chg is not None and chg.id > rev.id else rev
            if not isinstance(base.detail.get("hashes"), dict):
                continue                    # reviewed before hashes were recorded
            paths = review_paths(self.cfg, item_id)
            key = tuple(_stat(p) for p in paths.values())
            if self.report_stats.get(item_id) == key:
                continue
            try:
                now_hashes = retry_io(report_hashes, self.cfg, item_id)
            except OSError:
                continue                    # held or mid-save: never a change; looked at again next scan
            self.report_stats[item_id] = key
            if now_hashes == base.detail["hashes"]:
                continue
            landed = self.log.last("land", item=item_id)    # conductor's own stamp after a `land:`
            if landed and landed.id > base.id and landed.detail.get("hashes") == now_hashes:
                continue
            # What differs from the review, not just from the last change: the flag names it all.
            what = [k for k in now_hashes if now_hashes[k] != rev.detail["hashes"].get(k)]
            if not what:
                what = [k for k in now_hashes if now_hashes[k] != base.detail["hashes"].get(k)]
            # A session opened by hand since it finished, before or after the review, is the likely writer.
            opened = self.log.last("session_opened", item=item_id)
            fin = view.finishes.get(item_id)
            extra = ({"opened": opened.id, "opened_by": opened.detail.get("by")}
                     if opened and (fin is None or opened.id > fin.id) else {})
            self.log.append("report_changed", item=item_id, paths=[self.cfg.rel(paths[k]) for k in what],
                            files=what, reviewed=rev.id, hashes=now_hashes, **extra)

    def _session_opened(self, ref: str, rel: str) -> None:
        """`session_opened: <item>`: a finished item's session was opened by hand (the app's
        Terminal, `conductor open`), so a later change to its report can name it."""
        item_id, view = self._finished_item("session_opened", ref)
        self.log.append("session_opened", item=item_id, paths=[rel], session_id=view.finishes[item_id].detail.get("session_id"),
                        by=self._by or "unknown")

    def _review(self, value: str, rel: str) -> None:
        """`review.md`: every unreviewed report inlined (and conductor's own past the planner's
        marker) for `all`, or one finished item's. Marks nothing reviewed."""
        if value.lower() == "all":
            item_id, view = None, state.scan(self.cfg, self.log)
        else:
            item_id, view = self._finished_item("review", value)
        bundle = review_mod.render(self.cfg, view, rel, item_id)
        write_text(self.cfg.path("review.md"), bundle.text)
        self.log.append("review", item=item_id, paths=[rel, "review.md"], which=item_id or "all",
                        items=bundle.items, conductor=bundle.conductor)

    def _checkpoint(self, label: str, rel: str, **extra) -> Event:
        """A session boundary: its event id is its number. Every report still unreviewed
        gets a `warning` naming it, so the new session raises it first."""
        cp = self.log.append("checkpoint", paths=[rel], label=label, by=self._by or "unknown", **extra)
        for fin in state.scan(self.cfg, self.log).unreviewed():
            self.log.append("warning", item=fin.item, paths=fin.paths[:1], checkpoint=cp.id, finish=fin.id,
                            note=f"{fin.item} is still unreviewed at checkpoint #{cp.id} (finished "
                                 f"{fin.detail.get('status')} {clock(fin.ts)}); it carries forward: raise it first")
        return cp

    def _ack(self, ids: list[int], rel: str) -> None:
        """`ack: invalid [#N ...]`: the rejected signals named (without ids, every live one)
        are dealt with, so they drop from the app's header, the inbox and the next digest; a
        checkpoint clears them anyway. They stay in Events."""
        live = [e.id for e in live_invalids(self.log)]
        if stale := [i for i in ids if i not in live]:
            raise ValueError(f"ack: {', '.join(f'#{i}' for i in stale)}: not a rejected signal still unacknowledged "
                             "since the latest checkpoint")
        self.log.append("ack", paths=[rel], what="invalid", events=sorted(ids or live), by=self._by or "unknown")

    def _session(self, label: str, rel: str) -> None:
        """A planner session's start in one signal: a checkpoint with the label, then
        `session.md` (the inbox since the read mark, conductor's reports past the planner's
        marker, each repo's maintenance facts), then the read mark moved past it all."""
        cp = self._checkpoint(label, rel, session="session.md")
        top = self.log.max_id()
        view = state.scan(self.cfg, self.log)
        write_text(self.cfg.path("session.md"), session_mod.render(self.cfg, self.log, view, cp, rel, top))
        self.log.append("read", paths=[rel, "session.md"], through=max(top, self.log.read_mark()), session=cp.id)

    def _tell(self, value: str, rel: str, **extra) -> None:
        """A message for a running executor: logged `tell`, then left in its mailbox for
        the PostToolUse hook to deliver, or, while it waits on its own question, given as
        that question's answer (`daemon_messages._message`). An `outline:` signal is a tell
        of the fixed request, marked `outline_request`."""
        ref, text = value.split(None, 1)
        run = self._running_target(ref, "outline" if extra.get("outline_request") else "tell")
        self._message(run, text, rel, **extra)

    def _audit(self, ref: str, rel: str) -> None:
        """`audit: <item>` on a running item: conductor's own *Audit notes* text, the one
        `audit: true` gives at launch, sent as a tell; its run record gains `audit`, so its
        finish lists the notes for the next cleanup as if it had been asked at launch."""
        run = self._running_target(ref, "audit")
        if audits.writes_notes(run.rec):
            raise ValueError(f"audit: {run.item_id} already keeps audit notes")
        notes = executor.RunPaths.for_name(self.cfg, run.rec["repo"], run.rec["name"]).audit
        text = AUDIT_LATE + audits.fill(audits.template_text("audit"), notes_path=str(notes.resolve()))
        run.rec["audit"] = True
        self._save(run)
        self._tell(f"{run.item_id} {text}", rel, audit=True)

    def _running_target(self, ref: str, verb: str) -> Run:
        """The running executor `ref` names, or why a message can't reach it."""
        prefix, slash, name_ref = ref.rpartition("/")
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"{verb}: {prefix!r} is not a repo")
        repos = [prefix] if slash else list(self.cfg.repos)
        hits = [f"{r}/{p.stem}" for r in repos if (p := self._running_file(r, name_ref))]
        if len(hits) > 1:
            raise ValueError(f"{verb}: {ref!r} is ambiguous: {hits}")
        run = self.runs.get(hits[0]) if hits else None
        if run is not None and run.rec.get("phase") == "setup":
            raise ValueError(f"{verb}: {run.item_id} is still in setup; a message reaches only a running executor "
                             "(an amendment waits for its prompt)")
        if run is None or run.rec.get("phase", "exec") != "exec" or run.exited():
            view = state.scan(self.cfg, self.log)
            ids = [i for i in [*view.finishes, *view.running, *(it.id for q in view.queued.values() for it in q)]
                   if i.split("/", 1)[0] in repos and items.number_matches(i.split("/", 1)[1], name_ref)]
            if not ids:
                raise ValueError(f"{verb}: no running item {ref!r}")
            started = ids[0] in view.finishes or ids[0] in view.running
            raise ValueError(f"{verb}: {ids[0]} " + ("has exited; a message reaches only a running executor"
                                                   if started else "has not started; a message reaches only a running executor"))
        return run

    def _cancel_file(self, src: Path, origin: str, item_id: str, repo: str, rel: str) -> None:
        cdir = self.cfg.state_dir / "cancelled"
        cdir.mkdir(parents=True, exist_ok=True)
        base = f"{time.strftime('%Y%m%d-%H%M%S')}__{repo if origin == 'queue' else 'proposed'}__{src.stem}"
        dest, n = cdir / f"{base}.md", 2
        while dest.exists():
            dest, n = cdir / f"{base}-{n}.md", n + 1
        retry_io(os.replace, src, dest)
        kept = self.cfg.rel(dest)
        self.log.append("cancel", item=item_id, repo=repo, paths=[rel, kept], kept=kept, **{"from": origin})

    def _drop_stage(self, repo: str, name: str, reason: str) -> None:
        """Discard the stage of queued `repo/name`, if it has one (its process tree is killed)."""
        item_id = f"{repo}/{name}"
        run = self.stages.get(item_id)
        if run is None:
            spath = self.cfg.path("running", repo, f"{name}.stage.json")
            if not spath.is_file():
                return
            rec = state.read_run(spath)
            run = self._stage_run(rec)
            if procs.alive(rec.get("pid"), rec.get("create_time")):
                procs.kill_tree(rec["pid"])
        self._discard_stage(run, reason)

    def _reorder(self, where: str, refs: list[str], rel: str) -> None:
        if self._reorder_journal.is_file():
            raise ValueError("reorder: an earlier reorder is still being finished (.conductor/reorder.json); "
                             "send this one again once it is logged")
        folder = self.cfg.path("proposed") if where == "proposed" else self.cfg.path("queue", where)
        label = "proposed/" if where == "proposed" else f"queue/{where}/"
        chosen: list[Path] = []
        for ref in refs:
            found = items.find(folder, ref)
            if not found and where != "proposed" and (p := self._running_file(where, ref)):
                raise ValueError(f"reorder: {where}/{p.stem} is running")
            src = self._one(found, "reorder", ref, label)
            if src in chosen:
                raise ValueError(f"reorder: {src.stem} is listed twice")
            if not items.NAME_RE.match(src.stem) or items.WATCH_RE.match(src.stem):
                raise ValueError(f"reorder: {src.name} is not named NNN_name.md")
            chosen.append(src)
        listed = [p.stem for p in chosen]
        new = items.renumber(listed)
        pairs = [(o, n) for o, n in zip(listed, new) if o != n]
        unlisted = {p.stem for p in items.item_files(folder)} - set(listed)
        if clash := sorted(n for _, n in pairs if n in unlisted):
            raise ValueError(f"reorder: the new name(s) {clash} are taken by unlisted items")
        if where != "proposed":
            # A queue id is never reused: not one that ran, nor one running now.
            known = set(state.scan(self.cfg, self.log).known())
            if used := sorted(n for _, n in pairs if f"{where}/{n}" in known and n not in listed):
                raise ValueError(f"reorder: the new name(s) {used} are ids of items that ran or are running "
                                 f"in {where}; a re-run needs a new item number")
            # A renumbered item must still come after what it depends on in its own queue:
            # items start strictly in NNN order, so a dependency behind it would never run.
            stays = [f"{where}/{n}" for n in sorted(unlisted | {o for o in listed if o not in dict(pairs)})]
            for o, n in pairs:
                for ref in items.parse(folder / f"{o}.md", where).depends_on:
                    target = items.resolve_ref(ref, where, stays)
                    if target and int(target.split("/", 1)[1].split("_", 1)[0]) >= int(n.split("_", 1)[0]):
                        raise ValueError(f"reorder: {o} has depends_on {ref!r} ({target}); as {n} it would come "
                                         "before it in its own queue and hold forever")
        # A renumbered item is a new id: any depends_on naming the old one would dangle.
        def item_id(name: str) -> str | None:
            repo = where if where != "proposed" else items.front_repo(folder / f"{name}.md")
            return f"{repo}/{name}" if repo else None
        renamed = [i for o, _ in pairs if (i := item_id(o))]
        dependents = [items.parse(p, r) for r in self.cfg.repos for p in items.item_files(self.cfg.path("queue", r))]
        for p in items.item_files(self.cfg.path("proposed")):
            if (r := items.front_repo(p)) is not None:
                dependents.append(items.parse(p, r))
        for d in dependents:
            for ref in d.depends_on:
                if target := items.resolve_ref(ref, d.repo, renamed):
                    raise ValueError(f"reorder: {d.path.stem} has depends_on {ref!r}, which names {target}; "
                                     "renumbering it would break the dependency")
            if d.resume_of in renamed:
                raise ValueError(f"reorder: {d.path.stem} has resume {d.resume_of!r}; renumbering it would "
                                 "leave the resume naming an item that never finishes")
        if where != "proposed":
            for o, _ in pairs:
                self._drop_stage(where, o, "the item was renumbered by a reorder")
        before = sorted(listed, key=lambda n: (int(n.split("_", 1)[0]), n))
        after = sorted(new, key=lambda n: (int(n.split("_", 1)[0]), n))
        journal = {"where": where, "pairs": pairs, "before": before, "after": after, "rel": rel,
                   **({"by": self._by} if self._by else {})}
        write_text(self._reorder_journal, json.dumps(journal, indent=1))
        try:
            self._roll_reorder(journal)
        except OSError as exc:
            # A rename failed part-way (a file held open). The journal stays: every tick
            # rolls it forward until it completes, and `where` launches nothing meanwhile.
            self._reorder_stuck = repr(exc)
            self.log.append("warning", repo=where, paths=[rel],
                            note=f"reorder of {where} interrupted: {exc!r}; it is finished on a later tick "
                                 f"(nothing in {where} launches until then)")

    @property
    def _reorder_journal(self) -> Path:
        return self.cfg.state_dir / "reorder.json"

    def _reorder_folder(self, where: str) -> Path:
        return self.cfg.path("proposed") if where == "proposed" else self.cfg.path("queue", where)

    def _retry_reorder(self) -> None:
        """Roll forward a reorder an earlier tick could not finish (see `_reorder`)."""
        if not self._reorder_journal.is_file():
            return
        try:
            j = json.loads(self._reorder_journal.read_text(encoding="utf-8"))
            self._roll_reorder(j, recovered=True, note=f"completed on a later tick (first try: {self._reorder_stuck})")
            self._reorder_stuck = None
        except OSError:
            pass                                    # still held open: next tick
        except (ValueError, KeyError, TypeError) as exc:
            bad = self._reorder_journal.with_name("reorder.json.bad")
            os.replace(self._reorder_journal, bad)
            self.log.append("warning", note=f"unreadable reorder journal set aside as {self.cfg.rel(bad)}: {exc!r}; "
                                            "any `*.reorder~*` temp names are restored at the next start")

    def _reorder_pending(self) -> str | None:
        """The queue (repo or `proposed`) of a reorder not finished yet."""
        if not self._reorder_journal.is_file():
            return None
        try:
            return json.loads(self._reorder_journal.read_text(encoding="utf-8")).get("where")
        except (OSError, ValueError, AttributeError):
            return None

    def _roll_reorder(self, j: dict, recovered: bool = False, note: str = "completed after a restart") -> None:
        """Carry out a journalled reorder: every `old.md` → temp, then every temp →
        `new.md`; then log it and drop the journal. Repeatable after a crash at any point."""
        folder = self._reorder_folder(j["where"])
        moved, stuck = False, []
        temp = items.REORDER_TEMP
        # The item and every amend file still waiting beside it (`o.amend.x.md` → `n.amend.x.md`)
        # move together: `<old stem><temp><new stem>`, then `<new stem>.md`.
        for o, n in j["pairs"]:
            for src in [folder / f"{o}.md", *sorted(folder.glob(f"{o}.amend*.md"))]:
                if src.stem != o and (items.amend_of(src) or ("",))[0] != o:
                    continue
                tmp = folder / f"{src.stem}{temp}{n}{src.stem[len(o):]}"
                if src.exists() and not tmp.exists():
                    retry_io(os.rename, src, tmp)
                    moved = True
        for o, n in j["pairs"]:
            for tmp in [folder / f"{o}{temp}{n}", *sorted(folder.glob(f"{o}.amend*{temp}*"))]:
                dst = folder / f"{tmp.name.partition(temp)[2]}.md"
                if tmp.exists():
                    if dst.exists():
                        stuck.append(tmp.name)
                        continue
                    retry_io(os.rename, tmp, dst)
                    moved = True
        if j["where"] != "proposed":
            for o, n in j["pairs"]:
                self._forget(f"{j['where']}/{o}")
                talk.move_mail(self.cfg.control, f"{j['where']}/{o}", f"{j['where']}/{n}")
        last = self.log.last("reorder", repo=j["where"])
        repeat = (recovered and not moved and last is not None
                  and last.detail.get("before") == j["before"] and last.detail.get("after") == j["after"])
        if not repeat:
            extra = {"by": j["by"]} if j.get("by") else {}
            if recovered:
                extra["note"] = note
            self.log.append("reorder", repo=j["where"], paths=[j["rel"]], before=j["before"], after=j["after"],
                            **extra)
        if stuck:
            self.log.append("warning", repo=j["where"],
                            note=f"reorder left {stuck} in {self.cfg.rel(folder)}: the target name is taken; "
                                 "rename them by hand")
        self._reorder_journal.unlink(missing_ok=True)

    def _recover_reorder(self) -> None:
        """Finish a reorder a crash interrupted; temp names without a journal go to the
        name they carry (or back to their old name if that is taken)."""
        if self._reorder_journal.is_file():
            # Its signal file, if it never finished, is set aside by _recover_signal.
            try:
                j = json.loads(self._reorder_journal.read_text(encoding="utf-8"))
                self._roll_reorder(j, recovered=True)
            except (ValueError, KeyError, TypeError, OSError) as exc:
                self.log.append("warning", note=f"could not finish the interrupted reorder in "
                                                f"{self.cfg.rel(self._reorder_journal)}: {exc!r}; retried every tick")
                self._reorder_stuck = repr(exc)
                return                  # its temp names belong to the journal
        folders = [self.cfg.path("proposed")] + [self.cfg.path("queue", r) for r in self.cfg.repos]
        for folder in folders:
            for tmp in sorted(folder.glob(f"*{items.REORDER_TEMP}*")) if folder.is_dir() else []:
                old, _, new = tmp.name.partition(items.REORDER_TEMP)
                for name in (new, old):
                    if not (folder / f"{name}.md").exists():
                        retry_io(os.rename, tmp, folder / f"{name}.md")
                        self.log.append("warning", note=f"reorder temp {self.cfg.rel(tmp)} restored as {name}.md")
                        break
