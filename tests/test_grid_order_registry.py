from app.core.grid_order_registry import GridOrderRegistry


def test_registry_add_remove_list() -> None:
    reg = GridOrderRegistry()
    reg.add(order_id=10, level_id=2, side="BUY", price=80000.0, qty=0.01)
    assert len(reg.list_active()) == 1
    assert reg.find_by_level(2)[0]["order_id"] == 10
    reg.remove(10)
    assert reg.list_active() == []
