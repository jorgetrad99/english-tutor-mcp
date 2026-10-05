import hashlib
import logging
import sys
from uuid import UUID

import pytest
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError

from tutor.mcp.errors import error_text
from tutor.mcp.observe import REDACTED, ScrubFilter, guard_library_logs, outcome_of, user_hash
from tutor.mcp.ratelimit import SlidingWindowLimiter

pytestmark = pytest.mark.unit


def test_user_hash_is_12_hex_of_sha256() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert user_hash(uid) == hashlib.sha256(str(uid).encode()).hexdigest()[:12]
    assert len(user_hash(uid)) == 12


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (ToolError(error_text("session_closed")), "session_closed"),
        (ToolError("plain text"), "tool_error"),
        (ToolError('["json", "list"]'), "tool_error"),
        (PermissionError("no token"), "unauthenticated"),
        (MCPError(code=-32602, message="Unknown tool"), "protocol_error"),
        (RuntimeError("boom"), "internal_error"),
    ],
)
def test_outcome_codes(exc: BaseException, expected: str) -> None:
    assert outcome_of(exc) == expected


def test_limiter_allows_up_to_the_limit_per_key() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window_s=60.0, clock=lambda: now[0])
    assert [limiter.allow("a"), limiter.allow("a"), limiter.allow("a")] == [True, True, False]
    assert limiter.allow("b") is True


def test_limiter_window_slides_and_rejections_do_not_count() -> None:
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window_s=60.0, clock=lambda: now[0])
    limiter.allow("a")
    now[0] = 30.0
    limiter.allow("a")
    assert limiter.allow("a") is False
    now[0] = 60.0
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    now[0] = 90.0
    assert limiter.allow("a") is True


def test_default_limit_is_60_per_minute() -> None:
    limiter = SlidingWindowLimiter(clock=lambda: 0.0)
    assert sum(limiter.allow("u") for _ in range(61)) == 60


def test_scrub_filter_keeps_the_template_and_drops_values_and_tracebacks() -> None:
    try:
        raise RuntimeError("ZQX-exception")
    except RuntimeError:
        info = sys.exc_info()
    record = logging.LogRecord("fastmcp.x", logging.ERROR, __file__, 1, "call %s", ("ZQX",), info)
    assert ScrubFilter().filter(record) is True
    assert logging.Formatter("%(message)s").format(record) == f"call {REDACTED}"
    mapping = logging.LogRecord(
        "fastmcp.x", logging.INFO, __file__, 1, "%(a)s", ({"a": "ZQX"},), None
    )
    ScrubFilter().filter(mapping)
    assert mapping.getMessage() == REDACTED


def test_library_log_guards_are_installed_once() -> None:
    guard_library_logs()
    guard_library_logs()
    server_logger = logging.getLogger("fastmcp.server.server")
    assert sum(isinstance(f, ScrubFilter) for f in server_logger.filters) == 1
    assert not logging.getLogger("fastmcp.server.auth.oauth_proxy.proxy").isEnabledFor(
        logging.ERROR
    )
