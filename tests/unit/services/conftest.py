"""Fixtures for service and repository tests on the in-memory store."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from tutor.domain.profile import ProfileInput
from tutor.services.context import Services
from tutor.services.memory import MemoryIdentity, MemoryStore, memory_uow
from tutor.services.ports import IdentityResolver, UowFactory

NOW = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # a Wednesday; 09:00 in Mexico City
MEXICO_CITY = "America/Mexico_City"
NEW_YORK = "America/New_York"
VALID_TIMEZONES = frozenset({MEXICO_CITY, NEW_YORK, "Europe/Madrid", "UTC"})


class FixedClock:
    """A settable clock for `Services.clock`: assign `clock.now` or call `advance`."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> datetime:
        self.now = self.now + delta
        return self.now


def profile_input(**changes: Any) -> ProfileInput:
    """Valid onboarding answers (B1 to B2, standup and code review, 20 min x 3 days)."""
    data: dict[str, Any] = {
        "self_level": "B1",
        "domains": ["it"],
        "use_cases": ["standup", "code_review"],
        "minutes_per_day": 20,
        "days_per_week": 3,
        "target_level": "B2",
    }
    data.update(changes)
    return ProfileInput(**data)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


@pytest.fixture
def uow_factory(store: MemoryStore) -> UowFactory:
    return memory_uow(store)


@pytest.fixture
def identity(store: MemoryStore) -> IdentityResolver:
    return MemoryIdentity(store)


@pytest.fixture
def user_id(identity: IdentityResolver, now: datetime) -> UUID:
    return identity.resolve("google-sub-ana", "ana@example.com", "Ana", now).id


@pytest.fixture
def other_user_id(identity: IdentityResolver, now: datetime) -> UUID:
    return identity.resolve("google-sub-beto", "beto@example.com", "Beto", now).id


@pytest.fixture
def clock(now: datetime) -> FixedClock:
    return FixedClock(now)


@pytest.fixture
def svc(uow_factory: UowFactory, clock: FixedClock) -> Services:
    return Services(uow=uow_factory, clock=clock, valid_timezones=VALID_TIMEZONES)
