from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from tutor.settings import REQUIRED, Settings

pytestmark = pytest.mark.unit

PRIVATE_MARKER = "do-not-print-this-value"


def env(**overrides: str) -> dict[str, str]:
    base = {
        "TUTOR_BASE_URL": "https://tutor.example.com/",
        "DATABASE_URL": f"postgresql+psycopg://tutor:{PRIVATE_MARKER}@db/tutor",
        "GOOGLE_CLIENT_ID": "id.apps.googleusercontent.com",
        "GOOGLE_CLIENT_SECRET": PRIVATE_MARKER,
        "TUTOR_JWT_SIGNING_KEY": "j" * 40,
        "TUTOR_OAUTH_STORAGE_KEY": Fernet.generate_key().decode(),
        "TUTOR_WEB_SESSION_SECRET": "w" * 40,
    }
    return {**base, **overrides}


def test_defaults_and_trailing_slash() -> None:
    s = Settings.from_env(env())
    assert s.env == "dev"
    assert s.base_url == "https://tutor.example.com"
    assert s.port == 8000
    assert s.oauth_storage_dir == Path("data/oauth")


def test_optional_keys_are_read() -> None:
    s = Settings.from_env(
        env(TUTOR_ENV="prod", TUTOR_PORT="9000", TUTOR_OAUTH_STORAGE_DIR="/var/lib/tutor/oauth")
    )
    assert (s.env, s.port, s.oauth_storage_dir) == ("prod", 9000, Path("/var/lib/tutor/oauth"))


@pytest.mark.parametrize("key", REQUIRED)
def test_missing_key_is_named(key: str) -> None:
    values = env()
    del values[key]
    with pytest.raises(SystemExit, match=key) as info:
        Settings.from_env(values)
    assert PRIVATE_MARKER not in str(info.value)


def test_every_missing_key_is_listed() -> None:
    with pytest.raises(SystemExit) as info:
        Settings.from_env({"GOOGLE_CLIENT_SECRET": "  "})
    message = str(info.value)
    assert all(key in message for key in REQUIRED)


def test_short_signing_key_is_rejected_without_its_value() -> None:
    short = "s" * 31
    with pytest.raises(SystemExit, match="TUTOR_JWT_SIGNING_KEY must be at least 32") as info:
        Settings.from_env(env(TUTOR_JWT_SIGNING_KEY=short))
    assert short not in str(info.value)


def test_storage_key_must_be_a_fernet_key() -> None:
    with pytest.raises(SystemExit, match="TUTOR_OAUTH_STORAGE_KEY must be a Fernet key") as info:
        Settings.from_env(env(TUTOR_OAUTH_STORAGE_KEY=PRIVATE_MARKER))
    assert PRIVATE_MARKER not in str(info.value)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"TUTOR_ENV": "staging"}, "TUTOR_ENV must be dev, test or prod"),
        ({"TUTOR_PORT": "http"}, "TUTOR_PORT must be a number"),
        ({"TUTOR_PORT": "70000"}, "TUTOR_PORT must be a number"),
        ({"TUTOR_BASE_URL": "tutor.example.com"}, "TUTOR_BASE_URL must be an https URL"),
        ({"TUTOR_BASE_URL": "http://tutor.example.com"}, "TUTOR_BASE_URL must be an https URL"),
    ],
)
def test_invalid_values_are_named(overrides: dict[str, str], message: str) -> None:
    with pytest.raises(SystemExit, match=message):
        Settings.from_env(env(**overrides))


@pytest.mark.parametrize(
    "url", ["http://localhost:8000", "http://127.0.0.1:8000", "http://[::1]:8000", "https://x.io/"]
)
def test_loopback_http_and_https_are_accepted(url: str) -> None:
    assert Settings.from_env(env(TUTOR_BASE_URL=url)).base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@tutor.example.com",
        "https://tutor.example.com?a=1",
        "https://tutor.example.com#frag",
        "https://tutor.example.com/app",
        "https:///nohost",
        "http://localhost.evil.com",
    ],
)
def test_malformed_base_urls_are_rejected_without_the_value(url: str) -> None:
    with pytest.raises(SystemExit, match="TUTOR_BASE_URL must be an https URL") as info:
        Settings.from_env(env(TUTOR_BASE_URL=url))
    assert "pw@" not in str(info.value)


def test_secrets_are_stripped() -> None:
    s = Settings.from_env(env(TUTOR_JWT_SIGNING_KEY="  " + "j" * 40 + " "))
    assert s.jwt_signing_key == "j" * 40


def test_reused_secret_is_rejected_naming_keys_only() -> None:
    with pytest.raises(SystemExit, match="must not share the same value") as info:
        Settings.from_env(env(TUTOR_WEB_SESSION_SECRET="j" * 40))
    message = str(info.value)
    assert "TUTOR_JWT_SIGNING_KEY" in message
    assert "TUTOR_WEB_SESSION_SECRET" in message
    assert "j" * 40 not in message


def test_repr_hides_secrets() -> None:
    text = repr(Settings.from_env(env()))
    assert PRIVATE_MARKER not in text
    assert "j" * 40 not in text
    assert "w" * 40 not in text
    assert "tutor.example.com" in text


def test_repr_and_errors_hide_the_migration_dsn() -> None:
    dsn = f"postgresql+psycopg://owner:{PRIVATE_MARKER}@db/tutor"
    s = Settings.from_env(env(MIGRATION_DATABASE_URL=dsn))
    assert s.migration_database_url == dsn
    assert PRIVATE_MARKER not in repr(s)
    assert PRIVATE_MARKER not in str(s)


def test_migration_dsn_is_optional() -> None:
    assert Settings.from_env(env()).migration_database_url is None
    assert Settings.from_env(env(MIGRATION_DATABASE_URL="  ")).migration_database_url is None


def test_short_web_session_secret_is_rejected_without_its_value() -> None:
    short = "w" * 31
    with pytest.raises(SystemExit, match="TUTOR_WEB_SESSION_SECRET must be at least 32") as info:
        Settings.from_env(env(TUTOR_WEB_SESSION_SECRET=short))
    assert short not in str(info.value)
