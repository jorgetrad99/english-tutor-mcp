"""Jinja rendering with one environment per language and the shared base context."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from functools import partial
from typing import Any
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import HTMLResponse
from jinja2 import Environment, FileSystemLoader, StrictUndefined, pass_context
from jinja2.runtime import Context

from tutor.domain.dashboard.billing import banners_for
from tutor.domain.dashboard.home import local_today
from tutor.domain.dashboard.types import Lang, Tier, User
from tutor.web.assets import AssetManifest
from tutor.web.i18n import (
    TEMPLATES_DIR,
    fmt_date,
    fmt_decimal,
    fmt_int,
    fmt_money,
    fmt_percent,
    load_translations,
    negotiate,
)
from tutor.web.ports import WebDeps
from tutor.web.security import safe_next


def _day_filter(lang: Lang) -> Callable[..., str]:
    """`{{ value|day("weekday") }}`; aware datetimes become the learner's local date."""

    @pass_context
    def day(ctx: Context, value: date | datetime, style: str = "short") -> str:
        if isinstance(value, datetime):
            user = ctx.get("user")
            value = local_today(value, user.timezone if user else "UTC")
        return fmt_date(value, lang, style)  # type: ignore[arg-type]

    return day


class Views:
    def __init__(self, assets: AssetManifest) -> None:
        self.envs = {lang: self._env(lang, assets) for lang in Lang}

    @staticmethod
    def _env(lang: Lang, assets: AssetManifest) -> Environment:
        env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR),
            autoescape=True,
            extensions=["jinja2.ext.i18n"],
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        translations = load_translations(lang)
        env.install_gettext_translations(translations, newstyle=True)  # type: ignore[attr-defined]
        env.globals["asset"] = assets.url
        env.filters["decimal"] = lambda v, digits=1: fmt_decimal(v, lang, digits)
        env.filters["integer"] = partial(fmt_int, lang=lang)
        env.filters["percent"] = partial(fmt_percent, lang=lang)
        env.filters["money"] = partial(fmt_money, lang=lang)
        env.filters["day"] = _day_filter(lang)
        return env


def _user(request: Request) -> User | None:
    user: User | None = getattr(request.state, "user", None)
    return user


def is_htmx(request: Request) -> bool:
    return (
        request.headers.get("hx-request") == "true"
        and request.headers.get("hx-history-restore-request") != "true"
    )


def request_lang(request: Request) -> Lang:
    return negotiate(
        request.query_params.get("lang"), request.headers.get("accept-language"), _user(request)
    )


def today_for(request: Request) -> date:
    deps: WebDeps = request.app.state.deps
    user = _user(request)
    now = deps.clock()
    return local_today(now, user.timezone) if user else now.astimezone(UTC).date()


def _csrf(request: Request) -> str:
    holder = getattr(request.state, "web", None)
    session = getattr(holder, "session", None)
    return session.csrf_token if session is not None and session.user_id is not None else ""


def _next_qs(request: Request) -> str:
    """`&next=<encoded>` for the language link; only a same-site path survives."""
    raw = request.query_params.get("next")
    return f"&next={quote(safe_next(raw), safe='/')}" if raw else ""


def render(
    request: Request,
    template: str,
    ctx: Mapping[str, Any] | None = None,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
) -> HTMLResponse:
    views: Views = request.app.state.views
    deps: WebDeps = request.app.state.deps
    lang = request_lang(request)
    user = _user(request)
    today = today_for(request)
    subscription = deps.subscriptions.subscription(user.id) if user else None
    usage = deps.reader.usage(user.id, today) if user else None

    def public_url(path: str) -> str:
        if user is not None:
            return path
        code = "en" if lang is Lang.EN else "es"
        return f"{path}{'&' if '?' in path else '?'}lang={code}"

    base: dict[str, Any] = {
        "request": request,
        "user": user,
        "csrf_token": _csrf(request),
        "config": request.app.state.config,
        "lang": lang,
        "html_lang": "es-MX" if lang is Lang.ES_MX else "en",
        "path": request.url.path,
        "banners": banners_for(subscription, usage, today) if subscription else (),
        "subscription": subscription,
        "usage": usage if subscription and subscription.tier is Tier.FREE else None,
        "today": today,
        "reduce_motion": bool(user and user.reduce_motion),
        "public_url": public_url,
        "next_qs": _next_qs(request),
    }
    html = views.envs[lang].get_template(template).render({**base, **(ctx or {})})
    return HTMLResponse(html, status_code=status_code, headers=dict(headers or {}))
