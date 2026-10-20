"""The daemon's signal files: parsing, the verbs, and reorder's rename journal."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from . import items, procs, state, talk
from . import session as session_mod
from .events import Event
from .util import clock, read_text, retry_io, write_text


SIGNAL_SETTLE_SECONDS = 1.0     # a signal or amend file younger than this may still be being written


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


class SignalsMixin:
    def process_signals(self) -> None:
        sdir = self.cfg.path("signals")
        if not sdir.is_dir():
            return
        for f in sorted(p for p in sdir.iterdir() if p.is_file()):
            if f.suffix in (".bad", ".tmp") or time.time() - f.stat().st_mtime < SIGNAL_SETTLE_SECONDS:
                continue
            rel = f"signals/{f.name}"
            by, applied = None, []
            try:
                text = read_text(f)
                by = _signal_by(text)
                lines = self._parse_signal(text)        # every line checked before any applies
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
            value = value.strip() if key in ("tell", "checkpoint", "session") else _strip_comment(value)
            if not sep or not key:
                raise ValueError(f"line {n}: expected `key: value`, got {s!r}")
            if key == "by":
                continue
            if key == "read":
                if value.lower() not in ("latest", "all") and not value.lstrip("#").isdigit():
                    raise ValueError(f"line {n}: read: expects an event id or `latest`, got {value!r}")
            elif key == "clear":
                if value not in self.cfg.repos:
                    raise ValueError(f"line {n}: clear: {value!r} is not a repo")
            elif key in ("approve", "cancel", "reviewed", "outline"):
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
            elif key == "stop":
                if value.lower() != "daemon":
                    raise ValueError(f"line {n}: stop: only `stop: daemon` is known, got {value!r}")
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
        elif key == "approve":
            self._approve(value, rel)
        elif key == "cancel":
            self._cancel(value, rel)
        elif key == "reviewed":
            self._reviewed(value, rel)
        elif key == "tell":
            self._tell(value, rel)
        elif key == "outline":
            self._tell(f"{value} {talk.OUTLINE_REQUEST}", rel, outline_request=True)
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
        elif key == "stop":
            self.stop_requested = {"paths": [rel], **({"by": self._by} if self._by else {})}

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

    def _reviewed(self, ref: str, rel: str) -> None:
        """Mark one finished item's report reviewed. Reviewed state is the event log's:
        an item is reviewed once a `reviewed` event names it."""
        prefix, slash, name_ref = ref.rpartition("/")
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"reviewed: {prefix!r} is not a repo")
        view = state.scan(self.cfg, self.log)
        repos = [prefix] if slash else list(self.cfg.repos)
        def named(ids) -> list[str]:
            return [i for i in ids if i.split("/", 1)[0] in repos and items.number_matches(i.split("/", 1)[1], name_ref)]

        hits = named(view.finishes)
        if not hits:
            if live := named([it.id for q in view.queued.values() for it in q] + list(view.running)):
                raise ValueError(f"reviewed: {live[0]} has not finished; only a finished item's report "
                                 "can be reviewed")
            raise ValueError(f"reviewed: no finished item {ref!r}")
        if len(hits) > 1:
            raise ValueError(f"reviewed: {ref!r} is ambiguous: {sorted(hits)}")
        item_id = hits[0]
        if prior := view.reviews.get(item_id):
            self.log.append("warning", item=item_id, paths=[rel],
                            note=f"{item_id} was already reviewed (#{prior.id}, {clock(prior.ts)}); nothing changed")
            return
        fin = view.finishes[item_id]
        self.log.append("reviewed", item=item_id, paths=[rel, *fin.paths[:1]], finish=fin.id,
                        status=fin.detail.get("status"), by=self._by or "unknown")

    def _checkpoint(self, label: str, rel: str, **extra) -> Event:
        """A session boundary: its event id is its number. Every report still unreviewed
        gets a `warning` naming it, so the new session raises it first."""
        cp = self.log.append("checkpoint", paths=[rel], label=label, by=self._by or "unknown", **extra)
        for fin in state.scan(self.cfg, self.log).unreviewed():
            self.log.append("warning", item=fin.item, paths=fin.paths[:1], checkpoint=cp.id, finish=fin.id,
                            note=f"{fin.item} is still unreviewed at checkpoint #{cp.id} (finished "
                                 f"{fin.detail.get('status')} {clock(fin.ts)}); it carries forward: raise it first")
        return cp

    def _session(self, label: str, rel: str) -> None:
        """A planner session's start in one signal: a checkpoint with the label, then
        `session.md` (the inbox since the read mark, conductor's reports past HANDOFF's
        marker, each repo's maintenance facts), then the read mark moved past it all."""
        cp = self._checkpoint(label, rel, session="session.md")
        top = self.log.max_id()
        view = state.scan(self.cfg, self.log)
        write_text(self.cfg.path("session.md"), session_mod.render(self.cfg, self.log, view, cp, rel, top))
        self.log.append("read", paths=[rel, "session.md"], through=max(top, self.log.read_mark()), session=cp.id)

    def _tell(self, value: str, rel: str, **extra) -> None:
        """A message for a running executor: logged `tell`, then left in its mailbox for
        the PostToolUse hook (or its next `progress` call) to deliver. An `outline:`
        signal is a tell of the fixed request, marked `outline_request`."""
        ref, text = value.split(None, 1)
        verb = "outline" if extra.get("outline_request") else "tell"
        prefix, slash, name_ref = ref.rpartition("/")
        if slash and prefix not in self.cfg.repos:
            raise ValueError(f"{verb}: {prefix!r} is not a repo")
        repos = [prefix] if slash else list(self.cfg.repos)
        hits = [f"{r}/{p.stem}" for r in repos if (p := self._running_file(r, name_ref))]
        if len(hits) > 1:
            raise ValueError(f"{verb}: {ref!r} is ambiguous: {hits}")
        run = self.runs.get(hits[0]) if hits else None
        if run is None or run.rec.get("phase", "exec") != "exec" or run.exited():
            view = state.scan(self.cfg, self.log)
            ids = [i for i in [*view.finishes, *view.running, *(it.id for q in view.queued.values() for it in q)]
                   if i.split("/", 1)[0] in repos and items.number_matches(i.split("/", 1)[1], name_ref)]
            if not ids:
                raise ValueError(f"{verb}: no running item {ref!r}")
            started = ids[0] in view.finishes or ids[0] in view.running
            raise ValueError(f"{verb}: {ids[0]} " + ("has exited; a message reaches only a running executor"
                                                   if started else "has not started; a message reaches only a running executor"))
        item_id = hits[0]
        ev = self.log.append("tell", item=item_id, paths=[rel], text=text, by=self._by or "unknown", **extra)
        talk.post(self.cfg.control, item_id, ev.id, text, self._by)

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
            if not items.NAME_RE.match(src.stem):
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
        for o, n in j["pairs"]:
            src, tmp = folder / f"{o}.md", folder / f"{o}{items.REORDER_TEMP}{n}"
            if src.exists() and not tmp.exists():
                retry_io(os.rename, src, tmp)
                moved = True
        for o, n in j["pairs"]:
            tmp, dst = folder / f"{o}{items.REORDER_TEMP}{n}", folder / f"{n}.md"
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
