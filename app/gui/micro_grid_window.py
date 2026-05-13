from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget

from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
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
        self.market_ws = MarketWSClient(self.settings.symbol, self.settings.symbol)

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        self.market_labels = {}
        market_grid = QGridLayout()
        for i, key in enumerate(["Current price", "Best bid", "Best ask", "Spread U", "Spread ticks", "WS status", "REST status", "API status", "Last update"]):
            market_grid.addWidget(QLabel(key), 0, i)
            lbl = QLabel("-")
            self.market_labels[key] = lbl
            market_grid.addWidget(lbl, 1, i)
        layout.addLayout(market_grid)

        form = QFormLayout()
        self.symbol = QLineEdit(self.settings.symbol)
        self.step = QLineEdit(str(self.settings.grid_step_u))
        self.buy_levels = QLineEdit(str(self.settings.buy_levels_down))
        self.sell_levels = QLineEdit(str(self.settings.sell_levels_up))
        self.investment = QLineEdit(str(self.settings.investment_u))
        self.buy_budget = QLineEdit(str(self.settings.budget_buy_side_u))
        self.sell_budget = QLineEdit(str(self.settings.budget_sell_side_u))
        form.addRow("SYMBOL", self.symbol); form.addRow("Grid step U", self.step); form.addRow("Buy levels down", self.buy_levels); form.addRow("Sell levels up", self.sell_levels)
        form.addRow("Total investment U", self.investment); form.addRow("Buy side budget U", self.buy_budget); form.addRow("Sell side budget U", self.sell_budget)
        layout.addLayout(form)
        self.runtime_info = QLabel("-"); layout.addWidget(self.runtime_info)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True); layout.addWidget(self.log_box)
        row = QHBoxLayout(); self.start_btn = QPushButton("Start"); self.stop_btn = QPushButton("Stop"); self.cancel_btn = QPushButton("Cancel All")
        row.addWidget(self.start_btn); row.addWidget(self.stop_btn); row.addWidget(self.cancel_btn); layout.addLayout(row)
        self.start_btn.clicked.connect(self.on_start); self.stop_btn.clicked.connect(self.on_stop); self.cancel_btn.clicked.connect(self.cancel_all)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick)

    def _log(self, msg: str) -> None:
        self.log_box.append(msg)

    def on_start(self) -> None:
        mid = ((self.market_state.snapshot.bid or 0) + (self.market_state.snapshot.ask or 0)) / 2
        self.runtime.build_two_sided_levels(mid=mid, grid_step_u=float(self.step.text()), buy_levels_down=int(self.buy_levels.text()), sell_levels_up=int(self.sell_levels.text()), budget_buy_side_u=float(self.buy_budget.text()), budget_sell_side_u=float(self.sell_budget.text()), tick_size=float(self.filters["tickSize"]), step_size=float(self.filters["stepSize"]), min_qty=float(self.filters["minQty"]), min_notional=float(self.filters["minNotional"]))
        bal = self.adapter.refresh_balances()
        self.runtime.check_balances(free_u=bal.get("U", {}).get("free", 0.0), free_btc=bal.get("BTC", {}).get("free", 0.0))
        self._log("GRID_TWO_SIDED_START")
        self.runtime.start(max_live_buy_orders=self.settings.max_live_buy_orders, max_live_sell_orders=self.settings.max_live_sell_orders, live_enabled=self.settings.live_enabled, dry_run=self.settings.dry_run, test_order_limit=self.settings.test_order_limit)
        self.timer.start(1500)

    def on_tick(self) -> None:
        self.runtime.poll()
        self.runtime_info.setText(f"Total levels: {len(self.runtime.levels)} | BUY levels: {len(self.runtime.buy_levels)} | SELL levels: {len(self.runtime.sell_levels)} | Closed cycles: {self.runtime.closed_cycles} | Realized PnL: {self.runtime.realized_pnl:.8f}")

    def on_stop(self) -> None:
        self.timer.stop(); self._log("GRID_MODE_STOP")

    def cancel_all(self) -> None:
        self._log(f"GRID_CANCEL_ALL {self.adapter.cancel_grid_orders()}")
