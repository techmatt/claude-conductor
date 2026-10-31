import json
import os
import time

import pytest

from pathlib import Path

from conductor import globs, state

from conftest import git


def main_head(env) -> str:
    return git(env.repo, "rev-parse", "main").strip()


def branches(env) -> list[str]:
    return git(env.repo, "branch", "--format=%(refname:short)").split()


def report(env, name: str) -> str:
    return (env.control / "reports" / f"code__{name}.md").read_text(encoding="utf-8")


# ---------------------------------------------------------------- globs
def test_glob_matching():
    assert globs.matches("src/a/b.py", "src/**")
    assert globs.matches("src/a/b.py", "src")
    assert globs.matches("src/a/b.py", "src/**/*.py")
    assert globs.matches("README.md", "*.md")
    assert not globs.matches("docs/README.md", "*.md")
    assert globs.matches("Docs/x.md", "docs/*")
    assert globs.outside(["a/x", "b/y"], ["a/**"]) == ["b/y"]
    assert globs.invalid("../x") and globs.invalid("/abs") and globs.invalid("C:/x") and not globs.invalid("a/*")


def test_glob_disjointness_is_conservative():
    assert globs.disjoint(["src/a/**"], ["src/b/*.py"])
    assert globs.disjoint(["docs/*.md"], ["src/**"])
    assert not globs.disjoint(["src/**"], ["src/a/x.py"])
    assert not globs.disjoint(["src"], ["src/a/x.py"])
    assert not globs.disjoint(["*.md"], ["README.md"])
    assert not globs.disjoint(["src/*"], ["src/*.py"])        # both wild: assumed to overlap
    assert not globs.disjoint(["**/x"], ["a/y"])
    assert globs.disjoint(["a/*.py"], ["a/x.md"])
    assert not globs.disjoint([], ["a"])                       # undeclared never shares


# ---------------------------------------------------------------- worktrees and merges
def test_item_runs_in_worktree_and_merges(env):
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_a"))
    fin = env.finished("code/001_a")
    assert fin.detail["status"] == "done" and fin.detail["merged"] == main_head(env)
    assert (env.repo / "a.txt").read_text(encoding="utf-8") == "a\n"
    start = env.events("start")[0]
    assert start.detail["branch"] == "conductor/001_a"
    [merge] = env.events("merge")
    assert merge.detail["commits"] == 1
    assert "conductor/001_a" not in branches(env) and not env.worktree("001_a").exists()
    assert [e.detail["action"] for e in env.events("worktree")] == ["created", "removed"]


@pytest.mark.slow
def test_disjoint_paths_run_at_once_overlapping_in_turn(env):
    env.queue("001_a", f"{env.hold()}\nFAKE commit a/x.txt a", paths="['a/**']")
    env.queue("002_b", "FAKE commit b/y.txt b", paths="['b/**']")
    env.queue("003_a2", "FAKE commit a/z.txt z", paths="['a/z.txt']", stage="false")
    env.tick_until(lambda: env.d.log.last("start", item="code/002_b"))
    env.release()
    env.tick_until(lambda: env.finished("code/003_a2"))
    start = {e.item: e.id for e in env.events("start")}
    fin = {e.item: e.id for e in env.events("finish")}
    assert start["code/002_b"] < fin["code/001_a"]           # ran beside 001
    assert start["code/003_a2"] > fin["code/001_a"]          # overlaps 001: waited
    assert all(e.detail["status"] == "done" for e in env.events("finish"))
    for f in ("a/x.txt", "b/y.txt", "a/z.txt"):
        assert (env.repo / f).is_file()
    assert len(env.events("merge")) == 3


def test_paths_violation_is_red_and_unmerged(env):
    before = main_head(env)
    env.queue("001_out", "FAKE commit a/ok.txt ok\nFAKE commit b.txt nope", paths="['a/**']")
    env.tick_until(lambda: env.finished("code/001_out"))
    fin = env.finished("code/001_out")
    assert fin.detail["status"] == "red" and "paths violation" in fin.detail["reasons"][0]
    [pv] = env.events("paths_violation")
    assert pv.detail["files"] == ["b.txt"]
    assert main_head(env) == before and "conductor/001_out" in branches(env)
    assert not env.events("merge")


