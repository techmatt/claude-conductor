"""The broker's core count for a browser worker (`cores.py`), alone and through the board."""

import json

from conductor.remote import cores, diag
from conductor.remote.board import Board
from conductor.remote.cores import Plan


def test_a_held_rate_raises_and_a_fallen_rate_backs_off_to_the_count_below():
    p = Plan(cores=2)
    for _ in range(2):
        cores.sample(p, "j", 2, 1000.0)
    assert cores.decide(p, 8, 10.0) == "2 → 3: rate held at 60 seeds/min a core"
    for _ in range(3):
        cores.sample(p, "j", 3, 1050.0)               # within HOLD of 2's 1000
    assert cores.decide(p, 8, 20.0).startswith("3 → 4: rate held at 57 seeds/min a core (best 60 at 2)")
    for _ in range(4):
        cores.sample(p, "j", 4, 1500.0)
    assert cores.decide(p, 8, 30.0) == "4 → 3: rate fell to 40 seeds/min a core from 60 at 2"
    for _ in range(3):                                 # 4 is not tried again within the hour
        cores.sample(p, "j", 3, 1000.0)
    assert cores.decide(p, 8, 40.0) is None and p.cores == 3
    for _ in range(3):
        cores.sample(p, "j", 3, 1000.0)
    assert cores.decide(p, 8, 40.0 + cores.CEILING_SECONDS) .startswith("3 → 4")


def test_losses_to_the_main_box_back_off_by_half_and_a_lost_shard_holds_the_count():
    p = Plan(cores=7)
    for won in (True, False, False, False):
        cores.outcome(p, "j", 7, won)
    assert cores.decide(p, 8, 1.0) == "7 → 4: shards lost to the main box 3 of 4"
    for _ in range(4):
        cores.sample(p, "j", 4, 1000.0)
    cores.outcome(p, "j", 4, False)                    # one loss: no raise, no back-off
    assert cores.decide(p, 8, 2.0) is None and p.cores == 4


def test_a_cap_below_the_count_decides_nothing_and_the_limit_bounds_a_raise():
    p = Plan(cores=4)
    for won in (False, False):
        cores.outcome(p, "j", 4, won)
    assert cores.decide(p, 3, 1.0) is None             # the user's cap holds: nothing measured is the plan's
    q = Plan(cores=4)
    for _ in range(4):
        cores.sample(q, "j", 4, 1000.0)
    assert cores.decide(q, 4, 1.0) is None


def test_the_best_lower_count_is_the_bar_so_a_slow_creep_stops():
    """99's walk: each step within HOLD of the one below, 2 to 9 cores on 4 CPUs."""
    p = Plan(cores=2)
    for n, ms in ((2, 1000.0), (3, 1100.0), (4, 1180.0)):
        for _ in range(cores.need(n)):
            cores.sample(p, "j", n, ms)
        assert cores.decide(p, 12, float(n)).startswith(f"{n} → {n + 1}: rate held")
    for _ in range(4):
        cores.sample(p, "j", 5, 1300.0)               # within HOLD of 4's 1180, not of 2's 1000
    assert cores.decide(p, 12, 5.0) == "5 → 4: rate fell to 46 seeds/min a core from 60 at 2"


def test_a_shard_from_a_count_already_left_is_not_a_sample():
    p = Plan(cores=3)
    cores.sample(p, "j", 2, 5000.0)
    assert p.samples == {}


def test_a_new_job_starts_its_measurements_over():
    p = Plan(cores=2)
    cores.sample(p, "j1", 2, 1000.0)
    cores.outcome(p, "j1", 2, False)
    cores.sample(p, "j2", 2, 9000.0)
    assert p.samples == {"2": [9000.0]} and p.outcomes == []


# ------------------------------------------------------------------ through the board
class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _board(tmp_path, start=2):
    clock = Clock()
    b = Board(tmp_path, lease_seconds=600, fresh_seconds=30, native_argv=["sim"], clock=clock, start_cores=start)
    return b, clock


def _tab(b, wid, name, hw=8, cap=None, cores=0, fresh=False):
    return b.hello(wid, "browser", cores, name, fresh=fresh, hw=hw, cap=cap)


def _job(b, n=40):
    jid = b.add_job("k1", "fp", [], [(i * 4, 4) for i in range(n)], ".")
    b.set_bundle("k1", "ready", wasm="wfp")
    return jid


def _run_round(b, clock, wid, ms_seed, main_wins=0):
    """Each of the tab's cores takes a shard; the main box wins `main_wins` of them first."""
    n = b.workers[wid].cores
    got = [b.poll(wid) for _ in range(n)]
    got = [t for t in got if t["task"] == "shard"]
    clock.t += 1
    for t in got[:main_wins]:
        b.claim(t["job"])
        assert b.result("main", t["job"], t["shard"], True, b"m", ms=100)
    for t in got:
        b.result(wid, t["job"], t["shard"], True, b"x", ms=ms_seed * t["count"])


