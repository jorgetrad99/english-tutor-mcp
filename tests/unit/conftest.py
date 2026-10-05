"""Unit-suite speedups that do not change what any test asserts.

A conftest fixture applies only to tests under this directory, and both patches below are function
scoped and undone after every test, so integration tests never run with them in any ordering.
"""

from collections.abc import Callable, Iterator
from typing import Any

import fastmcp
import pytest
from fastmcp.server.auth.oauth_proxy import proxy as oauth_proxy
from jinja2 import Environment
from jinja2.utils import _PassArg

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

_JWT_CACHE: dict[tuple[Any, ...], bytes] = {}
_COMPILE_CACHE: dict[tuple[Any, ...], Any] = {}


def _callables(table: dict[str, Any]) -> tuple[Any, ...]:
    """Name and pass-argument marker: all the compiler reads (it never embeds the function).

    The function objects are deliberately not part of the key: the website builds fresh closures per
    environment, so keying on them would never hit. Two functions with the same name and marker
    compile to identical code.
    """
    entries = ((name, _PassArg.from_obj(fn)) for name, fn in table.items())
    return tuple(sorted(entries, key=lambda entry: entry[0]))


def _compile_key(env: Environment) -> tuple[Any, ...]:
    extensions = sorted(
        f"{type(e).__module__}.{type(e).__qualname__}" for e in env.extensions.values()
    )
    return (
        type(env),
        *(getattr(env, name, None) for name in _COMPILE_SETTINGS),
        env.autoescape,
        env.finalize,
        tuple(extensions),
        _callables(env.filters),
        _callables(env.tests),
    )


@pytest.fixture(autouse=True)
def _memoized_jwt_key_derivation() -> Iterator[None]:
    """FastMCP derives the JWT signing key with PBKDF2 at 1,000,000 iterations (about 0.5 s) every
    time a Google provider is built, and each unit test builds its own provider.

    The derivation is a pure function of its keyword arguments and of `fastmcp.settings.test_mode`
    (which picks the low-entropy iteration count), so all of those are in the memo key: a different
    secret, salt or mode derives afresh.
    """
    original: Callable[..., bytes] = oauth_proxy.derive_jwt_key

    def memoized(**kwargs: Any) -> bytes:
        key = (fastmcp.settings.test_mode, *sorted(kwargs.items()))
        if key not in _JWT_CACHE:
            _JWT_CACHE[key] = original(**kwargs)
        return _JWT_CACHE[key]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(oauth_proxy, "derive_jwt_key", memoized)
        yield


@pytest.fixture(autouse=True)
def _shared_template_compilation() -> Iterator[None]:
    """Every test that builds the website builds fresh Jinja environments, and each environment
    recompiles every template it renders (about 680 compilations per run).

    Compiling is a pure function of the template source and the compile-time environment state
    (settings, extensions, autoescape, finalize, and the filters and tests with their pass-argument
    markers, which the compiler reads), and its result is an immutable code object, so it is cached
    on exactly those inputs. Rendering, globals and translations stay per environment.
    """
    original = Environment.compile

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
        if key not in _COMPILE_CACHE:
            _COMPILE_CACHE[key] = original(self, source, name, filename, raw, defer_init)  # type: ignore[call-overload]
        return _COMPILE_CACHE[key]

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Environment, "compile", compile_cached)
        yield
