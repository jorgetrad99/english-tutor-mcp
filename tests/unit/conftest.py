"""Unit-suite speedups that do not change what any test asserts."""

from collections.abc import Callable, Iterator
from functools import lru_cache
from typing import Any

import pytest
from fastmcp.server.auth.oauth_proxy import proxy as oauth_proxy
from jinja2 import Environment

_COMPILE_SETTINGS = (
    "block_start_string",
    "block_end_string",
    "variable_start_string",
    "variable_end_string",
    "comment_start_string",
    "comment_end_string",
    "line_statement_prefix",
    "line_comment_prefix",
    "trim_blocks",
    "lstrip_blocks",
    "newline_sequence",
    "keep_trailing_newline",
    "optimized",
    "is_async",
    "newstyle_gettext",
)


@pytest.fixture(scope="session", autouse=True)
def _memoized_jwt_key_derivation() -> Iterator[None]:
    """FastMCP derives the JWT signing key with PBKDF2 at 1,000,000 iterations (about 0.5 s) every
    time a Google provider is built, and each unit test builds its own provider.

    The derivation is a pure function of its keyword arguments, so memoizing it returns the same
    key for the same inputs; a different secret or salt still derives afresh. No test asserts on
    the iteration count, and the integration suite (a separate directory) is not affected.
    """
    original: Callable[..., bytes] = oauth_proxy.derive_jwt_key
    cached: Any = lru_cache(maxsize=None)(original)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(oauth_proxy, "derive_jwt_key", cached)
        yield


def _compile_key(env: Environment) -> tuple[Any, ...]:
    extensions = sorted(
        f"{type(e).__module__}.{type(e).__qualname__}" for e in env.extensions.values()
    )
    return (
        type(env),
        *(getattr(env, name, None) for name in _COMPILE_SETTINGS),
        id(env.autoescape),
        id(env.finalize),
        tuple(extensions),
    )


@pytest.fixture(scope="session", autouse=True)
def _shared_template_compilation() -> Iterator[None]:
    """Every test that builds the website builds fresh Jinja environments, and each environment
    recompiles every template it renders (about 680 compilations per run).

    Compiling is a pure function of the template source and the compile-time environment settings,
    and its result is an immutable code object, so it is cached on exactly those inputs. Rendering,
    globals, filters and translations stay per environment, so no test state is shared.
    """
    original = Environment.compile
    cache: dict[tuple[Any, ...], Any] = {}

    def compile_cached(
        self: Environment,
        source: Any,
        name: str | None = None,
        filename: str | None = None,
        raw: bool = False,
        defer_init: bool = False,
    ) -> Any:
        if not isinstance(source, str):
            return original(self, source, name, filename, raw, defer_init)  # type: ignore[call-overload]
        key = (_compile_key(self), source, name, filename, raw, defer_init)
        if key not in cache:
            cache[key] = original(self, source, name, filename, raw, defer_init)  # type: ignore[call-overload]
        return cache[key]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Environment, "compile", compile_cached)
        yield
