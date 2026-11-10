"""Prompt 86: planner notes of 2026-11-07 to 11-08 (aicomplete). `wait` and `ask` returns
carry the clock too (aicomplete 143 froze early believing 1 h was left of 2h40m); a repo's
`doc_budget_exempt` globs keep generated docs off the digest's doc trim; the preambles'
freeze line, full test suite beside a sim and helpers' `git checkout` lines."""

import io
import re
import threading
from datetime import timedelta

import pytest

from conductor import ask_server, config, questions
from conductor.config import RepoOptions
from conductor.util import now, stamp
from conftest import git
from test_doc_facts import facts
from test_lean import record
from test_preamble import SHORT, render

CLOCK = r"Now \d{1,2}:\d\d[ap]m, 2h09m left to the cap\."


def _server(env) -> ask_server.Server:
    return ask_server.Server(env.control, "code/001_a", 10, None, out=io.StringIO(), workdir=env.control)


# ------------------------------------------------------------------ the clock on every return
def test_wait_returns_carry_the_clock_and_the_time_left(env):
    record(env, "001_a", phase="exec", deadline=stamp(now() + timedelta(hours=2, minutes=9, seconds=30)))
    text, err = _server(env).wait({"minutes": 0.001}, threading.Event())
    assert not err and text.startswith("No message yet.") and re.search(CLOCK + "$", text)


def test_an_answered_ask_carries_the_clock_on_its_own_line(env):
    record(env, "001_a", phase="exec", deadline=stamp(now() + timedelta(hours=2, minutes=9, seconds=30)))
    q = questions.create(env.control, "code/001_a", "Go?", [], "yes", False, 10, None, False)
    questions.write_answer(q, "go")
    text, err = _server(env).ask({"resume": q.id}, threading.Event())
    assert not err and re.fullmatch(rf"Answer to {re.escape(q.id)}:\ngo\n{CLOCK}", text)


def test_a_watch_gets_the_clock_without_a_cap(env):
    record(env, "001_a", phase="exec", template="watch", deadline=stamp(now() + timedelta(hours=1)))
    text, _ = _server(env).wait({"minutes": 0.001}, threading.Event())
    assert re.search(r" Now \d{1,2}:\d\d[ap]m\.$", text) and "left to the cap" not in text


# ------------------------------------------------------------------ doc-trim exemptions
def test_a_repos_exempt_docs_are_never_due(env):
    """aicomplete's adversary cards are generated but say "written by", so the header check
    misses them; its CLAUDE.md exempts them from the 15 KB budget."""
    (env.repo / "docs").mkdir()
    for name in ("adversary-cards.md", "pricing.md"):
        (env.repo / "docs" / name).write_text("x" * 20 * 1024, encoding="utf-8")
    git(env.repo, "add", "docs")
    git(env.repo, "commit", "-qm", "docs")
    due = lambda: [ln for ln in facts(env) if "**due**" in ln]
    assert len(due()) == 2
    env.d.cfg.repo_opts["code"] = RepoOptions(doc_budget_exempt=["docs/adversary-cards.md"])
    assert [ln for ln in due() if "doc trim" in ln] == [
        "- **due** doc trim: `docs/pricing.md` 20.0 KB, budget 15 KB, +0.0 KB since the last digest"]


def test_doc_budget_exempt_takes_globs_inside_the_repo():
    opts = config._repo_options("code", {"path": "x", "doc_budget_exempt": ["docs/*cards.md", "docs/ops.md"]})
    assert opts.doc_budget_exempt == ["docs/*cards.md", "docs/ops.md"]
    assert config._repo_options("code", {"path": "x", "doc_budget_exempt": "docs/ops.md"}).doc_budget_exempt == [
        "docs/ops.md"]
    with pytest.raises(config.ConfigError, match="doc_budget_exempt"):
        config._repo_options("code", {"path": "x", "doc_budget_exempt": ["../other/docs.md"]})


# ------------------------------------------------------------------ preamble lines
def test_the_preambles_carry_the_new_lines_inside_their_caps(env):
    full = " ".join(render(env, SHORT * 3).split())
    assert "Run a full test suite (`cargo test --workspace`) only between sims, never beside one" in full
    assert "Helpers never `git checkout` or `git restore` a shared folder" in full
    assert "every conductor tool return gives the clock and time left" in full
    calibrating = " ".join(render(env, SHORT * 3 + " Freeze the band at 45-85.").split())
    assert "posts a freeze line as progress before its gate: every done-when line, met or unmet" in calibrating
    light = render(env, SHORT)
    assert "a full suite only between sims" in " ".join(light.split()) and len(light.split()) <= 430
