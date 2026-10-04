import uuid
from pathlib import Path

from conftest import FIXED_NOW

from tutor_spike.sessions import SessionRegistry


def test_issue_returns_uuid_owned_by_tester(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW)
    session_id = registry.issue("author-free")
    uuid.UUID(session_id)
    assert registry.owner(session_id) == "author-free"


def test_unknown_session_has_no_owner(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW)
    assert registry.owner("not-issued") is None


def test_record_end_counts_earlier_ends(tmp_path: Path) -> None:
    registry = SessionRegistry(tmp_path / "sessions.jsonl", FIXED_NOW)
    session_id = registry.issue("author-pro")
    assert registry.record_end(session_id) == 0
    assert registry.record_end(session_id) == 1


def test_sessions_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "sessions.jsonl"
    before = SessionRegistry(path, FIXED_NOW)
    session_id = before.issue("author-free")
    before.record_end(session_id)
    after = SessionRegistry(path, FIXED_NOW)
    assert after.owner(session_id) == "author-free"
    assert after.record_end(session_id) == 1


def test_blank_lines_in_the_file_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "sessions.jsonl"
    registry = SessionRegistry(path, FIXED_NOW)
    session_id = registry.issue("author-free")
    path.write_text(path.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert SessionRegistry(path, FIXED_NOW).owner(session_id) == "author-free"
