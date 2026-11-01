"""Questions in the app: answers, late ones, the scan, and the tab's layout."""

import pytest
from conductor import questions
from conductor.app import actions
from conductor.app.model import Project
from test_app import _settings


def test_answer_writes_the_same_file_as_the_cli(tmp_path):
    q = questions.create(tmp_path, "code/001_x", "Name?", [], "a", False, 5)
    actions.answer(tmp_path, "001__1", "  b  ")
    # The same file, plus a `by:` line the executor never sees (read_answer drops it).
    assert q.answer_path.read_text(encoding="utf-8") == "---\nby: app\n---\nb\n"
    assert questions.read_answer(q, settle=0) == "b" and questions.answer_by(q) == "app"
    with pytest.raises(actions.AnswerError):
        actions.answer(tmp_path, q.id, "again")
    with pytest.raises(actions.AnswerError):
        actions.answer(tmp_path, "nope__9", "x")


@pytest.mark.slow
def test_an_app_answer_is_logged_by_app(env):
    """Found proving phase 4 live: the app's answer event had no `by`, unlike every
    signal it writes. The executor still gets the bare answer text."""
    # The suite's 1.8s default can expire before the answer lands under full-suite load
    # (then the answer is logged `late`): give this question a minute.
    toml = env.control / "conductor.toml"
    toml.write_text(toml.read_text(encoding="utf-8").replace("ask_default_minutes = 0.03", "ask_default_minutes = 1"),
                    encoding="utf-8", newline="\n")
    env.configure('[repos]\ncode = "../code"\n')
    env.queue("001_ask", 'FAKE ask {"question": "Which?", "default": "a"}')
    env.tick_until(lambda: questions.scan(env.control))
    [q] = questions.scan(env.control)
    actions.answer(env.control, q.id, "b")
    env.tick_until(lambda: env.finished("code/001_ask"))
    [ans] = env.events("answer")
    assert ans.detail["by"] == "app" and ans.detail["text"] == "b"
    text = (env.control / "reports" / "code__001_ask.md").read_text(encoding="utf-8")
    assert "Answer to code__001_ask__1:\nb\n" in text and "by: app" not in text


def test_a_late_answer_is_reported_late(tmp_path):
    q = questions.create(tmp_path, "code/001_x", "Name?", [], "a", False, 5)
    questions.resolve(q, "default", "a", "server")
    late = actions.answer(tmp_path, q.id, "b")
    assert not late.is_open and "late" in actions.answer_note(late)
    q2 = questions.create(tmp_path, "code/002_y", "Name?", [], "a", False, 5)
    assert actions.answer_note(actions.answer(tmp_path, q2.id, "b")).startswith("answered")


def test_question_scan_is_cached_but_sees_changes(env):
    p = Project(env.control)
    q = questions.create(env.control, "code/001_x", "Q?", [], "a", False, 5)
    assert [x.is_open for x in p.scan_questions()] == [True]
    questions.resolve(q, "default", "a", "server")
    assert [x.is_open for x in p.scan_questions()] == [False]
    p.close()


def test_a_long_question_wraps_to_the_window(qapp, env, tmp_path):
    from PySide6.QtCore import QPoint
    from conductor.app.window import MainWindow
    words = " ".join(f"word{n}" for n in range(80))
    default = "keep every screenshot and delete only the duplicated originals " * 3
    opts = [default.strip(), "delete everything under scratch/shots and start again from nothing",
            "move the whole folder to Drive", "ask me again tomorrow"]
    q = questions.create(env.control, "code/001_x", f"Long body: {words}", opts, opts[0], False, 10)
    questions.create(env.control, "code/001_x", "Done?", ["yes"], "yes", False, 1)
    win = MainWindow([env.control], settings=_settings(tmp_path), tray=False, interval_ms=60_000)
    win.show()
    win.refresh()
    view = win.views[env.control.resolve()]
    panel = view.questions_panel
    view.tabs.setCurrentWidget(panel)
    for width in (1250, 760):                      # the default size, then narrowed
        win.resize(width, 780)
        for _ in range(5):
            qapp.processEvents()
        port = panel.scroll.viewport()
        card = panel.cards[q.id]
        assert card.width() <= port.width(), width
        right = card.send.mapTo(port, QPoint(card.send.width(), 0)).x()
        assert right <= port.width(), width
        assert card.status.width() <= port.width() and card.status.wordWrap()
    win._quitting = True
    win.close()
