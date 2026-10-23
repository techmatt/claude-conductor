"""The night shift: the stall check, the watch item, backlog draws and their rules."""

import json
import os
import re
import time
from datetime import datetime

import pytest

from conductor import daemon_watch, items, nightshift, questions, state
from conductor import session as session_mod
from conductor.util import read_text


def night_cfg(env, **values) -> None:
    """Checks every tick and a tiny silence threshold, unless a test says otherwise."""
    for k, v in {"watch_check_minutes": 0.0001, "watch_quiet_minutes": 0.0001, **values}.items():
        setattr(env.d.cfg, k, v)


def backlog(env, name: str, body: str, **meta) -> None:
    fm = "\n".join(f"{k}: {v}" for k, v in {"repo": "code", **meta}.items())
    p = env.control / "backlog" / "code" / f"{name}.md"
    p.write_text(f"---\n{fm}\n---\n{body}\n", encoding="utf-8")


def watch_prompt(env, ev) -> str:
    name = ev.item.split("/", 1)[1]
    return read_text(env.control / "reports" / f"code__{name}.prompt.md")


@pytest.mark.slow
def test_a_stall_is_flagged_and_a_watch_tells_the_item(env, monkeypatch):
    monkeypatch.setattr(daemon_watch, "WATCH_BODY", "{reason}\nFAKE note ## Actions\\n- tell: code/001_long "
                        "post where you are\\n- cancel: code/001_long\n")
    night_cfg(env)
    env.queue("001_long", "FAKE until-message 30")
    env.tick_until(lambda: env.events("watch"))
    [flag] = env.events("watch_flag")
    assert flag.detail["flag"] == "quiet" and flag.item == "code/001_long" and "watch" not in flag.detail
    [watch] = env.events("watch")
    assert watch.item == "code/W1_watch" and "no progress line" in watch.detail["reason"]
    env.tick_until(lambda: env.finished("code/W1_watch") and env.finished("code/001_long"))
    text = watch_prompt(env, watch)
    assert "Watch over test" in text and "> FAKE until-message 30" in text     # the item's prompt, quoted
    assert "Why conductor launched this watch:" in text and "The planner adds" not in text
    [tell] = env.events("tell")
    assert tell.item == "code/001_long" and tell.detail["by"] == "watch" and tell.detail["text"] == "post where you are"
    assert env.events("delivered")[0].detail["tell"] == tell.id
    assert any("watch action `cancel: code/001_long` not carried out" in w.detail["note"] for w in env.events("warning"))
    # De-duplicated: the same silence is one flag however many checks see it (its reply starts a new one).
    assert [e.detail["key"] for e in env.events("watch_flag")].count("quiet:code/001_long:0") == 1
    inbox = read_text(env.control / "inbox.md")
    assert "## Night shift" in inbox and "watch report code/W1_watch **done**" in inbox
    assert "code/W1_watch" not in inbox.split("## Unreviewed reports", 1)[-1].split("##", 1)[0]
    env.signal("planner-1.signal", "by: planner\nreviewed: code/W1_watch")     # needs none, and breaks nothing
    env.tick_until(lambda: "needs no review" in env.events("warning")[-1].detail["note"])
    # Watches have their own series (Word Warriors 093_watch beside 093_hunt5_calls): the queue's
    # next number is still one past the last real item, and the next watch one past the last.
    view = state.scan(env.d.cfg, env.d.log)
    watches = [e.item for e in env.events("watch")]
    assert watches == [f"code/W{n}_watch" for n in range(1, len(watches) + 1)]
    assert nightshift.queue_name(env.d.cfg, view, "code", "next") == "002_next"
    assert nightshift.watch_name(view, "code") == f"W{len(watches) + 1}_watch"
    assert "watch item's" in items.parse(env.queue("W3_x", "FAKE report done"), "code").errors[-1]


