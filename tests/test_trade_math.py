from app.core.config import SettingsData
from app.core.market_state import MarketState
from app.core.trade_math import TradeMathEngine


def make_state(bid: float | None, ask: float | None) -> MarketState:
    state = MarketState()
    state.snapshot.bid = bid
    state.snapshot.ask = ask
    return state


def base_filters() -> dict:
    return {"loaded": True, "fallback": False, "tickSize": 1.0, "stepSize": 0.001, "minQty": 0.001, "minNotional": 5.0}


def base_balances() -> dict:
    return {"U": {"free": 1000.0, "locked": 0.0}, "BTC": {"free": 0.0, "locked": 0.0}}


def test_entry_exit_capture() -> None:
    settings = SettingsData(entry_offset=1.0, exit_offset=1.0, lot_size=0.001)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert plan.entry_price == 80001
    assert plan.exit_price == 80006
    assert plan.capture_per_btc == 5


def test_expected_profit() -> None:
    settings = SettingsData(entry_offset=1.0, exit_offset=1.0, lot_size=0.001)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert plan.expected_profit_u == 0.005


def test_spread_too_small() -> None:
    settings = SettingsData(min_spread=10.0)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert plan.status == "SPREAD_TOO_SMALL"


def test_capture_too_small() -> None:
    settings = SettingsData(target_capture=8.0)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert plan.status == "CAPTURE_TOO_SMALL"


def test_min_notional_fail() -> None:
    settings = SettingsData(lot_size=0.001)
    filters = base_filters()
    filters["minNotional"] = 200.0
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, filters, base_balances(), api_status="OK")
    assert plan.status == "FILTER_FAIL"
    assert "Notional" in plan.reason


def test_balance_low() -> None:
    settings = SettingsData(lot_size=0.01)
    balances = base_balances()
    balances["U"]["free"] = 100.0
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), balances, api_status="OK")
    assert plan.status == "BALANCE_LOW"


def test_hot_classification() -> None:
    settings = SettingsData(target_capture=3.0)
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, base_filters(), base_balances(), api_status="OK")
    assert plan.status == "HOT"


def test_ticksize_float_bug_passes_for_decimal_values() -> None:
    settings = SettingsData(min_spread=0.01, target_capture=0.01, entry_offset=0.01, exit_offset=0.01, lot_size=0.001)
    filters = {"loaded": True, "fallback": False, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
    plan = TradeMathEngine().build_plan(make_state(80808.23, 80808.30), settings, filters, base_balances(), api_status="OK")
    assert plan.status in {"READY", "HOT"}
    assert plan.entry_price == 80808.24


def test_entry_exit_round_to_tick_001() -> None:
    settings = SettingsData(min_spread=0.01, target_capture=0.01, entry_offset=0.017, exit_offset=0.013, lot_size=0.00123)
    filters = {"loaded": True, "fallback": False, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
    plan = TradeMathEngine().build_plan(make_state(80808.23, 80808.30), settings, filters, base_balances(), api_status="OK")
    assert plan.entry_price == 80808.24
    assert plan.exit_price == 80808.28


def test_lot_rounds_to_step() -> None:
    settings = SettingsData(lot_size=0.001234567)
    filters = {"loaded": True, "fallback": False, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
    plan = TradeMathEngine().build_plan(make_state(80000, 80007), settings, filters, base_balances(), api_status="OK")
    assert plan.lot_size == 0.00123


def test_balance_ok_for_large_u_and_small_lot() -> None:
    settings = SettingsData(min_spread=0.01, target_capture=0.01, entry_offset=0.01, exit_offset=0.01, lot_size=0.001)
    filters = {"loaded": True, "fallback": False, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
    balances = base_balances()
    balances["U"]["free"] = 38000.0
    plan = TradeMathEngine().build_plan(make_state(80808.23, 80808.30), settings, filters, balances, api_status="OK")
    assert plan.status in {"READY", "HOT"}
    assert plan.balance_ok is True


def test_required_u_is_computed_correctly() -> None:
    settings = SettingsData(min_spread=0.01, target_capture=0.01, lot_size=0.001, entry_offset=0.01)
    filters = {"loaded": True, "fallback": False, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
    plan = TradeMathEngine().build_plan(make_state(80808.23, 80808.30), settings, filters, base_balances(), api_status="OK")
    assert plan.required_u == 80.80824
