"""Prompt 73: tracked docs and `state.md` over their size budgets in the digest's maintenance
facts, with growth since the last digest, and the preamble's thread default and home for
working papers."""

from datetime import datetime

from conftest import git

from conductor import executor, load, session, state
from conductor.items import Item


def facts(env) -> list[str]:
    view = state.scan(env.d.cfg, env.d.log)
    return session.maintenance(env.d.cfg, env.d.log, view, "code", session.doc_growth(env.d.cfg))


def test_docs_over_budget_are_due_with_their_growth(env):
    (env.repo / "docs").mkdir()
    (env.repo / "docs" / "pricing.md").write_text("x" * 20 * 1024, encoding="utf-8")
    (env.repo / "untracked.md").write_text("x" * 20 * 1024, encoding="utf-8")     # not tracked: not listed
    (env.repo / "results" / "a").mkdir(parents=True)
    (env.repo / "results" / "a" / "report.md").write_text("x" * 20 * 1024, encoding="utf-8")   # output, not a doc
    git(env.repo, "add", "docs/pricing.md", "results")
    git(env.repo, "commit", "-qm", "docs")
    first = [ln for ln in facts(env) if "**due**" in ln]
    assert first == ["- **due** doc trim: `docs/pricing.md` 20.0 KB, budget 15 KB, no earlier digest to compare"]
    (env.repo / "docs" / "pricing.md").write_text("x" * 22 * 1024, encoding="utf-8")
    assert [ln for ln in facts(env) if "**due**" in ln] == [
        "- **due** doc trim: `docs/pricing.md` 22.0 KB, budget 15 KB, +2.0 KB since the last digest"]
    env.d.cfg.doc_budget_kb = 30                           # `[defaults] doc_budget_kb`: nothing under it is listed
    assert not [ln for ln in facts(env) if "pricing" in ln]


def test_a_doc_is_root_markdown_docs_or_a_readme():
    for rel in ("notes.md", "docs/a/b.md", "web/README.md", "app/CLAUDE.md"):
        assert session.is_doc(rel), rel
    for rel in ("experiments/results/x/report.md", "docs/notes.json", "web/guide.md"):
        assert not session.is_doc(rel), rel


def test_state_md_past_its_trim_line_is_due(env):
    cfg = env.d.cfg
    sizes, prev = session.doc_growth(cfg)
    assert session.size_line("state.md", sizes, prev, session.STATE_TRIM_KB, "trim state") is None
    (env.control / "state.md").write_text("y" * 26 * 1024, encoding="utf-8")
    sizes, prev = session.doc_growth(cfg)
    line = session.size_line("state.md", sizes, prev, session.STATE_TRIM_KB, "trim state")
    assert line.startswith("- **due** trim state: `state.md` 26.0 KB, budget 25 KB, +26.0 KB")


def test_the_full_preamble_gives_a_thread_count_and_a_home_for_working_papers(env):
    item = Item("code", "076_cards", env.control / "queue" / "code" / "076_cards.md", num=76, body="x " * 300)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"worktree": str(env.root / "wt"), "branch": "conductor/076_cards", "base": "0123456789abcdef"}
    text = executor.build_prompt(env.cfg, item, paths, 1.0, datetime(2026, 11, 3, 15), run)
    text = " ".join(text.split(executor.PROMPT_FOLLOWS)[0].split())
    assert f"simulation and test runs at {load.run_threads()} threads" in text
    assert f"leaves at least {load.FREE_THREADS} hardware threads free" in text
    assert "`experiments/results/076_cards/`), never `out/`" in text
