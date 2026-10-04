import pytest

from tutor_spike.analysis.fidelity import (
    Match,
    cefr_stats,
    errors_fidelity,
    turns_fidelity,
    user_messages,
)

TRANSCRIPT = """U: Let's practise English for 15 minutes.
A: Sure! Which situation?
U: The standup, about the outage
that happened yesterday.
A: Great.
U: Yesterday I go to the office.
"""


def test_user_messages_joins_continuation_lines() -> None:
    assert user_messages(TRANSCRIPT) == [
        "Let's practise English for 15 minutes.",
        "The standup, about the outage that happened yesterday.",
        "Yesterday I go to the office.",
    ]


def test_turns_fidelity_counts_substring_matches_both_ways() -> None:
    payload = ["the standup about the outage", "Yesterday I go to the office", "I love Python"]
    match = turns_fidelity(payload, user_messages(TRANSCRIPT))
    assert (match.hit_ref, match.n_ref) == (2, 3)
    assert (match.hit_pred, match.n_pred) == (2, 3)


def test_fuzzy_turns_accept_near_copies() -> None:
    transcript = ["We need two more days for the release"]
    strict = turns_fidelity(["We need two more day for the release"], transcript)
    fuzzy = turns_fidelity(["We need two more day for the release"], transcript, fuzzy=True)
    assert strict.recall == 0.0
    assert fuzzy.recall == 1.0


def test_errors_fidelity_matches_when_one_said_contains_the_other() -> None:
    match = errors_fidelity(
        payload_said=["I go to the office", "invented"],
        annotated_said=["Yesterday I go to the office", "I have 30 years"],
    )
    assert (match.recall, match.precision) == (0.5, 0.5)


def test_match_is_none_when_there_is_nothing_to_compare() -> None:
    assert errors_fidelity([], []).recall is None
    assert errors_fidelity([], []).precision is None


def test_matches_add_up_as_micro_average() -> None:
    total = Match(1, 2, 1, 1) + Match(1, 2, 0, 1)
    assert (total.recall, total.precision) == (0.5, 0.5)


def test_cefr_stats_on_half_step_scale() -> None:
    stats = cefr_stats(["B1+", "B1+", "B2", "B1", "C1"])
    assert stats is not None
    assert (stats.n, stats.mode, stats.low, stats.high) == (5, "B1+", "B1", "C1")
    # values [1, 1, 2, 0, 4]: mode 1; |v-1| <= 1 for 4 of 5; jumps 2->0 and 0->4
    assert stats.within_one_of_mode == pytest.approx(0.8)
    assert stats.full_level_jumps == 2
    assert stats.sd_half_steps == pytest.approx(1.3565, abs=1e-3)


def test_cefr_stats_empty_is_none() -> None:
    assert cefr_stats([]) is None
