"""Desktop notifications. Windows toasts through WinRT (`windows-toasts`); elsewhere none.

A toast is best effort: the daemon logs a failure as a `warning` and carries on.
"""

from __future__ import annotations

import sys

APP_TEXT = "Conductor"


class Toaster:
    def __init__(self):
        self._toaster = None

    def __call__(self, title: str, body: str, tag: str) -> None:
        if sys.platform != "win32":
            raise RuntimeError("desktop toasts are implemented for Windows only")
        from windows_toasts import InteractableWindowsToaster, Toast
        if self._toaster is None:
            # The default AUMID (Command Prompt's) is registered on every Windows box, so the
            # toast shows without installing a shortcut; "Conductor" is the attribution line.
            self._toaster = InteractableWindowsToaster(APP_TEXT)
        # The tag (`<project>#<event id>`) is what the Action Center history records.
        toast = Toast([title, body], group="conductor")
        toast.tag = tag[:64]
        self._toaster.show_toast(toast)


def message(project: str, kind: str, item: str | None, repo: str | None, detail: dict) -> tuple[str, str] | None:
    """(title, body) for the event kinds that toast, else None. Each names project and item."""
    who = item or repo or ""
    match kind:
        case "ask":
            how = "blocking" if detail.get("block") else f"default at {detail.get('deadline_clock')}"
            return f"{project}: question from {who}", f"({how}) {detail.get('text', '')}"[:250]
        case "red":
            return f"{project}: {who} is red", "; ".join(detail.get("reasons", []))[:250]
        case "tamper":
            who = who or ", ".join(detail.get("concurrent", [])) or "an executor"
            return (f"{project}: {who} changed the control folder",
                    "Reverted. " + "; ".join(detail.get("changes", []))[:200])
        case "hold":
            return f"{project}: {who} held", str(detail.get("reason", ""))[:250]
        case "run_failed":
            return f"{project}: `{detail.get('target')}` did not come up", str(detail.get("error", ""))[:250]
        case "pick_failed":
            what = f"`{detail['name']}`" if detail.get("name") else str(detail.get("manifest", ""))
            return f"{project}: picker {what} did not open", str(detail.get("error", ""))[:250]
        case "amend_refused":
            return f"{project}: amendment to {who} refused", str(detail.get("reason", ""))[:250]
        case "memory" if detail.get("level") != "ok":
            return (f"{project}: memory pressure",
                    f"{detail.get('free')} GiB free, {detail.get('percent')}% committed. {detail.get('action', '')}"[:250])
        case "memory_critical":
            return (f"{project}: memory CRITICAL",
                    f"{detail.get('free')} GiB free, {detail.get('percent')}% committed. {detail.get('action', '')}"[:250])
        case "daemon_died":
            return (f"{project}: the daemon had died",
                    f"Last heartbeat {detail.get('last_beat_clock', '?')}; nothing ran for {detail.get('gap')}.")
        case "notify":
            return f"{project}: from the {detail.get('by') or 'planner'}", str(detail.get("text", ""))[:250]
        case "queue_empty":
            return f"{project}: queue `{repo}` is empty", f"last item {detail.get('last')}"
    return None
