"""`python -m tutor.ops.dev_seed`: the two dev identities, idempotently, through the services.

Dev-environment design D4. `dev-ana` is onboarded (one saved profile, so a plan exists) and
`dev-nuevo` only has a user row (onboarding pending). Run by the `migrate-dev` compose service
after the migrations and the roles, as the app's LOGIN role (DATABASE_URL, never the owner: the
seed must go through row-level security like every other write). Refuses (exit 2) without
TUTOR_DEV_AUTH. No English content is seeded here; the migrations seed the track.
"""

from __future__ import annotations

import os
import sys
import zoneinfo
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TextIO

from tutor.auth.dev_auth import DEV_IDENTITIES
from tutor.db.engine import check_app_role, make_engine
from tutor.db.uow import PgIdentity, pg_uow_factory
from tutor.domain.profile import ProfileInput
from tutor.services import profile as profile_svc
from tutor.services.context import Services
from tutor.services.ports import IdentityResolver
from tutor.settings import Settings

ONBOARDED_SUB = "dev-ana"
ANA_PROFILE = ProfileInput(
    self_level="B1",
    domains=["it"],
    use_cases=["standup"],
    minutes_per_day=20,
    days_per_week=3,
    target_level="B2",
    timezone="America/Mexico_City",
)


@dataclass(frozen=True, slots=True)
class SeedReport:
    created: tuple[str, ...]
    existing: tuple[str, ...]


def seed(identity: IdentityResolver, services: Services, now: datetime) -> SeedReport:
    """Find or create each dev user by sub; save dev-ana's profile only while she has none."""
    created: list[str] = []
    existing: list[str] = []
    for who in DEV_IDENTITIES.values():
        user = identity.resolve(who.sub, who.email, who.name, now)
        (created if user.created else existing).append(who.sub)
        if (
            who.sub == ONBOARDED_SUB
            and profile_svc.get_profile(services, user.id).onboarding_needed
        ):
            profile_svc.save_profile(services, user.id, ANA_PROFILE)
    return SeedReport(created=tuple(created), existing=tuple(existing))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main(
    env: Mapping[str, str] | None = None, *, out: TextIO = sys.stdout, err: TextIO = sys.stderr
) -> int:
    settings = Settings.from_env(os.environ if env is None else env)
    if not settings.dev_auth:
        print(
            "the dev seed needs TUTOR_DEV_AUTH=1 (loopback dev only); nothing was written", file=err
        )
        return 2
    engine = make_engine(settings.database_url)
    try:
        check_app_role(engine)  # the login role, never the owner; SystemExit otherwise
        services = Services(
            uow=pg_uow_factory(engine),
            clock=_utc_now,
            valid_timezones=frozenset(zoneinfo.available_timezones()),
        )
        report = seed(PgIdentity(engine), services, _utc_now())
    finally:
        engine.dispose()
    print(
        f"dev seed: created {', '.join(report.created) or 'nothing'}; "
        f"existing {', '.join(report.existing) or 'nothing'}",
        file=out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
