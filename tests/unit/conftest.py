"""Unit-suite speedups that do not change what any test asserts.

A conftest fixture applies only to tests under this directory, and both patches below are function
scoped and undone after every test, so integration tests never run with them in any ordering.
"""

from collections.abc import Callable, Iterator
from enum import Enum
from functools import partial
from types import FunctionType
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


_IMMUTABLE = (str, int, float, bool, bytes, type(None), Enum)


def _immutable(value: Any) -> bool:
    if isinstance(value, tuple | frozenset):
        return all(_immutable(item) for item in value)
    return isinstance(value, _IMMUTABLE)


class _Identity:
    """Key an object by identity while keeping it alive, so its id can never be reused."""

    __slots__ = ("obj",)

    def __init__(self, obj: Any) -> None:
        self.obj = obj

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Identity) and other.obj is self.obj

    def __hash__(self) -> int:
        return id(self.obj)


def _tag(value: Any) -> Any:
    """Type-tag an immutable value so 1, 1.0, True, 0.0 and -0.0 never collide."""
    if isinstance(value, tuple | frozenset):
        return (type(value), tuple(_tag(item) for item in value))
    if isinstance(value, float):
        return (float, repr(value))
    return (type(value), value)


def _function_key(fn: Any) -> Any:
    """What decides a filter or test's behaviour, for constant folding at compile time.

    Jinja calls filters and tests on constant input while compiling and embeds the result, so the
    compiled code depends on the function's behaviour. A plain function, a lambda or a closure is
    keyed on its code object, defaults and closure cell contents; a `functools.partial` on its
    target, arguments and keywords. That key is used only when every captured value is immutable
    (str, number, bytes, None, Enum, or tuples of those) and any global the code reads is module
    state shared by all copies (the globals dict is part of the key). Anything else is keyed on the
    object itself, which never shares. The cache assumes filter inputs (captured objects and module
    state) do not change during the session.
    """
    if isinstance(fn, partial):
        inner = _function_key(fn.func)
        values = (*fn.args, *fn.keywords.values())
        if inner is not fn.func and _immutable(values):
            return (
                "partial",
                inner,
                _tag(fn.args),
                tuple(sorted((k, _tag(v)) for k, v in fn.keywords.items())),
            )
        return fn
    code = getattr(fn, "__code__", None)
    if code is None or type(fn) is not FunctionType:
        return fn
    try:
        cells = tuple(cell.cell_contents for cell in fn.__closure__ or ())
    except ValueError:  # an empty cell
        return fn
    captured = (*cells, *(fn.__defaults__ or ()), *(fn.__kwdefaults__ or {}).values())
    if not _immutable(captured):
        return fn
    return (
        "function",
        code,
        _Identity(fn.__globals__),
        _tag(cells),
        _tag(fn.__defaults__),
        tuple(sorted((k, _tag(v)) for k, v in (fn.__kwdefaults__ or {}).items())),
    )


def _callables(table: dict[str, Any]) -> tuple[Any, ...]:
    """Name, pass-argument marker and the function's behaviour key."""
    entries = ((name, _PassArg.from_obj(fn), _function_key(fn)) for name, fn in table.items())
    return tuple(sorted(entries, key=lambda entry: entry[0]))


def _policies(env: Environment) -> tuple[Any, ...]:
    """Policies such as `truncate.leeway` and `json.dumps_kwargs` change filter output."""
    items = ((k, _tag(v) if _immutable(v) else _Identity(v)) for k, v in env.policies.items())
    return tuple(sorted(items, key=lambda item: item[0]))


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
        _policies(env),
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
    (settings, extensions, autoescape, finalize, and the filters and tests: names, pass-argument
    markers and behaviour, because the compiler checks them and constant-folds calls to them), and
    its result is an immutable code object, so it is cached on exactly those inputs. Rendering,
    globals and translations stay per environment.
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
