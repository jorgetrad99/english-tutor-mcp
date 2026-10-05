"""`just dashboard-demo`: the dashboard over in-memory demo data. No Google, no Stripe."""

from __future__ import annotations

from datetime import UTC, datetime

from tutor.web.app import create_app
from tutor.web.config import WebConfig
from tutor.web.demo import seed_demo
from tutor.web.memory import MemoryBackend, memory_deps
from tutor.web.profile import MemoryProfiles, install_profiles

_backend = MemoryBackend()
seed_demo(_backend, datetime.now(UTC).date())
app = create_app(
    memory_deps(_backend, lambda: datetime.now(UTC)),
    WebConfig(
        env="test",
        base_url="http://localhost:8780",
        mcp_url="http://localhost:8780/mcp",
        support_email="soporte@example.test",
        test_login=True,
    ),
)
install_profiles(app, MemoryProfiles(lambda: datetime.now(UTC)))
