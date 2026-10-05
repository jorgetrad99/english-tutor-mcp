import pytest

from tutor.domain.dashboard.charts import cefr_label, line_chart, meter_fraction

pytestmark = pytest.mark.unit


def test_two_points_scale_into_padded_box() -> None:
    chart = line_chart([10, 20])
    assert chart.path == "M8 64.7 L312 17.5"
    assert chart.dots == ((8, 64.7), (312, 17.5))
    assert chart.y_max == pytest.approx(22.0)


def test_single_point_is_centred() -> None:
    assert line_chart([5]).path == "M160 17.5"


def test_gaps_lift_the_pen() -> None:
    assert line_chart([1, None, 1]).path.count("M") == 2


def test_all_none_and_empty_give_empty_path() -> None:
    assert line_chart([None, None]).path == ""
    assert line_chart([]).path == ""


def test_all_zero_does_not_divide_by_zero() -> None:
    chart = line_chart([0, 0])
    assert chart.path == "M8 112 L312 112"
    assert chart.y_max == 1.0


def test_target_extends_the_scale_and_gets_a_y() -> None:
    chart = line_chart([20], target=25)
    assert chart.y_max == pytest.approx(27.5)
    assert chart.target_y == 17.5


@pytest.mark.parametrize(
    ("used", "cap", "expected"),
    [(2, 3, 2 / 3), (5, 3, 1.0), (0, 3, 0.0), (1, 0, 1.0), (-1, 3, 0.0)],
)
def test_meter_fraction_is_clamped(used: int, cap: int, expected: float) -> None:
    assert meter_fraction(used, cap) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("value", "label"),
    [(3.0, "B1"), (3.2, "B1"), (3.4, "B1+"), (4.5, "B2+"), (5.0, "C1"), (0.2, "A1"), (9, "C2")],
)
def test_cefr_label_rounds_to_half_steps(value: float, label: str) -> None:
    assert cefr_label(value) == label
