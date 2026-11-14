"""Prompt 114: `partial` is a real shortfall (a soft-band miss the done-when table reports
is `done`, and queues no retro); both preambles carry the clock line; a project's
`write_roots` (default its `open_roots`) are named writable. farm-seeds' safety is in
`src/conductor/remote/tests/test_remote_cancel.py`."""

from pathlib import Path

import pytest

from conductor import config, session

from test_preamble import SHORT, render

CLOCK = "Plan from the clock in the last tool return, never from your own estimate."


def flat(text: str) -> str:
    return " ".join(text.split())


def test_both_preambles_say_a_soft_miss_is_done_and_partial_is_a_real_shortfall(env):
    full, light = flat(render(env, SHORT * 3)), flat(render(env, SHORT))
    assert "a soft-band or measurement miss that the done-when table reports is still `done`" in full
    assert ("planned work skipped or not delivered (an instruction declined with a reason included), "
            "or a hard bar missed (a locked ruling, a required check, a feature that doesn't work)") in full
    assert "a reported soft-band miss is done" in light
    assert "planned work undelivered or a hard bar missed (ruling, required check, working feature)" in light
    assert "part met" not in full + light


def test_a_soft_band_only_miss_reported_done_queues_no_retro(env):
    env.queue("001_tune", "FAKE report done")       # the done-when table names its soft-band miss
    env.tick_until(lambda: env.finished("code/001_tune"))
    fin = env.finished("code/001_tune")
    assert fin.detail["status"] == "done" and session.retro_trigger(env.d.log, fin) is None
    env.queue("002_short", "FAKE report partial")   # a real shortfall still does
    env.tick_until(lambda: env.finished("code/002_short"))
    assert session.retro_trigger(env.d.log, env.finished("code/002_short")) == "partial"


def test_both_preambles_carry_the_clock_line(env):
    full, light = flat(render(env, SHORT * 3)), flat(render(env, SHORT))
    assert CLOCK in full and "Plan by the last tool return's clock, never your estimate." in light
    assert len(light.split()) <= 430


def _toml(env, line: str) -> config.Config:
    """The test project's config with `line` on top (each call starts from the original)."""
    path = env.control / "conductor.toml"
    if not hasattr(env, "toml_base"):
        env.toml_base = path.read_text(encoding="utf-8")
    path.write_text(line + "\n" + env.toml_base, encoding="utf-8")
    return config.load(env.control)


def test_open_roots_are_named_writable_unless_write_roots_says_otherwise(env, tmp_path):
    drive = (tmp_path / "drive-sync").as_posix()
    env.cfg = env.d.cfg = _toml(env, f'open_roots = ["{drive}"]')
    full, light = flat(render(env, SHORT * 3)), flat(render(env, SHORT))
    assert f"Writable outside the repo: `{drive}` (the project's `write_roots`)" in full
    assert "only thing you may write outside the working directory and the writable folders above." in full
    assert f"Writable outside the repo: `{drive}`." in light
    assert "Nothing else outside the working directory and the writable folders above; others there" in light
    assert "Writable outside the repo" not in flat(render(env, SHORT * 3, readonly=True))


def test_write_roots_overrides_and_an_empty_list_names_none(env, tmp_path):
    a, b = (tmp_path / "a").as_posix(), (tmp_path / "b").as_posix()
    env.cfg = env.d.cfg = _toml(env, f'open_roots = ["{a}"]\nwrite_roots = ["{b}"]')
    text = flat(render(env, SHORT * 3))
    assert f"Writable outside the repo: `{b}`" in text and f"`{a}`" not in text
    env.cfg = env.d.cfg = _toml(env, "write_roots = []")
    assert "Writable outside the repo" not in flat(render(env, SHORT * 3))
    with pytest.raises(config.ConfigError, match="write_roots"):
        _toml(env, 'write_roots = "x"')


def test_the_fractals_preamble_names_its_writable_root(env):
    real = Path("C:/Code/fractals-control/conductor.toml")
    if not real.is_file():
        pytest.skip("not on Matt's box")
    roots = config.load(real.parent).write_roots           # read only: no change to a real control folder
    env.cfg = env.d.cfg = _toml(env, "open_roots = [" + ", ".join(f'"{p.as_posix()}"' for p in roots) + "]")
    assert "`C:/Code/fractal-drive-sync`" in flat(render(env, SHORT * 3))
