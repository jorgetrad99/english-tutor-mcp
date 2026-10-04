import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from test_report import write_sample_data

from tutor_spike.redact import export, redact


def test_redact_replaces_names_case_insensitively_and_emails() -> None:
    text = "Hi Jorge, mail jorge.x@gmail.com. JORGE said hi to Jorgensen."
    assert redact(text, ["Jorge"]) == "Hi [name], mail [email]. [name] said hi to Jorgensen."


def test_redact_handles_longer_names_first() -> None:
    assert redact("Ana Maria and Ana", ["Ana", "Ana Maria"]) == "[name] and [name]"


def test_export_writes_redacted_copies(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    data = tmp_path / "data"
    data.mkdir()
    write_sample_data(data, end_session_schema)
    transcript = data / "transcripts" / "r01.md"
    transcript.write_text(
        transcript.read_text(encoding="utf-8") + "A: Bye Jorge.\n", encoding="utf-8"
    )
    repo = tmp_path / "repo"
    written = export(data, repo, ["Jorge"], ZoneInfo("UTC"))
    exported = repo / "docs/spike/03-data/transcripts/r01.md"
    assert exported in written
    assert "Jorge" not in exported.read_text(encoding="utf-8")
    fixture = repo / "evals/fixtures/transcripts/2026-10-06-claude-free-r01.md"
    assert fixture.exists()
    payload = json.loads((repo / "docs/spike/02-data/payloads/r01.json").read_text("utf-8"))
    assert payload["session_id"] == "sid-1"
    assert (repo / "evals/fixtures/annotations/2026-10-06-claude-free-r01.json").exists()
