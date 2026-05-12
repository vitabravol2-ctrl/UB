from app.core.grid_order_registry import GridOrderRegistry
from app.core.config import SettingsData
from app.core.grid_runtime import GridRuntime
from app.core.grid_trade_adapter import GridTradeAdapter


def test_registry_lifecycle() -> None:
    reg = GridOrderRegistry()
    reg.add(order_id=1, level_id=3, side="BUY", price=10.0, qty=1.0, client_order_id="UBGRID_x")
    reg.mark_filled(1)
    reg.add(order_id=2, level_id=3, side="SELL", price=11.0, qty=1.0, client_order_id="UBGRID_y")
    reg.mark_filled(2)
    assert reg.list_by_level(3)[-1]["status"] == "FILLED"


def test_runtime_duplicate_block() -> None:
    rt = GridRuntime()
    rt.registry.add(order_id=7, level_id=1, side="BUY", price=10, qty=1, client_order_id="UBGRID_z")
    assert rt.can_place_level(1, "BUY") is False


def test_ubgrid_client_id_generated() -> None:
    adapter = GridTradeAdapter()
    assert adapter.generate_client_order_id().startswith("UBGRID_")


def test_micro_grid_levels_and_budget_split() -> None:
    rt = GridRuntime()
    s = SettingsData(
        micro_grid_enabled=True,
        micro_grid_size_ticks=400,
        micro_grid_step_ticks=10,
        micro_grid_budget_u=5000.0,
        max_exposure_u=4000.0,
        max_live_exposure_u=3000.0,
    )
    levels = rt.configure_micro_grid(
        bid=80000.0,
        tick_size=1.0,
        step_size=0.00001,
        min_qty=0.00001,
        min_notional=5.0,
        settings=s,
    )
    assert len(levels) == 40
    assert levels[0].target_buy_price == 79990.0
    assert levels[-1].target_buy_price == 79600.0
    assert levels[0].budget_u == 75.0


def test_micro_grid_level_lifecycle_and_telemetry() -> None:
    rt = GridRuntime()
    s = SettingsData(micro_grid_enabled=True, micro_grid_size_ticks=20, micro_grid_step_ticks=10, micro_grid_budget_u=100.0, max_exposure_u=200.0, max_live_exposure_u=120.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_placed(1, 1001)
    rt.mark_buy_filled(1)
    t = rt.grid_telemetry()
    assert t["GRID FILLED LEVELS"] == 1
    assert t["GRID BUDGET USED"] == 60.0
    rt.recycle_level(1)
    t2 = rt.grid_telemetry()
    assert t2["GRID FILLED LEVELS"] == 0
