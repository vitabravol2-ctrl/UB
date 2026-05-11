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
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol.lower()
        self.signals = MarketWSSignals()
        self._thread: threading.Thread | None = None
        self._ws: websocket.WebSocketApp | None = None
        self._running = False

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

    def _run(self) -> None:
        self.signals.status.emit("CONNECTING")
        self.signals.log.emit("WS", f"connecting bookTicker {self.symbol.upper()}")
        url = f"wss://stream.binance.com:9443/ws/{self.symbol}@bookTicker"

        def on_message(_ws: websocket.WebSocketApp, message: str) -> None:
            try:
                payload = json.loads(message)
                bid = float(payload["b"])
                ask = float(payload["a"])
                self.signals.book.emit(bid, ask, int(time.time() * 1000))
            except Exception as exc:
                self.signals.log.emit("WS", f"parse error: {exc}")

        def on_open(_ws: websocket.WebSocketApp) -> None:
            self.signals.status.emit("OK")
            self.signals.log.emit("WS", "bookTicker OK")

        def on_error(_ws: websocket.WebSocketApp, error: Exception) -> None:
            self.signals.status.emit("LOST")
            self.signals.log.emit("WS", f"error: {error}")

        def on_close(_ws: websocket.WebSocketApp, *_args: object) -> None:
            self.signals.status.emit("LOST")
            self.signals.log.emit("WS", "closed")

        self._ws = websocket.WebSocketApp(
            url,
            on_message=on_message,
            on_open=on_open,
            on_error=on_error,
            on_close=on_close,
        )

        while self._running:
            self._ws.run_forever(ping_interval=20, ping_timeout=5)
            if self._running:
                time.sleep(2)
