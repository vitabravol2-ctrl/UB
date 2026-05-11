from app.core.binance_account import BINANCE_BASE_URL, BinanceAccountClient


class DummyResponse:
    status_code = 200

    def json(self):
        return {}


class DummySession:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url, headers=None, timeout=None, params=None):
        self.calls.append({"method": "POST", "url": url, "headers": headers, "timeout": timeout, "params": params})
        return DummyResponse()

    def get(self, url, headers=None, timeout=None, params=None):
        self.calls.append({"method": "GET", "url": url, "headers": headers, "timeout": timeout, "params": params})
        return DummyResponse()

    def delete(self, url, headers=None, timeout=None, params=None):
        self.calls.append({"method": "DELETE", "url": url, "headers": headers, "timeout": timeout, "params": params})
        return DummyResponse()


def test_signed_query_and_signature_are_deterministic(monkeypatch) -> None:
    client = BinanceAccountClient()
    client.api_key = "k"
    client.api_secret = "s"
    client.time_offset_ms = 0

    monkeypatch.setattr("app.core.binance_account.time.time", lambda: 1700000000.0)

    query, signature = client._build_signed_query(
        {
            "symbol": "BTCU",
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": "0.00024000",
            "price": "80667.00",
        }
    )
    assert query == "symbol=BTCU&side=BUY&type=LIMIT&timeInForce=GTC&quantity=0.00024000&price=80667.00&recvWindow=5000&timestamp=1700000000000"
    assert signature == client.sign_params(query, "s")


def test_signed_post_uses_query_url_and_no_params(monkeypatch) -> None:
    client = BinanceAccountClient()
    client.api_key = "k"
    client.api_secret = "s"
    client.session = DummySession()

    monkeypatch.setattr("app.core.binance_account.time.time", lambda: 1700000000.0)

    client.signed_post("/api/v3/order", {"symbol": "BTCU", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC", "quantity": "0.00024", "price": "80667.00"})

    call = client.session.calls[0]
    assert call["method"] == "POST"
    assert call["params"] is None
    assert call["url"].startswith(f"{BINANCE_BASE_URL}/api/v3/order?")
    assert "&signature=" in call["url"]
    assert call["url"].count("&signature=") == 1


def test_signed_get_and_delete_use_same_signing_flow(monkeypatch) -> None:
    client = BinanceAccountClient()
    client.api_key = "k"
    client.api_secret = "s"
    client.session = DummySession()

    monkeypatch.setattr("app.core.binance_account.time.time", lambda: 1700000000.0)

    client.signed_get("/api/v3/account")
    client.signed_delete("/api/v3/order", {"symbol": "BTCU", "orderId": 1})

    get_call = client.session.calls[0]
    del_call = client.session.calls[1]
    assert get_call["method"] == "GET"
    assert del_call["method"] == "DELETE"
    assert get_call["params"] is None
    assert del_call["params"] is None
    assert get_call["url"].endswith("signature=" + get_call["url"].split("signature=")[1])
    assert del_call["url"].endswith("signature=" + del_call["url"].split("signature=")[1])
