from __future__ import annotations

import polars as pl

from src.ml.disruption_warning.labels import drop_episodes, label_drops
from src.ml.port_forecast.baselines import weekly_grid
from tests.ml.test_port_forecast import _week, _weekly


def _labels(calls, n: int = 30, port: str = "p1", partial=None) -> pl.DataFrame:
    return label_drops(weekly_grid(_weekly(n, calls, port, partial)))


def _at(labels: pl.DataFrame, i: int) -> dict:
    return labels.filter(pl.col("week_start") == _week(i)).row(0, named=True)


def test_a_real_collapse_is_a_drop() -> None:
    lab = _labels(lambda i: 5 if i in (20, 21) else 50 + (i % 3))
    week = _at(lab, 20)
    assert week["eligible"] and week["is_drop"]
    assert week["base"] == 51  # median of weeks 7-19
    assert week["drop_pct"] > 0.9
    assert _at(lab, 19)["is_drop"] is False


def test_count_noise_is_not_a_drop() -> None:
    # 20 a week, then 13: 35% down but under 2.5 spreads (sqrt(20) = 4.5).
    lab = _labels(lambda i: 13 if i == 20 else 20)
    week = _at(lab, 20)
    assert week["drop_pct"] > 0.3 and week["z"] > -2.5
    assert week["is_drop"] is False


def test_a_jumpy_port_needs_a_bigger_fall() -> None:
    # Swings 30 <-> 70 every week: IQR 40 (~30 as a spread), so even a 15 is only
    # about -1.5 spreads below the median.
    lab = _labels(lambda i: 15 if i == 20 else (30 if i % 2 else 70))
    assert _at(lab, 20)["is_drop"] is False


def test_small_ports_and_short_history_are_not_labelled() -> None:
    small = _labels(lambda i: 0 if i == 20 else 6)
    assert _at(small, 20)["eligible"] is False and _at(small, 20)["is_drop"] is None
    young = _labels(lambda i: 50)
    assert _at(young, 9)["eligible"] is False  # only 9 prior weeks
    assert _at(young, 10)["eligible"] is True


def test_partial_weeks_are_unknown_not_drops() -> None:
    lab = _labels(lambda i: 50, partial={20})
    assert _at(lab, 20)["eligible"] is False


def test_episodes_group_consecutive_drop_weeks() -> None:
    lab = pl.concat(
        [
            _labels(lambda i: 2 if 20 <= i <= 22 else 50, port="p1"),
            _labels(lambda i: 3 if i == 25 else 60, port="p2"),
        ]
    )
    eps = drop_episodes(lab)
    assert eps.select("port_id", "start", "weeks").rows() == [
        ("p1", _week(20), 3),
        ("p2", _week(25), 1),
    ]


def test_a_drop_that_repeats_a_year_later_is_seasonal() -> None:
    # Weeks 20 and 73 (52 + 1 later, a moved holiday) both collapse.
    lab = _labels(lambda i: 5 if i in (20, 73) else 50, n=80)
    first, again = _at(lab, 20), _at(lab, 73)
    # Week 20 has no labelled year before it: can't tell, so null.
    assert first["is_drop"] and first["is_disruption"] is None
    assert _at(lab, 76)["is_disruption"] is False  # a year of history by now
    assert again["is_drop"] and again["is_seasonal"] and not again["is_disruption"]
    other = _labels(lambda i: 5 if i == 70 else 50, n=80)
    assert _at(other, 70)["is_disruption"] and not _at(other, 70)["is_seasonal"]
