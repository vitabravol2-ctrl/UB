import json
import threading
import time

import requests
import websocket
from PySide6.QtCore import QObject, Signal


class MarketWSSignals(QObject):
    book = Signal(float, float, int)
    status = Signal(str)
    log = Signal(str, str)


class MarketWSClient:
    EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"

    def __init__(self, stream_symbol: str, binance_symbol: str, max_ws_age_ms: int = 5000) -> None:
        self.stream_symbol = stream_symbol.lower()
        self.binance_symbol = binance_symbol.upper()
        self.exchange_symbol = self.binance_symbol
        self.signals = MarketWSSignals()
        self.max_ws_age_ms = max_ws_age_ms

        self._thread: threading.Thread | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._running = False
        self._mode = "primary"
        self._connected_at_ms: int | None = None
        self._accepted_tick_ms: int | None = None
        self._no_match_count = 0
        self._last_no_match_log_ms = 0
        self._last_live_log_ms = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._mode = "primary"
        self._connected_at_ms = None
        self._accepted_tick_ms = None
        self._no_match_count = 0
        self._last_no_match_log_ms = 0
        self._last_live_log_ms = 0
        self.exchange_symbol = self._resolve_exchange_symbol()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            self._ws.close()

    def _resolve_exchange_symbol(self) -> str:
        try:
            resp = requests.get(self.EXCHANGE_INFO_URL, params={"symbol": self.binance_symbol}, timeout=5)
            resp.raise_for_status()
            payload = resp.json()
            symbols = payload.get("symbols", [])
            if not symbols:
                raise ValueError("symbols list is empty")
            info = symbols[0]
            symbol = str(info.get("symbol", self.binance_symbol)).upper()
            base = str(info.get("baseAsset", "?"))
            quote = str(info.get("quoteAsset", "?"))
            status = str(info.get("status", "UNKNOWN"))
            permissions = info.get("permissions")
            perm_suffix = f" permissions={permissions}" if permissions is not None else ""
            self.signals.log.emit("EXCHANGE", f"symbol={symbol} base={base} quote={quote} status={status}{perm_suffix}")
            return symbol
        except Exception as exc:
            self.signals.log.emit("EXCHANGE", f"error {exc} using fallback symbol={self.binance_symbol}")
            return self.binance_symbol

    def _primary_url(self) -> str:
        stream_symbol = self.exchange_symbol.lower()
        return f"wss://stream.binance.com:9443/ws/{stream_symbol}@bookTicker"

    def _fallback_url(self) -> str:
        return "wss://stream.binance.com:9443/ws/!bookTicker"

    def _current_url(self) -> str:
        return self._primary_url() if self._mode == "primary" else self._fallback_url()

    def _run(self) -> None:
        reconnect_delay = 2
        while self._running:
            url = self._current_url()
            self.signals.status.emit("CONNECTING")
            if self._mode == "primary":
                self.signals.log.emit("WS", f"primary url={url}")
                self.signals.log.emit("WS", "connecting primary")
            else:
                self.signals.log.emit("WS", f"fallback url={url}")
                self.signals.log.emit("WS", "connecting fallback")

            def on_message(_ws: websocket.WebSocketApp, message: str) -> None:
                try:
                    payload = json.loads(message)
                    symbol = str(payload.get("s", "")).upper()
                    bid = payload.get("b")
                    ask = payload.get("a")
                    ts = int(payload.get("E") or payload.get("u") or int(time.time() * 1000))

                    if symbol != self.exchange_symbol:
                        now_ms = int(time.time() * 1000)
                        self._no_match_count += 1
                        if self._no_match_count <= 3 or now_ms - self._last_no_match_log_ms >= 10000:
                            if self._mode == "fallback":
                                self.signals.log.emit("WS", f"fallback alive but {self.exchange_symbol} not found yet")
                            else:
                                self.signals.log.emit("WS", f"ignored tick symbol={symbol}")
                            self._last_no_match_log_ms = now_ms
                        return

                    bid_f = float(bid)
                    ask_f = float(ask)
                    self._accepted_tick_ms = int(time.time() * 1000)
                    self.signals.book.emit(bid_f, ask_f, ts)
                    now_ms = int(time.time() * 1000)
                    if now_ms - self._last_live_log_ms >= 5000:
                        self._last_live_log_ms = now_ms
                        self.signals.log.emit("WS", f"LIVE bid={bid_f:.2f} ask={ask_f:.2f}")
                except Exception as exc:
                    self.signals.status.emit("ERROR")
                    self.signals.log.emit("WS", f"error {exc}")

            def on_open(_ws: websocket.WebSocketApp) -> None:
                self._connected_at_ms = int(time.time() * 1000)
                if self._mode == "primary":
                    self.signals.status.emit("CONNECTED")
                    self.signals.log.emit("WS", "connected")

                    def switch_if_no_ticks() -> None:
                        time.sleep(self.max_ws_age_ms / 1000)
                        if (
                            self._running
                            and self._mode == "primary"
                            and self._connected_at_ms is not None
                            and self._accepted_tick_ms is None
                            and self._ws is not None
                        ):
                            self.signals.log.emit("WS", f"primary no ticks for {self.max_ws_age_ms}ms -> switching to all bookTicker")
                            self._mode = "fallback"
                            self._ws.close()

                    threading.Thread(target=switch_if_no_ticks, daemon=True).start()
                else:
                    self.signals.status.emit("CONNECTED")
                    self.signals.log.emit("WS", "reconnected")

            def on_error(_ws: websocket.WebSocketApp, error: Exception) -> None:
                self.signals.status.emit("ERROR")
                self.signals.log.emit("WS", f"error {error}")

            def on_close(_ws: websocket.WebSocketApp, *_args: object) -> None:
                self.signals.status.emit("LOST")
                self.signals.log.emit("WS", "stale/lost")

            self._ws = websocket.WebSocketApp(url, on_message=on_message, on_open=on_open, on_error=on_error, on_close=on_close)
            self._ws.run_forever(ping_interval=20, ping_timeout=5)

            if self._running:
                time.sleep(reconnect_delay)
