"""Postgres unit of work: one transaction per use case, run as tutor_app under RLS (ruling 5).

Every transaction runs SET LOCAL ROLE tutor_app and sets app.user_id, app.google_sub and
app.web_session with set_config(..., true), so the settings and the role end with it.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import NoReturn, cast
from uuid import UUID

from sqlalchemy import Connection, Engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tutor.db.tables import users
from tutor.services.ports import (
    AuditRepo,
    GlossaryRepo,
    IdentityResolver,
    PlanRepo,
    ProfileRepo,
    ResolvedUser,
    ReviewRepo,
    SessionRepo,
    TrackRepo,
    UnitOfWork,
    UowFactory,
    UserRepo,
)

APP_ROLE = "tutor_app"

_SET_ROLE = text("SET LOCAL ROLE tutor_app")
_SET_SCOPE = text(
    "SELECT set_config('app.user_id', :user_id, true),"
    " set_config('app.google_sub', :google_sub, true),"
    " set_config('app.web_session', :web_session, true)"
)


@contextmanager
def scoped_connection(
    engine: Engine,
    *,
    user_id: UUID | None = None,
    google_sub: str | None = None,
    web_session: str | None = None,
) -> Iterator[Connection]:
    """A transaction as tutor_app with the RLS settings; commits on clean exit.

    An unset value is stored as '' and the policies read it through nullif, so it matches nothing.
    """
    with engine.begin() as conn:
        conn.execute(_SET_ROLE)
        conn.execute(
            _SET_SCOPE,
            {
                "user_id": str(user_id) if user_id is not None else "",
                "google_sub": google_sub or "",
                "web_session": web_session or "",
            },
        )
        yield conn


class _Pending:
    """Stands in for a repository a later task implements (Tasks 15-17 replace each one)."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, attr: str) -> NoReturn:
        raise NotImplementedError(f"uow.{self._name}.{attr} is not implemented on Postgres yet")


class PgUnitOfWork:
    """One transaction for one user; every repository shares the connection."""

    user_id: UUID
    users: UserRepo
    profiles: ProfileRepo
    track: TrackRepo
    plans: PlanRepo
    sessions: SessionRepo
    glossary: GlossaryRepo
    reviews: ReviewRepo
    audit: AuditRepo

    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self.conn = conn
        self.user_id = user_id
        self.users = cast(UserRepo, _Pending("users"))
        self.profiles = cast(ProfileRepo, _Pending("profiles"))
        self.track = cast(TrackRepo, _Pending("track"))
        self.plans = cast(PlanRepo, _Pending("plans"))
        self.sessions = cast(SessionRepo, _Pending("sessions"))
        self.glossary = cast(GlossaryRepo, _Pending("glossary"))
        self.reviews = cast(ReviewRepo, _Pending("reviews"))
        self.audit = cast(AuditRepo, _Pending("audit"))


def pg_uow_factory(engine: Engine) -> UowFactory:
    @contextmanager
    def unit_of_work(user_id: UUID) -> Iterator[UnitOfWork]:
        with scoped_connection(engine, user_id=user_id) as conn:
            yield PgUnitOfWork(conn, user_id)

    return unit_of_work


def _display_name(name: str | None, email: str | None) -> str:
    if name and name.strip():
        return name.strip()[:200]
    if email:
        return email.split("@", 1)[0][:200]
    return ""


class PgIdentity(IdentityResolver):
    """Find-or-create a user by Google sub (never by email), scoped by app.google_sub."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def resolve(
        self, google_sub: str, email: str | None, display_name: str | None, now: datetime
    ) -> ResolvedUser:
        by_sub = select(users.c.id).where(users.c.google_sub == google_sub)
        with scoped_connection(self._engine, google_sub=google_sub) as conn:
            found = conn.execute(by_sub).scalar_one_or_none()
            if found is not None:
                return ResolvedUser(id=found, created=False)
            created = conn.execute(
                pg_insert(users)
                .values(
                    google_sub=google_sub,
                    email=email,
                    display_name=_display_name(display_name, email),
                    created_at=now,
                )
                .on_conflict_do_nothing(index_elements=[users.c.google_sub])
                .returning(users.c.id)
            ).scalar_one_or_none()
            if created is not None:
                return ResolvedUser(id=created, created=True)
            # A concurrent resolve committed the same sub first; read its row.
            return ResolvedUser(id=conn.execute(by_sub).scalar_one(), created=False)
