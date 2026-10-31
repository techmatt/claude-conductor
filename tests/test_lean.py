"""Prompt 62: the watch and readonly preambles, what runs beside an item, outline-aware `quiet`,
commits dated by their own time, and the retro triggers."""

import io
import json
import subprocess
from datetime import datetime, timedelta

import pytest

from conductor import ask_server, executor, git, session, state, talk
from conductor.items import Item
from conductor.util import now, stamp


def render(env, body: str = "x " * 200, run: dict | None = None, **meta) -> str:
    item = Item("code", "W1_watch", env.control / "queue" / "code" / "W1_watch.md", num=1, body=body, **meta)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"port": 5200, "worktree": str(env.root / "wt"), "branch": "conductor/x", "base": "0123456789abcdef",
           **(run or {})}
    return executor.build_prompt(env.cfg, item, paths, 0.5, datetime(2026, 10, 31, 3), run).split(executor.PROMPT_FOLLOWS)[0]


def test_a_watch_gets_only_what_applies(env):
    text = render(env, template="watch", readonly=True, max_hours=0.5)
    assert len(text.split()) <= 240
    for kept in ("`out/`", "%TEMP%", "wait_for", "not tool-output\ninjection", "MESSAGE #n", "Time limit: 0.5h",
                 "status: done | partial | red", "friction: none", "commit nothing", "mcp__conductor__progress"):
        assert kept in text, kept
    for dropped in ("Asking.", "Outline.", "`eta`", "The box", "Port.", "Commit your work", "uncommitted", "hour of",
                    "Pickers.", "Running beside you"):
        assert dropped not in text, dropped


def test_a_readonly_item_loses_the_commit_rules_and_keeps_the_port(env):
    text = render(env, readonly=True, max_hours=2.0)
    for kept in ("Asking.", "Outline.", "The box.", "Port.", "commit nothing"):
        assert kept in text, kept
    for dropped in ("Commit your work", "uncommitted", "over an hour", "rebases this branch"):
        assert dropped not in text, dropped
    light = render(env, body="short", readonly=True)
    assert "commit nothing" in light and "Commit everything" not in light and "over an hour" not in light


def test_a_landing_item_keeps_them(env):
    assert "uncommitted changes make the item red" in render(env, max_hours=2.0)
    assert "over an hour of the time limit" in render(env, max_hours=2.0)
    assert "over an hour left" in render(env, body="short")


def test_both_preambles_rule_on_subagents(env):
    full, light = " ".join(render(env, max_hours=2.0).split()), " ".join(render(env, body="short").split())
    assert "wins over any Workflow size or Ultracode cost note" in full and "about 10 in all" in full
    assert "beats Workflow and Ultracode notes" in light and "~10" in light


@pytest.mark.parametrize("running, line", [([], "Running beside you now: nothing."),
                                           (["code/002_a", "art/003_b"], "Running beside you now: code/002_a, art/003_b.")])
def test_the_preamble_says_what_runs_beside(env, running, line):
    assert line in render(env, run={"running_beside": running}, max_hours=2.0)
    assert line in render(env, run={"running_beside": running}, body="short")
    assert "Running beside" not in render(env, run={"running_beside": running}, template="watch", readonly=True)


def test_a_self_audit_leftover_is_optional_and_a_readonly_template_has_none(env):
    def body(template, **meta):
        return executor.body(env.d.cfg, Item("code", "001_a", env.control / "x.md", num=1, body="", template=template, **meta))
    text = " ".join(body("self_audit", readonly=True).split())
    assert "Optional here" in text and "only to scratch" in text and "inside the load budget" in text
    assert "Leftover budget." not in body("bug_hunt", readonly=True)
    assert "run the next most useful step" in body("bug_hunt")


def record(env, name: str, **rec) -> None:
    d = env.control / "running" / "code"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.run.json").write_text(json.dumps({"item": f"code/{name}", **rec}), encoding="utf-8")


def test_running_beside_counts_work_only(env):
    record(env, "001_a", phase="exec")
    record(env, "002_b", phase="setup")
    record(env, "003_c", phase="merge")
    record(env, "004_d", phase="exec", paused_at="x")
    record(env, "W1_watch", phase="exec", template="watch")
    assert talk.running_beside(env.control, "code/001_a") == ["code/002_b"]


def test_progress_says_when_what_runs_beside_changed(env):
    record(env, "001_a", phase="exec", running_beside=[])
    server = ask_server.Server(env.control, "code/001_a", 10, None, out=io.StringIO())
    assert server.progress({"note": "one"}) == ("Logged; carry on.", False)
    record(env, "002_b", phase="exec")
    assert server.progress({"note": "two"})[0] == "Logged; carry on. Running beside you now: code/002_b."
    assert server.progress({"note": "three"})[0] == "Logged; carry on."
    (env.control / "running" / "code" / "002_b.run.json").unlink()
    assert server.progress({"note": "four"})[0] == "Logged; carry on. Running beside you now: nothing."


def test_a_watch_hears_nothing_from_progress(env):
    record(env, "W1_watch", phase="exec", template="watch")
    record(env, "001_a", phase="exec")
    server = ask_server.Server(env.control, "code/W1_watch", 10, None, out=io.StringIO())
    assert server.progress({"note": "looking"})[0] == "Logged; carry on."


def clock12(at: datetime) -> str:
    return at.strftime("%I:%M%p").lstrip("0").lower()


