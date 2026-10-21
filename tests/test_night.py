"""The night shift: the stall check, the watch item, backlog draws and their rules."""

from datetime import datetime

import pytest

from conductor import daemon_watch, nightshift
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
    assert watch.item == "code/002_watch" and "no progress line" in watch.detail["reason"]
    env.tick_until(lambda: env.finished("code/002_watch") and env.finished("code/001_long"))
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
    assert "## Night shift" in inbox and "watch report code/002_watch **done**" in inbox
    assert "code/002_watch" not in inbox.split("## Unreviewed reports", 1)[-1].split("##", 1)[0]
    env.signal("planner-1.signal", "by: planner\nreviewed: code/002_watch")     # needs none, and breaks nothing
    env.tick_until(lambda: "needs no review" in env.events("warning")[-1].detail["note"])


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


@pytest.mark.slow
def test_two_full_length_items_never_run_back_to_back(env):
    night_cfg(env, watch_quiet_minutes=20)
    env.queue("001_big", "FAKE report done", max_hours=3)
    env.tick_until(lambda: env.finished("code/001_big"))
    backlog(env, "a_big2", "FAKE report done", max_hours=3)
    backlog(env, "b_small", "FAKE report done")
    env.tick_until(lambda: env.finished("code/002_b_small"))      # the short one goes past it
    for _ in range(5):
        env.d.tick()
    assert [e.item for e in env.events("backlog_draw")] == ["code/002_b_small"]
    flags = [e.detail["text"] for e in env.events("watch_flag") if e.detail["flag"] == "backlog"]
    assert any("a_big2 is full-length and nothing came from Matt or the planner since code/001_big" in t for t in flags)
    env.signal("app-1.signal", "by: app\ncheckpoint: back")         # Matt in between
    env.tick_until(lambda: env.finished("code/003_a_big2"))


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
