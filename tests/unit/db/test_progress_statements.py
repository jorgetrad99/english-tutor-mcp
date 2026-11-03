"""PgProgressRepo reads no learner text (subjects spec 16.11; slice B Review Focus 9)."""

import re
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from tutor.db.repos.progress import (
    activity_statements,
    concepts_statement,
    practice_days_statement,
    sessions_statement,
    showings_statement,
    templates_statement,
    unscoped_searches_statement,
)

pytestmark = pytest.mark.unit

USER, SUBJECT = uuid4(), uuid4()
STATEMENTS = (
    showings_statement(USER, SUBJECT),
    practice_days_statement(USER, SUBJECT, "America/Mexico_City"),
    sessions_statement(USER, SUBJECT),
    *activity_statements(USER, SUBJECT),
    concepts_statement(USER, SUBJECT),
    templates_statement(USER, SUBJECT),
    unscoped_searches_statement(USER),
)
# Columns that hold learner text other than concept and template names.
FORBIDDEN = (
    r"\banswer\b",
    r"\bprompt_text\b",
    r"\bparams\b",
    r"\braw_text\b",
    r"\bclean_text\b",
    r"\bhtml\b",
    r"\bgoal_text\b",
    r"\bcontext_text\b",
    r"\btext\b",
)


@pytest.mark.parametrize("statement", STATEMENTS)
def test_no_statement_reads_learner_text(statement: object) -> None:
    sql = str(statement.compile(dialect=postgresql.dialect()))  # type: ignore[attr-defined]
    for pattern in FORBIDDEN:
        assert re.search(pattern, sql) is None, (pattern, sql)


@pytest.mark.parametrize("statement", STATEMENTS)
def test_every_statement_filters_by_the_user(statement: object) -> None:
    compiled = statement.compile(dialect=postgresql.dialect())  # type: ignore[attr-defined]
    tables = set(re.findall(r"\bFROM (\w+)|\bJOIN (\w+)", str(compiled)))
    names = {name for pair in tables for name in pair if name}
    for table in names:
        assert f"{table}.user_id = " in str(compiled), table
    assert USER in compiled.params.values()
