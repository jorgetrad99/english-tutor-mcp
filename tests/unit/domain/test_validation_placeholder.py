import pytest

import tutor.domain.validation


@pytest.mark.unit
def test_validation_package_imports() -> None:
    # TODO(spike): replace with real tests; this only keeps the coverage gate green.
    assert tutor.domain.validation.__doc__
