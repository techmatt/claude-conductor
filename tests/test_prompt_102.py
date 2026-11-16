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
    assert "never ask about it" in text
    assert "Method is a suggested route, sized to the time budget" in text
    assert "lines marked required, the repo's CLAUDE.md *Working rules* and *Measurement* below bind" in text
    assert "Paired seeds for a comparison, fresh seeds for a final read" in text
    assert "Score each bar strictly as written, but don't chase one" in text
    assert "is reachable before working toward it" in text
    assert text.count("only hard stop is a prompt's wrong-repo guard") == 1
    assert "Decide anything you don't ask about" not in text
    # the leftover check no longer pushes a bar already shown out of reach
    assert "one unmet and not shown unreachable" in text


def test_the_light_preamble_carries_them_short(env):
    text = flat(render(env, SHORT))
    assert "Conflicts: locked rulings, goal, done-when bars, method; report what gave way, don't ask" in text
    assert "Score bars strictly; check a numeric target is reachable before chasing it" in text
    assert len(text.split()) <= 400
