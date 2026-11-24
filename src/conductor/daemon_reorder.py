"""`reorder:`: renumbering items in a queue or `proposed/` through a rename journal
(`.conductor/reorder.json`) that a later tick or a restart rolls forward."""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import items, state, talk
from .util import retry_io, write_text


class ReorderMixin:
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
