"""deploy/*.example files match what the processes read (core Task 27)."""

from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tutor.__main__ import run_kwargs
from tutor.ops.provision_roles import KEYS as MIGRATE_KEYS
from tutor.ops.provision_roles import check_inputs
from tutor.settings import Settings
from tutor.web.config import WebConfig

pytestmark = pytest.mark.unit

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
SUFFIX = ".env.example"
# The app never gets the owner URL (Settings reads it only for local tooling).
OWNER_ONLY = {"MIGRATION_DATABASE_URL"}
FULL = {
    "TUTOR_ENV": "prod",
    "TUTOR_BASE_URL": "https://tutor.example.com",
    "TUTOR_MCP_URL": "https://tutor.example.com/mcp",
    "TUTOR_SUPPORT_EMAIL": "soporte@example.com",
    "DATABASE_URL": "postgresql://tutor:secret@db:5432/tutor",
    "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
    "GOOGLE_CLIENT_SECRET": "google-secret",
    "TUTOR_JWT_SIGNING_KEY": "j" * 48,
    "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
    "TUTOR_OAUTH_STORAGE_DIR": "/data/oauth",
    "TUTOR_WEB_SESSION_SECRET": "w" * 48,
    "TUTOR_PORT": "8000",
    "TUTOR_HOST": "0.0.0.0",  # noqa: S104 - the container listens on its own network only
    "FORWARDED_ALLOW_IPS": "172.30.10.3",
    "MIGRATION_DATABASE_URL": "postgresql://owner:x@db:5432/tutor",
}


class Recording(Mapping[str, str]):
    """A mapping that remembers every key looked up (get() and [] both go through here)."""

    def __init__(self, data: Mapping[str, str]) -> None:
        self._data = dict(data)
        self.read: set[str] = set()

    def __getitem__(self, key: str) -> str:
        self.read.add(key)
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def _pairs(name: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in (DEPLOY / (name + SUFFIX)).read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        assert sep, f"not KEY=VALUE in {name}: {line}"
        pairs[key] = value
    return pairs


def test_app_example_lists_exactly_the_keys_the_server_reads() -> None:
    env = Recording(FULL)
    settings = Settings.from_env(env)
    WebConfig.from_env(env)
    run_kwargs(settings, env)
    assert set(_pairs("tutor")) == env.read - OWNER_ONLY


def test_app_example_has_production_values_and_no_owner_url() -> None:
    pairs = _pairs("tutor")
    assert pairs["TUTOR_ENV"] == "prod"
    assert pairs["TUTOR_BASE_URL"].startswith("https://")
    assert pairs["TUTOR_MCP_URL"] == pairs["TUTOR_BASE_URL"] + "/mcp"
    assert pairs["TUTOR_OAUTH_STORAGE_DIR"] == "/data/oauth"
    assert pairs["FORWARDED_ALLOW_IPS"] and "*" not in pairs["FORWARDED_ALLOW_IPS"]
    assert pairs["TUTOR_TEST_LOGIN"] == ""
    assert not set(pairs) & {*OWNER_ONLY, "POSTGRES_PASSWORD", "TUNNEL_TOKEN", "APP_DB_PASSWORD"}
    assert pairs["DATABASE_URL"].startswith("postgresql://")


def test_unfilled_app_example_cannot_start_the_server() -> None:
    with pytest.raises(SystemExit):
        Settings.from_env(_pairs("tutor"))


def test_unfilled_migrate_example_cannot_provision() -> None:
    pairs = _pairs("migrate")
    assert set(pairs) == set(MIGRATE_KEYS)
    assert check_inputs(pairs)


def test_db_and_tunnel_examples_name_their_keys() -> None:
    assert set(_pairs("db")) == {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"}
    assert set(_pairs("tunnel")) == {"TUNNEL_TOKEN"}


@pytest.mark.parametrize("name", ["tutor", "db", "migrate", "tunnel"])
def test_examples_hold_placeholders_not_secrets(name: str) -> None:
    for key, value in _pairs(name).items():
        if any(word in key for word in ("PASSWORD", "SECRET", "KEY", "TOKEN")):
            assert value.startswith("<"), f"{key} must be a <placeholder>"
