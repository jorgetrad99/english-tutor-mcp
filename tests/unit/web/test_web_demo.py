from datetime import date

from tutor.domain.dashboard.types import GlossaryFilter, Role, SubStatus, Tier
from tutor.web.demo import seed_demo
from tutor.web.memory import MemoryBackend

TODAY = date(2027, 1, 12)


def test_seed_creates_four_distinct_situations() -> None:
    backend = MemoryBackend()
    demo = seed_demo(backend, TODAY)
    assert len({demo.ana, demo.beto, demo.nuevo, demo.admin}) == 4

    ana_home = backend.home(demo.ana, TODAY)
    assert ana_home.has_connected and ana_home.has_plan and ana_home.today is not None
    assert backend.usage(demo.ana, TODAY).sessions_this_week == 2
    assert len(backend.glossary(demo.ana, GlossaryFilter(), TODAY)) >= 6
    assert backend.subscription(demo.ana).tier is Tier.FREE

    beto = backend.subscription(demo.beto)
    assert (beto.tier, beto.status) == (Tier.ANNUAL, SubStatus.ACTIVE)
    assert backend.reports(demo.beto).weekly

    assert not backend.home(demo.nuevo, TODAY).has_connected
    assert backend.plan(demo.nuevo) is None

    admin = backend.find_user(demo.admin)
    assert admin is not None and admin.role is Role.ADMIN
    assert {s.key for s in backend.list_settings()} >= {"free_sessions_per_week"}
