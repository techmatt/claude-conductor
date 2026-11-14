"""Desktop toasts from the event log (`notify.Toasts`; `docs/inbox.md`, *Toasts*)."""

from __future__ import annotations

from . import items, notify
from .events import Event


class ToastsMixin:
    def _init_toasts(self) -> None:
        self.toasts = notify.Toasts(self.cfg.project, self.notifier, self._queue_state, short=len(self.cfg.repos) == 1)

    def _toast_event(self, ev: Event) -> None:
        if self.cfg.toasts and self.notifier:
            self.toasts.add(ev.kind, ev.item, ev.repo, ev.detail, ev.id)

    def flush_toasts(self, force: bool = False) -> None:
        """At each tick's end and at stop. A toast never stops the daemon."""
        self.toasts.send = self.notifier        # tests swap the notifier on a live daemon
        ids = ",".join(str(n.ev_id) for _, n in self.toasts.pending)
        try:
            self.toasts.flush(force)
        except Exception as exc:     # noqa: BLE001
            self.log.append("warning", note=f"toast for #{ids} failed: {exc!r}")

    def _queue_state(self, repo: str | None, since_id: int) -> str:
        """Where `repo`'s queue stands after the finish `since_id`: what started since,
        else what runs, else what is next, else empty."""
        if not repo:
            return ""
        short = lambda i: i.split("/", 1)[1] if len(self.cfg.repos) == 1 and "/" in i else i     # noqa: E731
        running = sorted(i for i, r in self.runs.items() if r.rec.get("repo") == repo)
        started = [i for i in running if (e := self.log.last("start", item=i)) and e.id > since_id
                   or (s := self.log.last("setup", item=i)) and s.id > since_id]
        if started:
            return f"{', '.join(short(i) for i in started)} started"
        if running:
            return f"{', '.join(short(i) for i in running)} running"
        queued = items.item_files(self.cfg.path("queue", repo))
        if queued:
            more = f" (+{len(queued) - 1})" if len(queued) > 1 else ""
            head = f"{repo}/{queued[0].stem}"
            return f"{short(head)} next{' (held)' if head in self.holds else ''}{more}"
        return "queue now empty"
