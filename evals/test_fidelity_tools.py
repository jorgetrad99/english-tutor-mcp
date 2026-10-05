"""Evidence-fidelity tooling (copied from spike/src/tutor_spike/analysis, adapted to
tutor.domain.text.normalize). Not an eval: no LLM; runs in `just check`."""

import json
import unicodedata
from pathlib import Path

import pytest
from fidelity.annotate import main as annotate_main
from fidelity.metrics import Match, chunks_fidelity, errors_fidelity, turns_fidelity, user_messages
from fidelity.redact import main as redact_main
from fidelity.redact import redact, redact_json
from fidelity.report import failures, load_sessions, totals
from fidelity.report import main as report_main

pytestmark = pytest.mark.unit

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


def test_curly_apostrophes_match_straight_ones() -> None:
    curly = "Let\N{RIGHT SINGLE QUOTATION MARK}s practise English for 15 minutes."
    assert turns_fidelity([curly], user_messages(TRANSCRIPT)).hit_pred == 1


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


def test_chunks_fidelity_compares_ids() -> None:
    match = chunks_fidelity(["it-01-c1", "it-01-c9"], ["it-01-c1", "it-01-c2"])
    assert (match.recall, match.precision) == (0.5, 0.5)


def test_match_is_none_when_there_is_nothing_to_compare() -> None:
    assert errors_fidelity([], []).recall is None
    assert errors_fidelity([], []).precision is None


def test_matches_add_up_as_micro_average() -> None:
    total = Match(1, 1, 1, 1) + Match(1, 3, 0, 3)
    assert (total.recall, total.precision) == (0.5, 0.25)


def test_redact_replaces_names_case_insensitively_and_emails() -> None:
    text = "Hi Mateo, mail mateo.x@example.com. MATEO said hi to Mateos."
    assert redact(text, ["Mateo"]) == "Hi [name], mail [email]. [name] said hi to Mateos."


def test_redact_handles_longer_names_first() -> None:
    assert redact("Ana Maria and Ana", ["Ana", "Ana Maria"]) == "[name] and [name]"


def test_redact_underscore_digit_possessive_and_letters() -> None:
    assert redact("Mateo_Perez", ["Mateo"]) == "[name]_Perez"
    assert redact("mateo2 _Mateo_1 xMateo", ["Mateo"]) == "[name]2 _[name]_1 xMateo"
    assert redact("Mateo's book", ["Mateo"]) == "[name]'s book"
    assert redact("Mateos", ["Mateo"]) == "Mateos"
    assert redact("a@b.co", ["Mateo"]) == "[email]"


def test_redact_accented_name_nfc_and_nfd() -> None:
    nfc = unicodedata.normalize("NFC", "Luc\N{LATIN SMALL LETTER I WITH ACUTE}a")
    nfd = unicodedata.normalize("NFD", "Luc\N{LATIN SMALL LETTER I WITH ACUTE}a")
    assert nfc != nfd
    assert redact(f"hi {nfc} and {nfd}", [nfc]) == "hi [name] and [name]"
    assert redact(f"hi {nfc}", [nfd]) == "hi [name]"


def test_redact_folds_accents_on_both_sides() -> None:
    i_acute = "\N{LATIN SMALL LETTER I WITH ACUTE}"
    capital_i_acute = "\N{LATIN CAPITAL LETTER I WITH ACUTE}"
    a_acute = "\N{LATIN SMALL LETTER A WITH ACUTE}"
    lucia = f"Luc{i_acute}a"
    assert (
        redact(f"Lucia met {lucia} and LUC{capital_i_acute}A.", [lucia])
        == "[name] met [name] and [name]."
    )
    fernandez = f"Fern{a_acute}ndez"
    text = f"Luc{i_acute}a {fernandez}? Lucia Fernandez, the {fernandez} family."
    assert redact(text, [f"{lucia} {fernandez}", "Fernandez"]) == (
        "[name]? [name], the [name] family."
    )


