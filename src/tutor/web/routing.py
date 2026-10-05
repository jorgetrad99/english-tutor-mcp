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


def iter_api_routes(app: FastAPI) -> list[APIRoute]:
    """Every `APIRoute`, including those of included routers (recursively).

    Fails loudly when an include adds a prefix or dependencies: the original route would
    then report a wrong path or miss a dependency, and a guard must not trust that.
    """
    return _walk(app.routes)


def _walk(routes: Iterable[Any]) -> list[APIRoute]:
    found: list[APIRoute] = []
    for route in routes:
        if isinstance(route, APIRoute):
            found.append(route)
            continue
        router = getattr(route, "original_router", None)
        if router is None:
            continue  # Mount, static files, websockets: not APIRoutes
        context = route.include_context
        if context.prefix or context.dependencies:
            raise RuntimeError(
                "include_router with a prefix or dependencies is not supported by "
                "iter_api_routes; put them on the router itself or extend the walker"
            )
        found.extend(_walk(router.routes))
    return found
