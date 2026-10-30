"""Questions: `questions/<repo>__<name>__<n>.md`, its answer and its resolution.

Three files per question, each with one writer:

- `<id>.md`           the ask server (front-matter + question and options)
- `<id>.answer.md`    the planner, or `conductor answer`
- `<id>.result.json`  whoever resolves it first: the ask server (answered, default,
                      cancelled) or the daemon (closed, when the item finishes first).
                      Created exclusively, so a question resolves exactly once.
- `<id>.away`         the ask server, while a message has handed its call back early
                      (the question still open, the executor working until `resume`).

The daemon only observes these files and logs `ask`, `answer`, `default`, `late` and
`question_closed`; the executor gets its answer even while the daemon is down.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from .util import FrontMatterError, read_text, split_front_matter, stamp

ANSWER_SETTLE_SECONDS = 1.0       # an answer file is read once it stops changing


@dataclass
class Question:
    id: str                       # `sandbox__001_x__1`
    path: Path
    item: str
    asked: str
    block: bool
    default: str | None
    deadline: str | None          # when the default is taken; None when blocking
    body: str                     # the question, then its options
    result: dict | None           # None while open

    @property
    def answer_path(self) -> Path:
        return self.path.with_name(f"{self.id}.answer.md")

    @property
    def result_path(self) -> Path:
        return self.path.with_name(f"{self.id}.result.json")

    @property
    def away_path(self) -> Path:
        """Present while a message has cut the `ask` call short: the executor is working, not waiting."""
        return self.path.with_name(f"{self.id}.away")

    @property
    def is_open(self) -> bool:
        return self.result is None

    def summary(self) -> str:
        return " ".join(self.body.split("\nOptions:")[0].split())


def qdir(control: Path) -> Path:
    return control / "questions"


def question_id(item: str, n: int) -> str:
    repo, _, name = item.partition("/")
    return f"{repo}__{name}__{n}"


def _result_name(qid: str) -> str:
    return f"{qid}.result.json"


def create(control: Path, item: str, question: str, options: list[str], default: str | None,
           block: bool, default_minutes: float, session_id: str | None = None) -> Question:
    """Write a new question file under the next free number for this item."""
    d = qdir(control)
    d.mkdir(parents=True, exist_ok=True)
    if default is None:
        block = True              # nothing to fall back on
    n = 1
    while True:
        qid = question_id(item, n)
        try:
            os.close(os.open(d / f"{qid}.md", os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            n += 1
    asked = time.time()
    meta = {
        "item": item, "asked": stamp(), "block": block, "default": default,
        "deadline": None if block else stamp_at(asked + default_minutes * 60),
        "session_id": session_id,
    }
    body = question.strip() + "\n"
    if options:
        body += "\nOptions:\n" + "".join(f"- {o}\n" for o in options)
    text = "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True) + "---\n" + body
    _write(d / f"{qid}.md", text)
    return load(d / f"{qid}.md")


def stamp_at(epoch: float) -> str:
    return stamp(datetime.fromtimestamp(epoch).astimezone())


def _write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)


def load(path: Path) -> Question | None:
    """A question file, or None while it is still being written or is malformed."""
    try:
        meta, body = split_front_matter(read_text(path))
    except (FrontMatterError, OSError, UnicodeDecodeError):
        return None
    if not meta.get("item") or not meta.get("asked"):
        return None
    qid = path.name.removesuffix(".md")
    result = None
    rpath = path.with_name(_result_name(qid))
    if rpath.is_file():
        try:
            result = json.loads(rpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            result = None
    return Question(
        id=qid, path=path, item=str(meta["item"]), asked=str(meta["asked"]),
        block=bool(meta.get("block")), default=None if meta.get("default") is None else str(meta["default"]),
        deadline=None if meta.get("deadline") is None else str(meta["deadline"]),
        body=body.strip() + "\n", result=result,
    )


def scan(control: Path) -> list[Question]:
    d = qdir(control)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("*.md")):
        if p.name.endswith(".answer.md"):
            continue
        if (q := load(p)) is not None:
            out.append(q)
    return sorted(out, key=lambda q: q.asked)


def find(control: Path, ref: str) -> Question | None:
    """`sandbox__001_x__1`, or any unambiguous suffix of it (`001_x__1`, `001__1`)."""
    qs = scan(control)
    exact = [q for q in qs if q.id == ref]
    if exact:
        return exact[0]
    def short(q: Question) -> set[str]:
        repo, rest = q.id.split("__", 1)
        name, n = rest.rsplit("__", 1)
        return {f"{name}__{n}", f"{name.split('_', 1)[0]}__{n}", f"{repo}__{name.split('_', 1)[0]}__{n}"}
    hits = [q for q in qs if ref in short(q)]
    return hits[0] if len(hits) == 1 else None


def read_answer(q: Question, settle: float = ANSWER_SETTLE_SECONDS) -> str | None:
    """The answer text once the file has stopped changing; front-matter, if any, is dropped."""
    p = q.answer_path
    try:
        if time.time() - p.stat().st_mtime < settle:
            return None
        text = read_text(p)
    except (OSError, UnicodeDecodeError):
        return None
    try:
        _, text = split_front_matter(text)
    except FrontMatterError:
        pass
    return text.strip() or None


def write_answer(q: Question, text: str, by: str | None = None) -> None:
    """`by` (the app writes `app`) goes in a front-matter line the executor never sees;
    the daemon puts it on the `answer` (or `late`) event."""
    head = "---\n" + yaml.safe_dump({"by": by}, allow_unicode=True) + "---\n" if by else ""
    _write(q.answer_path, head + text.strip() + "\n")


def answer_by(q: Question) -> str | None:
    """Who wrote the answer file, when its front-matter says (`by:`)."""
    try:
        meta, _ = split_front_matter(read_text(q.answer_path))
    except (FrontMatterError, OSError, UnicodeDecodeError):
        return None
    by = meta.get("by") if isinstance(meta, dict) else None
    return str(by) if by else None


def set_away(q: Question, away: bool) -> None:
    """The ask server's mark that its call handed back for a message (`away_path`)."""
    if away:
        _write(q.away_path, stamp() + "\n")
    else:
        q.away_path.unlink(missing_ok=True)


def resolve(q: Question, outcome: str, text: str | None, by: str, **extra) -> bool:
    """Record the resolution exactly once. False when something resolved it first."""
    tmp = q.path.with_name(f"{q.id}.result.{os.getpid()}.tmp")
    data = {"outcome": outcome, "text": text, "at": stamp(), "by": by, **extra}
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, indent=1))
    try:
        os.link(tmp, q.result_path)        # atomic, and fails if the result exists
        q.result = data
        q.away_path.unlink(missing_ok=True)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)
