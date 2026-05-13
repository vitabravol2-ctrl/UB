from app.core.micro_grid_config import MicroGridSettings, MicroGridSettingsStore
from app.core.micro_grid_runtime import MicroGridRuntime


class DummyAdapter:
    def __init__(self):
        self.i = 1
        self.buy_calls = []
        self.sell_calls = []

    def generate_client_order_id(self):
        return "UBGRID_X"

    def place_limit_buy(self, price, qty, cid):
        self.buy_calls.append((price, qty, cid))
        self.i += 1
        return {"orderId": self.i}

    def place_limit_sell(self, price, qty, cid):
        self.sell_calls.append((price, qty, cid))
        self.i += 1
        return {"orderId": self.i}

    def get_order_status(self, order_id):
        return {"status": "FILLED"}


def test_grid_calculation():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    step, step_ticks, budget, qty = rt.calculate(80500, 81000, 100, 30000, 0.00001, 0.01)
    assert step == 5
    assert step_ticks == 500
    assert budget == 300
    assert qty > 0


def test_qty_rounding():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    assert rt._round_down(0.003719, 0.00001) == 0.00371


def test_active_window_and_test_limit():
    adapter = DummyAdapter()
    rt = MicroGridRuntime(adapter, lambda _: None)
    rt.calculate(80500, 81000, 20, 30000, 0.00001, 0.01)
    rt.build_levels(80500, 20, 30000, 0.01, 0.00001, 0.00001, 5.0)
    rt.start(25, live_enabled=True, dry_run=False, test_order_limit=3, current_bid=80700, active_buy_window_levels=10, max_live_buy_orders=5)
    assert len(adapter.buy_calls) <= 3
    assert all(c[2].startswith("UBGRID_") for c in adapter.buy_calls)


def test_lifecycle_linked_sell_and_recycle():
    logs = []
    adapter = DummyAdapter()
    rt = MicroGridRuntime(adapter, logs.append)
    rt.calculate(80500, 81000, 3, 1000, 0.00001, 0.01)
    rt.build_levels(80500, 3, 1000, 0.01, 0.00001, 0.00001, 5.0)
    rt.start(3, live_enabled=True, dry_run=False, test_order_limit=3, current_bid=81000, active_buy_window_levels=50, max_live_buy_orders=3)
    rt.poll()
    assert any("GRID_BUY_FILLED" in x for x in logs)
    assert any("GRID_SELL_PLACED" in x for x in logs)
    rt.poll()
    assert rt.closed_cycles > 0
    assert rt.realized_pnl > 0
    assert any("GRID_RECYCLED" in x for x in logs)


def test_config_load_save(tmp_path):
    store = MicroGridSettingsStore(str(tmp_path / "micro.json"))
    s = MicroGridSettings(grid_count=33)
    store.save(s)
    loaded = store.load()
    assert loaded.grid_count == 33


def test_min_notional_skip():
    logs = []
    rt = MicroGridRuntime(DummyAdapter(), logs.append)
    rt.calculate(80500, 81000, 2, 5, 0.00001, 0.01)
    rt.build_levels(80500, 2, 5, 0.01, 0.00001, 0.00001, 1000.0)
    assert any("GRID_LEVEL_SKIP" in x and "MIN_NOTIONAL" in x for x in logs)


def test_gui_important_filter_does_not_break_runtime():
    important = []

    def gui_log(msg: str):
        if msg.startswith(("GRID_MODE_START", "GRID_BUY_PLACED", "GRID_BUY_FILLED", "GRID_SELL_PLACED", "GRID_SELL_FILLED", "GRID_PNL", "GRID_CANCEL_ALL", "GRID_MODE_STOP", "GRID_ERROR")):
            important.append(msg)

    rt = MicroGridRuntime(DummyAdapter(), gui_log)
    rt.calculate(80500, 81000, 2, 1000, 0.00001, 0.01)
    rt.build_levels(80500, 2, 1000, 0.01, 0.00001, 0.00001, 5.0)
    rt.start(2, live_enabled=True, dry_run=False, test_order_limit=2, current_bid=81000, active_buy_window_levels=50, max_live_buy_orders=2)
    rt.poll()
    assert any("GRID_BUY_PLACED" in x for x in important)
