from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget

from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.micro_grid_config import MICRO_GRID_SETTINGS_STORE
from app.core.micro_grid_runtime import MicroGridRuntime


class MicroGridWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = MICRO_GRID_SETTINGS_STORE.load()
        self.adapter = GridTradeAdapter(live_enabled=self.settings.live_enabled, symbol=self.settings.symbol)
        self.runtime = MicroGridRuntime(self.adapter, self._log)
        self.filters = self.adapter.load_filters()
        self.market_state = MarketState()
        self.market_rest = MarketREST()

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.market_labels = {}
        market_grid = QGridLayout()
        for i, key in enumerate(["Current price", "Best bid", "Best ask", "Spread", "API status", "WS status", "REST status", "Step U", "Step ticks", "Budget per level"]):
            market_grid.addWidget(QLabel(key), 0, i)
            lbl = QLabel("-")
            self.market_labels[key] = lbl
            market_grid.addWidget(lbl, 1, i)
        layout.addLayout(market_grid)

        form = QFormLayout()
        self.symbol = QLineEdit(self.settings.symbol)
        self.lower = QLineEdit(str(self.settings.lower_price))
        self.upper = QLineEdit(str(self.settings.upper_price))
        self.grid_count = QLineEdit(str(self.settings.grid_count))
        self.investment = QLineEdit(str(self.settings.investment_u))
        self.max_active = QLineEdit(str(self.settings.max_active_orders))
        self.live_enabled = QCheckBox(); self.live_enabled.setChecked(self.settings.live_enabled)
        self.dry_run = QCheckBox(); self.dry_run.setChecked(self.settings.dry_run)
        for k,v in [("SYMBOL",self.symbol),("Lower price",self.lower),("Upper price",self.upper),("Grid count",self.grid_count),("Total investment U",self.investment),("Max active orders",self.max_active),("Live enabled",self.live_enabled),("Dry run",self.dry_run)]:
            form.addRow(k,v)
        layout.addLayout(form)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True); layout.addWidget(self.log_box)
        row = QHBoxLayout(); self.start_btn = QPushButton("Start"); self.stop_btn = QPushButton("Stop"); self.cancel_btn = QPushButton("Cancel All"); self.balance_btn = QPushButton("Load Balances")
        row.addWidget(self.start_btn); row.addWidget(self.stop_btn); row.addWidget(self.cancel_btn); row.addWidget(self.balance_btn); layout.addLayout(row)
        self.start_btn.clicked.connect(self.on_start); self.stop_btn.clicked.connect(self.on_stop); self.cancel_btn.clicked.connect(self.cancel_all); self.balance_btn.clicked.connect(self.load_balances)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick)
        self._update_api_state()

    def _log(self, msg: str) -> None:
        self.log_box.append(msg)

    def _update_api_state(self) -> bool:
        ready = self.adapter.has_keys()
        self.market_labels["API status"].setText("READY" if ready else "NOT SET")
        if ready:
            self._log("GRID_API_READY")
        else:
            self._log("GRID_API_NOT_SET")
        self.start_btn.setEnabled(ready)
        self.balance_btn.setEnabled(ready)
        return ready

    def _get_market(self) -> tuple[float, float, float]:
        snap = self.market_rest.fetch_ticker(self.symbol.text().strip())
        bid = float(snap.get("bidPrice", 0) or 0)
        ask = float(snap.get("askPrice", 0) or 0)
        current = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        return current, bid, ask

    def load_balances(self) -> None:
        try:
            self.adapter.refresh_balances()
        except ValueError as exc:
            if str(exc) == "API NOT SET":
                self._log("GRID_API_NOT_SET")

    def on_start(self) -> None:
        if not self._update_api_state():
            return
        lower, upper, count, inv = float(self.lower.text()), float(self.upper.text()), int(self.grid_count.text()), float(self.investment.text())
        self.runtime.build_range_levels(lower_price=lower, upper_price=upper, grid_count=count, total_investment_u=inv, tick_size=float(self.filters["tickSize"]), step_size=float(self.filters["stepSize"]), min_qty=float(self.filters["minQty"]), min_notional=float(self.filters["minNotional"]))
        current, bid, ask = self._get_market()
        if self.live_enabled.isChecked() and (current <= 0 or bid <= 0 or ask <= 0):
            self._log("GRID_MARKET_NOT_READY")
            return
        self.market_labels["Current price"].setText(str(current)); self.market_labels["Best bid"].setText(str(bid)); self.market_labels["Best ask"].setText(str(ask)); self.market_labels["Spread"].setText(str(max(0.0, ask-bid)))
        self.market_labels["Step U"].setText(str(self.runtime.step_u)); self.market_labels["Step ticks"].setText(str(self.runtime.step_ticks)); self.market_labels["Budget per level"].setText(str(self.runtime.budget_per_level))
        bal = {"BTC": {"free": 0.0}}
        try:
            bal = self.adapter.refresh_balances()
        except ValueError as exc:
            if str(exc) == "API NOT SET":
                self._log("GRID_API_NOT_SET")
                return
        self.runtime.select_active_levels(current if current > 0 else (lower + upper) / 2, int(self.max_active.text()), bal.get("BTC", {}).get("free", 0.0))
        self.runtime.place_active_orders(live_enabled=self.live_enabled.isChecked(), dry_run=self.dry_run.isChecked(), best_bid=bid, best_ask=ask)
        self.timer.start(1500)

    def on_tick(self) -> None:
        pass

    def on_stop(self) -> None:
        self.timer.stop(); self._log("GRID_MODE_STOP")

    def cancel_all(self) -> None:
        try:
            self._log(f"GRID_CANCEL_ALL {self.adapter.cancel_grid_orders()}")
        except ValueError as exc:
            if str(exc) == "API NOT SET":
                self._log("GRID_API_NOT_SET")
