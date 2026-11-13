"""Prompt 111: an API outage costs a wait, not a red. An executor that exits on an API failure
resumes its own session after a backoff and finishes as usual; `retry:` does the same on
demand for a kept red item; an API-only red queues no self-audit; a resume item's red never
holds its queue, and its cost is its own, not its original's running total."""

from conductor import session, state
from conductor.daemon_api import api_error, api_only
from conductor.util import read_text

from conftest import transcript

API_LINE = "Failed to authenticate. API Error: 403 Attention Required! | Cloudflare"


def test_an_api_failure_resumes_the_same_session_and_finishes_done(env):
    env.d.cfg.api_retry_minutes = [0.0, 0.0]
    env.queue("001_long", "FAKE api-error 1\nFAKE commit a.txt one\nFAKE cost 2.5")
    env.tick_until(lambda: env.finished("code/001_long"))
    fin = env.finished("code/001_long")
    assert fin.detail["status"] == "done" and fin.detail["api_tries"] == 1 and fin.detail.get("merged")
    assert not env.events("red") and not state.scan(env.d.cfg, env.d.log).red_hold
    waiting, resumed = env.events("api_retry")
    assert waiting.detail["state"] == "waiting" and "403" in waiting.detail["error"]
    assert resumed.detail["state"] == "resumed" and resumed.detail["session_id"] == fin.detail["session_id"]
    prompt = env.control / resumed.paths[0]
    assert prompt.name == "code__001_long.api1.md" and "the Claude API dropped this session" in read_text(prompt)
    assert fin.detail["cost_usd"] == 2.5                    # the same session: its total is the item's
    env.d.write_inbox(force=True)
    inbox = read_text(env.control / "inbox.md")
    assert "the API dropped it (Failed to authenticate" in inbox and "its session resumed, try 1" in inbox


def test_retries_spent_is_a_red_naming_the_api_and_queues_no_self_audit(env):
    env.d.cfg.api_retry_minutes = [0.0]
    env.queue("001_down", "FAKE api-error 5")
    env.tick_until(lambda: env.finished("code/001_down"))
    fin = env.finished("code/001_down")
    assert fin.detail["status"] == "red" and [e.detail["state"] for e in env.events("api_retry")] == ["waiting", "resumed"]
    [reason] = fin.detail["reasons"]
    assert reason.startswith("API error (") and "1 retry spent" in reason and api_only(fin.detail["reasons"])
    assert session.retro_trigger(env.d.log, fin) is None
    fin.detail["session_id"] = transcript(fin.detail["session_id"])
    assert session.retros(env.d.log, state.scan(env.d.cfg, env.d.log), "code").due is None


def test_a_resume_items_red_never_holds_and_its_cost_is_its_own(env):
    env.queue("001_orig", "FAKE cost 9.48\nFAKE commit a.txt one")
    env.tick_until(lambda: env.finished("code/001_orig"))
    assert env.finished("code/001_orig").detail["cost_usd"] == 9.48
    env.queue("002_ask", "Why?\nFAKE cost 0.5\nFAKE report red", resume="code/001_orig")
    env.tick_until(lambda: env.finished("code/002_ask"))
    fin = env.finished("code/002_ask")
    assert fin.detail["status"] == "red" and fin.detail["cost_usd"] == 0.5
    assert [e.detail["after_red"] for e in env.events("red")] == ["continue"]
    assert "code" not in state.scan(env.d.cfg, env.d.log).red_hold
    env.queue("003_next", "FAKE report done")
    env.tick_until(lambda: env.finished("code/003_next"))       # the queue never stopped


