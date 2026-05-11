import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests import HTTPError

BINANCE_BASE_URL = "https://api.binance.com"
DEBUG_API_LOGS = False


@dataclass
class APIStatus:
    status: str
    message: str = ""




class BinanceAPIError(HTTPError):
    def __init__(self, message: str, response: requests.Response, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message, response=response)
        self.status_code = response.status_code
        self.response_text = response.text
        self.payload = payload or {}
        self.binance_code = self.payload.get("code") if isinstance(self.payload, dict) else None
        self.binance_msg = self.payload.get("msg") if isinstance(self.payload, dict) else None


class BinanceAccountClient:
    def __init__(self) -> None:
        self.api_key = ""
        self.api_secret = ""
        self.time_offset_ms = 0
        self.session = requests.Session()
        self.debug_api_logs = DEBUG_API_LOGS
        self._last_debug_log_ms = 0

    @staticmethod
    def _mask_key(key: str) -> str:
        if len(key) <= 8:
            return "****"
        return f"{key[:4]}...{key[-4:]}"

    def load_api_keys(self) -> tuple[str, str]:
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("BINANCE_API_KEY=") and not os.getenv("BINANCE_API_KEY"):
                    os.environ["BINANCE_API_KEY"] = line.split("=", 1)[1].strip()
                if line.startswith("BINANCE_API_SECRET=") and not os.getenv("BINANCE_API_SECRET"):
                    os.environ["BINANCE_API_SECRET"] = line.split("=", 1)[1].strip()
        self.api_key = os.getenv("BINANCE_API_KEY", "").strip()
        self.api_secret = os.getenv("BINANCE_API_SECRET", "").strip()
        return self.api_key, self.api_secret

    def save_api_keys(self, api_key: str, api_secret: str) -> None:
        Path(".env").write_text(
            f"BINANCE_API_KEY={api_key.strip()}\nBINANCE_API_SECRET={api_secret.strip()}\n",
            encoding="utf-8",
        )
        self.api_key = api_key.strip()
        self.api_secret = api_secret.strip()

    def sign_params(self, query_string: str, secret: str) -> str:
        return hmac.new(secret.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).hexdigest()

    def _build_signed_query(self, params: dict[str, Any] | None = None) -> tuple[str, str]:
        payload: dict[str, Any] = dict(params or {})
        payload["recvWindow"] = 5000
        payload["timestamp"] = int(time.time() * 1000) + self.time_offset_ms
        query_string = urlencode(payload, doseq=True)
        signature = self.sign_params(query_string, self.api_secret)
        return query_string, signature

    def _signed_request(self, method: str, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        if not self.api_key or not self.api_secret:
            raise ValueError("API NOT SET")
        query_string, signature = self._build_signed_query(params)
        now_ms = int(time.time() * 1000)
        if self.debug_api_logs and now_ms - self._last_debug_log_ms >= 5000:
            print(f"[API] signed {method.upper()} {path} params={query_string}")
            self._last_debug_log_ms = now_ms
        final_url = f"{BINANCE_BASE_URL}{path}?{query_string}&signature={signature}"
        headers = {"X-MBX-APIKEY": self.api_key}
        if method.upper() == "GET":
            return self.session.get(final_url, headers=headers, timeout=6)
        if method.upper() == "POST":
            return self.session.post(final_url, headers=headers, timeout=6)
        if method.upper() == "DELETE":
            return self.session.delete(final_url, headers=headers, timeout=6)
        raise ValueError(f"Unsupported method: {method}")

    def sync_time(self) -> int:
        local_ms = int(time.time() * 1000)
        resp = self.session.get(f"{BINANCE_BASE_URL}/api/v3/time", timeout=5)
        resp.raise_for_status()
        server_ms = int(resp.json()["serverTime"])
        self.time_offset_ms = server_ms - local_ms
        return self.time_offset_ms

    @staticmethod
    def _parse_json_body(resp: requests.Response) -> dict[str, Any] | list[dict[str, Any]] | None:
        try:
            return resp.json()
        except ValueError:
            return None

    def _raise_binance_error(self, resp: requests.Response, data: dict[str, Any] | list[dict[str, Any]] | None) -> None:
        payload = data if isinstance(data, dict) else {}
        code = payload.get("code")
        msg = payload.get("msg")
        raise BinanceAPIError(
            f"Binance request failed status={resp.status_code} code={code} msg={msg} body={resp.text}",
            response=resp,
            payload=payload,
        )

    def signed_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | list[dict[str, Any]]:
        resp = self._signed_request("GET", path, params)
        if resp.status_code in {401, 403}:
            raise RuntimeError("INVALID API KEY")
        data = self._parse_json_body(resp)
        if isinstance(data, dict) and data.get("code") == -1021:
            raise RuntimeError("TIME SYNC ERROR")
        if isinstance(data, dict) and data.get("code") in {-2015, -2014}:
            raise RuntimeError("INVALID API KEY")
        if resp.status_code >= 400:
            self._raise_binance_error(resp, data)
        assert data is not None
        return data


    def signed_post(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self._signed_request("POST", path, params)
        data = self._parse_json_body(resp)
        if resp.status_code >= 400:
            self._raise_binance_error(resp, data)
        assert isinstance(data, dict)
        return data

    def signed_delete(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        resp = self._signed_request("DELETE", path, params)
        data = self._parse_json_body(resp)
        if resp.status_code >= 400:
            self._raise_binance_error(resp, data)
        assert isinstance(data, dict)
        return data

    def place_limit_order(self, symbol: str, side: str, price: float, qty: float) -> dict[str, Any]:
        price_decimal = Decimal(str(price)).normalize()
        qty_decimal = Decimal(str(qty)).normalize()
        return self.signed_post(
            "/api/v3/order",
            {
                "symbol": symbol,
                "side": side,
                "type": "LIMIT",
                "timeInForce": "GTC",
                "quantity": format(qty_decimal, "f"),
                "price": format(price_decimal, "f"),
            },
        )

    def cancel_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        return self.signed_delete("/api/v3/order", {"symbol": symbol, "orderId": order_id})

    def get_order(self, symbol: str, order_id: int) -> dict[str, Any]:
        data = self.signed_get("/api/v3/order", {"symbol": symbol, "orderId": order_id})
        assert isinstance(data, dict)
        return data

    def get_all_orders(self, symbol: str, limit: int = 20) -> list[dict[str, Any]]:
        data = self.signed_get("/api/v3/allOrders", {"symbol": symbol, "limit": limit})
        assert isinstance(data, list)
        return data

    def test_account_connection(self) -> APIStatus:
        try:
            self.load_api_keys()
            if not self.api_key or not self.api_secret:
                return APIStatus("NOT SET", "API key/secret missing")
            self.sync_time()
            self.signed_get("/api/v3/account")
            return APIStatus("OK", "connected")
        except requests.RequestException:
            return APIStatus("ERROR", "NETWORK ERROR")
        except RuntimeError as exc:
            return APIStatus("ERROR", str(exc))
        except Exception as exc:
            return APIStatus("ERROR", f"API ERROR: {exc}")

    def get_account_balances(self) -> dict[str, dict[str, float]]:
        data = self.signed_get("/api/v3/account")
        assert isinstance(data, dict)
        out: dict[str, dict[str, float]] = {"BTC": {"free": 0.0, "locked": 0.0}, "U": {"free": 0.0, "locked": 0.0}}
        for row in data.get("balances", []):
            if row.get("asset") in out:
                out[row["asset"]] = {"free": float(row.get("free", 0)), "locked": float(row.get("locked", 0))}
        return out

    def get_open_orders(self, symbol: str) -> list[dict[str, Any]]:
        data = self.signed_get("/api/v3/openOrders", {"symbol": symbol})
        assert isinstance(data, list)
        return data

    def get_open_orders_safe(self, symbol: str) -> tuple[list[dict[str, Any]], bool]:
        try:
            return self.get_open_orders(symbol), False
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 400:
                return [], True
            raise

    def get_exchange_filters(self, symbol: str) -> dict[str, float | bool]:
        resp = self.session.get(f"{BINANCE_BASE_URL}/api/v3/exchangeInfo", params={"symbol": symbol}, timeout=5)
        resp.raise_for_status()
        payload = resp.json()
        symbols = payload.get("symbols", [])
        if not symbols:
            return {"loaded": True, "fallback": True, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
        filters = symbols[0].get("filters", [])
        out = {"loaded": True, "fallback": False, "tickSize": 0.0, "stepSize": 0.0, "minQty": 0.0, "minNotional": 0.0}
        for flt in filters:
            if flt.get("filterType") == "PRICE_FILTER":
                out["tickSize"] = float(flt.get("tickSize", 0))
            elif flt.get("filterType") == "LOT_SIZE":
                out["stepSize"] = float(flt.get("stepSize", 0))
                out["minQty"] = float(flt.get("minQty", 0))
            elif flt.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}:
                out["minNotional"] = float(flt.get("minNotional", flt.get("notional", 0)))
        if out["tickSize"] <= 0 or out["stepSize"] <= 0 or out["minQty"] <= 0 or out["minNotional"] <= 0:
            return {"loaded": True, "fallback": True, "tickSize": 0.01, "stepSize": 0.00001, "minQty": 0.00001, "minNotional": 5.0}
        return out


def round_price_to_tick(price: float, tick_size: float) -> float:
    return float((Decimal(str(price)) / Decimal(str(tick_size))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(tick_size)))


def round_qty_to_step(qty: float, step_size: float) -> float:
    return float((Decimal(str(qty)) / Decimal(str(step_size))).to_integral_value(rounding=ROUND_DOWN) * Decimal(str(step_size)))


def validate_min_notional(price: float, qty: float, min_notional: float) -> bool:
    return price * qty >= min_notional


def validate_min_qty(qty: float, min_qty: float) -> bool:
    return qty >= min_qty
