from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from cryptography.fernet import Fernet

from tutor.settings import DEV_ONLY_KEYS, REQUIRED, Settings

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
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://app.localhost:8000",  # RFC 6761: *.localhost resolves to loopback
        "https://x.io/",
    ],
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
    key = Fernet.generate_key().decode()
    with pytest.raises(SystemExit, match="must not share the same value") as info:
        Settings.from_env(env(TUTOR_JWT_SIGNING_KEY=key, TUTOR_OAUTH_STORAGE_KEY=key))
    message = str(info.value)
    assert "TUTOR_JWT_SIGNING_KEY" in message
    assert "TUTOR_OAUTH_STORAGE_KEY" in message
    assert key not in message


def test_web_session_secret_is_neither_required_nor_read() -> None:
    # Final review M3: web sessions are random tokens stored as SHA-256 hashes; no secret.
    assert "TUTOR_WEB_SESSION_SECRET" not in REQUIRED
    s = Settings.from_env(env(TUTOR_WEB_SESSION_SECRET="j" * 40))
    assert not hasattr(s, "web_session_secret")


def test_repr_hides_secrets() -> None:
    text = repr(Settings.from_env(env()))
    assert PRIVATE_MARKER not in text
    assert "j" * 40 not in text
    assert "tutor.example.com" in text


def test_the_migration_dsn_is_never_stored() -> None:
    # Only the prod guard reads MIGRATION_DATABASE_URL; the app never keeps it.
    dsn = f"postgresql+psycopg://owner:{PRIVATE_MARKER}@db/tutor"
    s = Settings.from_env(env(MIGRATION_DATABASE_URL=dsn))
    assert not hasattr(s, "migration_database_url")
    assert PRIVATE_MARKER not in repr(s)


@pytest.mark.parametrize("url", ["sqlite://", "mysql://u:p@h/d"])
def test_prod_requires_a_postgres_database_url(url: str) -> None:
    with pytest.raises(SystemExit, match="DATABASE_URL") as info:
        Settings.from_env(env(TUTOR_ENV="prod", DATABASE_URL=url))
    assert url not in str(info.value)
    assert Settings.from_env(env(DATABASE_URL=url)).env == "dev"


@pytest.mark.parametrize("key", ["MIGRATION_DATABASE_URL", "GATE_REPORT_DATABASE_URL"])
def test_prod_refuses_other_roles_credentials(key: str) -> None:
    with pytest.raises(SystemExit) as exc:
        Settings.from_env(env(TUTOR_ENV="prod", **{key: "postgresql://owner:hunter2@db/x"}))
    assert key in str(exc.value)
    assert "hunter2" not in str(exc.value)
    assert Settings.from_env(env(**{key: "postgresql://owner:x@db/x"})).env == "dev"


# --- TUTOR_DEV_AUTH (dev-environment design D1, D10) ---------------------------------------------

LOOPBACK = "http://localhost:8000"


@pytest.mark.parametrize("value", ["1", "true", "YES", " Yes "])
def test_dev_auth_flag_is_parsed_on_a_loopback_dev_url(value: str) -> None:
    assert Settings.from_env(env(TUTOR_DEV_AUTH=value, TUTOR_BASE_URL=LOOPBACK)).dev_auth is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "on"])
def test_dev_auth_is_off_by_default_and_for_other_values(value: str) -> None:
    assert Settings.from_env(env(TUTOR_DEV_AUTH=value, TUTOR_BASE_URL=LOOPBACK)).dev_auth is False
    assert Settings.from_env(env()).dev_auth is False


def test_dev_auth_is_refused_in_prod() -> None:
    with pytest.raises(SystemExit, match="TUTOR_DEV_AUTH requires TUTOR_ENV") as info:
        Settings.from_env(env(TUTOR_ENV="prod", TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=LOOPBACK))
    assert PRIVATE_MARKER not in str(info.value)


@pytest.mark.parametrize("url", ["https://tutor.example.com", "https://x.io"])
def test_dev_auth_is_refused_on_a_public_base_url(url: str) -> None:
    with pytest.raises(SystemExit, match="loopback TUTOR_BASE_URL") as info:
        Settings.from_env(env(TUTOR_ENV="dev", TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=url))
    assert PRIVATE_MARKER not in str(info.value)


