"""Service result types that cross a storage boundary."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from repo_contract import sample_metrics

from tutor.services.views import EndSessionResult

pytestmark = pytest.mark.unit


def test_end_session_result_round_trips_through_json() -> None:
    result = EndSessionResult(
        status="closed",
        low_trust=False,
        metrics=sample_metrics(),
        summary_text="one\ntwo\nthree\nfour",
        streak=3,
        already_closed=False,
        errors_rejected=1,
        chunks_rejected=0,
    )
    data = json.loads(json.dumps(result.to_json()))
    assert "already_closed" not in data
    assert EndSessionResult.from_json(data, already_closed=True) == replace(
        result, already_closed=True
    )
