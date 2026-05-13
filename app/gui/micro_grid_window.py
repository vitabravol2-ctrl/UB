from __future__ import annotations

from datetime import datetime

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
        self.filters = {}
        self.market_state = MarketState()
        self.market_rest = MarketREST()

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        self.market_labels = {}
        keys = ["API status", "REST status", "WS status", "Current price", "Best bid", "Best ask", "Spread U", "Spread ticks", "Last update", "tickSize", "stepSize", "minNotional", "Step U", "Step ticks", "Budget per level"]
        market_grid = QGridLayout()
        for i, key in enumerate(keys):
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
        self.test_order_limit = QLineEdit(str(self.settings.test_order_limit))
        self.live_enabled = QCheckBox(); self.live_enabled.setChecked(self.settings.live_enabled)
        self.dry_run = QCheckBox(); self.dry_run.setChecked(self.settings.dry_run)
        for k,v in [("SYMBOL",self.symbol),("Lower price",self.lower),("Upper price",self.upper),("Grid count",self.grid_count),("Investment U",self.investment),("Max active orders",self.max_active),("Dry run",self.dry_run),("Live enabled",self.live_enabled),("Test order limit",self.test_order_limit)]:
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
        ok, status, reason = self.adapter.check_api_ready()
        self.market_labels["API status"].setText("OK" if ok else status)
        if ok:
            self._log("GRID_API_READY")
        elif status == "NOT SET":
            self._log("GRID_API_NOT_SET reason=missing_keys")
        else:
            self._log(f"GRID_API_ERROR reason={reason}")
        self.start_btn.setEnabled(ok)
        self.balance_btn.setEnabled(ok)
        return ok

    def _get_market(self) -> tuple[float, float, float]:
        bid, ask, _ = self.market_rest.fetch_book_ticker(self.symbol.text().strip())
        current = (bid + ask) / 2 if bid > 0 and ask > 0 else 0.0
        return current, bid, ask

    def load_balances(self) -> None:
        if not self._update_api_state():
            return
        self.adapter.refresh_balances()

    def on_start(self) -> None:
        if not self._update_api_state():
            return
        self.filters = self.adapter.load_filters()
        self._log(f"GRID_FILTERS_LOADED tickSize={self.filters['tickSize']} stepSize={self.filters['stepSize']} minQty={self.filters['minQty']} minNotional={self.filters['minNotional']}")
        lower, upper, count, inv = float(self.lower.text()), float(self.upper.text()), int(self.grid_count.text()), float(self.investment.text())
        self.runtime.build_range_levels(lower_price=lower, upper_price=upper, grid_count=count, total_investment_u=inv, tick_size=float(self.filters["tickSize"]), step_size=float(self.filters["stepSize"]), min_qty=float(self.filters["minQty"]), min_notional=float(self.filters["minNotional"]))
        current, bid, ask = self._get_market()
        if bid > 0 and ask > 0:
            self._log(f"GRID_MARKET_READY bid={bid} ask={ask} mid={current}")
        elif self.live_enabled.isChecked() and not self.dry_run.isChecked():
            self._log("GRID_MARKET_NOT_READY reason=empty_book")
            return

        spread = max(0.0, ask - bid)
        spread_ticks = int(round(spread / float(self.filters["tickSize"]))) if float(self.filters["tickSize"]) > 0 else 0
        self.market_labels["Current price"].setText(str(current)); self.market_labels["Best bid"].setText(str(bid)); self.market_labels["Best ask"].setText(str(ask))
        self.market_labels["Spread U"].setText(str(spread)); self.market_labels["Spread ticks"].setText(str(spread_ticks)); self.market_labels["Last update"].setText(datetime.utcnow().isoformat())
        self.market_labels["tickSize"].setText(str(self.filters["tickSize"])); self.market_labels["stepSize"].setText(str(self.filters["stepSize"])); self.market_labels["minNotional"].setText(str(self.filters["minNotional"]))
        self.market_labels["Step U"].setText(str(self.runtime.step_u)); self.market_labels["Step ticks"].setText(str(self.runtime.step_ticks)); self.market_labels["Budget per level"].setText(str(self.runtime.budget_per_level))

        bal = self.adapter.refresh_balances()
        self.runtime.select_active_levels(current if current > 0 else (lower + upper) / 2, int(self.max_active.text()), bal.get("BTC", {}).get("free", 0.0))
        self.runtime.place_active_orders(symbol=self.symbol.text().strip(), dry_run=self.dry_run.isChecked(), best_bid=bid, best_ask=ask, test_order_limit=int(self.test_order_limit.text()))
        self.timer.start(1500)

    def on_tick(self) -> None:
        pass

    def on_stop(self) -> None:
        self.timer.stop(); self._log("GRID_MODE_STOP")

    def cancel_all(self) -> None:
        if not self._update_api_state():
            return
        orders = self.adapter.get_grid_open_orders()
        self._log(f"GRID_OPEN_ORDERS n={len(orders)}")
        for order in orders:
            self.adapter.account.cancel_order(self.symbol.text().strip(), int(order["orderId"]))
            self._log(f"GRID_CANCEL_ORDER order_id={order.get('orderId')} client_id={order.get('clientOrderId')}")
        self._log(f"GRID_CANCEL_ALL n={len(orders)}")
