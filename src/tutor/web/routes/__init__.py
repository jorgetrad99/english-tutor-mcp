"""Every web router. Each page task appends its router to `all_routers`."""

from __future__ import annotations

from fastapi import APIRouter

from tutor.web.config import WebConfig


def all_routers(config: WebConfig) -> list[APIRouter]:
    from tutor.web.routes import auth, public

    routers: list[APIRouter] = [public.router, *auth.routers(config)]
    return routers
