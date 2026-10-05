"""How the web reaches the core profile services (core loop v0 spec 6.3; plan Task 25).

WebDeps stays as the dashboard plan defined it; the Perfil port lives on `app.state.profiles`.
"""

from __future__ import annotations

import functools
import zoneinfo
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from fastapi import FastAPI, Request

from tutor.domain.profile import ProfileInput
from tutor.services.context import Services
from tutor.services.memory import MemoryStore, UserRecord, memory_uow
from tutor.services.profile import get_profile, save_profile
from tutor.services.views import ProfileView, SaveProfileResult

PROFILE_PATH = "/app/profile"


class ProfilePort(Protocol):
    def view(self, user_id: UUID) -> ProfileView: ...

    def save(self, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
        """Raises ServiceError("validation_failed", fields) on invalid answers."""
        ...


@functools.cache
def iana_zones() -> frozenset[str]:
    return frozenset(zoneinfo.available_timezones())


class ServicesProfiles:
    """Production: the core use cases over the Postgres unit of work."""

    def __init__(self, svc: Services) -> None:
        self._svc = svc

    def view(self, user_id: UUID) -> ProfileView:
        return get_profile(self._svc, user_id)

    def save(self, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
        return save_profile(self._svc, user_id, raw)


class MemoryProfiles:
    """Tests and `just dashboard-demo`: the core use cases over the in-memory store.

    Dashboard users are created by the web memory backend, so a user record is added to the core
    store the first time an id is seen (test adapter only; production users come from PgIdentity).
    """

    def __init__(self, clock: Callable[[], datetime], store: MemoryStore | None = None) -> None:
        self.store = store if store is not None else MemoryStore()
        self._clock = clock
        self._svc = Services(uow=memory_uow(self.store), clock=clock, valid_timezones=iana_zones())

    def _ensure_user(self, user_id: UUID) -> None:
        if user_id not in self.store.tables.users:
            self.store.tables.users[user_id] = UserRecord(
                id=user_id,
                google_sub=f"web:{user_id}",
                email=None,
                display_name=None,
                created_at=self._clock(),
            )

    def view(self, user_id: UUID) -> ProfileView:
        self._ensure_user(user_id)
        return get_profile(self._svc, user_id)

    def save(self, user_id: UUID, raw: ProfileInput) -> SaveProfileResult:
        self._ensure_user(user_id)
        return save_profile(self._svc, user_id, raw)


def install_profiles(app: FastAPI, port: ProfilePort) -> None:
    app.state.profiles = port


def optional_profiles(request: Request) -> ProfilePort | None:
    port: ProfilePort | None = getattr(request.app.state, "profiles", None)
    return port


def get_profiles(request: Request) -> ProfilePort:
    port = optional_profiles(request)
    if port is None:
        raise RuntimeError("no ProfilePort installed; call install_profiles(app, port)")
    return port