def test_no_watch_while_the_planner_is_active_until_asked(env):
    night_cfg(env)
    env.signal("planner-1.signal", "by: planner\ncheckpoint: working")
    env.queue("001_long", env.hold())
    env.tick_until(lambda: env.events("watch_flag"))
    [flag] = env.events("watch_flag")
    assert flag.detail["watch"].startswith("skipped: a planner session is active")
    assert not env.events("watch")
    env.signal("planner-2.signal", "by: planner\nwatch: now")
    env.tick_until(lambda: env.events("watch"))
    assert env.events("watch")[0].detail["by"] == "planner"
    env.signal("planner-3.signal", "by: planner\nwatch: now")      # one at a time
    env.tick_until(lambda: list((env.control / "signals").glob("planner-3.signal.bad")))
    receipt = read_text(env.control / "receipts" / "signals" / "planner-3.md")
    assert "outcome: \"refused\"" in receipt and "a watch is already queued or running" in receipt
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))


def test_a_backlog_item_is_drawn_into_an_empty_queue(env):
    backlog(env, "07_feature", "FAKE report done")
    env.tick_until(lambda: env.finished("code/001_feature"))
    [draw] = env.events("backlog_draw")
    assert draw.item == "code/001_feature" and draw.detail["from"] == "backlog/code/07_feature.md"
    receipt = read_text(env.control / "receipts" / "backlog" / "code" / "07_feature.md")
    assert "drawn into the queue as code/001_feature" in receipt
    assert not list((env.control / "backlog" / "code").glob("*.md"))
    assert (env.control / "backlog" / ".gitignore").read_text() == "*\n"


def assert_each_once(text: str) -> None:
    """No section heading and no event line (`- <clock> #<id> …`) appears twice."""
    heads = [ln for ln in text.splitlines() if ln.startswith("#")]
    ids = re.findall(r"^- \d{1,2}:\d\d[ap]m #(\d+) ", text, flags=re.M)
    assert len(heads) == len(set(heads)), heads
    assert len(ids) == len(set(ids)), ids


@pytest.mark.slow
def test_inbox_and_digests_print_each_section_once(env, tmp_path, monkeypatch):
    """Word Warriors' inbox printed everything after *Night shift* twice once (38)."""
    # Not this checkout's own reports: two of them may share a heading like `## Decisions`.
    monkeypatch.setattr(session_mod, "CONDUCTOR_ROOT", tmp_path / "no-conductor")
    env.queue("001_first", "FAKE report done")
    env.tick_until(lambda: env.finished("code/001_first"))
    backlog(env, "07_ask", "FAKE ask " + json.dumps({"question": "Which?", "block": True}) + "\nFAKE report done")
    env.tick_until(lambda: questions.scan(env.control))
    [q] = questions.scan(env.control)
    q.answer_path.write_text("This one.\n", encoding="utf-8")
    env.tick_until(lambda: env.finished("code/002_ask"))
    env.d.write_inbox(force=True)
    inbox = read_text(env.control / "inbox.md")
    for title in ("## Night shift", "## Finished", "## Questions resolved", "## Now"):
        assert title in inbox
    assert_each_once(inbox)
    env.signal("planner-1.signal", "by: planner\nreview: all")
    env.tick_until(lambda: env.events("review"))
    assert_each_once(read_text(env.control / "review.md"))
    env.signal("planner-2.signal", "by: planner\nsession: start")
    env.tick_until(lambda: env.events("checkpoint"))
    session = read_text(env.control / "session.md")
    assert "## Night shift" in session and "## Questions resolved" in session
    assert_each_once(session)


