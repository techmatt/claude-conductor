"""Which Claude model a new Claude Code session conductor launches runs on: one setting for
the whole machine, `opus` or `fable`, read at each launch by every project's daemon
(docs/executors.md, *Model*).

    ~/.conductor/model.json    {"model": "fable", "at": "...", "by": "app"}

$CONDUCTOR_MODEL_FILE names another file (the test suite's). No file, or one that can't be
read, is `opus`. A launch records the full id it ran with (`rec["model"]`), and a resume of
that session (a stage resumed at launch, an API wait, a machine restart, an `ask` fork) passes
the recorded id again, so only brand-new sessions take a change.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .util import stamp

# The one place to change when a new model ships. Full ids, not Claude Code's own aliases
# (`opus`, `fable` resolve to the latest too): the id a run records then says exactly what ran.
MODELS = {"opus": "claude-opus-5-5", "fable": "claude-fable-5-2"}
DEFAULT = "opus"
LABELS = {"opus": "Opus", "fable": "Fable"}
ENV = "CONDUCTOR_MODEL_FILE"


def path() -> Path:
    return Path(os.environ.get(ENV) or Path.home() / ".conductor" / "model.json")


def current() -> str:
    """The setting's name, `opus` or `fable`."""
    try:
        name = json.loads(path().read_text(encoding="utf-8")).get("model")
    except (OSError, ValueError, AttributeError):
        return DEFAULT
    return name if name in MODELS else DEFAULT


def current_id() -> str:
    return MODELS[current()]


def name_of(model_id: str | None) -> str | None:
    """`claude-fable-5-2` → `fable`; an id no longer in the table → the id itself."""
    if not model_id:
        return None
    return next((n for n, i in MODELS.items() if i == model_id), model_id)


def describe(name: str | None = None) -> str:
    """`Fable (claude-fable-5-2)`."""
    name = name or current()
    return f"{LABELS.get(name, name)} ({MODELS.get(name, name)})"


def set_model(name: str, by: str) -> str:
    """Make `name` the setting; returns what it was. ValueError for a name not in MODELS."""
    name = name.strip().lower()
    if name not in MODELS:
        raise ValueError(f"model: {name!r} is not a model")
    was = current()
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"model": name, "at": stamp(), "by": by}, indent=1) + "\n",
                   encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    return was
