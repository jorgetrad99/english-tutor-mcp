"""Users, profiles and audit on Postgres. Every query filters by user_id; RLS is the backstop."""

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Connection, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tutor.db.tables import audit_log, profiles, users
from tutor.domain.profile import Profile
from tutor.services.ports import AuditEvent


class PgUserRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def timezone(self) -> str:
        tz = self._conn.execute(
            select(users.c.timezone).where(users.c.id == self._user_id)
        ).scalar_one()
        return str(tz)

    def set_timezone(self, tz: str) -> None:
        self._conn.execute(update(users).where(users.c.id == self._user_id).values(timezone=tz))

    def note_mcp_use(self, now: datetime) -> bool:
        result = self._conn.execute(
            update(users)
            .where(users.c.id == self._user_id, users.c.mcp_first_seen_at.is_(None))
            .values(mcp_first_seen_at=now)
        )
        return result.rowcount == 1


class PgProfileRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def get(self) -> Profile | None:
        row = (
            self._conn.execute(
                select(profiles, users.c.timezone)
                .join(users, users.c.id == profiles.c.user_id)
                .where(profiles.c.user_id == self._user_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return Profile(
            self_level=row["self_level"],
            domains=tuple(row["domains"]),
            use_cases=tuple(row["use_cases"]),
            minutes_per_day=row["minutes_per_day"],
            days_per_week=row["days_per_week"],
            target_level=row["target_level"],
            target_date=row["target_date"],
            goal_text=row["goal_text"],
            timezone=row["timezone"],
        )

    def upsert(self, profile: Profile, now: datetime) -> None:
        values: dict[str, Any] = {
            "domains": list(profile.domains),
            "use_cases": list(profile.use_cases),
            "goal_text": profile.goal_text,
            "minutes_per_day": profile.minutes_per_day,
            "days_per_week": profile.days_per_week,
            "self_level": profile.self_level,
            "target_level": profile.target_level,
            "target_date": profile.target_date,
            "updated_at": now,
        }
        self._conn.execute(
            pg_insert(profiles)
            .values(user_id=self._user_id, onboarded_at=now, **values)
            .on_conflict_do_update(index_elements=[profiles.c.user_id], set_=values)
        )
        # Ruling 4: the timezone lives on users.
        self._conn.execute(
            update(users).where(users.c.id == self._user_id).values(timezone=profile.timezone)
        )


class PgAuditRepo:
    def __init__(self, conn: Connection, user_id: UUID) -> None:
        self._conn = conn
        self._user_id = user_id

    def record(self, event: AuditEvent, meta: Mapping[str, Any], now: datetime) -> None:
        # Same TypeError as the in-memory store for values JSONB cannot hold.
        json.dumps(dict(meta))
        self._conn.execute(
            insert(audit_log).values(user_id=self._user_id, event=event, meta=dict(meta), at=now)
        )
