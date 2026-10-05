import pytest

from tutor.web.config import WebConfig

ENV = {
    "TUTOR_ENV": "prod",
    "TUTOR_BASE_URL": "https://tutor.example.com/",
    "TUTOR_SUPPORT_EMAIL": "soporte@example.com",
}


def test_from_env_strips_trailing_slash() -> None:
    config = WebConfig.from_env(ENV)
    assert config.base_url == "https://tutor.example.com"
    assert config.env == "prod" and not config.test_login


def test_test_login_is_refused_outside_test_env() -> None:
    with pytest.raises(ValueError, match="TUTOR_ENV=test"):
        WebConfig.from_env({**ENV, "TUTOR_TEST_LOGIN": "1"})


def test_test_login_allowed_in_test_env() -> None:
    env = {
        **ENV,
        "TUTOR_ENV": "test",
        "TUTOR_TEST_LOGIN": "1",
        "TUTOR_BASE_URL": "http://localhost",
    }
    assert WebConfig.from_env(env).test_login


def test_unknown_env_is_refused() -> None:
    with pytest.raises(ValueError, match="TUTOR_ENV"):
        WebConfig.from_env({**ENV, "TUTOR_ENV": "staging"})


@pytest.mark.parametrize(
    "url",
    [
        "",
        "tutor.example.com",
        "ftp://tutor.example.com",
        "https://",
        "http://tutor.example.com",
        "https://user:pw@tutor.example.com",
        "javascript://x",
    ],
)
def test_bad_base_url_is_refused_naming_only_the_key(url: str) -> None:
    with pytest.raises(ValueError, match="TUTOR_BASE_URL") as info:
        WebConfig.from_env({**ENV, "TUTOR_BASE_URL": url})
    assert url not in str(info.value) or url == ""


def test_http_is_allowed_on_loopback() -> None:
    config = WebConfig.from_env({**ENV, "TUTOR_BASE_URL": "http://localhost:8780/"})
    assert config.base_url == "http://localhost:8780"


def test_test_login_requires_a_loopback_base_url() -> None:
    env = {**ENV, "TUTOR_ENV": "test", "TUTOR_TEST_LOGIN": "1"}
    with pytest.raises(ValueError, match="loopback"):
        WebConfig.from_env(env)  # https://tutor.example.com
    assert WebConfig.from_env({**env, "TUTOR_BASE_URL": "http://127.0.0.1:8780"}).test_login


def test_tutor_env_is_stripped_like_settings() -> None:
    # Final review M13: Settings strips TUTOR_ENV, so " prod " must not become two envs.
    assert WebConfig.from_env({**ENV, "TUTOR_ENV": " prod "}).env == "prod"


@pytest.mark.parametrize("value", [None, "", "  "])
def test_mcp_url_is_derived_from_the_base_url_when_unset(value: str | None) -> None:
    env = {k: v for k, v in ENV.items() if k != "TUTOR_MCP_URL"}
    if value is not None:
        env["TUTOR_MCP_URL"] = value
    assert WebConfig.from_env(env).mcp_url == "https://tutor.example.com/mcp"


def test_a_matching_mcp_url_is_accepted_with_spaces_stripped() -> None:
    config = WebConfig.from_env({**ENV, "TUTOR_MCP_URL": " https://tutor.example.com/mcp "})
    assert config.mcp_url == "https://tutor.example.com/mcp"


@pytest.mark.parametrize(
    "url",
    [
        "https://other.example.com/mcp",
        "https://tutor.example.com/mcp/",
        "http://tutor.example.com/mcp",
        "https://tutor.example.com/api/mcp",
    ],
)
def test_a_different_mcp_url_is_refused_naming_keys_only(url: str) -> None:
    with pytest.raises(ValueError, match="TUTOR_MCP_URL must equal TUTOR_BASE_URL") as info:
        WebConfig.from_env({**ENV, "TUTOR_MCP_URL": url})
    assert url not in str(info.value)
    with pytest.raises(ValueError, match="TUTOR_MCP_URL"):
        WebConfig(
            env="prod",
            base_url="https://tutor.example.com",
            mcp_url=url,
            support_email="soporte@example.com",
        )
