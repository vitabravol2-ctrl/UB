from app.core.micro_grid_config import MicroGridSettings, MicroGridSettingsStore
from app.core.micro_grid_runtime import MicroGridRuntime


class DummyAdapter:
    def __init__(self):
        self.i = 100
        self.buy_calls = []
        self.sell_calls = []

    def generate_client_order_id(self, side="X", level=0):
        return f"UBGRID_{side}_{level}_123"

    def place_limit_buy(self, symbol, price, qty, cid):
        self.buy_calls.append((symbol, price, qty, cid))
        self.i += 1
        return {"orderId": self.i}

    def place_limit_sell(self, symbol, price, qty, cid):
        self.sell_calls.append((symbol, price, qty, cid))
        self.i += 1
        return {"orderId": self.i}


def test_range_step_is_20_for_80000_81000_50():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_range_levels(lower_price=80000.0, upper_price=81000.0, grid_count=50, total_investment_u=10000.0, tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    assert rt.step_u == 20.0


def test_no_level_near_zero():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_range_levels(lower_price=80000.0, upper_price=81000.0, grid_count=50, total_investment_u=10000.0, tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    assert all(x.buy_price > 0 for x in rt.levels)


def test_active_selection_buy_below_current():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_range_levels(lower_price=80000.0, upper_price=81000.0, grid_count=50, total_investment_u=10000.0, tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    buys, _sells = rt.select_active_levels(current_price=80500.0, max_active_orders=10, free_btc=0.0)
    assert all(x.buy_price < 80500.0 for x in buys)


def test_sell_skipped_when_no_btc():
    rt = MicroGridRuntime(DummyAdapter(), lambda _: None)
    rt.build_range_levels(lower_price=80000.0, upper_price=81000.0, grid_count=50, total_investment_u=10000.0, tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    _buys, sells = rt.select_active_levels(current_price=80500.0, max_active_orders=10, free_btc=0.0)
    assert len(sells) == 0


def test_order_limit_3():
    ad = DummyAdapter()
    rt = MicroGridRuntime(ad, lambda _: None)
    rt.build_range_levels(lower_price=80000.0, upper_price=81000.0, grid_count=50, total_investment_u=10000.0, tick_size=0.01, step_size=0.00001, min_qty=0.00001, min_notional=5.0)
    rt.select_active_levels(current_price=80500.0, max_active_orders=10, free_btc=10.0)
    rt.place_active_orders(symbol="BTCU", dry_run=False, best_bid=80500.0, best_ask=80510.0, test_order_limit=3)
    assert len(ad.buy_calls) + len(ad.sell_calls) <= 3


def test_config_load_save(tmp_path):
    store = MicroGridSettingsStore(str(tmp_path / "micro.json"))
    s = MicroGridSettings(grid_count=33)
    store.save(s)
    loaded = store.load()
    assert loaded.grid_count == 33
