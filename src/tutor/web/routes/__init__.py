"""Every web router. Each page task appends its router to `all_routers`."""

from __future__ import annotations

from fastapi import APIRouter

from tutor.web.config import WebConfig


def all_routers(config: WebConfig) -> list[APIRouter]:
    from tutor.web.routes import public

    routers: list[APIRouter] = [public.router]
    return routers
