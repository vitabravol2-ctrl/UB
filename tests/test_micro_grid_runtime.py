from app.core.micro_grid_config import MicroGridSettings, MicroGridSettingsStore
from app.core.micro_grid_runtime import MicroGridRuntime


class DummyAdapter:
    def __init__(self):
        self.i = 1

    def generate_client_order_id(self):
        return "UBGRID_X"

    def place_limit_buy(self, price, qty, cid):
        self.i += 1
        return {"orderId": self.i}

    def place_limit_sell(self, price, qty, cid):
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


def test_lifecycle():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.calculate(80500, 81000, 2, 1000, 0.00001, 0.01)
    rt.build_levels(80500, 2, 1000, 0.01, 0.00001, 0.00001, 5.0)
    rt.start(2, live_enabled=True, dry_run=False, test_order_limit=3)
    rt.poll()
    rt.poll()
    assert rt.closed_cycles > 0


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


def test_live_limit_and_prefix():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.calculate(80500, 81000, 10, 30000, 0.00001, 0.01)
    rt.build_levels(80500, 10, 30000, 0.01, 0.00001, 0.00001, 5.0)
    rt.start(100, live_enabled=True, dry_run=False, test_order_limit=3)
    placed = len([x for x in rt.levels if x.state == "BUY_PLACED"])
    assert placed <= 3
    assert DummyAdapter().generate_client_order_id().startswith("UBGRID_")
