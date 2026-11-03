"""`uvicorn --factory tutor.dev:create --reload`: the app of the dev compose profile (design D6).

What `python -m tutor` does minus starting uvicorn itself, so uvicorn's own reloader can re-import
the bind-mounted source on every change. Settings and the web config come from the environment
(deploy/dev.env plus the TUTOR_DEV_AUTH=1 that compose sets on the service); the flag is refused
outside loopback dev, so this factory cannot serve production.
"""

from __future__ import annotations

import os

from tutor.__main__ import configure_logging
from tutor.app import PathDispatch, build_app
from tutor.settings import Settings


def create() -> PathDispatch:
    settings = Settings.from_env(os.environ)
    configure_logging()
    return build_app(settings)
