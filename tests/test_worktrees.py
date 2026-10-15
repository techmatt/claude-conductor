from conductor import globs

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


def test_disjoint_paths_run_at_once_overlapping_in_turn(env):
    env.queue("001_a", "FAKE sleep 2\nFAKE commit a/x.txt a", paths="['a/**']")
    env.queue("002_b", "FAKE commit b/y.txt b", paths="['b/**']")
    env.queue("003_a2", "FAKE commit a/z.txt z", paths="['a/z.txt']", stage="false")
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


def test_commit_to_main_during_run_conflicts(env):
    before = main_head(env)
    env.queue("001_c", "FAKE sleep 1.5\nFAKE commit README.md from the item")
    env.tick_until(lambda: env.events("start"))
    (env.repo / "README.md").write_text("from main\n", encoding="utf-8")
    git(env.repo, "commit", "-qam", "direct")
    direct = main_head(env)
    env.tick_until(lambda: env.finished("code/001_c"))
    fin = env.finished("code/001_c")
    assert fin.detail["status"] == "red" and any("conflicted" in r for r in fin.detail["reasons"])
    [c] = env.events("conflict")
    assert c.detail["files"] == ["README.md"]
    assert main_head(env) == direct != before
    assert "conductor/001_c" in branches(env) and env.worktree("001_c").is_dir()
    assert not git(env.worktree("001_c"), "status", "--porcelain").strip()     # rebase aborted


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


def test_worktrees_off_runs_in_main_checkout(env):
    env.configure('[repos.code]\npath = "../code"\nworktrees = false\n')
    env.queue("001_a", "FAKE sleep 1\nFAKE commit a.txt a", paths="['a.txt']")
    env.queue("002_b", "FAKE commit b.txt b", paths="['b.txt']")
    env.tick_until(lambda: env.finished("code/002_b"))
    assert not env.events("worktree") and not env.events("stage")
    fin = {e.item: e.id for e in env.events("finish")}
    assert env.d.log.last("start", item="code/002_b").id > fin["code/001_a"]
    assert git(env.repo, "log", "-1", "--format=%s").strip() == "fake: b.txt"


# ---------------------------------------------------------------- staging
def test_head_is_staged_then_resumed(env):
    env.queue("001_long", "FAKE sleep 3\nFAKE commit a.txt a")
    env.queue("002_next", "FAKE commit b.txt b")
    env.tick_until(lambda: env.events("staged"))
    assert not env.finished("code/001_long")
    [stage] = env.events("stage")
    assert stage.item == "code/002_next"
    status = env.d.log.query(kinds=["staged"])[0].detail
    assert status["plan"].startswith("Plan:")
    env.tick_until(lambda: env.finished("code/002_next"))
    start = env.d.log.last("start", item="code/002_next")
    assert start.detail["resumed"] and start.detail["session_id"] == stage.detail["session_id"]
    assert start.detail["landed"] == 1                      # 001's commit landed while staged
    assert "STAGED MEMORY: recalled the staged prompt" in report(env, "002_next")
    assert (env.repo / "b.txt").is_file() and (env.repo / "a.txt").is_file()
    assert env.finished("code/002_next").detail["status"] == "done"


def test_stage_that_writes_is_discarded_and_launched_fresh(env):
    env.queue("001_long", "FAKE sleep 2\nFAKE report done")
    env.queue("002_bad", "FAKE stage-write scratch.txt oops\nFAKE commit b.txt b")
    env.tick_until(lambda: env.finished("code/002_bad"))
    assert any("stage discarded" in e.detail["note"] for e in env.events("warning"))
    assert not env.events("staged")
    start = env.d.log.last("start", item="code/002_bad")
    assert not start.detail.get("resumed")
    assert env.finished("code/002_bad").detail["status"] == "done"


def test_restart_while_staging_adopts_the_stage(env):
    env.queue("001_long", "FAKE sleep 4\nFAKE report done")
    env.queue("002_next", "FAKE stage-sleep 2\nFAKE commit b.txt b")
    env.tick_until(lambda: env.events("stage"))
    env.d.runs.clear()
    env.d.stages.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert any(e.detail.get("stage") for e in env.events("adopt"))
    env.tick_until(lambda: env.finished("code/002_next"))
    assert env.d.log.last("start", item="code/002_next").detail["resumed"]
    assert len(env.events("stage")) == 1


def test_restart_after_stage_died_discards_it(env):
    from conductor import procs
    env.queue("001_long", "FAKE sleep 3\nFAKE report done")
    env.queue("002_next", "FAKE stage-sleep 30\nFAKE commit b.txt b")
    env.tick_until(lambda: env.events("stage"))
    procs.kill_tree(env.events("stage")[0].detail["pid"])
    env.d.runs.clear()
    env.d.stages.clear()
    env.d.log.close()
    env.d = env.new_daemon()
    assert any("interrupted" in e.detail.get("note", "") for e in env.events("warning"))
    env.tick_until(lambda: env.finished("code/002_next"))
    assert not env.d.log.last("start", item="code/002_next").detail.get("resumed")
    assert env.finished("code/002_next").detail["status"] == "done"
