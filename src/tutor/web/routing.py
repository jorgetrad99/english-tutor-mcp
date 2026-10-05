"""One way to list a FastAPI app's routes.

FastAPI 0.142 keeps `include_router` results as `_IncludedRouter` nodes in `app.routes`,
so a plain `isinstance(route, APIRoute)` scan sees nothing. Security guards and the route
sweep use this walker instead.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

STATIC_MOUNT = "/static"


def iter_api_routes(app: FastAPI) -> list[APIRoute]:
    """Every `APIRoute`, including those of included routers (recursively).

    Fails closed: it raises on any node it cannot classify (a plain `add_route`, a websocket,
    a mount other than `/static`), on low-priority routes, and on an include that adds a
    prefix or dependencies, because a guard must not trust a route list that may be wrong.
    """
    if app.router._low_priority_routes:
        raise RuntimeError("low-priority routes are not supported by iter_api_routes")
    return _walk(app.routes)


def _is_static_mount(route: Any) -> bool:
    return (
        isinstance(route, Mount)
        and route.path == STATIC_MOUNT
        and isinstance(route.app, StaticFiles)
    )


def _walk(routes: Iterable[Any]) -> list[APIRoute]:
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        if _is_static_mount(route):
            continue
        router = getattr(route, "original_router", None)
        if router is None or not hasattr(route, "include_context"):
            raise RuntimeError(
                f"unsupported route node {type(route).__name__} at "
                f"{getattr(route, 'path', '?')!r}: iter_api_routes only walks APIRoutes, "
                "included routers and the /static mount"
            )
        if router._low_priority_routes:
            raise RuntimeError("low-priority routes are not supported by iter_api_routes")
        context = route.include_context
        if context.prefix or context.dependencies:
            raise RuntimeError(
                "include_router with a prefix or dependencies is not supported by "
                "iter_api_routes; put them on the router itself or extend the walker"
            )
        found.extend(_walk(router.routes))
    return found
