from app.core.grid_order_registry import GridOrderRegistry
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
