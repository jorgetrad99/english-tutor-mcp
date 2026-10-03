import os
import socket
from urllib.parse import urlsplit

import pytest

DEFAULT_URL = "postgresql://tutor:tutor@localhost:5433/tutor_test"


@pytest.mark.integration
def test_db_test_accepts_connections() -> None:
    # TODO(spike): replace with a real query once the DB driver is chosen.
    url = urlsplit(os.environ.get("TEST_DATABASE_URL", DEFAULT_URL))
    with socket.create_connection((url.hostname or "localhost", url.port or 5432), timeout=5):
        pass