@pytest.mark.parametrize("tutor_env", ["dev", "test"])
@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://app.localhost:8000",
    ],
)
def test_dev_auth_is_accepted_on_every_loopback_form(tutor_env: str, url: str) -> None:
    s = Settings.from_env(env(TUTOR_ENV=tutor_env, TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=url))
    assert s.dev_auth and s.base_url == url


def test_google_keys_are_optional_only_with_dev_auth() -> None:
    with_flag = env(TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=LOOPBACK)
    values = {k: v for k, v in with_flag.items() if not k.startswith("GOOGLE_")}
    s = Settings.from_env(values)
    assert (s.google_client_id, s.google_client_secret) == ("", "")
    without = env(TUTOR_BASE_URL=LOOPBACK)
    del without["GOOGLE_CLIENT_ID"]
    with pytest.raises(SystemExit, match="GOOGLE_CLIENT_ID"):
        Settings.from_env(without)


def test_a_refused_dev_auth_keeps_the_google_keys_required() -> None:
    # Refused in prod, so the flag must not loosen anything: the missing key is still named.
    values = env(TUTOR_ENV="prod", TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=LOOPBACK)
    del values["GOOGLE_CLIENT_ID"]
    with pytest.raises(SystemExit, match="GOOGLE_CLIENT_ID"):
        Settings.from_env(values)


def test_the_dev_flag_is_a_dev_only_key() -> None:
    assert DEV_ONLY_KEYS == ("TUTOR_DEV_AUTH",)
    assert not set(DEV_ONLY_KEYS) & set(REQUIRED)


def _fields(**overrides: Any) -> dict[str, Any]:
    return {
        "database_url": "sqlite://",
        "google_client_id": "",
        "google_client_secret": "",
        "jwt_signing_key": "j" * 40,
        "oauth_storage_key": Fernet.generate_key().decode(),
        "oauth_storage_dir": Path("data/oauth"),
        **overrides,
    }


@pytest.mark.parametrize(
    ("tutor_env", "url"),
    [("prod", "https://tutor.example.com"), ("prod", LOOPBACK), ("dev", "https://x.io")],
)
def test_settings_built_directly_refuse_dev_auth_outside_loopback_dev(
    tutor_env: str, url: str
) -> None:
    # The guard lives on the dataclass too, not only in from_env (WebConfig does the same).
    with pytest.raises(SystemExit, match="TUTOR_DEV_AUTH requires TUTOR_ENV"):
        Settings(env=tutor_env, base_url=url, dev_auth=True, **_fields())  # type: ignore[arg-type]


def test_settings_built_directly_accept_dev_auth_on_loopback_dev_and_replace_rechecks() -> None:
    ok = Settings(env="dev", base_url=LOOPBACK, dev_auth=True, **_fields())
    assert ok.dev_auth
    assert not Settings(env="prod", base_url="https://tutor.example.com", **_fields()).dev_auth
    with pytest.raises(SystemExit, match="TUTOR_DEV_AUTH requires TUTOR_ENV"):
        replace(ok, env="prod")


@pytest.mark.parametrize("allowed", ["172.30.10.3", "127.0.0.1", "*"])
def test_dev_auth_is_refused_behind_a_proxy(allowed: str) -> None:
    # FORWARDED_ALLOW_IPS means a proxy (a tunnel) is in front: dev auth must never be reachable.
    with pytest.raises(SystemExit, match="FORWARDED_ALLOW_IPS") as info:
        Settings.from_env(
            env(TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=LOOPBACK, FORWARDED_ALLOW_IPS=allowed)
        )
    assert PRIVATE_MARKER not in str(info.value)


def test_a_blank_forwarded_allow_ips_is_unset_and_prod_keeps_it() -> None:
    blank = env(TUTOR_DEV_AUTH="1", TUTOR_BASE_URL=LOOPBACK, FORWARDED_ALLOW_IPS=" ")
    assert Settings.from_env(blank).dev_auth
    prod = env(TUTOR_ENV="prod", FORWARDED_ALLOW_IPS="172.30.10.3")
    assert Settings.from_env(prod).env == "prod"
