from app.core.micro_grid_config import MicroGridSettings, MicroGridSettingsStore
from app.core.micro_grid_runtime import MicroGridRuntime


class DummyAdapter:
    def __init__(self):
        self.i = 100
        self.buy_calls = []
        self.sell_calls = []

    def generate_client_order_id(self, side="X", level=0):
        return f"UBGRID_{side}_{level}_123"

    def place_limit_buy(self, price, qty, cid):
        self.buy_calls.append((price, qty, cid))
        self.i += 1
        return {"orderId": self.i}

    def place_limit_sell(self, price, qty, cid):
        self.sell_calls.append((price, qty, cid))
        self.i += 1
        return {"orderId": self.i}

    def get_order_status(self, _):
        return {"status": "FILLED"}


def test_two_sided_level_generation_25_25():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_two_sided_levels(50000.0, 5.0, 25, 25, 5000.0, 5000.0, 0.01, 0.00001, 0.00001, 5.0)
    assert len(rt.buy_levels) == 25
    assert len(rt.sell_levels) == 25
    assert all(x.entry_price < 50000.0 for x in rt.buy_levels)
    assert all(x.entry_price > 50000.0 for x in rt.sell_levels)


def test_budget_split():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_two_sided_levels(50000.0, 5.0, 25, 25, 5000.0, 5000.0, 0.01, 0.00001, 0.00001, 5.0)
    assert abs(sum([lv.entry_price * lv.qty for lv in rt.buy_levels]) - 5000.0) < 50


def test_qty_rounding_step_and_notional_valid():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_two_sided_levels(50000.0, 5.0, 2, 2, 500.0, 500.0, 0.01, 0.0001, 0.0001, 5.0)
    for lv in rt.levels:
        assert abs((lv.qty / 0.0001) - round(lv.qty / 0.0001)) < 1e-6
        assert lv.entry_price * lv.qty >= 5.0


def test_balance_check_insufficient_btc():
    logs = []
    rt = MicroGridRuntime(DummyAdapter(), logs.append)
    rt.build_two_sided_levels(50000.0, 5.0, 3, 3, 500.0, 500.0, 0.01, 0.00001, 0.00001, 5.0)
    rt.check_balances(free_u=1000.0, free_btc=0.0)
    assert any("GRID_BALANCE_INSUFFICIENT" in x for x in logs)


def test_test_order_limit_zero_means_unlimited_by_test_limit():
    ad = DummyAdapter()
    rt = MicroGridRuntime(ad, lambda _: None)
    rt.build_two_sided_levels(50000.0, 5.0, 5, 5, 500.0, 500.0, 0.01, 0.00001, 0.00001, 5.0)
    rt.available_sell_levels = 5
    rt.start(5, 5, True, False, test_order_limit=0)
    assert len(ad.buy_calls) == 5
    assert len(ad.sell_calls) == 5


def test_buy_fill_creates_linked_sell_and_sell_fill_creates_linked_buy():
    ad = DummyAdapter()
    rt = MicroGridRuntime(ad, lambda _: None)
    rt.build_two_sided_levels(50000.0, 5.0, 1, 1, 100.0, 100.0, 0.01, 0.00001, 0.00001, 5.0)
    rt.available_sell_levels = 1
    rt.start(1, 1, True, False, 0)
    rt.poll()
    assert len(ad.buy_calls) >= 2
    assert len(ad.sell_calls) >= 2


def test_config_load_save(tmp_path):
    store = MicroGridSettingsStore(str(tmp_path / "micro.json"))
    s = MicroGridSettings(buy_levels_down=33)
    store.save(s)
    loaded = store.load()
    assert loaded.buy_levels_down == 33
