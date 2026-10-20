"""The `open:` verbs, `notify:`, `open_roots` and `conductor open-path`; windows stubbed."""

import json

import pytest

from conductor import cli, desktop, procs

from conftest import git


def send(env, text: str, name: str = "o.signal") -> None:
    env.signal(name, text)
    env.tick_until(lambda: not (env.control / "signals" / name).exists())


@pytest.fixture
def shown(env) -> list[dict]:
    """What would have been shown: each launch spec, nothing on the desktop."""
    out: list[dict] = []
    env.d.opener.launch = out.append
    return out


def files(env) -> dict:
    (env.repo / "docs").mkdir(exist_ok=True)
    made = {"page": env.repo / "docs" / "page.html", "script": env.repo / "tool.py",
            "prog": env.repo / "setup.exe", "batch": env.repo / "go.bat", "note": env.control / "notes.md"}
    for p in made.values():
        p.write_text("x\n", encoding="utf-8")
    return made


def test_each_verb_opens_and_logs_opened(env, shown):
    f = files(env)
    send(env, "by: planner\n"
              f"open: folder {env.repo / 'docs'}\n"
              f"open: reveal {f['page']}\n"
              f"open: file {f['page']}\n"
              f"open: text {f['script']}\n"
              "open: file notes.md\n"                       # relative: from the control folder
              "open: url https://example.com/a?b=1#frag\n"
              "open: url http://localhost:5173/\n")
    opened = env.events("opened")
    assert [e.detail["verb"] for e in opened] == ["folder", "reveal", "file", "text", "file", "url", "url"]
    assert all(e.detail["by"] == "planner" for e in opened) and not env.events("open_refused")
    assert opened[2].detail["path"] == str(f["page"].resolve())
    assert opened[4].detail["path"] == str(f["note"].resolve()) and "notes.md" in opened[4].paths
    assert opened[5].detail["url"] == "https://example.com/a?b=1#frag"
    if procs.WINDOWS:
        assert shown[0] == {"how": "run", "target": f'explorer.exe "{(env.repo / "docs").resolve()}"'}
        assert shown[1] == {"how": "run", "target": f'explorer.exe /select,"{f["page"].resolve()}"'}
        assert shown[2] == {"how": "startfile", "target": str(f["page"].resolve())}
        assert shown[3]["target"].startswith("notepad.exe ")
        assert shown[6] == {"how": "startfile", "target": "http://localhost:5173/"}
    assert len(shown) == 7


def test_refusals_are_events_not_rejected_signals(env, shown, tmp_path):
    f = files(env)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("x\n", encoding="utf-8")
    link = env.repo / "escape"
    procs.link_dir(outside, link)                       # a junction out of the repo
    cases = [
        ("folder", str(outside), "is outside"),
        ("file", str(env.control / ".." / "outside" / "secret.txt"), "is outside"),
        ("file", str(link / "secret.txt"), "is outside"),
        ("folder", str(env.repo / "nope"), "does not exist"),
        ("file", str(f["script"]), ".py is a program"),
        ("file", str(f["prog"]), ".exe is a program"),
        ("file", str(f["batch"]), ".bat is a program"),
        ("folder", str(f["page"]), "is a file, not a folder"),
        ("file", str(env.repo / "docs"), "is a folder, not a file"),
        ("text", str(env.repo / "docs"), "is a folder, not a file"),
        ("url", "http://example.com/", "only https://"),
        ("url", "ftp://localhost/x", "only https://"),
        ("url", "file:///C:/Windows", "only https://"),
        ("url", "https://user:pw@example.com/", "user name or password"),
        ("url", "https://", "only https://"),
    ]
    send(env, "by: planner\n" + "".join(f"open: {v} {t}\n" for v, t, _ in cases) + "notify: still applied")
    refused = env.events("open_refused")
    assert [(e.detail["verb"], e.detail["target"]) for e in refused] == [(v, t) for v, t, _ in cases]
    for e, (_, _, why) in zip(refused, cases):
        assert why in e.detail["reason"], (e.detail, why)
        assert e.detail["by"] == "planner"
    assert not shown and not env.events("opened") and not env.events("invalid")
    assert env.events("notify") and not (env.control / "signals" / "o.signal.bad").exists()
    assert not any(e.kind == "red" for e in env.d.log.query())


def test_malformed_open_lines_reject_the_file(env, shown):
    for i, line in enumerate(["open: launch C:/x", "open: folder", "notify:"]):
        send(env, f"{line}\n", f"m{i}")
        assert (env.control / "signals" / f"m{i}.bad").is_file()
    assert len(env.events("invalid")) == 3 and not shown


def test_open_roots_extends_where_paths_may_be(env, shown, tmp_path):
    extra = tmp_path / "gdrive" / "proj"
    extra.mkdir(parents=True)
    toml = env.control / "conductor.toml"
    toml.write_text(f"open_roots = [{json.dumps(str(extra))}]\n" + toml.read_text(encoding="utf-8"),
                    encoding="utf-8", newline="\n")
    git(env.control, "commit", "-qam", "open_roots")
    env.d.log.close()
    env.d = env.new_daemon()
    env.d.opener.launch = shown.append
    send(env, f"open: folder {extra}\nopen: folder {extra.parent}")
    assert env.events("opened")[0].detail["path"] == str(extra.resolve())
    assert "is outside" in env.events("open_refused")[0].detail["reason"]


def test_notify_toasts_one_line(env):
    send(env, "by: planner\nnotify: ready when you are # the build is up")
    [ev] = env.events("notify")
    assert ev.detail == {"text": "ready when you are # the build is up", "by": "planner"}
    assert ("test: from the planner", "ready when you are # the build is up", f"test#{ev.id}") in env.toasts


@pytest.mark.skipif(not procs.WINDOWS, reason="Windows' own executable test")
def test_windows_judges_documents_safe_and_programs_not():
    assert desktop._assoc_is_dangerous(".exe") and desktop._assoc_is_dangerous(".bat")
    for ext in (".html", ".md", ".png", ".txt", ".pdf", ".json"):
        assert not desktop._assoc_is_dangerous(ext), ext


def test_cli_open_path(env, monkeypatch, capsys):
    shown = []
    monkeypatch.setattr(desktop.Opener, "_launch", lambda self, spec: shown.append(spec))
    f = files(env)
    ctl = ["--control", str(env.control)]
    assert cli.main(["open-path", "reveal", str(f["page"]), *ctl]) == 0
    assert "opened (reveal)" in capsys.readouterr().out
    assert cli.main(["open-path", "file", str(f["prog"]), *ctl]) == 1
    assert "refused" in capsys.readouterr().out
    (env.repo / "a b").mkdir()
    assert cli.main(["open-path", "folder", f"{env.repo}/a", "b", *ctl]) == 0     # words rejoined
    assert len(shown) == 2 and shown[1]["target"].endswith('a b"') == procs.WINDOWS
    assert [e.detail["by"] for e in env.d.log.query(kinds=["opened", "open_refused"])] == ["cli"] * 3
