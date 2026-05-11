from app.core.config import SettingsData
from app.core.market_state import MarketState
from app.core.trade_math import TradeMathEngine


def make_state(bid: float | None, ask: float | None) -> MarketState:
    state = MarketState()
    state.snapshot.bid = bid
    state.snapshot.ask = ask
    return state


def base_filters() -> dict:
    return {"loaded": True, "fallback": False, "tickSize": 1.0, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}


def base_balances() -> dict:
    return {"U": {"free": 1000.0, "locked": 0.0}, "BTC": {"free": 0.0, "locked": 0.0}}


def test_order_size_u_to_qty_btc() -> None:
    settings = SettingsData(entry_offset=1.0, exit_offset=1.0, order_size_u=20.0)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert abs(plan.qty_btc - 0.00024) < 0.00002


def test_required_u_not_above_order_size_u() -> None:
    settings = SettingsData(order_size_u=20.0)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert (plan.required_u or 0) <= 20.0


def test_profit_uses_qty_btc() -> None:
    settings = SettingsData(entry_offset=1.0, exit_offset=1.0, order_size_u=20.0)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert abs((plan.expected_profit_u or 0) - (plan.capture_per_btc * plan.qty_btc)) < 1e-9


def test_order_size_u_not_interpreted_as_btc() -> None:
    settings = SettingsData(order_size_u=20.0)
    balances = base_balances()
    balances["U"]["free"] = 50.0
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), balances, api_status="OK")
    assert plan.status in {"READY", "HOT"}
    assert (plan.required_u or 0) < 50.0
