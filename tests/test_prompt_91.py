"""Prompt 91: new-project setup. `conductor init` creates a missing code repo (or gives
one with no commits its first), writes `main = "<branch>"` for a repo on another branch;
until a repo has a remote, session.md and the app's repo header say so; the stub
`state.md` has every section planner.md expects."""

from conductor import config, git as git_mod, scaffold, session
from conductor.util import read_text
from conftest import git, init_repo


def _init(tmp_path, *repos: str) -> tuple[list[str], config.Config]:
    _, notes = scaffold.init(tmp_path / "proj-control", "proj", list(repos))
    return notes, config.load(tmp_path / "proj-control")


def test_init_creates_a_missing_repo_with_main_and_one_empty_commit(tmp_path):
    notes, cfg = _init(tmp_path, f"app={tmp_path / 'app'}")
    repo = tmp_path / "app"
    assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
    assert git(repo, "rev-list", "--count", "main").strip() == "1"
    assert git(repo, "remote").strip() == ""
    assert notes[0].startswith(f"repo app: created {repo}")
    assert notes[1] == "app has no remote: create its GitHub repo (session.md says so until it has one)"
    assert cfg.opts("app").main == "main"


def test_init_makes_a_plain_folder_a_repo_and_commits_an_empty_one(tmp_path):
    (tmp_path / "plain").mkdir()
    (tmp_path / "plain" / "notes.txt").write_text("keep\n", encoding="utf-8")
    (tmp_path / "empty").mkdir()
    git(tmp_path / "empty", "init", "-q", "-b", "trunk")      # no commits, unborn branch: becomes main
    notes, _ = _init(tmp_path, f"plain={tmp_path / 'plain'}", f"empty={tmp_path / 'empty'}")
    for name in ("plain", "empty"):
        assert git(tmp_path / name, "symbolic-ref", "HEAD").strip() == "refs/heads/main"
        assert git(tmp_path / name, "rev-list", "--count", "main").strip() == "1"
    assert (tmp_path / "plain" / "notes.txt").read_text(encoding="utf-8") == "keep\n"      # untouched, untracked
    assert any("made" in n and "plain" in n for n in notes)
    assert any("had no commits" in n for n in notes)


def test_init_writes_main_for_a_repo_on_another_branch(tmp_path):
    init_repo(tmp_path / "old", {"a.txt": "a\n"})
    git(tmp_path / "old", "branch", "-m", "main", "master")
    init_repo(tmp_path / "ok", {"a.txt": "a\n"})
    git(tmp_path / "ok", "remote", "add", "origin", "https://example.invalid/ok.git")
    notes, cfg = _init(tmp_path, f"old={tmp_path / 'old'}", f"ok={tmp_path / 'ok'}")
    assert cfg.opts("old").main == "master" and cfg.opts("ok").main == "main"
    assert 'main = "master"' in read_text(tmp_path / "proj-control" / "conductor.toml")
    assert notes == ['repo old: its branch is master, written as main = "master"',
                     "old has no remote: create its GitHub repo (session.md says so until it has one)"]


def test_the_stub_state_md_has_every_section_in_order(tmp_path):
    _init(tmp_path, f"app={tmp_path / 'app'}", f"web={tmp_path / 'web'}")
    control = tmp_path / "proj-control"
    text = read_text(control / "state.md")
    heads = [ln[3:] for ln in text.splitlines() if ln.startswith("## ")]
    assert heads == ["Repos", "Queued and running", "Waiting on Matt", "Open questions", "Paged",
                     "Pending fixes", "Maintenance", "Planner habits"]
    maint = session.state_sections(text)["maintenance"]
    for repo in ("app", "web"):
        line = next(ln for ln in maint if ln.startswith(f"- `{repo}`"))
        assert all(f"{word} never" in line for _, word in session.MAINTENANCE)
    # Nothing in the stub reads as due, and an empty Planner habits section relays nothing.
    assert not any("due" in ln.lower() for ln in session.from_state(control) if ln.startswith("- "))
    assert session.planner_habits(control) == []


def test_session_md_names_a_repo_with_no_remote_until_it_has_one(env):
    line = "**code has no remote: create its GitHub repo**"
    env.signal("planner-1.signal", "by: planner\nsession: start")
    env.tick_until(lambda: env.events("checkpoint"))
    assert line in read_text(env.control / "session.md")
    git(env.repo, "remote", "add", "origin", "https://example.invalid/code.git")
    env.signal("planner-2.signal", "by: planner\nsession: again")
    env.tick_until(lambda: len(env.events("checkpoint")) == 2)
    text = read_text(env.control / "session.md")
    assert "no remote" not in text


def test_remote_missing_caches_its_answer(tmp_path):
    init_repo(tmp_path / "r", {"a.txt": "a\n"})
    repos = {"r": tmp_path / "r"}
    assert git_mod.remote_missing(repos, ttl=0) == ["r"]
    git(tmp_path / "r", "remote", "add", "origin", "https://example.invalid/r.git")
    assert git_mod.remote_missing(repos) == ["r"]       # within the minute: the kept answer
    assert git_mod.remote_missing(repos, ttl=0) == []
    assert git_mod.remote_missing({"gone": tmp_path / "gone"}, ttl=0) == []     # unreadable: left out