@pytest.mark.slow
def test_two_full_length_items_never_run_back_to_back(env):
    night_cfg(env, watch_quiet_minutes=20)
    env.queue("001_big", "FAKE report done", max_hours=3)
    env.tick_until(lambda: env.finished("code/001_big"))
    backlog(env, "a_big2", "FAKE report done", max_hours=3)
    backlog(env, "b_small", "FAKE report done")
    old = time.time() - 3600                # written before 001_big started: a backlog write since counts as Matt
    for p in (env.control / "backlog" / "code").glob("*.md"):
        os.utime(p, (old, old))
    env.signal("planner-1.signal", "by: planner\nreviewed: code/001_big")     # bookkeeping: not Matt
    env.tick_until(lambda: env.finished("code/002_b_small"))      # the short one goes past it
    for _ in range(5):
        env.d.tick()
    assert [e.item for e in env.events("backlog_draw")] == ["code/002_b_small"]
    flags = [e.detail["text"] for e in env.events("watch_flag") if e.detail["flag"] == "backlog"]
    assert any("a_big2 is full-length and nothing came from Matt since code/001_big" in t for t in flags)
    env.signal("app-1.signal", "by: app\ncheckpoint: back")         # Matt in between
    env.tick_until(lambda: env.finished("code/003_a_big2"))


def test_only_matt_counts_between_full_length_items(env):
    log, cfg = env.d.log, env.d.cfg
    start = log.append("start", item="code/079_bug_hunt")
    # Bookkeeping (Word Warriors 080): a planner request, a planner `reviewed:`; conductor's own queue writes.
    log.append("request", paths=["state.md"], request="20261021-1227-state")
    log.append("reviewed", item="code/078_x", paths=["signals/planner-1.signal"], by="planner")
    log.append("backlog_draw", item="code/080_hunt", paths=["queue/code/080_hunt.md"])
    log.append("enqueue", item="code/080_hunt", paths=["queue/code/080_hunt.md"])
    log.append("enqueue", item="code/081_watch", paths=["queue/code/081_watch.md"], template="watch")
    assert nightshift.matt_since(cfg, log, start) is None
    for write in [lambda: log.append("checkpoint", paths=["signals/app-1.signal"], by="app"),
                  lambda: log.append("approve", item="code/082_y", paths=["signals/planner-2.signal"], by="planner"),
                  lambda: log.append("answer", item="code/079_bug_hunt", paths=["questions/q.answer.md"]),
                  lambda: log.append("enqueue", item="code/083_z", paths=["queue/code/083_z.md"])]:
        assert nightshift.matt_since(cfg, log, start) is None
        ev = write()
        assert nightshift.matt_since(cfg, log, start) == f"{ev.kind} #{ev.id}"
        start = ev
    backlog(env, "later", "FAKE report done")
    later = time.time() + 5
    os.utime(env.control / "backlog" / "code" / "later.md", (later, later))
    assert nightshift.matt_since(cfg, log, start) == "backlog backlog/code/later.md"


def test_the_night_budget_stops_draws(env):
    night_cfg(env, watch_check_minutes=10, night_start_hour=0, night_end_hour=24, backlog_night_hours=1)
    backlog(env, "1_first", "FAKE sleep 0.3")
    backlog(env, "2_second", "FAKE report done")
    env.tick_until(lambda: env.finished("code/001_first"))
    env.tick_until(lambda: env.events("backlog_sleep"))
    assert [e.item for e in env.events("backlog_draw")] == ["code/001_first"]
    # Each item may run its max_hours (1h here): the second no longer fits once the first used any.
    assert "fits tonight's budget" in env.events("backlog_sleep")[0].detail["reason"]
    assert (env.control / "backlog" / "code" / "2_second.md").is_file()
    assert "backlog budget used; sleeping since" in read_text(env.control / "inbox.md")


