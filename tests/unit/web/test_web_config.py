import pytest

from tutor.web.config import WebConfig

ENV = {
    "TUTOR_ENV": "prod",
    "TUTOR_BASE_URL": "https://tutor.example.com/",
    "TUTOR_MCP_URL": "https://tutor.example.com/mcp",
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
    assert WebConfig.from_env({**ENV, "TUTOR_ENV": "test", "TUTOR_TEST_LOGIN": "1"}).test_login


def test_unknown_env_is_refused() -> None:
    with pytest.raises(ValueError, match="TUTOR_ENV"):
        WebConfig.from_env({**ENV, "TUTOR_ENV": "staging"})
