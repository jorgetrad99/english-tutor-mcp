"""The repository contract (tests/repo_contract.py) on Postgres; fixtures in conftest.py."""

import pytest
from repo_contract import RepoContract

pytestmark = pytest.mark.integration


# Task 17 deletes this marker. raises= keeps every other exception a real failure, so the
# people/plans tests must pass while tests that reach a placeholder repository xfail.
@pytest.mark.xfail(
    raises=NotImplementedError,
    strict=False,
    reason="session, glossary and review repositories arrive in Task 17",
)
class TestPgRepos(RepoContract):
    """Every contract test against PgUnitOfWork and PgIdentity."""
