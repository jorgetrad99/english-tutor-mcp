"""Every web router. Each page task appends its router to `all_routers`."""

from __future__ import annotations

from fastapi import APIRouter

from tutor.web.config import WebConfig


def all_routers(config: WebConfig) -> list[APIRouter]:
    from tutor.web.routes import auth, connect, home, public, pwa

    routers: list[APIRouter] = [
        public.router,
        *auth.routers(config),
        pwa.router,
        pwa.app_router,
        connect.router,
        home.router,
    ]
    return routers
