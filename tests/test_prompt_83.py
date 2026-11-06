"""Prompt 83: the app restarts itself onto a new commit (and its banner says why, whole,
until it can); an open question whose item finished reads as a ruling, not a block."""

from conductor import inbox, questions, state
from conductor.app import model, stale
from conductor.app.model import Project
from test_app import _settings


def _design_and_live(env):
    done = questions.create(env.control, "code/001_x", "Warm or cool?", ["warm", "cool"], "warm", False, 10,
                            design=True)
    env.d.log.append("finish", item="code/001_x", status="done", carry="none")
    live = questions.create(env.control, "code/002_y", "Which?", ["a", "b"], "a", True, 10)
    return done, live


def test_a_question_whose_item_finished_is_marked_and_sorted_last(env):
    done, live = _design_and_live(env)
    snap = Project(env.control).snapshot()
    assert snap.item_done == {done.id}
    text = inbox.render(env.d.cfg, env.d.log, state.scan(env.d.cfg, env.d.log))
    qs = text.split("## Questions", 1)[1]
    assert qs.index(live.id) < qs.index(done.id)
    assert f"`{done.id}` from code/001_x — item finished: an answer records a ruling; design, held for Matt" in qs


def test_a_finish_before_the_question_does_not_count(tmp_path):
    from types import SimpleNamespace
    q = questions.create(tmp_path, "code/001_x", "Again?", ["y"], "y", False, 10, design=True)
    old = SimpleNamespace(ts="2020-01-01T00:00:00+00:00")
    assert questions.item_done([q], {"code/001_x": old}) == set()
    assert questions.item_done([q], {"code/001_x": SimpleNamespace(ts=q.asked)}) == {q.id}


def test_the_questions_tab_marks_it_and_puts_it_below(qapp, env, tmp_path):
    from conductor.app.window import MainWindow
    done, live = _design_and_live(env)
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    win.refresh()
    panel = win.views[env.control.resolve()].questions_panel
    assert list(panel.cards) == [live.id, done.id]
    card = panel.cards[done.id]
    assert card.status.text().startswith("Item finished: an answer records a ruling")
    assert card.status.property("pill") == "finished" and card.send.isEnabled()
    win._quitting = True
    win.close()


def test_the_banner_is_one_plain_sentence():
    probe = stale.Probe("v2", "abc1234 Conductor 83")
    text = stale.banner(probe, ["a dialog is open"], True)
    assert "`" not in text and "this app still runs the old code" in text
    assert "waiting for: a dialog is open." in text and "“Restart all” restarts the app and every daemon now" in text
    probe.blockers = ["the new code doesn't import (SyntaxError)"]
    assert "can't restart itself yet: the new code doesn't import (SyntaxError)." in stale.banner(probe, [], False)
    assert "daemon runs different code from this app" in stale.banner(None, [], True)
    assert stale.banner(None, [], False) == ""


class FakeWatcher:
    def __init__(self, probe):
        self.probe = probe

    def poll(self):
        return self.probe


def test_the_app_restarts_itself_once_nothing_is_mid_action(qapp, env, tmp_path, monkeypatch):
    from conductor.app.widgets import SendBox
    from conductor.app.window import MainWindow
    questions.create(env.control, "code/001_x", "Which?", ["a"], "a", True, 10)
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    view = win.views[env.control.resolve()]
    restarts = []
    monkeypatch.setattr(stale, "restart", lambda w, folders: restarts.append(folders))
    win.stale = FakeWatcher(stale.Probe("v2", "abc1234 Conductor 83"))
    win.findChildren(SendBox)[0].setPlainText("half an answer")
    win.refresh()
    assert restarts == [] and not view.code_label.isHidden() and view.code_label.wordWrap()
    assert "waiting for: a box holds unsent text" in view.code_label.text() and not view.restart_btn.isHidden()
    win.findChildren(SendBox)[0].setPlainText("")
    win.refresh()
    assert restarts == [[env.control.resolve()]]
    win._quitting = True
    win.close()


def test_the_watcher_probes_off_the_gui_thread(monkeypatch):
    monkeypatch.setattr(stale, "code_version", lambda: "v2")
    monkeypatch.setattr(stale, "code_head", lambda: "abc1234 subject")
    monkeypatch.setattr(stale, "checkout_blockers", lambda: [])
    calls = []
    monkeypatch.setattr(stale, "imports_ok", lambda cwd, check: calls.append(check) or "ImportError: x")
    w = stale.Watcher("v1")
    w.poll()
    w._thread.join(5)
    assert w.probe.head == "abc1234 subject" and w.probe.blockers == ["the new code doesn't import (ImportError: x)"]
    w._next = 0
    w.poll()
    w._thread.join(5)
    assert calls == [stale.APP_IMPORT]              # a failed version is tried once
    assert stale.Watcher(None).poll() is None and model.app_code is not None
