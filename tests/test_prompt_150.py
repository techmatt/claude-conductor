"""Prompt 150 (helium-hustle 100): `needs_pick` takes a list of pickers, carried in order; a
readonly item's report and carry lines name where its kept files can be read."""

from conductor import items, kept, picks, state

from test_needs_pick import picker, save
from test_picker import manifest_data, write_manifest
from test_preamble import SHORT, render


def second(env) -> picks.Manifest:
    data = manifest_data() | {"name": "glyphs_r1", "title": "Glyphs"}
    write_manifest(env.repo, data, rel="picks/n.json")
    env.d.log.append("finish", item="code/001_glyphs", repo="code", status="done", pick="code/picks/n.json")
    return picks.load_manifest(env.cfg, "code/picks/n.json")


def test_a_list_waits_for_every_picker_then_carries_each_in_order(env):
    art, glyphs = picker(env), second(env)
    item = items.parse(env.queue("002_x", "x", needs_pick="[glyphs_r1, art_alts, glyphs_r1]"), "code")
    assert not item.errors and item.needs_pick == ["glyphs_r1", "art_alts"]
    save(env, art)
    view = state.scan(env.cfg, env.d.log)
    assert state.pick_wait(env.cfg, view, item) == "Matt's choices for picker glyphs_r1 (`needs_pick`)"
    save(env, glyphs)
    assert state.pick_wait(env.cfg, state.scan(env.cfg, env.d.log), item) == ""
    text = picks.section(env.cfg, item.needs_pick)
    assert text.index("picker `glyphs_r1` (Glyphs)") < text.index("picker `art_alts` (Hero art)")
    assert items.parse(env.queue("003_y", "x", needs_pick="art_alts"), "code").needs_pick == ["art_alts"]


def test_an_unknown_picker_in_a_list_is_refused(env):
    picker(env)
    env.queue("002_x", "x", needs_pick="[art_alts, nope]")
    [item] = state.scan(env.cfg, env.d.log).queued["code"]
    assert item.errors == ["`needs_pick`: no picker is named nope; queue the item once a report names it"]


def test_a_readonly_item_names_its_kept_path_not_out_keep(env):
    where = (env.cfg.state_dir / "kept" / "code").resolve().as_posix() + "/001_a"
    for body in (SHORT * 3, SHORT):                                     # full and light
        text = " ".join(render(env, body, readonly=True).split())
        assert f"kept at `{where}`" in text and "never `out/keep/`" in text
    item = items.Item("code", "001_a", env.control / "queue" / "code" / "001_a.md")
    assert kept.papers(env.cfg, item, {}) == (env.repo / "out" / "keep" / "001_a").resolve()


def test_kept_reads_say_the_worktree_has_none(env):
    folder = kept.root(env.cfg, "code") / "001_a"
    folder.mkdir(parents=True)
    (folder / "notes.md").write_text("n", encoding="utf-8")
    assert "Your worktree has none of them" in kept.section(env.cfg, "code")
