from app.core.grid_runtime import GridRuntime


def test_start_with_empty_levels_no_crash() -> None:
    rt = GridRuntime()
    state, reason = rt.validate_inputs(levels=[], market={}, balances={}, filters={})
    assert state == "IDLE"
    assert reason == "EMPTY_LEVELS"


def test_stop_from_idle_no_crash() -> None:
    rt = GridRuntime()
    _, reason = rt.stop()
    assert reason == "ALREADY_STOPPED"


def test_double_start_no_duplicate_orders() -> None:
    rt = GridRuntime(live_enabled=True, user_confirmed=True)
    rt.start_live()
    rt.start_live()
    rt.registry.add(order_id=1, level_id=2, side="BUY", price=1, qty=1, client_order_id="UBGRID_a")
    assert rt.can_place_level(2, "BUY") is False


def test_double_stop_no_crash() -> None:
    rt = GridRuntime()
    rt.stop()
    _, reason = rt.stop()
    assert reason == "ALREADY_STOPPED"
