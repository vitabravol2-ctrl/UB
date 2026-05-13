from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTextEdit, QVBoxLayout, QWidget

from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.micro_grid_config import MICRO_GRID_SETTINGS_STORE
from app.core.micro_grid_runtime import MicroGridRuntime


class MicroGridWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = MICRO_GRID_SETTINGS_STORE.load()
        self.setWindowTitle("Micro Grid")
        self.resize(760, 560)

        self.adapter = GridTradeAdapter(live_enabled=self.settings.start_live)
        self.runtime = MicroGridRuntime(self.adapter, self._log)
        self.filters = self.adapter.load_filters()

        root = QWidget(self)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

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

        self.runtime_info = QLabel("Placed BUY orders: 0 | Filled BUY: 0 | Placed SELL: 0 | Closed cycles: 0 | Realized PnL: 0 | Open inventory: 0")
        layout.addWidget(self.runtime_info)
        self.log_box = QTextEdit(); self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box, 1)

        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn.clicked.connect(self.on_stop)
        self.cancel_btn.clicked.connect(self.cancel_all)
        self.balance_btn.clicked.connect(lambda: self._log(f"BALANCES {self.adapter.refresh_balances()}"))
        self.orders_btn.clicked.connect(lambda: self._log(f"OPEN_ORDERS n={len(self.adapter.get_open_orders())}"))

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.on_recalc()

    def _log(self, msg: str) -> None:
        self.log_box.append(msg)

    def on_recalc(self) -> None:
        step, budget, qty = self.runtime.calculate(float(self.lower.text()), float(self.upper.text()), int(self.count.text()), float(self.investment.text()), float(self.filters["stepSize"]))
        profit = step * qty
        self.stats.setText(f"Calculated step: {step:.8f} | Budget per level: {budget:.8f} | Qty per level: {qty:.8f} | Expected profit per cycle: {profit:.8f} | Total grid levels: {int(self.count.text())}")

    def on_start(self) -> None:
        self.on_recalc()
        self.runtime.build_levels(float(self.lower.text()), int(self.count.text()), self.runtime.calculate(float(self.lower.text()), float(self.upper.text()), int(self.count.text()), float(self.investment.text()), float(self.filters["stepSize"]))[2])
        self._log("GRID_MODE_START")
        self.runtime.start(int(self.settings.max_active_orders))
        self.timer.start(1500)

    def on_tick(self) -> None:
        self.runtime.poll()
        open_inventory = len([x for x in self.runtime.levels if x.state in {"BUY_PLACED", "BUY_FILLED", "SELL_PLACED"}])
        self.runtime_info.setText(f"Closed cycles: {self.runtime.closed_cycles} | Realized PnL: {self.runtime.realized_pnl:.8f} | Open inventory: {open_inventory}")

    def on_stop(self) -> None:
        self.timer.stop()
        if self.settings.cancel_on_stop:
            self.cancel_all()
        self._log("GRID_MODE_STOP")

    def cancel_all(self) -> None:
        out = self.adapter.cancel_grid_orders()
        self._log(f"GRID_CANCEL_ALL {out}")
