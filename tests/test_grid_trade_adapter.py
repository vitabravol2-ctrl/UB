from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.grid_engine import GridEngine


def test_live_locked_blocks_place_limit_buy() -> None:
    adapter = GridTradeAdapter(live_enabled=False)
    res = adapter.place_limit_buy(100.0, 0.01)
    assert res["status"] == "LIVE_LOCKED"


def test_live_locked_blocks_place_limit_sell() -> None:
    adapter = GridTradeAdapter(live_enabled=False)
    res = adapter.place_limit_sell(100.0, 0.01)
    assert res["status"] == "LIVE_LOCKED"


def test_filters_do_not_break_grid_calculation() -> None:
    engine = GridEngine()
    engine.set_filters(tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    rows = engine.calculate_levels(82000.0, 81000.0, 2000.0, 20, 5)
    assert len(rows) == 20


def test_invalid_min_notional_detected() -> None:
    engine = GridEngine(min_notional=999999.0)
    rows = engine.calculate_levels(100.0, 99.0, 10.0, 2, 1)
    assert rows[0].reason == "INVALID_MIN_NOTIONAL"
