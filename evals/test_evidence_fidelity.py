"""Evidence fidelity of real v0 text sessions against the section 11 thresholds."""

from pathlib import Path

import pytest
from fidelity.report import failures, load_sessions, totals

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.eval
def test_evidence_fidelity_meets_section_11_thresholds() -> None:
    sessions = load_sessions(FIXTURES)
    if not sessions:
        pytest.skip("no annotated v0 transcripts in evals/fixtures yet")
    assert failures(totals(sessions)) == []
