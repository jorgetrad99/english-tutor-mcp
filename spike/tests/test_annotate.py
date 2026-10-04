from pathlib import Path
from typing import Any

import pytest
from test_report import write_sample_data

from tutor_spike.analysis import annotate


def test_annotate_prints_only_the_learner_turns_numbered(
    tmp_path: Path,
    end_session_schema: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    (tmp_path / "transcripts" / "r01.md").write_text(
        "U: Yesterday I go to the office.\nA: Oh no. You went, not go.\n"
        "U: Sí, I went\nthere early.\nA: Great.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("sys.argv", ["annotate", "r01", "--data", str(tmp_path)])
    annotate.main()
    out = capsys.readouterr().out
    assert out == "1. Yesterday I go to the office.\n2. Sí, I went there early.\n"


def test_annotate_unknown_run_exits(
    tmp_path: Path, end_session_schema: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    monkeypatch.setattr("sys.argv", ["annotate", "r99", "--data", str(tmp_path)])
    with pytest.raises(SystemExit, match="r99"):
        annotate.main()


def test_annotate_run_without_transcript_exits(
    tmp_path: Path, end_session_schema: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_sample_data(tmp_path, end_session_schema)
    (tmp_path / "transcripts" / "r01.md").unlink()
    monkeypatch.setattr("sys.argv", ["annotate", "r01", "--data", str(tmp_path)])
    with pytest.raises(SystemExit, match="transcript"):
        annotate.main()
