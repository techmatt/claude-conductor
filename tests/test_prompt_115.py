"""Prompt 115: toasts only for what Matt acts on (a finish with its queue state merged in,
what waits on him, errors), coalesced per project within about a minute; standing warnings
fold into one line; a cancelled auto-retro stays cancelled and a new item never takes a
number already in use in its repo."""

from conductor import inbox, notify, state

from test_prompt_107 import auto, fin, queued
from test_signals import send


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def batcher(state_line="queue now empty", short=True):
    shown, clock = [], Clock()
    t = notify.Toasts("fractals", lambda *a: shown.append(a), lambda repo, since: state_line, short=short, clock=clock)
    return t, shown, clock


def test_a_finish_waits_to_name_its_queue_state_then_toasts_alone():
    t, shown, clock = batcher("052_next started")
    t.add("finish", "fractals/051_drop_wording_checks", "fractals", {"status": "done"}, 7)
    t.flush()
    assert shown == []                                          # settling: the next launch may follow
    clock.t += notify.SETTLE_SECONDS
    t.flush()
    assert shown == [("fractals: 051_drop_wording_checks done · 052_next started", "", "fractals#7")]


def test_partial_and_red_in_the_title_and_a_red_hold_names_the_clear():
    t, shown, clock = batcher()
    t.add("finish", "fractals/051_x", "fractals", {"status": "partial", "reasons": ["d0 short"]}, 1)
    t.flush(force=True)
    assert shown[-1][:2] == ("fractals: 051_x partial · queue now empty", "d0 short")
    t.add("finish", "fractals/052_y", "fractals", {"status": "red", "reasons": ["boom"]}, 2)
    t.flush(force=True)
    assert shown[-1][0] == "fractals: 052_y red · queue `fractals` HELD (`clear: fractals`)"


def test_toasts_within_a_minute_become_one():
    t, shown, clock = batcher()
    t.add("ask", "fractals/051_x", "fractals", {"block": True, "text": "Which band?"}, 1)
    t.flush()
    assert shown == [("fractals: question from 051_x", "Which band?", "fractals#1")]   # no finish: at once
    clock.t += 5
    t.add("finish", "fractals/051_x", "fractals", {"status": "done"}, 2)
    t.add("finish", "fractals/052_y", "fractals", {"status": "done", "pick": "fractals/picks/a.json"}, 3)
    clock.t += notify.SETTLE_SECONDS
    t.add("ask", "fractals/053_z", "fractals", {"block": True, "text": "Go?"}, 4)
    t.flush()
    assert len(shown) == 1                                      # within the minute of the last
    clock.t = 1000.0 + notify.WINDOW_SECONDS
    t.flush()
    title, body, tag = shown[-1]
    assert title == "fractals: 2 done, 1 question" and tag == "fractals#2,3,4"
    assert body.splitlines() == ["051_x done", "052_y done · queue now empty: picker ready: fractals/picks/a.json",
                                 "question from 053_z: Go?"]


def test_only_what_matt_acts_on_toasts():
    quiet = [("finish", {"status": "done", "template": "watch"}), ("ask", {"default": "items", "text": "?"}),
             ("ask", {"default": "x", "design": True}), ("memory", {"level": "pressure"}), ("queue_empty", {}),
             ("load_high", {}), ("retro", {"of": "x"}), ("progress", {"note": "n"}), ("watch_flag", {"flag": "eta"}),
             ("warning", {"note": "n"}), ("pick_open", {"by": "app"}), ("daemon_stop", {"reason": "signal"})]
    for kind, d in quiet:
        assert notify.note(kind, "code/1_x", "code", d) is None, kind
    loud = [("finish", {"status": "done"}), ("finish", {"status": "red", "template": "watch"}),
            ("ask", {"block": True}), ("pick_open", {"by": "planner", "name": "art"}), ("tamper", {}),
            ("push_failed", {"target": "code"}), ("request_failed", {"request": "r"}), ("invalid", {}),
            ("run_failed", {"target": "web"}), ("daemon_died", {}), ("memory_critical", {}),
            ("watch_flag", {"flag": "quiet", "text": "no progress 20 min"}), ("daemon_stop", {"reason": "error"}),
            ("notify", {"text": "hi"}), ("amend_refused", {}), ("checkout_dirty", {}), ("hold", {"reason": "r"})]
    for kind, d in loud:
        assert notify.note(kind, "code/1_x", "code", d) is not None, kind
    assert notify.note("red", "code/1_x", "code", {"reasons": ["x"]}) is None      # its finish says it


def test_a_daemon_finish_names_the_item_that_started_after_it(env):
    env.queue("001_a", "FAKE report done")
    env.queue("002_b", env.hold())
    env.tick_until(lambda: env.d.log.last("start", item="code/002_b"))
    assert ("test: 001_a done · 002_b started", "") in [t[:2] for t in env.toasts]
    env.release()
    env.tick_until(lambda: env.finished("code/002_b"))
    assert env.toasts[-1][0] == "test: 002_b done · queue now empty"


def _warn(env, name, warnings):
    return fin(env, name, warnings=warnings)


def test_a_warning_unchanged_since_the_last_reviewed_report_folds_to_one_standing_line(env):
    first = _warn(env, "001_a", "design: Remove agents over anchor; design: same-effect")
    env.d.log.append("reviewed", item="code/001_a", finish=first.id, status="done", by="planner")
    _warn(env, "002_b", "design: Remove agents over anchor; design: same-effect")
    _warn(env, "003_c", "design: same-effect; perf: 40s turns")
    view = state.scan(env.d.cfg, env.d.log)
    lines = inbox.warning_lines(view, view.unreviewed())
    assert not any(ln.startswith("- code/002_b") for ln in lines)          # nothing new on it
    assert any(ln.startswith("- code/003_c (done") and ln.endswith("**perf**: 40s turns") for ln in lines)
    [stand] = [ln for ln in lines if "standing" in ln]
    assert stand == ("- standing in `code`, unchanged since 001 (reviewed), again on 002, 003: "
                     "**design**: Remove agents over anchor; **design**: same-effect")
    send(env, "by: planner\nsession: start", "planner.signal")
    text = (env.control / "session.md").read_text(encoding="utf-8")
    assert "- standing in `code`, unchanged since 001" in text


def test_a_cancelled_retro_stays_cancelled(env):
    auto(env)
    fin(env, "001_a", status="partial")
    env.d.queue_retros()
    assert queued(env) == ["code/002_post_audit_001"]
    env.d.queue_retros()
    send(env, "by: planner\ncancel: code/002_post_audit_001", "cancel.signal")
    [c] = env.events("cancel")
    assert c.detail["retro_of"] == "code/001_a"
    env.d._retro_mark = None
    env.d.queue_retros()
    assert queued(env) == ["code/002_post_audit_001"]                       # never re-queued
    fin(env, "003_c", status="red")                                         # a later one still gets its own
    env.d.queue_retros()
    assert queued(env) == ["code/002_post_audit_001", "code/004_post_audit_003"]
