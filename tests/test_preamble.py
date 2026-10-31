"""The executor preamble's variants: light for a small item, full otherwise, with the
picker and server sections only where an item could use them."""

from datetime import datetime

from conductor import executor
from conductor.items import Item

SHORT = ("Import the new word lists: run `npm run words:import` and then `npm run words:check`, "
         "fix anything the check flags, commit the regenerated files, and delete the zip in "
         "`scratch/` once the import is in. Note in the report how many words each list gained "
         "and whether the check flagged anything. ")


def render(env, body: str, **meta) -> str:
    item = Item("code", "001_a", env.control / "queue" / "code" / "001_a.md", num=1, body=body, **meta)
    paths = executor.RunPaths.for_item(env.cfg, item)
    run = {"port": 5200, "worktree": str(env.root / "worktrees" / "001_a"), "branch": "conductor/001_a",
           "base": "0123456789abcdef"}
    text = executor.build_prompt(env.cfg, item, paths, 1.0, datetime(2026, 10, 25, 17), run)
    return text.split(executor.PROMPT_FOLLOWS)[0]


def test_a_short_item_gets_the_light_preamble(env):
    assert len(SHORT.split()) < 80
    text = render(env, SHORT)
    assert len(text.split()) <= 375
    for kept in ("Nobody is watching", "mcp__conductor__ask", "AMENDMENT #n", "run_in_background",
                 "Time limit: 1h", "status: done | partial | red", "slow: none", "`red`:", "uses port 5200", "branch `conductor/001_a`"):
        assert kept in text
    for dropped in ("Outline.", "`eta`", "Pickers.", "pick:", "Servers."):
        assert dropped not in text


def test_a_flagged_or_long_item_gets_the_full_preamble(env):
    for body, meta in ((SHORT, {"ultracode": True}), (SHORT, {"max_hours": 2.0}), (SHORT, {"audit": True}),
                       (SHORT * 3, {})):
        text = render(env, body, **meta)
        assert "Outline." in text and "`eta`" in text
        assert "Pickers." not in text and "pick:" not in text and "Servers." not in text


def test_pickers_only_for_an_item_that_could_produce_candidates(env):
    art = "Make 2-3 candidates for the hero with `art:alts`, write a picker manifest, and end at `pick:`."
    text = render(env, art)                              # short, but full: it needs the schema
    assert "Outline." in text and "Pickers." in text and "pick: <manifest path" in text
    assert '"art_alts_009"' in text


def test_servers_only_when_the_repo_runs_one(env):
    long = SHORT * 3
    assert "Servers." not in render(env, long)
    tree = env.root / "worktrees" / "001_a"                # the worktree's own package.json
    tree.mkdir(parents=True)
    (tree / "package.json").write_text('{"scripts": {"dev:web": "vite"}}', encoding="utf-8")
    assert "Servers." in render(env, long)
    assert "Servers." not in render(env, SHORT)          # the light preamble never has it