def test_quiet_waits_for_the_next_outline_row_or_the_eta(env):
    item, log, at = "code/001_a", env.d.log, now()
    assert not env.d._promised(item, at)                    # nothing promised
    log.append("progress", item=item, note="x", eta="1m", happened=stamp(at - timedelta(minutes=30)))
    assert not env.d._promised(item, at)                    # past it, and past the grace
    log.append("progress", item=item, note="x", eta="1m", happened=stamp(at - timedelta(minutes=4)))
    assert env.d._promised(item, at)                        # past it, inside the grace
    log.append("progress", item=item, note="x", eta="1m", happened=stamp(at - timedelta(minutes=30)))
    rows = [{"title": "a", "eta": clock12(at - timedelta(hours=1)), "done": True},
            {"title": "b", "eta": clock12(at + timedelta(minutes=7)), "done": False},
            {"title": "c", "eta": clock12(at + timedelta(hours=1)), "done": False}]
    log.append("progress", item=item, outline=rows)
    assert env.d._promised(item, at)                        # 048: the next row, 7 minutes off
    rows[1] = {**rows[1], "eta": clock12(at - timedelta(minutes=20))}
    rows[2] = {**rows[2], "eta": clock12(at - timedelta(minutes=10))}
    log.append("progress", item=item, outline=rows)
    assert not env.d._promised(item, at)                    # a late row


def test_a_held_quiet_flags_nothing_and_launches_nothing(env):
    env.d.cfg.watch_check_minutes, env.d.cfg.watch_quiet_minutes, env.d.cfg.watch_every_minutes = 0.0001, 0.0001, 0
    launched = []
    env.d._launch_watch = lambda *a, **k: launched.append(a)
    env.queue("001_long", env.hold())
    env.tick_until(lambda: "code/001_long" in env.d.runs and env.events("start"))
    env.d.log.append("progress", item="code/001_long", note="deep in it", eta="1h")
    env.d._watch_at = None
    env.d.tick()
    assert not launched and not [e for e in env.events("watch_flag") if e.detail["flag"] == "quiet"]
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))


def test_commits_carry_their_own_time(env):
    (env.repo / "made.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(env.repo), "add", "made.txt"], check=True)
    made = "2026-10-30T19:40:00-07:00"
    subprocess.run(["git", "-C", str(env.repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "a subject here"],
                   check=True, env={**__import__("os").environ, "GIT_COMMITTER_DATE": made})
    sha = git.head(env.repo)
    assert git.commits_dated(env.repo, f"{sha}~1", sha) == [(sha, made, "a subject here")]
    assert git.commits_between(env.repo, f"{sha}~1", sha) == [(sha, "a subject here")]


def test_the_commit_event_is_dated_by_the_commit(env):
    env.queue("001_c", "FAKE commit a.txt hello\nFAKE report done")
    env.tick_until(lambda: env.finished("code/001_c"))
    [ev] = [e for e in env.events("commit") if e.detail.get("scope") == "code"]
    made = git.run(env.repo, "log", "-1", "--format=%cI", ev.detail["sha"]).strip()
    assert ev.ts == made and "happened" not in ev.detail


def fin(env, name: str, **detail):
    done = env.control / "done" / "code"
    done.mkdir(parents=True, exist_ok=True)
    (done / f"{name}.md").write_text("x\n", encoding="utf-8")
    return env.d.log.append("finish", item=f"code/{name}", repo="code", status=detail.pop("status", "done"), **detail)


def retro(env) -> str:
    return session.workflow_retro(env.d.log, state.scan(env.d.cfg, env.d.log), "code")


@pytest.mark.parametrize("status", ["partial", "red"])
def test_a_retro_is_due_on_a_partial_or_red_item(env, status):
    fin(env, "010_audit", template="self_audit", resume_of="code/009_x")
    fin(env, "011_a")
    assert "**due**" not in retro(env)
    fin(env, "012_b", status=status, friction="stale doc")
    fin(env, "013_c", friction="one; two; three")           # more friction, but it triggered nothing
    assert f"**due**: post-audit 012_b ({status}, " in retro(env)
    fin(env, "014_audit", template="self_audit", resume_of="code/012_b")
    assert "**due**" not in retro(env)                      # the retro ran: the count starts again


def test_a_retro_is_due_on_a_run_past_half_again_its_first_eta(env):
    log, base = env.d.log, now() - timedelta(hours=5)
    def run(name: str, hours: float, eta: str):
        log.append("start", item=f"code/{name}", happened=stamp(base))
        log.append("progress", item=f"code/{name}", eta=eta, note="x", happened=stamp(base + timedelta(minutes=1)))
        log.append("progress", item=f"code/{name}", eta="9h", note="later", happened=stamp(base + timedelta(minutes=2)))
        return env.d.log.append("finish", item=f"code/{name}", repo="code", status="done",
                                happened=stamp(base + timedelta(hours=hours)))
    (env.control / "done" / "code").mkdir(parents=True, exist_ok=True)
    for name in ("001_ok", "002_small", "003_late"):
        (env.control / "done" / "code" / f"{name}.md").write_text("x\n", encoding="utf-8")
    run("001_ok", 1.4, "1h")
    run("002_small", 0.1, "1m")                             # many times over, but only minutes
    assert "**due**" not in retro(env)
    run("003_late", 2.1, "1h")
    assert "post-audit 003_late (ran 2.1× its first ETA)" in retro(env)


def test_a_retro_is_due_at_the_floor(env):
    for n in range(11, 22):
        fin(env, f"0{n}_w")
    assert "11 non-template item(s) finished since" in retro(env) and "**due**" not in retro(env)
    fin(env, "022_w")
    assert "12 non-template item(s) finished since · **due**: post-audit 022_w (no friction recorded; the latest)" in retro(env)
