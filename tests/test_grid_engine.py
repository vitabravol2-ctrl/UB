import pytest

from app.core.grid_engine import GridEngine


def test_levels_count() -> None:
    engine = GridEngine()
    rows = engine.calculate_levels(100.0, 90.0, 1000.0, 6, 5)
    assert len(rows) == 6


def test_upper_lower_validation() -> None:
    engine = GridEngine()
    with pytest.raises(ValueError):
        engine.calculate_levels(90.0, 100.0, 1000.0, 6, 5)


def test_budget_split() -> None:
    engine = GridEngine()
    rows = engine.calculate_levels(100.0, 90.0, 1200.0, 6, 5)
    assert rows[0].order_u == pytest.approx(200.0)


def test_target_sell_and_expected_pnl() -> None:
    engine = GridEngine(tick_size=0.5)
    rows = engine.calculate_levels(100.0, 90.0, 1000.0, 2, 4)
    row = rows[0]
    assert row.target_sell == pytest.approx(row.price + 2.0)
    assert row.expected_pnl == pytest.approx((row.target_sell - row.price) * row.qty_btc)


def test_min_notional_invalid_level() -> None:
    engine = GridEngine(tick_size=0.01, step_size=0.0001, min_qty=0.0001, min_notional=10000.0)
    rows = engine.calculate_levels(100.0, 90.0, 100.0, 3, 2)
    assert any((not r.valid and r.reason == "INVALID_MIN_NOTIONAL") for r in rows)


def test_qty_rounding_by_step_size() -> None:
    engine = GridEngine(step_size=0.01)
    rows = engine.calculate_levels(101.0, 100.0, 100.0, 2, 1)
    assert rows[0].qty_btc == pytest.approx(0.49)


def test_price_rounding_by_tick_size() -> None:
    engine = GridEngine(tick_size=0.5)
    rows = engine.calculate_levels(100.9, 99.9, 100.0, 2, 1)
    assert rows[0].price == pytest.approx(100.5)
