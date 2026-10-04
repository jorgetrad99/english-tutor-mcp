import json
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from test_report import write_sample_data

from tutor_spike.redact import export, redact

PII = "José Jorge jose@example.com"


def test_redact_replaces_names_case_insensitively_and_emails() -> None:
    text = "Hi Jorge, mail jorge.x@gmail.com. JORGE said hi to Jorgensen."
    assert redact(text, ["Jorge"]) == "Hi [name], mail [email]. [name] said hi to Jorgensen."


def test_redact_handles_longer_names_first() -> None:
    assert redact("Ana Maria and Ana", ["Ana", "Ana Maria"]) == "[name] and [name]"


def test_redact_underscore_digit_possessive_and_letters() -> None:
    assert redact("Jorge_Perez", ["Jorge"]) == "[name]_Perez"
    assert redact("jorge2 _Jorge_1 xJorge", ["Jorge"]) == "[name]2 _[name]_1 xJorge"
    assert redact("Jorge's book", ["Jorge"]) == "[name]'s book"
    assert redact("Jorgensen", ["Jorge"]) == "Jorgensen"
    assert redact("a@b.co", ["Jorge"]) == "[email]"


def test_redact_accented_name_nfc_and_nfd() -> None:
    nfc = unicodedata.normalize("NFC", "José")
    nfd = unicodedata.normalize("NFD", "José")
    assert nfc != nfd
    assert redact(f"hi {nfc} and {nfd}", ["José"]) == "hi [name] and [name]"
    assert redact(f"hi {nfc}", [nfd]) == "hi [name]"


def _calls(ts: datetime, sid: str, arguments: dict[str, Any]) -> list[dict[str, Any]]:
    profile = {"kind": "tool", "ts": ts.isoformat(), "tool": "get_profile"}
    profile |= {"tester": "author-free", "session_id": sid}
    end = {
        "kind": "http",
        "ts_in": (ts + timedelta(minutes=5)).isoformat(),
        "http": {"status": 200},
        "rpc": {
            "method": "tools/call",
            "tool": "end_session",
            "params_raw": {"name": "end_session", "arguments": arguments},
            "result_raw": {"structuredContent": {"accepted": True}},
            "error_raw": None,
        },
    }
    return [profile, end]


def _build(tmp_path: Path, schema: dict[str, Any]) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    write_sample_data(data, schema)
    runs = data / "runs.csv"
    runs.write_text(
        runs.read_text(encoding="utf-8").replace("ok,,\n", f"ok,,met {PII}\n")
        + "r02,2026-10-07,free,laptop,web,voice,Sonnet,3,10:00,10:15,yes,na,,,ok,,\n"
        + "r03,2026-10-07,free,laptop,web,text,Sonnet,3,11:00,11:15,na,na,,"
        + "transcripts/r03.md,aborted,crash,\n"
        + "r04,2026-10-07,free,laptop,web,text,Sonnet,3,12:00,12:15,na,na,,"
        + "transcripts/r04.md,ok,,\n",
        encoding="utf-8",
    )
    calls = data / "calls-2026-10-06.jsonl"
    calls.write_text(
        calls.read_text(encoding="utf-8").replace(
            "Yesterday I go to the office.", f"Yesterday {PII} go to the office."
        ),
        encoding="utf-8",
    )
    (data / "transcripts" / "r01.md").write_text(f"U: I am {PII}.\n", encoding="utf-8")
    annotation = data / "annotations" / "r01.json"
    annotation.write_text(
        json.dumps([{"said": "Hi José", "correct": f"Hi {PII}"}]), encoding="utf-8"
    )
    assert "\\u00e9" in annotation.read_text(encoding="utf-8")  # ensure_ascii escapes
    for rid in ("r03", "r04"):
        (data / "transcripts" / f"{rid}.md").write_text("U: hi\n", encoding="utf-8")
    good = {
        "session_id": "sid-2",
        "user_turns": ["hello"],
        "errors": [],
        "chunks_used": [],
        "task_result": "achieved",
        "hints_given": 0,
        "cefr_estimate": {"speaking": "B1+", "confidence": "low", "evidence": ["e"]},
        "confidence_1_5": 3,
    }
    t = datetime(2026, 10, 7, 10, 1, tzinfo=UTC)
    lines = _calls(t, "sid-2", good) + _calls(t + timedelta(hours=2), "sid-4", {"bogus": 1})
    (data / "calls-2026-10-07.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    return data


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


def test_export_leaks_no_name_or_email_in_any_file(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    data = _build(tmp_path, end_session_schema)
    repo = tmp_path / "repo"
    export(data, repo, ["José", "Jorge"], ZoneInfo("UTC"))
    files = [p for p in repo.rglob("*") if p.is_file()]
    assert files
    for path in files:
        text = unicodedata.normalize("NFC", path.read_text(encoding="utf-8")).lower()
        assert "josé" not in text, path
        assert "jorge" not in text, path
        assert "\\u00e9" not in text, path
        assert not re.search(r"\w@\w", text), path
    base = repo / "docs/spike"
    assert "[name]" in (base / "02-data/payloads/r01.json").read_text("utf-8")
    assert "[name]" in (base / "03-data/annotations/r01.json").read_text("utf-8")
    assert "[name]" in (base / "02-data/runs.csv").read_text("utf-8")
    assert "[name]" in (base / "03-data/transcripts/r01.md").read_text("utf-8")


def test_export_gating(tmp_path: Path, end_session_schema: dict[str, Any]) -> None:
    data = _build(tmp_path, end_session_schema)
    repo = tmp_path / "repo"
    written = {p.relative_to(repo).as_posix() for p in export(data, repo, ["x"], ZoneInfo("UTC"))}
    # voice run: payload but no transcript
    assert "docs/spike/02-data/payloads/r02.json" in written
    assert not any("r02" in w and "transcripts" in w for w in written)
    # aborted run: nothing
    assert not any("r03" in w for w in written)
    # invalid final: transcript but no payload
    assert "docs/spike/03-data/transcripts/r04.md" in written
    assert not any("r04" in w and "payload" in w for w in written)


def test_export_refuses_transcript_outside_data_dir(
    tmp_path: Path, end_session_schema: dict[str, Any]
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    write_sample_data(data, end_session_schema)
    runs = data / "runs.csv"
    runs.write_text(
        runs.read_text(encoding="utf-8").replace("transcripts/r01.md", "../outside.md"),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="r01"):
        export(data, tmp_path / "repo", ["x"], ZoneInfo("UTC"))
