"""The repository contract (tests/repo_contract.py) on Postgres; fixtures in conftest.py."""

import pytest
from repo_contract import RepoContract

pytestmark = pytest.mark.integration


class TestPgRepos(RepoContract):
    """Every contract test against PgUnitOfWork and PgIdentity."""
