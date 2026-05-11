from app.core.grid_risk_guard import GridRiskGuard


def base_kwargs() -> dict:
    return dict(live_enabled=True, user_confirmed=True, api_ok=True, filters_ok=True, balance_u=100.0, qty=0.01, min_qty=0.001, notional=10.0, min_notional=5.0, exposure_u=10.0, max_exposure_u=20.0, active_orders_count=1, levels=5, price=100.0, lower_price=90.0, upper_price=110.0, market_stale=False, duplicate_level_order=False)


def test_duplicate_level_blocked() -> None:
    g = GridRiskGuard()
    k = base_kwargs(); k["duplicate_level_order"] = True
    assert g.validate(**k).reason == "RISK_DUPLICATE_ORDER"


def test_max_exposure_blocked() -> None:
    g = GridRiskGuard()
    k = base_kwargs(); k["exposure_u"] = 30.0
    assert g.validate(**k).reason == "RISK_MAX_EXPOSURE"


def test_live_disabled_blocked() -> None:
    g = GridRiskGuard()
    k = base_kwargs(); k["live_enabled"] = False
    assert g.validate(**k).reason == "RISK_LIVE_DISABLED"