def test_retry_resumes_a_kept_red_item_lands_it_and_lifts_its_hold(env):
    env.queue("001_red", "FAKE commit a.txt one\nFAKE report red")
    env.tick_until(lambda: env.finished("code/001_red"))
    assert state.scan(env.d.cfg, env.d.log).red_hold["code"].item == "code/001_red"
    sid = env.finished("code/001_red").detail["session_id"]
    env.signal("r.md", "retry: code/001_red\nby: Matt")
    env.tick_until(lambda: env.finished("code/001_red").detail["status"] == "done")
    fin = env.finished("code/001_red")
    assert fin.detail.get("merged") and fin.detail["retried"] == 1 and fin.detail["session_id"] == sid
    [ev] = [e for e in env.events("api_retry") if e.detail["state"] == "retry"]
    assert ev.detail["by"] == "Matt" and "signals/r.md" in " ".join(ev.paths)
    assert "`retry:` by Matt resumes this item" in read_text(env.control / ev.paths[0])
    assert "code" not in state.scan(env.d.cfg, env.d.log).red_hold
    env.signal("r2.md", "retry: code/001_red")
    env.tick_until(lambda: env.events("invalid"))
    assert env.events("invalid")[-1].detail["error"] == "retry: code/001_red finished done, not red"


def test_what_counts_as_an_api_failure(tmp_path):
    log, err = tmp_path / "run.log", tmp_path / "err.log"
    log.write_text('{"type":"system"}\n', encoding="utf-8")
    err.write_text("", encoding="utf-8")
    assert api_error(log, err, {"is_error": True, "result": API_LINE}).startswith("Failed to authenticate")
    assert api_error(log, err, {"is_error": False, "result": API_LINE}) is None         # it finished
    for text in ("API Error: 529 Overloaded", "API Error: 500 Internal server error", "API Error: 429 rate limited",
                 '{"type":"error","error":{"type":"overloaded_error"}}', "API Error: Connection error."):
        err.write_text(text + "\n", encoding="utf-8")
        assert api_error(log, err, None), text
    err.write_text("API Error: 400 prompt is too long\n", encoding="utf-8")
    assert api_error(log, err, None) is None                                            # it would fail again
    assert not api_only(["executor error: x"]) and api_only(["launch failed: y"]) and not api_only([])


def test_kept_reads_name_the_seeds_each_read_spent(env):
    import json

    from conductor import kept
    cfg = env.d.cfg
    for name in ("004_r90", "005_none"):
        (kept.root(cfg, "code") / name).mkdir(parents=True)
        (kept.root(cfg, "code") / name / "games.jsonl").write_text("{}\n", encoding="utf-8")
    rows = kept.section(cfg, "code")
    assert "seeds spent" not in rows and "no seeds farmed" not in rows       # never farmed: no ledger
    ledger = cfg.state_dir / kept.SEEDS
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text(json.dumps({"item": "code/004_r90", "first": 5000, "count": 1000, "args": ["--rules", "r90"]})
                      + "\n" + json.dumps({"item": "other/004_r90", "first": 1, "count": 1}) + "\n", encoding="utf-8")
    text = kept.section(cfg, "code")
    assert "(004_r90, kept" in text and "seeds spent: 5000-5999 (--rules r90)" in text
    assert "(005_none, kept" in text and "no seeds farmed" in text


def test_farm_seeds_notes_the_seeds_a_finished_run_spent(tmp_path, monkeypatch):
    import json

    from conductor.remote import client, settings
    r = settings.Remote.__new__(settings.Remote)
    monkeypatch.setattr(settings.Remote, "state", property(lambda self: tmp_path / "remote"))
    monkeypatch.setenv(settings.ITEM_VAR, "code/007_read")
    client.note_seeds(r, 100, 50, ["--x"])
    [line] = (tmp_path / "remote" / client.SEEDS).read_text(encoding="utf-8").splitlines()
    assert json.loads(line) | {"at": 0} == {"item": "code/007_read", "first": 100, "count": 50, "args": ["--x"], "at": 0}


def test_the_preamble_binds_working_rules_and_asks_before_departing_from_an_answer(env):
    from datetime import datetime

    from conductor import executor
    from conductor.items import Item
    item = Item("code", "009_x", env.control / "queue" / "code" / "009_x.md", num=9, body="Do the work. " * 400)
    text = executor.build_prompt(env.d.cfg, item, executor.RunPaths.for_item(env.cfg, item), 1.0,
                                 datetime(2026, 11, 13, 9), {"worktree": None, "branch": None, "base": None})
    flat = " ".join(text.split())
    assert "CLAUDE.md *Working rules* steps (rulings, not method)" in flat
    assert "a run departing from Matt's own answer to a staging question (ask before it merges)" in flat
