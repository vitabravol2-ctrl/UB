from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.core.config import CONFIG
from app.core.grid_config import GRID_SETTINGS_STORE, GridSettings
from app.core.grid_engine import GridEngine
from app.gui.styles import main_qss
from app.gui.widgets import build_kv_card


class MicroGridWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UB Micro Grid / BTCU")
        self.resize(1400, 860)
        self.setStyleSheet(main_qss())
        self.grid_engine = GridEngine(CONFIG.tick_size_default)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        cards = QGridLayout()
        conn, self.conn_card = build_kv_card("CONNECTION", [("API", "N/A"), ("REST", "N/A")], compact=True)
        market, self.market_card = build_kv_card("MARKET", [("Bid", "N/A"), ("Ask", "N/A"), ("Spread", "N/A")], compact=True)
        status, self.status_card = build_kv_card("GRID STATUS", [("State", "IDLE"), ("Levels", "0")], compact=True)
        bal, self.bal_card = build_kv_card("BALANCES", [("BTC", "0"), ("U", "0")], compact=True)
        cards.addWidget(conn, 0, 0)
        cards.addWidget(market, 0, 1)
        cards.addWidget(status, 0, 2)
        cards.addWidget(bal, 0, 3)
        layout.addLayout(cards)

        main = QHBoxLayout()
        layout.addLayout(main)

        self.levels_table = QTableWidget(0, 8)
        self.levels_table.setHorizontalHeaderLabels([
            "Level", "Price", "Side", "Order U", "Qty BTC", "Status", "Target Sell", "Expected PnL",
        ])
        main.addWidget(self.levels_table, stretch=3)

        settings_box = QGroupBox("Grid Settings")
        settings_layout = QVBoxLayout(settings_box)
        form = QFormLayout()
        self.upper_price = QLineEdit()
        self.lower_price = QLineEdit()
        self.budget_u = QLineEdit()
        self.levels = QLineEdit()
        self.profit_ticks = QLineEdit()
        self.max_exposure_u = QLineEdit()
        self.auto_float_enabled = QCheckBox()
        self.live_enabled = QCheckBox()
        form.addRow("upper_price", self.upper_price)
        form.addRow("lower_price", self.lower_price)
        form.addRow("budget_u", self.budget_u)
        form.addRow("levels", self.levels)
        form.addRow("profit_ticks", self.profit_ticks)
        form.addRow("max_exposure_u", self.max_exposure_u)
        form.addRow("auto_float_enabled", self.auto_float_enabled)
        form.addRow("live_enabled", self.live_enabled)
        settings_layout.addLayout(form)

        btn_row = QGridLayout()
        self.calculate_btn = QPushButton("Calculate Grid")
        self.start_btn = QPushButton("Start Dry View")
        self.stop_btn = QPushButton("Stop")
        self.save_btn = QPushButton("Save Settings")
        self.load_btn = QPushButton("Load Settings")
        btn_row.addWidget(self.calculate_btn, 0, 0)
        btn_row.addWidget(self.start_btn, 0, 1)
        btn_row.addWidget(self.stop_btn, 1, 0)
        btn_row.addWidget(self.save_btn, 1, 1)
        btn_row.addWidget(self.load_btn, 2, 0, 1, 2)
        settings_layout.addLayout(btn_row)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        settings_layout.addWidget(QLabel("Grid Logs"))
        settings_layout.addWidget(self.log_box)
        main.addWidget(settings_box, stretch=2)

        self.calculate_btn.clicked.connect(self.on_calculate)
        self.start_btn.clicked.connect(self.on_start_dry)
        self.stop_btn.clicked.connect(self.on_stop)
        self.save_btn.clicked.connect(self.save_settings)
        self.load_btn.clicked.connect(self.load_settings)

        self.load_settings()

    def _log(self, msg: str) -> None:
        self.log_box.append(msg)

    def _collect_settings(self) -> GridSettings:
        return GridSettings(
            upper_price=float(self.upper_price.text()),
            lower_price=float(self.lower_price.text()),
            budget_u=float(self.budget_u.text()),
            levels=int(self.levels.text()),
            profit_ticks=int(self.profit_ticks.text()),
            max_exposure_u=float(self.max_exposure_u.text()),
            auto_float_enabled=self.auto_float_enabled.isChecked(),
            live_enabled=self.live_enabled.isChecked(),
        )

    def _apply_settings(self, s: GridSettings) -> None:
        self.upper_price.setText(str(s.upper_price))
        self.lower_price.setText(str(s.lower_price))
        self.budget_u.setText(str(s.budget_u))
        self.levels.setText(str(s.levels))
        self.profit_ticks.setText(str(s.profit_ticks))
        self.max_exposure_u.setText(str(s.max_exposure_u))
        self.auto_float_enabled.setChecked(s.auto_float_enabled)
        self.live_enabled.setChecked(s.live_enabled)

    def on_calculate(self) -> None:
        try:
            s = self._collect_settings()
            rows = self.grid_engine.calculate_levels(s.upper_price, s.lower_price, s.budget_u, s.levels, s.profit_ticks)
        except ValueError as exc:
            self._log(f"[GRID] error: {exc}")
            return
        self._log(f"[GRID] calculate levels={s.levels}")
        self.levels_table.setRowCount(len(rows))
        for row_idx, row in enumerate(rows):
            self.levels_table.setItem(row_idx, 0, QTableWidgetItem(str(row.level)))
            self.levels_table.setItem(row_idx, 1, QTableWidgetItem(f"{row.price:.2f}"))
            self.levels_table.setItem(row_idx, 2, QTableWidgetItem(row.side))
            self.levels_table.setItem(row_idx, 3, QTableWidgetItem(f"{row.order_u:.4f}"))
            self.levels_table.setItem(row_idx, 4, QTableWidgetItem(f"{row.qty_btc:.8f}"))
            self.levels_table.setItem(row_idx, 5, QTableWidgetItem(row.status))
            self.levels_table.setItem(row_idx, 6, QTableWidgetItem(f"{row.target_sell:.2f}"))
            self.levels_table.setItem(row_idx, 7, QTableWidgetItem(f"{row.expected_pnl:.8f}"))
            self._log(f"[GRID] level price={row.price:.2f} order_u={row.order_u:.2f}")

    def on_start_dry(self) -> None:
        if self.live_enabled.isChecked():
            self._log("[GRID] LIVE disabled in v0.8.0")
        self.status_card["State"].setText("DRY VIEW")

    def on_stop(self) -> None:
        self.status_card["State"].setText("STOPPED")

    def save_settings(self) -> None:
        settings = self._collect_settings()
        GRID_SETTINGS_STORE.save(settings)
        self._log("[GRID] settings saved")

    def load_settings(self) -> None:
        settings = GRID_SETTINGS_STORE.load()
        settings.live_enabled = bool(settings.live_enabled)
        self._apply_settings(settings)
        self._log("[GRID] settings loaded")