@pytest.mark.slow
def test_commit_to_main_during_run_conflicts(env):
    before = main_head(env)
    env.queue("001_c", f"{env.hold()}\nFAKE commit app.txt from the item")
    env.tick_until(lambda: env.events("start"))
    (env.repo / "app.txt").write_text("from main\n", encoding="utf-8")
    git(env.repo, "add", "app.txt")
    git(env.repo, "commit", "-qm", "direct")
    direct = main_head(env)
    env.release()
    env.tick_until(lambda: env.finished("code/001_c"))
    fin = env.finished("code/001_c")
    assert fin.detail["status"] == "red" and any("conflicted" in r for r in fin.detail["reasons"])
    [c] = env.events("conflict")
    assert c.detail["files"] == ["app.txt"] and not env.events("resolve")
    assert main_head(env) == direct != before
    assert "conductor/001_c" in branches(env) and env.worktree("001_c").is_dir()
    assert not git(env.worktree("001_c"), "status", "--porcelain").strip()     # rebase aborted


# ---------------------------------------------------------------- prose files
def test_prose_globs_are_outside_disjointness():
    prose = ["CLAUDE.md", "README.md", "docs/**/*.md"]
    assert globs.only_prose("README.md", prose) and globs.only_prose("docs/a/b.md", prose)
    assert globs.only_prose("docs/**/*.md", prose)
    assert not globs.only_prose("docs", prose) and not globs.only_prose("*.md", prose)
    assert globs.disjoint(["a/**", "README.md"], ["b/**", "README.md"], prose)
    assert not globs.disjoint(["a/**", "README.md"], ["b/**", "README.md"])
    assert globs.disjoint(["README.md"], ["src/**"], prose)          # a prose-only item shares
    assert not globs.disjoint([], ["src/**"], prose)                 # undeclared still never does


def two_concurrent(env, a_lines: str, b_lines: str) -> None:
    """001 and 002 run at once in disjoint code paths; 002 merges first: 001 goes on only
    once 002 has finished (merged), whatever the machine's load."""
    go = env.root / "go"
    env.queue("001_a", f"FAKE wait-for {go.as_posix()} 60\nFAKE commit a/x.txt a\n" + a_lines, paths="['a/**']")
    env.queue("002_b", "FAKE commit b/y.txt b\n" + b_lines, paths="['b/**']", stage="false")
    env.tick_until(lambda: env.finished("code/002_b"))
    go.touch()
    env.tick_until(lambda: env.finished("code/001_a"))
    start = {e.item: e.id for e in env.events("start")}
    fin = {e.item: e.id for e in env.events("finish")}
    assert start["code/002_b"] < fin["code/001_a"]            # README edits did not serialise them


@pytest.mark.slow
def test_prose_edits_merge_textually_beside_disjoint_code(env):
    (env.repo / "README.md").write_text("top\ncode\nbottom\n", encoding="utf-8", newline="\n")
    git(env.repo, "commit", "-qam", "longer readme")
    two_concurrent(env, "FAKE prepend README.md from a", "FAKE append README.md from b")
    assert all(e.detail["status"] == "done" for e in env.events("finish"))
    assert (env.repo / "README.md").read_text(encoding="utf-8") == "from a\ntop\ncode\nbottom\nfrom b\n"
    assert not env.events("resolve") and not env.events("paths_violation")


@pytest.mark.slow
def test_prose_conflict_is_resolved_by_a_merge_executor(env):
    two_concurrent(env, "FAKE commit README.md from a", "FAKE commit README.md from b")
    fin = env.finished("code/001_a")
    assert fin.detail["status"] == "done", fin.detail
    [res] = env.events("resolve")
    assert res.item == "code/001_a" and res.detail["files"] == ["README.md"]
    merge = env.d.log.last("merge", item="code/001_a")
    assert merge.detail["resolved"] == ["README.md"]
    text = (env.repo / "README.md").read_text(encoding="utf-8")
    assert "from a" in text and "from b" in text and "<<<<<<<" not in text
    assert (env.repo / "a" / "x.txt").is_file() and not env.worktree("001_a").exists()
    assert (env.control / "reports" / "code__001_a.merge.log").is_file()


