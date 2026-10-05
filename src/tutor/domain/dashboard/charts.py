"""Geometry for server-drawn SVG charts. The browser only animates the result."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

_LEVELS = ("A1", "A2", "B1", "B2", "C1", "C2")


@dataclass(frozen=True, slots=True)
class LineChart:
    width: int
    height: int
    path: str  # SVG path data; "" when there is nothing to draw
    dots: tuple[tuple[float, float], ...]
    target_y: float | None
    y_max: float


def line_chart(
    values: Sequence[float | None],
    *,
    target: float | None = None,
    width: int = 320,
    height: int = 120,
    pad: int = 8,
) -> LineChart:
    present = [v for v in values if v is not None]
    top = max([*present, *([target] if target is not None else []), 0.0])
    y_max = top * 1.1 if top > 0 else 1.0
    n = len(values)
    inner_w = width - 2 * pad
    inner_h = height - 2 * pad

    def x(i: int) -> float:
        return pad + (inner_w / 2 if n == 1 else inner_w * i / (n - 1))

    def y(v: float) -> float:
        return pad + inner_h * (1 - v / y_max)

    parts: list[str] = []
    dots: list[tuple[float, float]] = []
    pen_down = False
    for i, v in enumerate(values):
        if v is None:
            pen_down = False
            continue
        px, py = round(x(i), 1), round(y(v), 1)
        parts.append(f"{'L' if pen_down else 'M'}{px:g} {py:g}")
        dots.append((px, py))
        pen_down = True
    target_y = None if target is None else round(y(target), 1)
    return LineChart(width, height, " ".join(parts), tuple(dots), target_y, y_max)


def meter_fraction(used: int, cap: int) -> float:
    if cap <= 0:
        return 1.0
    return min(max(used / cap, 0.0), 1.0)


def cefr_label(value: float) -> str:
    """Numeric level (1.0 = A1 … 6.0 = C2) to a half-step label such as "B1+"."""
    halves = min(max(math.floor(value * 2 + 0.5), 2), 12)
    base, plus = divmod(halves, 2)
    return _LEVELS[base - 1] + ("+" if plus else "")
