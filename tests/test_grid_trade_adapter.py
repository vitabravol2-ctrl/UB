from app.core.grid_trade_adapter import GridTradeAdapter


class StubAccount:
    def __init__(self):
        self.orders = []
        self.cancelled = []

    def test_account_connection(self):
        class S:
            status = "NOT SET"
            message = "API key/secret missing"
        return S()

    def get_open_orders(self, symbol):
        return [
            {"orderId": 1, "clientOrderId": "UBGRID_B_1_123"},
            {"orderId": 2, "clientOrderId": "OTHER_1"},
        ]

    def cancel_order(self, symbol, order_id):
        self.cancelled.append(order_id)


def test_api_not_set_no_crash():
    adapter = GridTradeAdapter(live_enabled=True)
    adapter.account = StubAccount()
    ok, status, reason = adapter.check_api_ready()
    assert not ok
    assert status == "NOT SET"
    assert reason == "missing_keys"


def test_client_order_id_prefix():
    adapter = GridTradeAdapter(live_enabled=True)
    cid = adapter.generate_client_order_id(side="B", level=4)
    assert cid.startswith("UBGRID_B_4_")


def test_cancel_all_only_ubgrid():
    adapter = GridTradeAdapter(live_enabled=True)
    stub = StubAccount()
    adapter.account = stub
    adapter.cancel_grid_orders()
    assert stub.cancelled == [1]


def test_open_orders_filter_ubgrid():
    adapter = GridTradeAdapter(live_enabled=True)
    adapter.account = StubAccount()
    orders = adapter.get_grid_open_orders()
    assert len(orders) == 1
    assert orders[0]["clientOrderId"].startswith("UBGRID_")