@pytest.mark.slow
def test_failed_merge_executor_is_red_with_both_versions_kept(env):
    two_concurrent(env, "FAKE merge-fail\nFAKE commit README.md from a", "FAKE commit README.md from b")
    fin = env.finished("code/001_a")
    assert fin.detail["status"] == "red" and any("merge executor failed" in r for r in fin.detail["reasons"])
    [c] = env.events("conflict")
    assert c.detail["files"] == ["README.md"] and "markers remain" in c.detail["error"] and c.detail["resolver"]
    assert (env.repo / "README.md").read_text(encoding="utf-8") == "from b\n"           # main's version
    wt = env.worktree("001_a")
    assert git(wt, "show", "conductor/001_a:README.md") == "from a\n"                     # the item's
    assert wt.is_dir() and not git(wt, "status", "--porcelain").strip()                  # rebase aborted


@pytest.mark.slow
def test_merge_executor_touching_other_files_is_red(env):
    two_concurrent(env, "FAKE merge-stray\nFAKE commit README.md from a", "FAKE commit README.md from b")
    fin = env.finished("code/001_a")
    assert fin.detail["status"] == "red"
    assert "untracked" in env.d.log.last("conflict", item="code/001_a").detail["error"]


@pytest.mark.slow
def test_prose_and_code_conflict_together_is_a_plain_conflict(env):
    two_concurrent(env, "FAKE commit README.md from a\nFAKE commit c.txt a",
                   "FAKE commit README.md from b\nFAKE commit c.txt b")
    assert env.finished("code/001_a").detail["status"] == "red"
    assert not env.events("resolve")


def test_prose_outside_paths_is_no_violation(env):
    env.queue("001_a", "FAKE commit a/x.txt a\nFAKE commit docs/guide/use.md how\nFAKE commit CLAUDE.md c",
              paths="['a/**']")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done" and not env.events("paths_violation")


def test_prose_setting_replaces_the_default(env):
    env.configure('[repos.code]\npath = "../code"\nprose = []\n')
    env.queue("001_a", "FAKE commit a/x.txt a\nFAKE commit README.md r", paths="['a/**']")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "red" and env.events("paths_violation")


# ---------------------------------------------------------------- leftover worktrees
def flaky_discard(monkeypatch, failing: set[str]):
    """worktrees.discard fails with PermissionError 13 for existing trees named in `failing`."""
    from conductor import worktrees
    real = worktrees.discard

    def discard(cfg, repo, path, branch):
        if path.name in failing and path.exists():
            raise PermissionError(13, "The process cannot access the file", str(path / "held.log"))
        return real(cfg, repo, path, branch)
    monkeypatch.setattr(worktrees, "discard", discard)


@pytest.mark.slow
def test_failed_removal_is_retried_on_queue_empty_and_at_start(env, monkeypatch):
    failing = {"001_a"}
    flaky_discard(monkeypatch, failing)
    env.queue("001_a", "FAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done" and env.worktree("001_a").is_dir()
    assert any("retried when" in e.detail["note"] for e in env.events("warning"))
    leftovers = env.control / ".conductor" / "leftovers.json"
    assert leftovers.is_file()
    # The next time the queue empties, the removal is retried; still held: a warning.
    env.queue("002_b", "FAKE commit b.txt b")
    env.tick_until(lambda: env.finished("code/002_b"))
    assert any("still not removed (queue empty)" in e.detail["note"] for e in env.events("warning"))
    # Whatever held it lets go; the next daemon start removes it.
    failing.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert not env.worktree("001_a").exists() and not leftovers.exists()
    last = env.d.log.last("worktree", item="code/001_a")
    assert last.detail["action"] == "removed" and "leftover removed on retry (daemon start)" in last.detail["note"]
    assert "conductor/001_a" not in branches(env)


# ---------------------------------------------------------------- shared folders and ultracode
@pytest.mark.slow
def test_missing_shared_folder_is_created_in_the_main_checkout(env):
    env.configure('[repos.code]\npath = "../code"\nshared = ["data/cache"]\n')
    env.queue("001_use", "FAKE write data/cache/x.bin made\nFAKE check data/cache/x.bin\nFAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_use"))
    assert env.finished("code/001_use").detail["status"] == "done", report(env, "001_use")
    assert (env.repo / "data" / "cache" / "x.bin").is_file()            # written through the link
    created = env.d.log.query(kinds=["worktree"])[0]
    assert "created in the main checkout" in created.detail["note"]
    assert any("not git-ignored" in e.detail["note"] for e in env.events("warning"))


