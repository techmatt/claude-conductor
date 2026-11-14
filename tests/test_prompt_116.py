"""116: each item's scratch, bench and run output goes in its own `out/<item>/`, and `start`
writes a fresh log (fractals 008 and 010 read an earlier item's `out/` as fresh results)."""

from conductor.ask_server import Server, fresh_log
from test_preamble import SHORT, render


def flat(text: str) -> str:
    return " ".join(text.split())


def test_every_preamble_names_the_items_own_scratch_folder(env):
    full, light = flat(render(env, SHORT * 3)), flat(render(env, SHORT))
    assert "Scratch, bench and run output go in `out/001_a/`" in full
    assert "anything else in `out/` is an earlier item's, never a fresh result" in full
    assert "Scratch, output: `out/001_a/` (not older `out/`), never %TEMP%." in light
    assert len(light.split()) <= 430
    watch = flat(render(env, SHORT, template="watch"))
    assert "Scratch goes in `out/001_a/`" in watch


def test_start_moves_an_older_log_aside_and_writes_a_fresh_one(tmp_path):
    old = tmp_path / "out" / "browser.log"
    old.parent.mkdir()
    old.write_text("007's rows\n", encoding="utf-8")
    aside = fresh_log(old)
    assert aside is not None and aside.parent == old.parent and aside.read_text(encoding="utf-8") == "007's rows\n"
    assert aside.name.startswith("browser.") and aside.suffix == ".log" and not old.exists()
    assert fresh_log(old) is None                     # nothing there: nothing moves


def test_starts_default_log_sits_under_the_items_folder(tmp_path):
    server = Server(tmp_path, "code/008_bench", 10, None, workdir=tmp_path)
    text, err = server.start({"command": "echo hi"})
    assert not err and "Output: out/008_bench/echo-" in text and "(fresh)" in text
    (tmp_path / "out" / "x.log").parent.mkdir(exist_ok=True)
    (tmp_path / "out" / "x.log").write_text("old\n", encoding="utf-8")
    text, err = server.start({"command": "echo hi", "log": "out/x.log"})
    assert not err and "An older log there moved to x." in text
