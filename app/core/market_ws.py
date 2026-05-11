import json
import threading
import time

import websocket
from PySide6.QtCore import QObject, Signal


class MarketWSSignals(QObject):
    book = Signal(float, float, int)
    status = Signal(str)
    log = Signal(str, str)


class MarketWSClient:
    def __init__(self, stream_symbol: str, binance_symbol: str) -> None:
        self.stream_symbol = stream_symbol.lower()
        self.binance_symbol = binance_symbol.upper()
        self.accept_symbols = {self.binance_symbol, self.binance_symbol.replace("USDT", "")}
        self.signals = MarketWSSignals()
        self._thread: threading.Thread | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._running = False
        self._use_all_book_ticker = False
        self._raw_ticks_logged = 0
        self._last_raw_log_ms = 0

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            self._ws.close()

    def _stream_url(self) -> str:
        channel = "!bookTicker" if self._use_all_book_ticker else f"{self.stream_symbol}@bookTicker"
        return f"wss://stream.binance.com:9443/ws/{channel}"

    def _run(self) -> None:
        reconnect_delay = 2
        while self._running:
            url = self._stream_url()
            self.signals.status.emit("CONNECTING")
            self.signals.log.emit("WS", f"connecting url={url}")

            def on_message(_ws: websocket.WebSocketApp, message: str) -> None:
                try:
                    payload = json.loads(message)
                    symbol = str(payload.get("s", ""))
                    bid = payload.get("b")
                    ask = payload.get("a")
                    ts = int(payload.get("E") or payload.get("u") or int(time.time() * 1000))
                    update_id = payload.get("u", "n/a")

                    now_ms = int(time.time() * 1000)
                    should_log_raw = self._raw_ticks_logged < 5 or now_ms - self._last_raw_log_ms >= 3000
                    if should_log_raw:
                        self.signals.log.emit(
                            "WS",
                            f"raw tick received symbol={symbol} bid={bid} ask={ask} u={update_id}",
                        )
                        self._raw_ticks_logged += 1
                        self._last_raw_log_ms = now_ms

                    if symbol not in self.accept_symbols:
                        self.signals.log.emit("WS", f"ignored tick symbol={symbol}")
                        return

                    bid_f = float(bid)
                    ask_f = float(ask)
                    self.signals.book.emit(bid_f, ask_f, ts)
                    if self._use_all_book_ticker:
                        self.signals.log.emit("WS", f"accepted {symbol} from all-bookTicker")
                    self.signals.log.emit("WS", f"accepted tick bid={bid_f:.2f} ask={ask_f:.2f}")
                except Exception as exc:
                    self.signals.status.emit("ERROR")
                    self.signals.log.emit("WS", f"error {exc}")

            def on_open(_ws: websocket.WebSocketApp) -> None:
                self.signals.log.emit("WS", f"connected url={url}")

            def on_error(_ws: websocket.WebSocketApp, error: Exception) -> None:
                self.signals.status.emit("ERROR")
                self.signals.log.emit("WS", f"error {error}")

            def on_close(_ws: websocket.WebSocketApp, *_args: object) -> None:
                self.signals.status.emit("LOST")
                self.signals.log.emit("WS", "reconnecting reason=socket closed")

            self._ws = websocket.WebSocketApp(
                url,
                on_message=on_message,
                on_open=on_open,
                on_error=on_error,
                on_close=on_close,
            )
            self._ws.run_forever(ping_interval=20, ping_timeout=5)

            if self._running and not self._use_all_book_ticker:
                self._use_all_book_ticker = True
                self.signals.log.emit("WS", "primary stream failed, switching to all bookTicker")

            if self._running:
                time.sleep(reconnect_delay)
