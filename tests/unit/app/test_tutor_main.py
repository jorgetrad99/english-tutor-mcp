import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

import tutor.__main__ as entry
from tutor.settings import Settings

pytestmark = pytest.mark.unit


def env(tmp_path: Path) -> dict[str, str]:
    return {
        "TUTOR_BASE_URL": "https://tutor.example.com",
        "DATABASE_URL": "sqlite://",
        "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": "test-google-client-secret",
        "TUTOR_JWT_SIGNING_KEY": "j" * 40,
        "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
        "TUTOR_OAUTH_STORAGE_DIR": str(tmp_path / "oauth"),
        "TUTOR_WEB_SESSION_SECRET": "w" * 40,
        "TUTOR_PORT": "8123",
    }


@pytest.fixture
def tutor_logger() -> Iterator[logging.Logger]:
    logger = logging.getLogger("tutor")
    saved = (list(logger.handlers), logger.level, logger.propagate)
    yield logger
    logger.handlers[:] = saved[0]
    logger.setLevel(saved[1])
    logger.propagate = saved[2]


def test_uvicorn_runs_behind_the_proxy_without_access_log(tmp_path: Path) -> None:
    settings = Settings.from_env(env(tmp_path))
    kwargs = entry.run_kwargs(settings, {"FORWARDED_ALLOW_IPS": "172.18.0.0/16"})
    assert kwargs == {
        "host": "127.0.0.1",
        "port": 8123,
        "proxy_headers": True,
        "forwarded_allow_ips": "172.18.0.0/16",
        "access_log": False,
    }
    assert entry.run_kwargs(settings, {"TUTOR_HOST": "::"})["forwarded_allow_ips"] == "127.0.0.1"


def test_main_builds_the_app_and_runs_uvicorn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tutor_logger: logging.Logger
) -> None:
    for key, value in env(tmp_path).items():
        monkeypatch.setenv(key, value)
    calls: list[tuple[Any, dict[str, Any]]] = []
    monkeypatch.setattr(entry, "build_app", lambda settings: ("app", settings.port))
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, **kw: calls.append((app, kw)))
    entry.main()
    [(app, kwargs)] = calls
    assert app == ("app", 8123)
    assert kwargs["access_log"] is False
    assert tutor_logger.level == logging.INFO
    assert tutor_logger.propagate is False


def test_logging_is_configured_once(tutor_logger: logging.Logger) -> None:
    tutor_logger.handlers.clear()
    entry.configure_logging()
    entry.configure_logging()
    assert len(tutor_logger.handlers) == 1


def test_configure_logging_leaves_the_library_log_guards_alone(
    tutor_logger: logging.Logger,
) -> None:
    from tutor.mcp.observe import (
        SCRUBBED_LOGGERS,
        SILENCED_LOGGERS,
        ScrubFilter,
        guard_library_logs,
    )

    guard_library_logs()
    names = ("", "fastmcp", "mcp", "uvicorn", *SCRUBBED_LOGGERS, *SILENCED_LOGGERS)
    before = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).level) for n in names}
    entry.configure_logging()
    after = {n: (list(logging.getLogger(n).handlers), logging.getLogger(n).level) for n in names}
    assert after == before
    for name in SCRUBBED_LOGGERS:
        assert any(isinstance(f, ScrubFilter) for f in logging.getLogger(name).filters)
