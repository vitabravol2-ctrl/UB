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


def test_micro_grid_levels_and_order_size_budget() -> None:
    rt = GridRuntime()
    s = SettingsData(
        stream_count=40,
        stream_range_ticks=400,
        order_size_u=15.0,
        max_exposure_u=4000.0
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
    assert levels[0].budget_u == 15.0


def test_micro_grid_level_lifecycle_and_telemetry() -> None:
    rt = GridRuntime()
    s = SettingsData(stream_count=2, stream_range_ticks=20, max_exposure_u=200.0, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_placed(1, 1001)
    rt.mark_buy_filled(1)
    t = rt.grid_telemetry()
    assert t["GRID FILLED LEVELS"] == 1
    assert t["GRID BUDGET USED"] == 15.0
    rt.recycle_level(1)
    t2 = rt.grid_telemetry()
    assert t2["GRID FILLED LEVELS"] == 0


def test_streams_start_stagger_and_activate_independently() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=3, stream_range_ticks=30, order_size_u=15.0, stream_start_interval_ms=300)
    levels = rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    assert len(levels) == 3
    assert levels[0].state == "WAIT_BUY"
    assert levels[1].state == "WAIT_START"
    assert levels[2].state == "WAIT_START"
    assert levels[1].start_at_ms - levels[0].start_at_ms == 300
    assert levels[2].start_at_ms - levels[1].start_at_ms == 300
    assert any("STREAM_WAIT_START stream_id=2" in x for x in logs)
    assert any("STREAM_ACTIVATED stream_id=1" in x for x in logs)


def test_signal_logs_are_log_only_and_recycle_cooldown_releases() -> None:
    logs: list[str] = []
    rt = GridRuntime(log_callback=logs.append)
    s = SettingsData(stream_count=1, stream_range_ticks=10, order_size_u=15.0)
    rt.configure_micro_grid(80000.0, 1.0, 0.00001, 0.00001, 5.0, s)
    rt.mark_buy_filled(1)
    rt.recycle_level(1, recycle_delay_ms=500)
    assert rt.levels[0].state == "RECYCLE_COOLDOWN"
    rt.release_recycle_streams(now_ms=rt.levels[0].recycle_ready_at_ms + 1)
    assert rt.levels[0].state == "WAIT_BUY"
    assert any("STREAM_SIGNAL_BUY_FILL stream_id=1" in x for x in logs)
    assert any("STREAM_SIGNAL_SELL_FILL stream_id=1" in x for x in logs)
