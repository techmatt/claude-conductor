"""`needs_pick: <picker>`: an item waits for a picker's saved choices, then carries them."""

import json

import pytest

from conductor import executor, items, nightshift, picks, state
from conductor.daemon_receipts import rel_for
from conductor.util import read_text, split_front_matter

from test_picker import REF, manifest_data, write_manifest


def picker(env, data=None) -> picks.Manifest:
    """A picker a report has named: what `needs_pick` may name."""
    write_manifest(env.repo, data)
    env.d.log.append("finish", item="code/001_art", repo="code", status="done", pick=REF)
    return picks.load_manifest(env.cfg, REF)


def save(env, manifest, hero="1", logo="current", note="hair still too orange") -> dict:
    return picks.save_choices(env.control, manifest, {"rows": [{"id": "hero", "key": hero, "note": note},
                                                               {"id": "logo", "key": logo}]})


def receipt(env, name: str) -> dict:
    return split_front_matter(read_text(env.control / rel_for(f"queue/code/{name}.md")))[0]


@pytest.mark.slow
def test_it_waits_then_launches_with_the_choices_in_its_prompt(env):
    manifest = picker(env)
    env.queue("002_fonts", "FAKE report done", needs_pick="art_alts")
    for _ in range(3):
        env.d.tick()
    assert not env.events("start") and not env.finished("code/002_fonts")
    assert env.events("hold") == []                                     # a wait: no toast
    d = state.decide(env.cfg, state.scan(env.cfg, env.d.log))["code"]
    assert (d.action, d.reason) == ("wait", "waiting on Matt's choices for picker art_alts (`needs_pick`)")
    assert receipt(env, "002_fonts")["outcome"] == "accepted"
    assert "waits for Matt's choices for picker art_alts" in receipt(env, "002_fonts")["result"]

    save(env, manifest)
    env.tick_until(lambda: env.finished("code/002_fonts"))
    prompt = read_text(executor.RunPaths.for_name(env.d.cfg, "code", "002_fonts").prompt)
    assert "Matt's choices from picker `art_alts` (Hero art)" in prompt
    assert ("- Hero (`hero`): `1` (warmer), changed\n  Matt's note: hair still too orange\n"
            "- Logo (`logo`): `current`\n") in prompt
    assert prompt.index("FAKE report done") < prompt.index("Matt's choices from picker")
    assert env.events("enqueue")[-1].detail["needs_pick"] == "art_alts"


@pytest.mark.parametrize("retire", [False, True], ids=["unknown", "retired"])
def test_an_unknown_or_retired_picker_is_refused(env, retire):
    if retire:
        manifest = picker(env)
        save(env, manifest)
        env.d.log.append("pick_retired", name="art_alts", manifest=REF, deleted=[], kept=[], by="planner")
    env.queue("002_fonts", "FAKE report done", needs_pick="art_alts")
    env.tick_until(lambda: env.events("invalid"))
    env.d.tick()
    assert ("is retired" if retire else "no picker is named art_alts") in env.events("invalid")[0].detail["error"]
    meta = receipt(env, "002_fonts")
    assert meta["outcome"] == "refused" and "needs_pick" in meta["reason"]
    assert not env.events("start")


@pytest.mark.parametrize("value", ["Not A Name", "123", "[a, b]"])
def test_a_malformed_name_is_invalid(env, value):
    item = items.parse(env.queue("002_fonts", "x", needs_pick=value), "code")
    assert any("needs_pick" in e for e in item.errors) and item.needs_pick is None


def test_nothing_starts_it_early_and_a_stage_waits_for_the_choices(env):
    env.configure('[repos.code]\npath = "../code"\nstage = true\n')
    picker(env)
    env.queue("002_a", "x", needs_pick="art_alts")
    env.queue("003_b", "x", needs_pick="art_alts", alongside="only art", readonly="true")
    view = state.scan(env.d.cfg, env.d.log)
    d = state.decide(env.d.cfg, view)
    assert d["code"].action == "wait"                                   # never `stage`
    assert state.early_starts(env.d.cfg, view, d) == []


def test_a_save_older_than_a_later_finish_still_waits(env):
    manifest = picker(env)
    save(env, manifest)
    item = items.parse(env.queue("003_x", "x", needs_pick="art_alts"), "code")
    view = state.scan(env.cfg, env.d.log)
    assert state.pick_wait(env.cfg, view, item) == ""
    path = picks.choices_path(env.control, "art_alts")
    path.write_text(json.dumps(json.loads(read_text(path)) | {"saved_at": "2026-01-01T09:00:00-08:00"}), encoding="utf-8")
    env.d.log.append("finish", item="code/002_round", repo="code", status="done", pick=REF)
    view = state.scan(env.cfg, env.d.log)
    assert "art_alts" in state.pick_wait(env.cfg, view, item)


def test_a_many_row_and_a_gone_manifest_still_read_whole(env):
    data = manifest_data()
    data["rows"][0] |= {"kind": "many", "default": []}
    manifest = picker(env, data)
    picks.save_choices(env.control, manifest, {"rows": [{"id": "hero", "keys": ["1"], "note": "a\nb"},
                                                        {"id": "logo", "key": "2"}]})
    text = picks.section(env.cfg, "art_alts")
    assert "- Hero (`hero`): kept `1` (warmer), changed\n  Matt's note: a\n  b\n" in text
    (env.repo / "picks" / "m.json").unlink()
    text = picks.section(env.cfg, "art_alts")
    assert "- (`hero`): kept `1`, changed" in text and "- (`logo`): `2`, changed" in text
    assert picks.section(env.cfg, "other") == "" and picks.section(env.cfg, None) == ""


def test_a_backlog_item_is_not_drawn_before_its_choices(env):
    manifest = picker(env)
    item = items.parse(env.queue("002_x", "x", needs_pick="art_alts"), "code")
    log, cfg = env.d.log, env.cfg
    view = state.scan(cfg, log)
    got, why, _ = nightshift.pick(cfg, log, view, "code", [item])
    assert got is None and why == [("waits on picker art_alts", "002_x waits on Matt's choices for picker art_alts (`needs_pick`)")]
    save(env, manifest)
    assert nightshift.pick(cfg, log, view, "code", [item])[0] is item
