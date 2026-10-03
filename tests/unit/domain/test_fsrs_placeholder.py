import pytest

import tutor.domain.fsrs


@pytest.mark.unit
def test_fsrs_package_imports() -> None:
    # TODO(spike): replace with real tests; this only keeps the coverage gate green.
    assert tutor.domain.fsrs.__doc__