@pytest.mark.slow
def test_ultracode_item_launches_with_the_settings_key(env):
    import json
    env.queue("001_u", "FAKE report done", ultracode="true")
    env.queue("002_plain", "FAKE report done")
    env.tick_until(lambda: env.finished("code/002_plain"))
    settings = {n: json.loads((env.control / "reports" / f"code__{n}.settings.json").read_text(encoding="utf-8"))
                for n in ("001_u", "002_plain")}
    assert settings["001_u"]["ultracode"] is True and "hooks" in settings["001_u"]
    assert "ultracode" not in settings["002_plain"]
    assert env.d.log.last("enqueue", item="code/001_u").detail["ultracode"] is True


@pytest.mark.slow
def test_dirty_main_holds_merge_until_clean(env):
    env.queue("001_a", "FAKE sleep 0.5\nFAKE commit a.txt a")
    env.tick_until(lambda: env.events("start"))
    (env.repo / "README.md").write_text("editing\n", encoding="utf-8")
    env.tick_until(lambda: env.events("hold"))
    [hold] = env.events("hold")
    assert hold.detail["merge"] and "uncommitted" in hold.detail["reason"]
    for _ in range(5):
        env.d.tick()
    assert len(env.events("hold")) == 1 and not env.finished("code/001_a")
    inbox = (env.control / "inbox.md").read_text(encoding="utf-8")
    assert "## Holds" in inbox and "merging" in inbox
    git(env.repo, "checkout", "README.md")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done"
    assert (env.repo / "a.txt").is_file()


def part_way_ff(env, monkeypatch, failures: int, extra: dict[str, str] | None = None) -> list[int]:
    """The main checkout's first `failures` fast-forwards stop part-way as on Windows: the
    branch's files written into the checkout, then "unable to write new index file";
    `extra` writes files that are not the branch's too. Returns the attempt count."""
    from conductor import git as gitmod, worktrees
    monkeypatch.setattr(worktrees, "FF_BACKOFF", 0)
    real, calls = gitmod.run, [0]

    def run(repo, *args, **kw):
        if Path(repo) == env.repo and args[:3] == ("merge", "-q", "--ff-only"):
            calls[0] += 1
            if calls[0] <= failures:
                for f in real(repo, "diff", "--name-only", "HEAD", args[3]).split():
                    (env.repo / f).write_bytes(real(repo, "show", f"{args[3]}:{f}").encode("utf-8"))
                for f, text in (extra or {}).items():
                    (env.repo / f).write_text(text, encoding="utf-8", newline="\n")
                raise gitmod.GitError(f"git {' '.join(args)}: error: unable to write new index file")
        return real(repo, *args, **kw)
    monkeypatch.setattr(gitmod, "run", run)
    return calls


