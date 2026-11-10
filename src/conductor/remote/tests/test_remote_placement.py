"""Placement by expected finish, `main_slots` over every job, and core plans that recover
(100; `docs/remote-placement.md`, `docs/remote-cores.md`)."""

from conductor.remote import cores, diag, placement
from conductor.remote.board import Board
from conductor.remote.cores import Plan


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def _board(tmp_path, main_slots=6):
    clock = Clock()
    b = Board(tmp_path, fresh_seconds=30, native_argv=["sim"], clock=clock, main_slots=main_slots)
    return b, clock


def _job(b, n=4, seeds=2):
    jid = b.add_job("k1", "fp", [], [(i * seeds, seeds) for i in range(n)], ".")
    b.set_bundle("k1", "ready", wasm="wfp")
    return jid


def _tab(b, wid="bl", name="Work laptop", cores_=2, hw=8):
    return b.hello(wid, "browser", cores_, name, hw=hw)


def _gate(b, wid="bl"):
    assert b.poll(wid)["task"] == "gate"
    assert b.gate(wid, "k1", "wfp")


def _claims(b, jid) -> list[int]:
    got = []
    while "shard" in (c := b.claim(jid)):
        got.append(c["shard"])
    return got


# ------------------------------------------------------------------ the deal
def test_each_free_shard_goes_to_the_slot_that_finishes_it_first():
    now = 100.0
    # 4 shards; the main box's 6 free slots at 160 s a shard, a laptop's 2 at 85 s: 2 each
    got = placement.deal(4, {"main": [now] * 6, "bl": [now] * 2}, {"main": 160_000, "bl": 85_000}, now)
    assert got == {"bl": 2, "main": 2}
    # the laptop busy for another 10 s still beats the main box: free main slots get none
    got = placement.deal(2, {"main": [now] * 6, "bl": [now + 10] * 2}, {"main": 160_000, "bl": 85_000}, now)
    assert got == {}
    # a tie goes to the remote worker, and among those to the one asking
    assert placement.deal(1, {"main": [now], "a": [now], "b": [now]}, {}, now, asking="b") == {"b": 1}
    # an overdue busy slot is taken as needing a whole shard more
    assert placement.busy_until(0.0, 5000, now) == now + 5
    assert placement.slots_of([now + 3], 1, now, asking=True) == [now]


def test_a_gated_faster_browser_gets_shards_of_a_small_job_beside_a_free_main_box(tmp_path):
    b, clock = _board(tmp_path)
    b.ratios["bl"] = 85 / 160                           # measured beside the main box in an earlier job
    jid = _job(b)
    _tab(b)
    _gate(b)
    assert _claims(b, jid) == [0, 1]                    # the main box takes 2 of 4, leaving 2 for the laptop
    assert b.claim(jid) == {"wait": True}               # not a re-run: the free shards are the laptop's
    assert [b.poll("bl")["task"] for _ in range(2)] == ["shard", "shard"]
    # a laptop that stops polling isn't waited for: its free cores no longer count
    jid2 = _job(b)
    clock.t += 6
    _tab(b)
    assert _claims(b, jid2) == [0, 1, 2, 3]


def test_an_unmeasured_browser_ties_with_the_main_box_and_a_failing_one_drops_out(tmp_path):
    b, clock = _board(tmp_path)
    jid = _job(b)
    _tab(b)
    _gate(b)
    assert _claims(b, jid) == [0, 1]                    # a tie: the laptop's 2 cores get 2
    t = b.poll("bl")
    assert not b.result("bl", jid, t["shard"], False, log="no such spec")
    assert len(_claims(b, jid)) == 2                    # what it held and what it left both run here
    assert b.poll("bl")["task"] == "idle"               # and it gets no more of this job


def test_main_slots_cap_the_main_box_over_every_job(tmp_path):
    b, clock = _board(tmp_path, main_slots=6)
    j1, j2 = _job(b, n=5), _job(b, n=5)
    a = _claims(b, j1)
    bb = _claims(b, j2)
    assert (len(a), len(bb)) == (5, 1)                  # 6 at once between the two jobs
    assert b.claim(j2) == {"wait": True}
    assert b.result("main", j1, a[0], True, b"x", ms=1000)
    assert len(_claims(b, j2)) == 1                     # a slot freed in one job serves the other


# ------------------------------------------------------------------ core plans recover
def test_a_backed_off_worker_samples_again_and_climbs_within_one_job_once_its_ceiling_lifts(tmp_path):
    b, clock = _board(tmp_path, main_slots=None)
    b.plans["Work laptop"] = Plan(cores=2, why="3 → 2: shards lost to the main box 2 of 3", at=clock.t - 4000,
                                  ceiling=3, ceiling_at=clock.t - 4000)
    jid = _job(b, n=40)
    reply = _tab(b)
    assert reply["cores"] == 2
    _gate(b)
    for n in (2, 3):
        _tab(b, cores_=n)
        for t in [b.poll("bl") for _ in range(n)]:
            clock.t += 1
            assert b.result("bl", jid, t["shard"], True, b"x", ms=1000 * t["count"])
    assert b.plans["Work laptop"].cores == 4            # 2 -> 3 -> 4 within one job, the ceiling gone
    rows = {r["id"]: r for r in b.panel()["workers"]}
    text = diag.render("p", b.panel(), tmp_path, now=clock.t)
    assert rows["bl"]["lifted"] == 3 and "; ceiling 3 lifted" in text and "ceiling 3 lifted: an hour" in text


def test_an_hour_connected_with_no_sample_starts_the_climb_again(tmp_path):
    b, clock = _board(tmp_path)
    b.plans["Work laptop"] = Plan(cores=2, why="3 → 2", at=clock.t, ceiling=3, ceiling_at=clock.t,
                                  samples={"2": [1.0]}, job="j0")
    _tab(b)
    text = diag.render("p", b.panel(), tmp_path, now=clock.t)
    assert "; ceiling 3 until" in text
    for _ in range(int(cores.STALE_SECONDS) // 2 + 1):     # hellos every 2 s; a gap past `fresh` counts only `fresh`
        clock.t += 2
        _tab(b)
    p = b.plans["Work laptop"]
    assert p.ceiling == 0 and p.samples == {} and "no rate sample in an hour connected" in p.why
    p.quiet = 0
    cores.sample(p, "j1", 2, 5.0)
    assert p.quiet == 0
