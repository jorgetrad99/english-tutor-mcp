"""The repository contract (tests/repo_contract.py) on Postgres; fixtures in conftest.py."""

import pytest
from repo_contract import PracticeContract, RepoContract

pytestmark = pytest.mark.integration


class TestPgRepos(RepoContract, PracticeContract):
    """Every contract test against PgUnitOfWork and PgIdentity."""