@pytest.mark.slow
def test_part_way_fast_forward_is_restored_and_retried(env, monkeypatch):
    calls = part_way_ff(env, monkeypatch, failures=2)
    env.queue("001_a", "FAKE commit README.md new\nFAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done" and calls[0] == 3
    assert (env.repo / "README.md").read_text(encoding="utf-8") == "new\n" and (env.repo / "a.txt").is_file()
    restored = [e for e in env.events("warning") if "restored to its HEAD" in e.detail["note"]]
    assert restored and restored[0].detail["files"] == ["README.md", "a.txt"]
    assert not env.events("checkout_dirty") and not env.events("hold")


@pytest.mark.slow
def test_part_way_fast_forward_with_foreign_changes_is_red_and_holds(env, monkeypatch):
    (env.repo / "other.txt").write_text("mine\n", encoding="utf-8", newline="\n")
    git(env.repo, "add", "other.txt")
    git(env.repo, "commit", "-qm", "other")
    part_way_ff(env, monkeypatch, failures=1, extra={"other.txt": "edited by hand\n"})
    env.queue("001_a", "FAKE commit README.md new")
    env.tick_until(lambda: env.events("checkout_dirty"))
    [dirty] = env.events("checkout_dirty")
    assert dirty.item == "code/001_a" and dirty.detail["files"] == ["other.txt"]
    assert "unable to write new index file" in dirty.detail["error"]
    assert (env.repo / "README.md").read_text(encoding="utf-8") == "new\n"        # nothing touched
    assert not env.finished("code/001_a") and env.events("hold")
    # Put right by hand: the branch's own leftover goes back by itself, the merge lands.
    git(env.repo, "checkout", "other.txt")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done"
    assert any("restored to its HEAD" in e.detail["note"] for e in env.events("warning"))


@pytest.mark.slow
def test_merge_resumes_after_restart(env):
    env.queue("001_a", "FAKE commit a.txt a")
    (env.repo / "README.md").write_text("editing\n", encoding="utf-8")
    env.tick_until(lambda: env.events("hold"))
    env.d.runs.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert env.d.log.last("recover", item="code/001_a")
    git(env.repo, "checkout", "README.md")
    env.tick_until(lambda: env.finished("code/001_a"))
    assert env.finished("code/001_a").detail["status"] == "done" and (env.repo / "a.txt").is_file()
    assert len(env.events("start")) == 1


@pytest.mark.slow
def test_shared_folder_is_linked_not_copied(env):
    (env.repo / ".gitignore").write_text("data/\n", encoding="utf-8")
    git(env.repo, "add", ".gitignore")
    git(env.repo, "commit", "-qm", "ignore data")
    (env.repo / "data").mkdir()
    (env.repo / "data" / "big.bin").write_text("payload\n", encoding="utf-8")
    env.configure('[repos.code]\npath = "../code"\nshared = ["data"]\nsetup = ["echo setup-ran > setup.txt"]\n')
    env.queue("001_use", "FAKE check data/big.bin\nFAKE check setup.txt\nFAKE commit a.txt a")
    env.tick_until(lambda: env.finished("code/001_use"))
    assert env.finished("code/001_use").detail["status"] == "done", report(env, "001_use")
    assert (env.repo / "data" / "big.bin").is_file()          # the link went, the folder stayed
    assert not env.worktree("001_use").exists()


def test_discard_never_empties_a_shared_folder_even_after_config_drops_it(env):
    from conductor import config, worktrees
    (env.repo / "data").mkdir()
    (env.repo / "data" / "keep.txt").write_text("precious\n", encoding="utf-8")
    env.configure('[repos.code]\npath = "../code"\nshared = ["data"]\n')
    made = worktrees.create(env.d.cfg, "code", "001_x", env.root / "setup.log")
    assert (made.path / "data" / "keep.txt").is_file()
    plain = config.load(env.control)
    plain.repo_opts["code"].shared = []                 # the entry was removed meanwhile
    worktrees.discard(plain, "code", made.path, made.branch)
    assert (env.repo / "data" / "keep.txt").read_text(encoding="utf-8") == "precious\n"
    assert not made.path.exists() and "conductor/001_x" not in branches(env)


def test_leaving_the_branch_is_red_and_nothing_is_lost(env):
    env.queue("001_det", "FAKE commit a.txt a")
    env.d.tick()
    wt = env.worktree("001_det")
    git(wt, "checkout", "-q", "--detach")
    env.tick_until(lambda: env.finished("code/001_det"))
    fin = env.finished("code/001_det")
    assert fin.detail["status"] == "red" and any("left conductor/001_det" in r for r in fin.detail["reasons"])
    assert wt.is_dir() and not env.events("merge")


def test_readonly_worktree_is_discarded(env):
    before = main_head(env)
    env.queue("001_ro", "FAKE report done", readonly="true")
    env.tick_until(lambda: env.finished("code/001_ro"))
    assert env.finished("code/001_ro").detail["status"] == "done"
    assert main_head(env) == before and not env.worktree("001_ro").exists()
    assert "conductor/001_ro" not in branches(env)


@pytest.mark.slow
def test_readonly_item_runs_beside_others_with_its_own_port(env):
    gate = env.control / "gate"
    env.configure('[repos]\ncode = "../code"\n\n[run.game]\nrepo = "code"\ncommand = "x"\n'
                  'url = "http://127.0.0.1:5173/"\n')
    env.queue("001_a", f"FAKE wait-for {gate.as_posix()} 20\nFAKE commit a.txt a", stage="false")
    env.queue("002_ro", f"FAKE wait-for {gate.as_posix()}.ro 20\nFAKE env CONDUCTOR_PORT", readonly="true")
    env.queue("003_b", "FAKE commit b.txt b", stage="false")
    # 002 starts beside 001 (no repo lock), and 003 waits for 001, never for 002.
    env.tick_until(lambda: len(env.events("start")) == 2)
    view = state.scan(env.cfg, env.d.log)
    assert set(view.running) == {"code/001_a", "code/002_ro"}
    assert state.decide(env.cfg, view)["code"].reason == "repo busy: code/001_a running"
    ports = {e.item: e.detail["port"] for e in env.events("start")}
    assert len(set(ports.values())) == 2 and 5173 not in ports.values()
    gate.touch()
    env.tick_until(lambda: env.finished("code/003_b"))
    assert not env.finished("code/002_ro")                      # 003 never waited for it
    Path(f"{gate}.ro").touch()
    env.tick_until(lambda: env.finished("code/002_ro"))
    assert env.finished("code/002_ro").detail["status"] == "done"
    assert f"CONDUCTOR_PORT={ports['code/002_ro']}" in report(env, "002_ro")
    text = (env.control / "reports" / "code__002_ro.prompt.md").read_text(encoding="utf-8")
    assert f"uses port {ports['code/002_ro']} (`CONDUCTOR_PORT`)" in text      # a short item: the light text


@pytest.mark.slow
def test_readonly_item_still_takes_its_named_locks(env):
    gate = env.control / "gate"
    env.queue("001_a", f"FAKE wait-for {gate.as_posix()} 20", locks="['gpu']", stage="false")
    env.queue("002_ro", "FAKE report done", readonly="true", locks="['gpu']", stage="false")
    env.tick_until(lambda: env.events("start"))
    env.d.tick()
    assert state.decide(env.cfg, state.scan(env.cfg, env.d.log))["code"].reason == "lock gpu held by code/001_a"
    gate.touch()
    env.tick_until(lambda: env.finished("code/002_ro"))


@pytest.mark.slow
def test_readonly_item_behind_a_staged_head_starts_at_once(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE commit b.txt b")
    env.queue("003_ro", "FAKE report done", readonly="true")
    env.tick_until(lambda: env.events("staged") and env.finished("code/003_ro"))
    assert not env.finished("code/001_long")
    assert env.finished("code/003_ro").detail["status"] == "done"
    enq, start = env.d.log.last("enqueue", item="code/003_ro"), env.d.log.last("start", item="code/003_ro")
    assert enq.id < start.id
    # The head's stage is untouched by the readonly item beside it.
    srec = state.read_run(env.cfg.path("running", "code", "002_next.stage.json"))
    assert srec["phase"] == "staged" and srec["item"] == "code/002_next"
    view = state.scan(env.cfg, env.d.log)
    assert state.decide(env.cfg, view)["code"].reason == "repo busy: code/001_long running; staged"
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]
    assert env.finished("code/002_next").detail["status"] == "done"
    assert not any("stage discarded" in e.detail.get("note", "") for e in env.events("warning"))
    assert (env.control / "done" / "code" / "003_ro.md").is_file()


@pytest.mark.slow
def test_readonly_item_behind_the_head_waits_for_its_locks_and_dependencies(env):
    env.queue("001_a", env.hold(), locks="['gpu']", stage="false")
    env.queue("002_b", "FAKE commit b.txt b", stage="false")
    env.queue("003_lock", "FAKE report done", readonly="true", locks="['gpu']")
    env.queue("004_dep", "FAKE report done", readonly="true", depends_on="002")
    env.queue("005_free", "FAKE report done", readonly="true")
    env.tick_until(lambda: env.finished("code/005_free"))
    for _ in range(3):
        env.d.tick()
    assert not env.d.log.last("start", item="code/003_lock") and not env.d.log.last("start", item="code/004_dep")
    assert not env.finished("code/001_a")
    env.release()
    env.tick_until(lambda: env.finished("code/003_lock") and env.finished("code/004_dep"))
    fin = {e.item: e.id for e in env.events("finish")}
    assert env.d.log.last("start", item="code/003_lock").id > fin["code/001_a"]
    assert env.d.log.last("start", item="code/004_dep").id > fin["code/002_b"]


def test_an_item_in_setup_holds_its_named_locks_against_an_early_readonly_item(env):
    """`decide` and `early_starts` share one rule (`state.running_locks`): an item still
    in setup holds its named locks, one merging has let them go."""
    env.queue("002_b", "FAKE commit b.txt b")
    env.queue("003_ro", "FAKE report done", readonly="true", locks="['gpu']")
    view = state.scan(env.cfg, env.d.log)
    rec = {"item": "code/001_a", "repo": "code", "name": "001_a", "locks": ["gpu"], "readonly": False,
           "worktree": str(env.worktree("001_a")), "paths": None}
    for phase, expected in (("setup", []), ("exec", []), ("merge", ["code/003_ro"])):
        view.running = {"code/001_a": rec | {"phase": phase}}
        assert state.running_locks(view) == ({} if phase == "merge" else {"gpu": "code/001_a"})
        decisions = state.decide(env.cfg, view)
        assert [i.id for i in state.early_starts(env.cfg, view, decisions)] == expected, phase


@pytest.mark.slow
def test_held_repo_starts_no_readonly_item(env):
    env.queue("001_bad", "FAKE report red")
    env.tick_until(lambda: env.finished("code/001_bad"))
    env.queue("002_next", "FAKE report done", stage="false")
    env.queue("003_ro", "FAKE report done", readonly="true")
    env.tick_until(lambda: env.events("hold"))
    for _ in range(3):
        env.d.tick()
    assert not env.d.log.last("start", item="code/003_ro")
    view = state.scan(env.cfg, env.d.log)
    assert state.early_starts(env.cfg, view, state.decide(env.cfg, view)) == []
    env.signal("clear.txt", "clear: code")
    env.tick_until(lambda: env.finished("code/003_ro"))


@pytest.mark.slow
def test_worktrees_off_runs_in_main_checkout(env):
    env.configure('[repos.code]\npath = "../code"\nworktrees = false\n')
    env.queue("001_a", f"{env.hold()}\nFAKE commit a.txt a", paths="['a.txt']")
    env.queue("002_b", "FAKE commit b.txt b", paths="['b.txt']")
    env.tick_until(lambda: env.events("start"))
    for _ in range(5):
        env.d.tick()
    env.release()
    env.tick_until(lambda: env.finished("code/002_b"))
    assert not env.events("worktree") and not env.events("stage")
    fin = {e.item: e.id for e in env.events("finish")}
    assert env.d.log.last("start", item="code/002_b").id > fin["code/001_a"]
    assert git(env.repo, "log", "-1", "--format=%s").strip() == "fake: b.txt"


# ---------------------------------------------------------------- staging
@pytest.mark.slow
def test_head_is_staged_then_resumed(env):
    env.queue("001_long", f"{env.hold()}\nFAKE commit a.txt a")
    env.queue("002_next", "FAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    assert not env.finished("code/001_long")
    [stage] = env.events("stage")
    assert stage.item == "code/002_next"
    status = env.d.log.query(kinds=["staged"])[0].detail
    assert status["plan"].startswith("Plan:")
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    start = env.d.log.last("start", item="code/002_next")
    assert start.detail["resumed"] and start.detail["session_id"] == stage.detail["session_id"]
    assert start.detail["landed"] == 1                      # 001's commit landed while staged
    assert "STAGED MEMORY: recalled the staged prompt" in report(env, "002_next")
    assert (env.repo / "b.txt").is_file() and (env.repo / "a.txt").is_file()
    assert env.finished("code/002_next").detail["status"] == "done"


@pytest.mark.slow
def test_rewrite_of_a_queued_item_takes_effect(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE commit b.txt b", stage="false")
    env.tick_until(lambda: env.d.log.last("start", item="code/001_long"))
    env.queue("002_next", "FAKE commit c.txt c", stage="false")
    env.tick_until(lambda: env.events("edited"))
    [edited] = env.events("edited")
    assert edited.item == "code/002_next" and edited.detail["staged"] is False
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert (env.repo / "c.txt").is_file() and not (env.repo / "b.txt").exists()
    assert len(env.events("edited")) == 1


@pytest.mark.slow
def test_rewrite_of_a_staged_item_restages_it(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    env.queue("002_next", "FAKE commit c.txt c")
    env.tick_until(lambda: len(env.events("staged")) == 2)
    [edited] = env.events("edited")
    assert edited.detail["staged"] is True
    assert any("item file changed while staged" in e.detail["note"] for e in env.events("warning"))
    first, second = env.events("stage")
    assert first.detail["session_id"] != second.detail["session_id"] and second.id > edited.id
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    start = env.d.log.last("start", item="code/002_next")
    assert start.detail["resumed"] and start.detail["session_id"] == second.detail["session_id"]
    assert (env.repo / "c.txt").is_file() and not (env.repo / "b.txt").exists()


@pytest.mark.slow
def test_rewrite_while_staging_kills_and_restages(env):
    from conductor import procs
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE stage-sleep 30\nFAKE commit b.txt b")
    env.tick_until(lambda: env.events("stage"))
    pid = env.events("stage")[0].detail["pid"]
    env.queue("002_next", "FAKE commit c.txt c")
    env.tick_until(lambda: len(env.events("staged")) == 1)
    assert not procs.alive(pid, None)
    assert env.events("edited")[0].detail["staged"] is True
    assert any("item file changed while staging" in e.detail["note"] for e in env.events("warning"))
    assert len(env.events("stage")) == 2
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]
    assert (env.repo / "c.txt").is_file() and not (env.repo / "b.txt").exists()


@pytest.mark.slow
def test_rewrite_during_launch_relaunches_the_new_text(env):
    env.queue("001_next", "FAKE commit b.txt b", stage="false")
    real = env.d._free_port

    def racing():
        # The planner's write lands after this tick's scan, before the move into running/.
        env.d._free_port = real
        env.queue("001_next", "FAKE commit c.txt c", stage="false", max_hours="3")
        return real()
    env.d._free_port = racing
    env.tick_until(lambda: env.finished("code/001_next"))
    [edited] = env.events("edited")
    assert edited.detail["launching"] is True and edited.detail["staged"] is False
    assert [e.detail["action"] for e in env.events("worktree")][:2] == ["created", "discarded"]
    [start] = env.events("start")
    assert start.id > edited.id
    assert len(env.events("enqueue")) == 1
    run = json.loads((env.control / "done" / "code" / "001_next.run.json").read_text(encoding="utf-8"))
    assert run["max_hours"] == 3                                    # the new front-matter, too
    assert (env.repo / "c.txt").is_file() and not (env.repo / "b.txt").exists()


@pytest.mark.slow
def test_write_after_launch_is_set_aside_not_held(env):
    env.queue("001_long", env.hold())
    env.queue("002_next", "FAKE report done", stage="false")
    env.tick_until(lambda: env.d.log.last("start", item="code/001_long"))
    late = env.queue("001_long", "FAKE commit late.txt x")
    os.utime(late, (time.time() - 5, time.time() - 5))
    env.tick_until(lambda: env.events("invalid"))
    [bad] = env.events("invalid")
    assert bad.item == "code/001_long" and "already launched (running)" in bad.detail["error"]
    assert not late.exists() and late.with_name("001_long.md.bad").is_file()
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert not env.events("hold") and not (env.repo / "late.txt").exists()


@pytest.mark.slow
def test_stage_that_writes_is_discarded_and_launched_fresh(env):
    env.queue("001_long", f"{env.hold()}\nFAKE report done")
    env.queue("002_bad", "FAKE stage-write scratch.txt oops\nFAKE commit b.txt b")
    env.tick_until(lambda: any("stage discarded" in e.detail["note"] for e in env.events("warning")))
    env.release()
    env.tick_until(lambda: env.finished("code/002_bad"))
    assert not env.events("staged")
    start = env.d.log.last("start", item="code/002_bad")
    assert not start.detail.get("resumed")
    assert env.finished("code/002_bad").detail["status"] == "done"


@pytest.mark.slow
def test_restart_while_staging_adopts_the_stage(env):
    env.queue("001_long", f"{env.hold()}\nFAKE report done")
    env.queue("002_next", "FAKE stage-sleep 2\nFAKE commit b.txt b")
    env.tick_until(lambda: env.events("stage"))
    env.d.runs.clear()
    env.d.stages.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert any(e.detail.get("stage") for e in env.events("adopt"))
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]
    assert len(env.events("stage")) == 1


@pytest.mark.slow
def test_restart_after_stage_died_discards_it(env):
    from conductor import procs
    env.queue("001_long", f"{env.hold()}\nFAKE report done")
    env.queue("002_next", "FAKE stage-sleep 30\nFAKE commit b.txt b")
    env.tick_until(lambda: env.events("stage"))
    procs.kill_tree(env.events("stage")[0].detail["pid"])
    env.d.runs.clear()
    env.d.stages.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert any("interrupted" in e.detail.get("note", "") for e in env.events("warning"))
    env.release()
    env.tick_until(lambda: env.finished("code/002_next"))
    assert not env.d.log.last("start", item="code/002_next").detail.get("resumed")
    assert env.finished("code/002_next").detail["status"] == "done"
