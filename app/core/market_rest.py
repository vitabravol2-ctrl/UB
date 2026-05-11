import time
import requests


class MarketREST:
    BASE_URL = "https://api.binance.com/api/v3/ticker/bookTicker"

    def fetch_book_ticker(self, symbol: str) -> tuple[float, float, int]:
        resp = requests.get(self.BASE_URL, params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        payload = resp.json()
        bid = float(payload["bidPrice"])
        ask = float(payload["askPrice"])
        return bid, ask, int(time.time() * 1000)
