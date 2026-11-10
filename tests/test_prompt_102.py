"""102: the preambles' *Latitude* rules (aicomplete 167 spent ~3 h tuning toward a gate its
locked rulings put out of reach, then stopped to ask): a priority order for conflicts, bars
scored strictly but not chased, method as a suggestion, and a feasibility check first."""

from test_preamble import SHORT, render


def flat(text: str) -> str:
    return " ".join(text.split())


def test_the_full_preamble_carries_the_four_rules(env):
    text = flat(render(env, SHORT * 3 + " Tune the bonus until the band is 45-85."))
    assert ("this order wins: the project's locked rulings (CLAUDE.md, docs, the prompt), the goal, "
            "the done-when bars, the method") in text
    assert "never ask about such a conflict" in text
    assert "Method (read counts, screen order, which tables) is a suggested route" in text
    assert "lines marked required, and what keeps measurement honest" in text
    assert "outside a band is unmet) but don't chase one" in text
    assert "starts with a cheap reachability check, ~30 min" in text
    # the branches-not-stops line is folded in, not kept beside it
    assert text.count("the only hard stop is a prompt's wrong-repo guard") == 1
    assert "Decide anything you don't ask about" not in text
    # the leftover check no longer pushes a bar already shown out of reach
    assert "for one unmet and not shown unreachable, take the next step" in text


def test_the_light_preamble_carries_them_short(env):
    text = flat(render(env, SHORT))
    assert "Conflicts: locked rulings, then goal, then done-when bars, then method" in text
    assert "(a suggestion unless required); report what gave way, don't ask" in text
    assert "Bars are scored strictly, not chased: check a numeric target is reachable first" in text
    assert len(text.split()) <= 430