def test_redact_json_touches_values_never_keys() -> None:
    data = {"Mateo": ["Mateo said hi", {"said": "mateo@example.com"}], "n": 3}
    assert redact_json(data, ["Mateo"]) == {
        "Mateo": ["[name] said hi", {"said": "[email]"}],
        "n": 3,
    }


def test_redact_cli_rewrites_files_in_place(tmp_path: Path) -> None:
    transcript = tmp_path / "s.md"
    payload = tmp_path / "s.payload.json"
    transcript.write_text("U: I am Mateo.\n", encoding="utf-8")
    payload.write_text(json.dumps({"user_turns": ["I am Mateo."]}), encoding="utf-8")
    assert redact_main(["--names", "Mateo", str(transcript), str(payload)]) == 0
    assert transcript.read_text(encoding="utf-8") == "U: I am [name].\n"
    assert json.loads(payload.read_text(encoding="utf-8")) == {"user_turns": ["I am [name]."]}


def test_redact_cli_needs_names(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="name"):
        redact_main(["--names", " ", str(tmp_path / "x.md")])


def test_annotate_prints_only_the_learner_turns(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "t.md"
    si = "S\N{LATIN SMALL LETTER I WITH ACUTE}"
    path.write_text(f"U: Yesterday I go.\nA: You went.\nU: {si}, I went\nthere early.\n", "utf-8")
    assert annotate_main([str(path)]) == 0
    assert capsys.readouterr().out == f"1. Yesterday I go.\n2. {si}, I went there early.\n"


def _fixtures(tmp_path: Path, *, said: str, chunks: list[str]) -> Path:
    (tmp_path / "transcripts").mkdir()
    (tmp_path / "annotations").mkdir()
    (tmp_path / "transcripts" / "s1.md").write_text(TRANSCRIPT, encoding="utf-8")
    payload = {
        "user_turns": user_messages(TRANSCRIPT),
        "errors": [{"said": said, "correct": "I went", "category": "grammar"}],
        "chunks_used": chunks,
    }
    (tmp_path / "transcripts" / "s1.payload.json").write_text(json.dumps(payload), "utf-8")
    annotation = {"errors": [{"said": "I go to the office"}], "chunks_used": ["it-01-c1"]}
    (tmp_path / "annotations" / "s1.json").write_text(json.dumps(annotation), "utf-8")
    (tmp_path / "transcripts" / "orphan.md").write_text(TRANSCRIPT, encoding="utf-8")
    return tmp_path


def test_report_passes_good_evidence(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fixtures = _fixtures(tmp_path, said="Yesterday I go to the office", chunks=["it-01-c1"])
    sessions = load_sessions(fixtures)
    assert [s.name for s in sessions] == ["s1"]  # the orphan transcript has no payload
    assert failures(totals(sessions)) == []
    assert report_main(["--fixtures", str(fixtures)]) == 0
    assert "| s1 |" in capsys.readouterr().out


def test_report_fails_below_the_thresholds(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path, said="invented words", chunks=[])
    problems = failures(totals(load_sessions(fixtures)))
    assert "errors recall 0% < 70%" in problems
    assert "chunks_used recall 0% < 80%" in problems
    assert report_main(["--fixtures", str(fixtures)]) == 1


def test_spike_list_annotations_still_load(tmp_path: Path) -> None:
    fixtures = _fixtures(tmp_path, said="I go to the office", chunks=[])
    (fixtures / "annotations" / "s1.json").write_text(
        json.dumps([{"said": "I go to the office"}]), "utf-8"
    )
    [session] = load_sessions(fixtures)
    assert session.chunks is None
    assert session.errors.recall == 1.0


def test_report_without_sessions_says_so(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert report_main(["--fixtures", str(tmp_path)]) == 0
    assert "No annotated sessions" in capsys.readouterr().out
