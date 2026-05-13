from __future__ import annotations

from datetime import datetime, UTC

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget

from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
from app.core.micro_grid_config import MICRO_GRID_SETTINGS_STORE
from app.core.micro_grid_runtime import MicroGridRuntime

IMPORTANT_EVENTS = {
    "GRID_MODE_START", "GRID_BUY_PLACED", "GRID_BUY_FILLED", "GRID_SELL_PLACED", "GRID_SELL_FILLED",
    "GRID_PNL", "GRID_CANCEL_ALL", "GRID_MODE_STOP", "GRID_ERROR",
}


class MicroGridWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = MICRO_GRID_SETTINGS_STORE.load()
        self.setWindowTitle("Micro Grid")
        self.resize(880, 640)

        self.adapter = GridTradeAdapter(live_enabled=self.settings.live_enabled, symbol=self.settings.symbol)
        self.runtime = MicroGridRuntime(self.adapter, self._log)
        self.filters = self.adapter.load_filters()
        self.market_state = MarketState()
        self.market_rest = MarketREST()
        self.market_ws = MarketWSClient(self.settings.symbol, self.settings.symbol)
        self.market_ws.signals.book.connect(self._on_ws_book)
        self.market_ws.signals.status.connect(self._on_ws_status)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.market_labels: dict[str, QLabel] = {}
        market_grid = QGridLayout()
        for i, key in enumerate(["SYMBOL", "API status", "REST status", "WS status", "Last update", "Current price", "Best bid", "Best ask", "Spread U", "Spread ticks"]):
            market_grid.addWidget(QLabel(key), 0, i)
            lbl = QLabel("-")
            self.market_labels[key] = lbl
            market_grid.addWidget(lbl, 1, i)
        layout.addLayout(market_grid)

        form = QFormLayout()
        self.symbol = QLineEdit(self.settings.symbol)
        self.lower = QLineEdit(str(self.settings.lower_price))
        self.upper = QLineEdit(str(self.settings.upper_price))
        self.count = QLineEdit(str(self.settings.grid_count))
        self.investment = QLineEdit(str(self.settings.investment_u))
        form.addRow("SYMBOL", self.symbol)
        form.addRow("Lower price", self.lower)
        form.addRow("Upper price", self.upper)
        form.addRow("Grid count", self.count)
        form.addRow("Investment U", self.investment)
        layout.addLayout(form)

        self.stats = QLabel("-")
        layout.addWidget(self.stats)

        row = QHBoxLayout()
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.cancel_btn = QPushButton("Cancel All")
        self.balance_btn = QPushButton("Load balances")
        self.orders_btn = QPushButton("Open orders")
        for b in [self.start_btn, self.stop_btn, self.cancel_btn, self.balance_btn, self.orders_btn]:
            row.addWidget(b)
        layout.addLayout(row)

        self.runtime_info = QLabel("-")
        layout.addWidget(self.runtime_info)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box, 1)

        api_status = self.adapter.load_api()
        if api_status == "OK":
            self._log("GRID_API_READY")
        self._update_market_panel()

        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.cancel_btn.clicked.connect(self.cancel_all)
        self.balance_btn.clicked.connect(lambda: self._log(f"BALANCES {self.adapter.refresh_balances()}"))
        self.orders_btn.clicked.connect(self.open_orders)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.market_timer = QTimer(self)
        self.market_timer.timeout.connect(self._rest_fallback_update)
        self.market_timer.start(2000)

        self.market_ws.start()
        self.on_recalc()

    def _log(self, msg: str) -> None:
        with open("logs/micro_grid.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
        if any(msg.startswith(evt) for evt in IMPORTANT_EVENTS):
            self.log_box.append(msg)

    def _on_ws_status(self, status: str) -> None:
        self.market_state.ws_status = status
        if status == "CONNECTED":
            self._log("GRID_WS_OK")
        elif status in {"LOST", "ERROR"}:
            self._log("GRID_WS_LOST")
        self._update_market_panel()

    def _on_ws_book(self, bid: float, ask: float, ts: int) -> None:
        self.market_state.snapshot.bid = bid
        self.market_state.snapshot.ask = ask
        self.market_state.snapshot.source = "WS"
        self.market_state.snapshot.updated_ms = ts
        self._log(f"GRID_MARKET_UPDATE bid={bid} ask={ask} spread={ask-bid} source=WS")
        self._update_market_panel()

    def _rest_fallback_update(self) -> None:
        if self.market_state.ws_status == "CONNECTED":
            return
        try:
            bid, ask, ts = self.market_rest.fetch_book_ticker(self.symbol.text().strip().upper())
            self.market_state.snapshot.bid = bid
            self.market_state.snapshot.ask = ask
            self.market_state.snapshot.source = "REST"
            self.market_state.snapshot.updated_ms = ts
            self.market_state.rest_status = "OK"
            self._log("GRID_REST_OK")
            self._log(f"GRID_MARKET_UPDATE bid={bid} ask={ask} spread={ask-bid} source=REST")
        except Exception as exc:
            self._log(f"GRID_ERROR rest={exc}")
            self.market_state.rest_status = "ERROR"
        self._update_market_panel()

    def _update_market_panel(self) -> None:
        bid = self.market_state.snapshot.bid
        ask = self.market_state.snapshot.ask
        spread = (ask - bid) if bid is not None and ask is not None else None
        current = ((ask + bid) / 2) if spread is not None else None
        spread_ticks = (spread / self.runtime.step) if spread is not None and self.runtime.step > 0 else None
        upd = datetime.fromtimestamp(self.market_state.snapshot.updated_ms / 1000, UTC).strftime("%H:%M:%S") if self.market_state.snapshot.updated_ms else "-"
        self.market_labels["SYMBOL"].setText(self.symbol.text().strip().upper())
        self.market_labels["API status"].setText(self.adapter.api_status)
        self.market_labels["REST status"].setText(self.market_state.rest_status)
        self.market_labels["WS status"].setText(self.market_state.ws_status)
        self.market_labels["Last update"].setText(upd)
        self.market_labels["Current price"].setText(f"{current:.8f}" if current is not None else "-")
        self.market_labels["Best bid"].setText(f"{bid:.8f}" if bid is not None else "-")
        self.market_labels["Best ask"].setText(f"{ask:.8f}" if ask is not None else "-")
        self.market_labels["Spread U"].setText(f"{spread:.8f}" if spread is not None else "-")
        self.market_labels["Spread ticks"].setText(f"{spread_ticks:.3f}" if spread_ticks is not None else "-")

    def on_recalc(self) -> None:
        step, step_ticks, budget, qty = self.runtime.calculate(float(self.lower.text()), float(self.upper.text()), int(self.count.text()), float(self.investment.text()), float(self.filters["stepSize"]), float(self.filters["tickSize"]))
        profit = step * qty
        total_profit = profit * len(self.runtime.levels or [None] * int(self.count.text()))
        self.stats.setText(f"Step U: {step:.8f} | Step ticks: {step_ticks} | Budget per level: {budget:.8f} | Qty per level: {qty:.8f} | Expected profit/cycle U: {profit:.8f} | Expected profit all U: {total_profit:.8f} | Total valid levels: {len(self.runtime.levels) if self.runtime.levels else int(self.count.text())}")

    def on_start(self) -> None:
        self.on_recalc()
        if self.adapter.api_status != "OK":
            self._log("GRID_ERROR api_not_ready=1")
            return
        self.runtime.build_levels(float(self.lower.text()), int(self.count.text()), float(self.investment.text()), float(self.filters["tickSize"]), float(self.filters["stepSize"]), float(self.filters["minQty"]), float(self.filters["minNotional"]))
        self.on_recalc()
        self._log("GRID_MODE_START")
        self.runtime.start(int(self.settings.max_active_orders), live_enabled=bool(self.settings.live_enabled), dry_run=bool(self.settings.dry_run), test_order_limit=int(self.settings.test_order_limit), current_bid=self.market_state.snapshot.bid, active_buy_window_levels=int(self.settings.active_buy_window_levels), max_live_buy_orders=int(self.settings.max_live_buy_orders))
        self.timer.start(1500)

    def on_tick(self) -> None:
        self.runtime.poll()
        self.runtime.rebalance_active_window(self.market_state.snapshot.bid, int(self.settings.active_buy_window_levels), int(self.settings.max_active_orders), int(self.settings.max_live_buy_orders), bool(self.settings.live_enabled), int(self.settings.test_order_limit))
        total = len(self.runtime.levels)
        valid = len([x for x in self.runtime.levels if x.qty > 0])
        waiting = len([x for x in self.runtime.levels if x.state == "WAIT_BUY"])
        open_buy = len([x for x in self.runtime.levels if x.state == "BUY_PLACED"])
        open_sell = len([x for x in self.runtime.levels if x.state == "SELL_PLACED"])
        open_inventory = len([x for x in self.runtime.levels if x.state in {"BUY_PLACED", "BUY_FILLED", "SELL_PLACED"}])
        self.runtime_info.setText(f"Total levels: {total} | Valid levels: {valid} | Waiting levels: {waiting} | Open BUY: {open_buy} | Open SELL: {open_sell} | Closed cycles: {self.runtime.closed_cycles} | Realized PnL: {self.runtime.realized_pnl:.8f} | Open inventory: {open_inventory}")

    def on_stop(self) -> None:
        self.timer.stop()
        self.market_ws.stop()
        if self.settings.cancel_on_stop:
            self.cancel_all()
        self._log("GRID_MODE_STOP")

    def cancel_all(self) -> None:
        if self.adapter.api_status != "OK":
            self._log("GRID_ERROR api_not_ready=1")
            return
        out = self.adapter.cancel_grid_orders()
        self._log(f"GRID_CANCEL_ALL {out}")

    def open_orders(self) -> None:
        if self.adapter.api_status != "OK":
            self._log("GRID_ERROR api_not_ready=1")
            return
        orders = [o for o in self.adapter.get_open_orders() if str(o.get("clientOrderId", "")).startswith("UBGRID_")]
        self._log(f"GRID_OPEN_ORDERS n={len(orders)}")
