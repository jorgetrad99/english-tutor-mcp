"""Inicio: today's practice, the week trail and what is pending (spec 6.3)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from tutor.domain.dashboard.home import build_week_trail, should_celebrate
from tutor.domain.dashboard.types import TrailNode, User
from tutor.web.deps import APP_ROUTER_DEPS, current_user, get_deps
from tutor.web.routes.connect import connect_context
from tutor.web.views import render, today_for

router = APIRouter(dependencies=APP_ROUTER_DEPS)

# One point per weekday, Monday first, inside a 320 x 96 viewBox.
TRAIL_POINTS: tuple[tuple[int, int], ...] = (
    (20, 70),
    (66, 46),
    (113, 60),
    (160, 34),
    (206, 52),
    (253, 28),
    (300, 44),
)


def _path(points: Sequence[tuple[int, int]]) -> str:
    return " ".join(f"{'M' if i == 0 else 'L'}{x} {y}" for i, (x, y) in enumerate(points))


def trail_view(trail: Sequence[TrailNode]) -> dict[str, Any]:
    nodes = [
        {"x": x, "y": y, "node": node} for (x, y), node in zip(TRAIL_POINTS, trail, strict=True)
    ]
    today_index = next((i for i, node in enumerate(trail) if node.is_today), len(trail) - 1)
    return {
        "nodes": nodes,
        "path": _path(TRAIL_POINTS),
        "walked": _path(TRAIL_POINTS[: today_index + 1]),
    }


@router.get("/app/", response_class=HTMLResponse)
def home_page(request: Request, user: Annotated[User, Depends(current_user)]) -> HTMLResponse:
    deps = get_deps(request)
    today = today_for(request)
    home = deps.reader.home(user.id, today)
    celebrate = should_celebrate(home.newest_closed_session_id, home.last_celebrated_session_id)
    if celebrate and home.newest_closed_session_id is not None:
        # Marked before rendering so a reload never celebrates the same session twice.
        deps.account.mark_celebrated(user.id, home.newest_closed_session_id)
    trail = build_week_trail(home.week_start, home.planned_days, home.session_marks, today)
    return render(
        request,
        "pages/home.html",
        {
            **connect_context(request, user),
            "home": home,
            "trail": trail_view(trail),
            "celebrate": celebrate,
            "install_dismissed": deps.reader.account(user.id).install_prompt_dismissed,
        },
    )
