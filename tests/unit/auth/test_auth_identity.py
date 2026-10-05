from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import pytest
from fastmcp.server import dependencies

from tutor.auth.identity import current_user_id, token_identity
from tutor.services.context import Services
from tutor.services.memory import MemoryIdentity, MemoryStore, memory_uow
from tutor.services.ports import UnitOfWork

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)


class FakeToken:
    def __init__(self, **claims: Any) -> None:
        self.claims = claims


def use_token(monkeypatch: pytest.MonkeyPatch, token: FakeToken | None) -> None:
    monkeypatch.setattr(dependencies, "get_access_token", lambda: token)


class _AuditSpy:
    def __init__(self, inner: Any, events: list[str]) -> None:
        self._inner = inner
        self._events = events

    def record(self, event: str, meta: Mapping[str, Any], now: datetime) -> None:
        self._events.append(event)
        self._inner.record(event, meta, now)


class _SpyUow:
    def __init__(self, inner: UnitOfWork, events: list[str]) -> None:
        self._inner = inner
        self.audit = _AuditSpy(inner.audit, events)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def services(store: MemoryStore, events: list[str]) -> Services:
    base = memory_uow(store)

    @contextmanager
    def spying(user_id: UUID) -> Iterator[UnitOfWork]:
        with base(user_id) as uow:
            yield cast(UnitOfWork, _SpyUow(uow, events))

    return Services(
        uow=spying, clock=lambda: NOW, valid_timezones=frozenset({"America/Mexico_City"})
    )


@pytest.mark.parametrize(
    ("claims", "expected"),
    [
        (
            {"sub": "1234", "email": "ana@example.com", "name": "Ana"},
            ("1234", "ana@example.com", "Ana"),
        ),
        ({"sub": "1234"}, ("1234", None, None)),
        ({"sub": "1234", "email": "", "name": 7}, ("1234", None, None)),
        ({"email": "ana@example.com"}, None),
        ({"sub": ""}, None),
    ],
)
def test_token_identity_reads_the_google_sub(
    monkeypatch: pytest.MonkeyPatch,
    claims: dict[str, Any],
    expected: tuple[str, str | None, str | None] | None,
) -> None:
    use_token(monkeypatch, FakeToken(**claims))
    assert token_identity() == expected


def test_token_identity_without_token_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    use_token(monkeypatch, None)
    assert token_identity() is None


def test_first_call_creates_the_user_and_audits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    events: list[str] = []
    svc, identity = services(store, events), MemoryIdentity(store)
    use_token(monkeypatch, FakeToken(sub="google-sub-1", email="ana@example.com"))
    first = current_user_id(identity, svc)
    second = current_user_id(identity, svc)
    assert first == second
    assert events == ["user_created", "mcp_first_use"]


def test_users_are_keyed_by_sub_not_email(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    events: list[str] = []
    svc, identity = services(store, events), MemoryIdentity(store)
    use_token(monkeypatch, FakeToken(sub="google-sub-1", email="same@example.com"))
    first = current_user_id(identity, svc)
    use_token(monkeypatch, FakeToken(sub="google-sub-2", email="same@example.com"))
    second = current_user_id(identity, svc)
    assert first != second
    assert events.count("user_created") == 2


def test_existing_user_first_mcp_use_is_audited(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    events: list[str] = []
    svc, identity = services(store, events), MemoryIdentity(store)
    existing = identity.resolve("google-sub-9", None, None, NOW)  # e.g. created by web login
    use_token(monkeypatch, FakeToken(sub="google-sub-9"))
    assert current_user_id(identity, svc) == existing.id
    assert events == ["mcp_first_use"]


def test_no_token_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()
    events: list[str] = []
    use_token(monkeypatch, None)
    with pytest.raises(PermissionError):
        current_user_id(MemoryIdentity(store), services(store, events))
    assert events == []
