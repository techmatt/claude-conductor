import json
from pathlib import Path

import pytest

from conductor import config, rename
from conductor.daemon import Daemon
from conductor.events import EventLog

from conftest import git


@pytest.mark.slow
def test_rename_repo_moves_checkout_and_rekeys_history(env):
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_a"))
    (env.control / "state.md").write_text("# State\n\n## Repos\n- `code`: ../code (the code)\n"
                                          "- **Docs:** `code/docs/x.md`; code review is prose\n", encoding="utf-8")
    git(env.control, "add", "state.md")
    git(env.control, "commit", "-qm", "state")
    env.queue("002_b", "FAKE commit b.txt b")
    env.d.acquire()
    with pytest.raises(rename.RenameError, match="daemon is running"):
        rename.rename(env.control, "code", "engine", str(env.root / "engine"))
    env.d.release()
    with pytest.raises(rename.RenameError, match="queue/code holds 002_b.md"):
        rename.rename(env.control, "code", "engine", str(env.root / "engine"))
    (env.control / "queue" / "code" / "002_b.md").unlink()

    lines = rename.rename(env.control, "code", "engine", str(env.root / "engine"))
    assert any("moved" in x for x in lines)
    assert (env.root / "engine" / "a.txt").is_file() and not env.repo.exists()
    cfg = config.load(env.control)
    assert cfg.repos == {"engine": (env.root / "engine").resolve()}
    assert (env.control / "done" / "engine" / "001_a.md").read_text(encoding="utf-8").startswith("---\nrepo: engine\n")
    run = json.loads((env.control / "done" / "engine" / "001_a.run.json").read_text(encoding="utf-8"))
    assert run["item"] == "engine/001_a" and run["repo"] == "engine" and "engine__001_a" in run["report"]
    assert (env.control / "reports" / "engine__001_a.md").is_file()
    assert not list((env.control / "reports").glob("code__*"))
    state = (env.control / "state.md").read_text(encoding="utf-8")
    assert "- `engine`: ../engine (the code)" in state and "`engine/docs/x.md`; code review is prose" in state
    assert git(env.control, "status", "--porcelain").strip() == ""
    assert git(env.control, "log", "-1", "--format=%s").strip() == "rename repo code -> engine"

    log = EventLog(cfg.events_db)
    try:
        assert not log.query(repo="code")
        assert log.last("finish", item="engine/001_a") is not None
        ev = log.last("repo_renamed")
        assert ev.detail["old"] == "code" and ev.detail["moved"] is True
        with pytest.raises(Exception, match="append-only"):
            log.conn.execute("UPDATE events SET kind = 'x' WHERE id = 1")
    finally:
        log.close()

    env.d = Daemon(config.load(env.control), echo=env.lines.append, notifier=env.notifier)
    env.d.startup()
    assert not env.events("drift")
    env.queue("002_b", "FAKE commit b.txt b", repo="engine")
    env.tick_until(lambda: env.finished("engine/002_b"))
    assert env.finished("engine/002_b").detail["status"] == "done"
    assert (env.root / "engine" / "b.txt").is_file()


def test_rekey_rewrites_the_key_not_the_word():
    forms = zip(rename._path_forms(Path(r"C:\x\code")), rename._path_forms(Path(r"C:\x\engine")))
    rk = rename.Rekey("code", "engine", [*forms, ("../code", "../engine")])
    assert rk.text("message for code/007_x: see src/code/main.rs and the code") == \
        "message for engine/007_x: see src/code/main.rs and the code"
    assert rk.text(r'"C:\\x\\code\\a", reports/code__W3_watch.md, 2026__code__001_a.md, queue/code, ../code (') == \
        r'"C:\\x\\engine\\a", reports/engine__W3_watch.md, 2026__engine__001_a.md, queue/engine, ../engine ('
    assert rk.text("- `code`: idle; `code/docs`; codebase/001_x, ../codex") == \
        "- `engine`: idle; `engine/docs`; codebase/001_x, ../codex"
    assert rk.value({"items": {"code/001_a": 1}, "repo": "code", "note": "code"}) == \
        {"items": {"engine/001_a": 1}, "repo": "engine", "note": "engine"}


def test_rename_repo_refuses_bad_names(env):
    env.d.release()
    for new, why in (("code", "the old one"), ("a__b", "may not"), ("x", None)):
        if why:
            with pytest.raises(rename.RenameError, match=why):
                rename.rename(env.control, "code", new)
    with pytest.raises(rename.RenameError, match="no repo 'nope'"):
        rename.rename(env.control, "nope", "x")
