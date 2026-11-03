"""deploy/*.example files match what the processes read (core Task 27)."""

import importlib.util
import os
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tutor.__main__ import run_kwargs
from tutor.ops.provision_roles import KEYS as MIGRATE_KEYS
from tutor.ops.provision_roles import check_inputs
from tutor.settings import DEV_ONLY_KEYS, OWNER_ONLY_KEYS, Settings
from tutor.web.config import WebConfig

pytestmark = pytest.mark.unit

DEPLOY = Path(__file__).resolve().parents[3] / "deploy"
SUFFIX = ".env.example"
# The app never gets the owner URL (Settings reads it only for local tooling).
OWNER_ONLY = set(OWNER_ONLY_KEYS)
# Settings reads the dev-auth flag too, but no production file may carry it (dev environment D10).
NEVER_IN_PROD = OWNER_ONLY | set(DEV_ONLY_KEYS)
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
    "TUTOR_PORT": "8000",
    "TUTOR_HOST": "0.0.0.0",  # noqa: S104 - the container listens on its own network only
    "FORWARDED_ALLOW_IPS": "172.30.10.3",
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
    assert set(_pairs("tutor")) == env.read - NEVER_IN_PROD


def test_app_example_has_production_values_and_no_owner_url() -> None:
    pairs = _pairs("tutor")
    assert pairs["TUTOR_ENV"] == "prod"
    assert pairs["TUTOR_BASE_URL"].startswith("https://")
    assert pairs["TUTOR_MCP_URL"] == pairs["TUTOR_BASE_URL"] + "/mcp"
    assert pairs["TUTOR_OAUTH_STORAGE_DIR"] == "/data/oauth"
    assert pairs["FORWARDED_ALLOW_IPS"] and "*" not in pairs["FORWARDED_ALLOW_IPS"]
    assert pairs["TUTOR_TEST_LOGIN"] == ""
    assert not set(pairs) & {*NEVER_IN_PROD, "POSTGRES_PASSWORD", "TUNNEL_TOKEN", "APP_DB_PASSWORD"}
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


# --- deploy/dev.env.example and scripts/dev_env.py (dev-environment design D8) ------------------

ROOT = DEPLOY.parent
DEV_FULL = {
    "TUTOR_ENV": "dev",
    "TUTOR_BASE_URL": "http://localhost:8000",
    "TUTOR_MCP_URL": "",
    "TUTOR_SUPPORT_EMAIL": "soporte@example.test",
    "DATABASE_URL": "postgresql://tutor_login:secret@db:5432/tutor",
    "GOOGLE_CLIENT_ID": "",
    "GOOGLE_CLIENT_SECRET": "",
    "TUTOR_JWT_SIGNING_KEY": "j" * 48,
    "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
    "TUTOR_OAUTH_STORAGE_DIR": "/tmp/oauth",  # noqa: S108 - inside the container
    "TUTOR_PORT": "8000",
    "TUTOR_TEST_LOGIN": "",
}


def _dev_env_module() -> Any:
    spec = importlib.util.spec_from_file_location("dev_env", ROOT / "scripts" / "dev_env.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse(path: Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            key, _, value = stripped.partition("=")
            pairs[key] = value
    return pairs


def test_dev_example_lists_exactly_what_the_dev_profile_reads() -> None:
    # Compose sets TUTOR_DEV_AUTH on the services; the file carries everything else the app
    # (Settings, WebConfig) and migrate-dev (provision_roles) read. uvicorn is started by the
    # image's CMD, so run_kwargs (TUTOR_HOST, FORWARDED_ALLOW_IPS) is not involved.
    env = Recording({**DEV_FULL, "TUTOR_DEV_AUTH": "1"})
    Settings.from_env(env)
    WebConfig.from_env(env)
    assert set(_pairs("dev")) == (env.read - {"TUTOR_DEV_AUTH"}) | set(MIGRATE_KEYS)


def test_dev_example_is_loopback_dev_without_google_and_cannot_start_as_is() -> None:
    pairs = _pairs("dev")
    assert pairs["TUTOR_ENV"] == "dev"
    assert pairs["TUTOR_BASE_URL"] == "http://localhost:8000"
    assert pairs["TUTOR_MCP_URL"] == "" and pairs["TUTOR_TEST_LOGIN"] == ""
    assert pairs["GOOGLE_CLIENT_ID"] == "" and pairs["GOOGLE_CLIENT_SECRET"] == ""
    assert pairs["MIGRATION_DATABASE_URL"] == "postgresql://tutor:tutor@db:5432/tutor"
    assert pairs["APP_DB_USER"] == "tutor_login"
    assert pairs["DATABASE_URL"] == "postgresql://tutor_login:<APP_DB_PASSWORD>@db:5432/tutor"
    for key, value in pairs.items():
        if any(word in key for word in ("PASSWORD", "KEY")):
            assert value.startswith("<"), f"{key} must be a <placeholder>"
    with pytest.raises(SystemExit):
        Settings.from_env({**pairs, "TUTOR_DEV_AUTH": "1"})
    assert check_inputs(pairs)  # placeholders are too short to provision


def test_dev_env_generator_fills_every_placeholder_and_is_idempotent(tmp_path: Path) -> None:
    dev_env = _dev_env_module()
    target = tmp_path / "dev.env"
    assert dev_env.generate(target) is True
    pairs = _parse(target)
    assert set(pairs) == set(_pairs("dev"))
    assert not any("<" in value or ">" in value for value in pairs.values())
    settings = Settings.from_env({**pairs, "TUTOR_DEV_AUTH": "1"})
    assert settings.dev_auth and settings.base_url == "http://localhost:8000"
    assert WebConfig.from_env({**pairs, "TUTOR_DEV_AUTH": "1"}).dev_auth
    assert check_inputs(pairs) == []
    assert pairs["DATABASE_URL"] == (
        f"postgresql://tutor_login:{pairs['APP_DB_PASSWORD']}@db:5432/tutor"
    )
    assert pairs["APP_DB_PASSWORD"] != pairs["REPORT_DB_PASSWORD"]
    assert pairs["TUTOR_JWT_SIGNING_KEY"] != pairs["TUTOR_OAUTH_STORAGE_KEY"]
    if os.name != "nt":
        assert target.stat().st_mode & 0o777 == 0o600
    # A second run keeps the file (the passwords are already in the database roles).
    assert dev_env.generate(target) is False
    assert _parse(target) == pairs
    # --force regenerates every secret.
    assert dev_env.generate(target, force=True) is True
    fresh = _parse(target)
    assert set(fresh) == set(pairs)
    for key in ("TUTOR_JWT_SIGNING_KEY", "TUTOR_OAUTH_STORAGE_KEY", "APP_DB_PASSWORD"):
        assert fresh[key] != pairs[key]


def test_dev_env_generator_cli_writes_once_and_never_prints_a_secret(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    dev_env = _dev_env_module()
    target = tmp_path / "dev.env"
    assert dev_env.main(["--target", str(target)]) == 0
    first = capsys.readouterr().out
    assert "written" in first
    assert dev_env.main(["--target", str(target)]) == 0
    second = capsys.readouterr().out
    assert "exists" in second and "--force" in second
    pairs = _parse(target)
    for output in (first, second):
        assert pairs["TUTOR_JWT_SIGNING_KEY"] not in output
        assert pairs["APP_DB_PASSWORD"] not in output