def _gate(b, wid):
    assert b.poll(wid)["task"] == "gate"
    assert b.gate(wid, "k1", "wfp")


def test_a_fast_tab_is_raised_a_slow_one_brought_down_and_a_reload_resumes_the_learned_count(tmp_path):
    b, clock = _board(tmp_path)
    _job(b)
    assert _tab(b, "bfast", "desk")["cores"] == 2
    _gate(b, "bfast")
    _tab(b, "bfast", "desk", cores=2)
    _run_round(b, clock, "bfast", 1000)
    reply = _tab(b, "bfast", "desk", cores=2)
    assert reply["cores"] == 3 and reply["why"].startswith("2 → 3: rate held")

    assert _tab(b, "bslow", "WOrk laptop")["cores"] == 2
    _gate(b, "bslow")
    for n in (2, 3, 4):
        _tab(b, "bslow", "WOrk laptop", cores=n)
        _run_round(b, clock, "bslow", 1000 if n < 4 else 1600)
    reply = _tab(b, "bslow", "WOrk laptop", cores=4)
    assert reply["cores"] == 3 and "rate fell" in reply["why"]

    # a reload (a new tab id, the same name) starts at the learned count; so does a new broker
    assert _tab(b, "bnew", "WOrk laptop", fresh=True)["cores"] == 3
    b.save(tmp_path / "board.json")
    b2, _ = _board(tmp_path)
    assert b2.restore(tmp_path / "board.json")
    assert _tab(b2, "bnew2", "WOrk laptop", fresh=True)["cores"] == 3
    assert _tab(b2, "bx", "desk")["cores"] == 3

    rows = {r["id"]: r for r in b.panel()["workers"]}
    assert rows["bslow"]["chosen"] == 3 and "rate fell" in rows["bslow"]["why"]
    text = diag.render("p", b.panel(), tmp_path, now=clock.t)
    assert "cores: 3 of 8 chosen by the broker; last change" in text and "core count" in text


def test_shards_lost_to_the_main_box_bring_a_tab_down(tmp_path):
    b, clock = _board(tmp_path)
    _job(b)
    b.plans["laptop"] = Plan(cores=7, why="", at=clock.t)
    assert _tab(b, "bl", "laptop")["cores"] == 7
    _gate(b, "bl")
    _tab(b, "bl", "laptop", cores=7)
    _run_round(b, clock, "bl", 1000, main_wins=3)
    reply = _tab(b, "bl", "laptop", cores=7)
    assert reply["cores"] == 4 and reply["why"] == "7 → 4: shards lost to the main box 2 of 2"   # at the second


def test_a_cap_and_an_old_page(tmp_path):
    b, clock = _board(tmp_path)
    _job(b)
    _tab(b, "bc", "capped", cap=1)
    _gate(b, "bc")
    for _ in range(3):
        _tab(b, "bc", "capped", cap=1, cores=1)
        _run_round(b, clock, "bc", 1000)
    assert b.plans["capped"].cores == 2                # its plan waits; the page runs at the cap
    row = {r["id"]: r for r in b.panel()["workers"]}["bc"]
    assert row["cap"] == 1 and row["chosen"] == 2
    assert "cores" not in b.hello("bold", "browser", 4, "old page")   # no hw: no plan


def test_start_cores_is_only_a_new_names_count_a_known_one_keeps_its_plan(tmp_path):
    """103: the start (4) is a new name's count. A known name keeps its learned count and
    ceiling across a broker restart under any start: the laptop stays at 2 under its ceiling 3."""
    clock = Clock()
    assert _tab(Board(tmp_path, native_argv=["sim"], clock=clock), "bn", "new")["cores"] == 4
    b, clock = _board(tmp_path, start=2)
    b.plans["Work laptop"] = Plan(cores=2, why="3 → 2: rate fell", at=clock.t,
                                  ceiling=3, ceiling_at=clock.t, job="j", samples={"2": [900.0]})
    b.save(tmp_path / "board.json")

    b2, clock = _board(tmp_path, start=4)
    assert b2.restore(tmp_path / "board.json")
    reply = _tab(b2, "bl", "Work laptop", hw=8)
    p = b2.plans["Work laptop"]
    assert (reply["cores"], p.cores, p.ceiling) == (2, 2, 3) and reply["why"] == "3 → 2: rate fell"
    text = diag.render("p", b2.panel(), tmp_path, now=clock.t)
    assert "cores: 2 of 8 chosen by the broker" in text and "reset" not in text
    assert _tab(b2, "bz", "someone new", hw=8)["cores"] == 4
