"""129: one signal, one receipt. `open: file` keeps a page's query, a refusal ends with the
form that would be accepted, and a tidy-up whose wish already holds is a no-op said only in
its receipt (no event, so no notice)."""

import pytest

from conductor import daemon_signals, desktop
from conductor.daemon_receipts import rel_for
from conductor.util import read_text, split_front_matter

from test_signals import bad, send


@pytest.fixture(autouse=True)
def checkout_elsewhere(monkeypatch, tmp_path_factory):
    monkeypatch.setattr(desktop, "CHECKOUT", tmp_path_factory.mktemp("no-checkout"))


@pytest.fixture
def shown(env, monkeypatch) -> list[dict]:
    out: list[dict] = []
    env.d.opener.launch = out.append
    monkeypatch.setattr(desktop, "browser_command", lambda url: f"browser.exe {url}")
    return out


def receipt(env, name: str) -> dict:
    return split_front_matter(read_text(env.control / rel_for(f"signals/{name}")))[0]


def page(env):
    (env.repo / "tools").mkdir(exist_ok=True)
    p = env.repo / "tools" / "phonics-recorder.html"
    p.write_text("<p>x</p>\n", encoding="utf-8")
    return p


def test_open_file_keeps_a_query_or_fragment(env, shown):
    p = page(env)
    send(env, f"by: planner\nopen: file {p}?missing\nopen: file {p}#top\nopen: file {p}",
         "planner-1.signal")
    opened = env.events("opened")
    url = p.resolve().as_uri()
    assert [e.detail.get("url") for e in opened] == [f"{url}?missing", f"{url}#top", None]
    assert opened[2].detail["path"] == str(p.resolve())
    assert shown[0] == {"how": "run", "target": f"browser.exe {url}?missing"}
    assert all("tools/phonics-recorder.html" in e.paths[-1] for e in opened[:2])
    assert receipt(env, "planner-1.signal")["outcome"] == "accepted"


def test_open_file_query_falls_back_to_webbrowser(env, shown, monkeypatch):
    monkeypatch.setattr(desktop, "browser_command", lambda url: None)
    p = page(env)
    send(env, f"open: file {p}?a=1&b=2")
    assert shown == [{"how": "browser", "target": f"{p.resolve().as_uri()}?a=1&b=2"}]


def test_open_url_file_under_the_roots_is_a_page(env, shown, tmp_path):
    p = page(env)
    outside = tmp_path / "elsewhere.html"
    outside.write_text("x\n", encoding="utf-8")
    send(env, f"open: url {p.resolve().as_uri()}?missing\nopen: url {outside.as_uri()}?x")
    [ok] = env.events("opened")
    assert ok.detail["url"] == f"{p.resolve().as_uri()}?missing"
    [no] = env.events("open_refused")
    assert "is outside" in no.detail["reason"] and "open_roots" in no.detail["reason"]


def test_a_missing_query_page_says_where_it_is_not(env, shown):
    send(env, f"open: file {env.repo / 'tools' / 'nope.html'}?missing")
    [no] = env.events("open_refused")
    assert "does not exist" in no.detail["reason"] and "`open: folder " in no.detail["reason"]


def test_refusals_end_with_the_accepted_form(env, shown):
    send(env, "by: planner\nopen: url http://example.com/x", "planner-u.signal")
    meta = receipt(env, "planner-u.signal")
    assert meta["outcome"] == "refused" and "`open: file <path>`" in meta["reason"]
    send(env, "opne: file x", "planner-k.signal")                      # a typo'd key
    assert bad(env, "planner-k.signal")
    assert "; use `open: folder|reveal|file|text <path>`" in receipt(env, "planner-k.signal")["reason"]
    send(env, "cancel: code/999", "planner-c.signal")                  # a verb refused when applied
    assert receipt(env, "planner-c.signal")["reason"].endswith(daemon_signals.FORMS["cancel"])
    send(env, "run: a b c", "planner-r.signal")                        # a syntax refusal
    assert receipt(env, "planner-r.signal")["reason"].endswith(daemon_signals.FORMS["run"])


def test_tidy_ups_already_true_are_no_ops_without_notices(env, shown):
    top = env.d.log.max_id()
    send(env, "by: planner\nack: invalid", "planner-a.signal")         # nothing live
    send(env, "by: planner\nack: invalid #1", "planner-b.signal")      # never a rejection
    send(env, "by: planner\nretire: art_alts", "planner-t.signal")     # unknown: still refused
    for name in ("planner-a.signal", "planner-b.signal"):
        meta = receipt(env, name)
        assert meta["outcome"] == "accepted" and "nothing to acknowledge" in meta["result"], meta
        assert not bad(env, name)
    assert [e.kind for e in env.d.log.query() if e.id > top] == ["invalid"]
    send(env, "pause: code", "planner-p1.signal")
    send(env, "pause: code\nnotify: hi", "planner-p2.signal")
    meta = receipt(env, "planner-p2.signal")
    assert meta["outcome"] == "accepted" and "already paused" in meta["result"] and len(env.events("pause")) == 1
    assert meta["result"].startswith("applied; ") and env.events("notify")