@pytest.mark.slow
def test_autopilot_holds_draws_and_watch_tells(env, monkeypatch):
    from conductor.app import model
    night_cfg(env, watch_check_minutes=10)
    monkeypatch.setattr(nightshift, "night", lambda cfg, at=None: None)        # always day
    env.signal("app-1.signal", "by: app\nautopilot: night")
    env.tick_until(lambda: env.events("autopilot"))
    assert env.events("autopilot")[0].detail == {"mode": "night", "was": "always", "by": "app"}
    backlog(env, "07_feature", "FAKE report done")
    env.tick_until(lambda: (env.control / "receipts" / "backlog" / "code" / "07_feature.md").is_file())
    for _ in range(3):
        env.d.tick()
    assert not env.events("backlog_draw") and nightshift.autopilot(env.d.log) == "night"
    snap = model.Project(env.control).snapshot()
    assert snap.autopilot == ("night", "next: 07_feature from 6pm")
    assert "autopilot: **night** (autopilot is night only (from 6pm)" in read_text(env.control / "inbox.md")
    # Off: the watch still runs and repairs (its tell goes out), but its draw is only recommended.
    monkeypatch.setattr(daemon_watch, "WATCH_BODY", "{reason}\nFAKE note ## Actions\\n- tell: code/001_long hi"
                                                    "\\n- draw: code\n")
    env.signal("cli-1.signal", "by: cli\nautopilot: off")
    env.queue("001_long", env.hold())
    env.signal("cli-2.signal", "by: cli\nwatch: now")
    env.tick_until(lambda: env.finished("code/W1_watch"))
    assert env.events("tell")[0].detail["by"] == "watch"
    assert any("autopilot is off: recommended only" in w.detail["note"] for w in env.events("warning"))
    env.release()
    env.tick_until(lambda: env.finished("code/001_long"))
    assert not env.events("backlog_draw")
    env.signal("cli-3.signal", "by: cli\nautopilot: on")        # on is always
    env.tick_until(lambda: env.events("backlog_draw"))
    assert [e.detail["mode"] for e in env.events("autopilot")] == ["night", "off", "always"]
    env.signal("cli-4.signal", "by: cli\nautopilot: sometimes")
    env.tick_until(lambda: list((env.control / "signals").glob("cli-4.signal.bad")))


def test_the_report_footer_under_actions_is_no_action(env):
    """Word Warriors W10-W17: `## Actions` is a watch report's last heading, so the footer every
    report ends with sat under it and was refused as an action, a warning each run."""
    report = env.control / "reports" / "code__W1_watch.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(f"# Watch\n\n## Actions\n\n- none\n\nReport written to {report}\n", encoding="utf-8")
    env.d._watch_actions({"item": "code/W1_watch", "report": str(report)})
    assert not env.events("warning")
    report.write_text(f"## Actions\n\n- cancel: code/001_x\n\n`Report written to {report}`\n", encoding="utf-8")
    env.d._watch_actions({"item": "code/W1_watch", "report": str(report)})
    [w] = env.events("warning")
    assert w.detail["action"] == "cancel: code/001_x"       # a real unknown action still warns


def test_eta_night_and_full_length_rules():
    posted = datetime(2026, 10, 21, 23, 30).astimezone()
    assert nightshift.parse_eta("~12:10am", posted).day == 22                  # past midnight
    assert nightshift.parse_eta("5:40 pm", posted).hour == 17 and nightshift.parse_eta("5:40 pm", posted).day == 22
    assert nightshift.parse_eta("25m", posted).minute == 55 and nightshift.parse_eta("1h30m", posted).hour == 1
    assert nightshift.parse_eta("soon", posted) is None and nightshift.parse_eta("", posted) is None
    assert nightshift.full_length({"template": "bug_hunt"}) and nightshift.full_length({"max_hours": 2})
    assert not nightshift.full_length({"template": "watch"}) and not nightshift.full_length({"max_hours": 1.5})

    class Cfg:
        night_start_hour, night_end_hour = 18.0, 8.0
    assert nightshift.night(Cfg, datetime(2026, 10, 21, 12, 0).astimezone()) is None
    start, end = nightshift.night(Cfg, datetime(2026, 10, 22, 3, 0).astimezone())
    assert (start.day, start.hour, end.day, end.hour) == (21, 18, 22, 8)
