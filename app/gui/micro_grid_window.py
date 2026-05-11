import threading

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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

from app.core.binance_account import BinanceAccountClient
from app.core.config import CONFIG
from app.core.grid_config import GRID_SETTINGS_STORE, GridSettings
from app.core.grid_engine import GridEngine
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
from app.gui.styles import main_qss
from app.gui.widgets import build_kv_card


class MicroGridWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UB Micro Grid / BTCU")
        self.resize(1450, 880)
        self.setMinimumSize(1280, 760)
        self.setStyleSheet(main_qss())

        self.grid_engine = GridEngine(CONFIG.tick_size_default)
        self.market_state = MarketState()
        self.market_ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol)
        self.market_rest = MarketREST()
        self.account = BinanceAccountClient()
        self.rows = []
        self.dry_view_active = False

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        cards = QGridLayout()
        conn, self.conn_card = build_kv_card("CONNECTION", [("WS", "N/A"), ("REST", "N/A")], compact=True)
        market, self.market_card = build_kv_card("MARKET", [("Bid", "N/A"), ("Ask", "N/A"), ("Spread", "N/A"), ("Source", "NONE")], compact=True)
        status, self.status_card = build_kv_card("GRID STATUS", [("State", "IDLE"), ("Levels", "0")], compact=True)
        bal, self.bal_card = build_kv_card(
            "BALANCES",
            [("BTC free", "0"), ("BTC locked", "0"), ("U free", "0"), ("U locked", "0"), ("API", "N/A"), ("Filters", "N/A")],
            compact=True,
        )
        cards.addWidget(conn, 0, 0)
        cards.addWidget(market, 0, 1)
        cards.addWidget(status, 0, 2)
        cards.addWidget(bal, 0, 3)
        layout.addLayout(cards)

        main = QHBoxLayout()
        layout.addLayout(main)

        self.levels_table = QTableWidget(0, 9)
        self.levels_table.setHorizontalHeaderLabels([
            "Level", "Price", "Side", "Order U", "Qty BTC", "Status", "Target Sell", "Expected PnL", "Band",
        ])
        self.levels_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.levels_table.verticalHeader().setVisible(False)
        self.levels_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.levels_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.levels_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.levels_table.horizontalHeader().setSectionResizeMode(8, QHeaderView.Stretch)
        self.levels_table.setStyleSheet("QTableWidget{background-color:#121821;color:#e7eef8;gridline-color:#2f3d51;} QHeaderView::section{background:#1a2432;color:#d7e4f6;}")
        main.addWidget(self.levels_table, stretch=4)

        right = QVBoxLayout()
        main.addLayout(right, stretch=2)
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
        self.live_enabled.setEnabled(False)
        form.addRow("Upper Price", self.upper_price)
        form.addRow("Lower Price", self.lower_price)
        form.addRow("Budget U", self.budget_u)
        form.addRow("Levels", self.levels)
        form.addRow("Profit Ticks", self.profit_ticks)
        form.addRow("Max Exposure U", self.max_exposure_u)
        form.addRow("Auto Float", self.auto_float_enabled)
        form.addRow("LIVE Locked", self.live_enabled)
        settings_layout.addLayout(form)

        btn_row = QGridLayout()
        self.calculate_btn = QPushButton("Calculate Grid")
        self.start_btn = QPushButton("Start Dry View")
        self.stop_btn = QPushButton("Stop")
        self.save_btn = QPushButton("Save Settings")
        self.load_btn = QPushButton("Load Settings")
        for btn in [self.calculate_btn, self.start_btn, self.stop_btn, self.save_btn, self.load_btn]:
            btn.setMinimumHeight(34)
        btn_row.addWidget(self.calculate_btn, 0, 0)
        btn_row.addWidget(self.start_btn, 0, 1)
        btn_row.addWidget(self.stop_btn, 1, 0)
        btn_row.addWidget(self.save_btn, 1, 1)
        btn_row.addWidget(self.load_btn, 2, 0, 1, 2)
        settings_layout.addLayout(btn_row)
        right.addWidget(settings_box)

        summary_box, self.summary_card = build_kv_card(
            "GRID SUMMARY",
            [("Range", "N/A"), ("Step", "N/A"), ("Levels", "0"), ("Budget / Level", "0"), ("Total Budget", "0"), ("Valid Levels", "0"), ("Invalid Levels", "0"), ("Total Expected PnL", "0"), ("Tick Size", "N/A"), ("Step Size", "N/A"), ("Min Notional", "N/A")],
            compact=True,
        )
        right.addWidget(summary_box)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(160)
        right.addWidget(QLabel("Grid Logs"))
        right.addWidget(self.log_box)

        self.calculate_btn.clicked.connect(self.on_calculate)
        self.start_btn.clicked.connect(self.on_start_dry)
        self.stop_btn.clicked.connect(self.on_stop)
        self.save_btn.clicked.connect(self.save_settings)
        self.load_btn.clicked.connect(self.load_settings)

        self.market_ws.signals.book.connect(self._on_ws_book)
        self.market_ws.signals.status.connect(self._on_ws_status)
        self.market_ws.start()
        self._log("[GRID] ws connected")

        self.rest_timer = QTimer(self)
        self.rest_timer.timeout.connect(self._rest_poll)
        self.rest_timer.start(3000)

        self.balance_timer = QTimer(self)
        self.balance_timer.timeout.connect(self._balances_refresh)
        self.balance_timer.start(12000)

        self.load_settings()
        self._load_filters()
        self._balances_refresh()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.market_ws.stop()
        super().closeEvent(event)

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
            live_enabled=True,
        )

    def _apply_settings(self, s: GridSettings) -> None:
        self.upper_price.setText(str(s.upper_price))
        self.lower_price.setText(str(s.lower_price))
        self.budget_u.setText(str(s.budget_u))
        self.levels.setText(str(s.levels))
        self.profit_ticks.setText(str(s.profit_ticks))
        self.max_exposure_u.setText(str(s.max_exposure_u))
        self.auto_float_enabled.setChecked(s.auto_float_enabled)
        self.live_enabled.setChecked(False)

    def _on_ws_status(self, status: str) -> None:
        self.market_state.ws_status = status
        self.conn_card["WS"].setText(status)

    def _on_ws_book(self, bid: float, ask: float, ts: int) -> None:
        self.market_state.snapshot.bid = bid
        self.market_state.snapshot.ask = ask
        self.market_state.snapshot.source = "WS"
        self.market_state.last_ws_ms = ts
        self.market_card["Bid"].setText(f"{bid:.2f}")
        self.market_card["Ask"].setText(f"{ask:.2f}")
        self.market_card["Spread"].setText(f"{ask-bid:.2f}")
        self.market_card["Source"].setText("WS")
        if self.dry_view_active:
            self._refresh_dry_bands()

    def _rest_poll(self) -> None:
        if self.market_state.last_ws_ms and self.market_state.age_ms(self.market_state.last_ws_ms) < 2500:
            return

        def work() -> None:
            try:
                bid, ask, ts = self.market_rest.fetch_book_ticker(CONFIG.binance_symbol)
                self.market_state.snapshot.bid = bid
                self.market_state.snapshot.ask = ask
                self.market_state.snapshot.source = "REST"
                self.market_state.last_rest_ms = ts
                self.market_state.rest_status = "OK"
                self.conn_card["REST"].setText("OK")
                self.market_card["Bid"].setText(f"{bid:.2f}")
                self.market_card["Ask"].setText(f"{ask:.2f}")
                self.market_card["Spread"].setText(f"{ask-bid:.2f}")
                self.market_card["Source"].setText("REST")
                self._log("[GRID] rest fallback")
                if self.dry_view_active:
                    self._refresh_dry_bands()
            except Exception:
                self.market_state.rest_status = "ERROR"
                self.conn_card["REST"].setText("ERROR")

        threading.Thread(target=work, daemon=True).start()

    def _balances_refresh(self) -> None:
        def work() -> None:
            status = self.account.test_account_connection()
            if status.status == "NOT SET":
                self.bal_card["API"].setText("API NOT SET")
                return
            self.bal_card["API"].setText(status.status)
            try:
                balances = self.account.get_account_balances()
                self.bal_card["BTC free"].setText(f"{balances['BTC']['free']:.6f}")
                self.bal_card["BTC locked"].setText(f"{balances['BTC']['locked']:.6f}")
                self.bal_card["U free"].setText(f"{balances['U']['free']:.2f}")
                self.bal_card["U locked"].setText(f"{balances['U']['locked']:.2f}")
                self._log("[GRID] balances updated")
            except Exception:
                self.bal_card["API"].setText("ERROR")

        threading.Thread(target=work, daemon=True).start()

    def _load_filters(self) -> None:
        try:
            filters = self.account.get_exchange_filters(CONFIG.binance_symbol)
            self.grid_engine.set_filters(filters["tickSize"], filters["stepSize"], filters["minQty"], filters["minNotional"])
            self.bal_card["Filters"].setText("OK")
            self.summary_card["Tick Size"].setText(str(filters["tickSize"]))
            self.summary_card["Step Size"].setText(str(filters["stepSize"]))
            self.summary_card["Min Notional"].setText(str(filters["minNotional"]))
            self._log(f"[GRID] filters loaded tickSize={filters['tickSize']}")
        except Exception:
            self.bal_card["Filters"].setText("ERROR")

    def on_calculate(self) -> None:
        try:
            s = self._collect_settings()
            self.rows = self.grid_engine.calculate_levels(s.upper_price, s.lower_price, s.budget_u, s.levels, s.profit_ticks)
        except ValueError as exc:
            self._log(f"[GRID] error: {exc}")
            return
        self._log(f"[GRID] calculated levels={s.levels}")
        self.status_card["Levels"].setText(str(len(self.rows)))
        self.levels_table.setRowCount(len(self.rows))
        for row_idx, row in enumerate(self.rows):
            vals = [str(row.level), f"{row.price:.2f}", row.side, f"{row.order_u:.4f}", f"{row.qty_btc:.8f}", row.status, f"{row.target_sell:.2f}", f"{row.expected_pnl:.8f}", "-"]
            for col, value in enumerate(vals):
                self.levels_table.setItem(row_idx, col, QTableWidgetItem(value))

        valid = len([r for r in self.rows if r.valid])
        invalid = len(self.rows) - valid
        self.summary_card["Range"].setText(f"{s.lower_price:.2f} .. {s.upper_price:.2f}")
        self.summary_card["Step"].setText(f"{(s.upper_price-s.lower_price)/(s.levels-1):.4f}")
        self.summary_card["Levels"].setText(str(s.levels))
        self.summary_card["Budget / Level"].setText(f"{s.budget_u/s.levels:.2f}")
        self.summary_card["Total Budget"].setText(f"{s.budget_u:.2f}")
        self.summary_card["Valid Levels"].setText(str(valid))
        self.summary_card["Invalid Levels"].setText(str(invalid))
        self.summary_card["Total Expected PnL"].setText(f"{sum(r.expected_pnl for r in self.rows):.6f}")

    def _refresh_dry_bands(self) -> None:
        bid = self.market_state.snapshot.bid
        ask = self.market_state.snapshot.ask
        if bid is None or ask is None:
            return
        mid = (bid + ask) / 2
        for idx, row in enumerate(self.rows):
            if row.price > ask:
                band = "ABOVE MARKET"
            elif row.price < bid:
                band = "BELOW MARKET"
            else:
                band = "NEAR MARKET"
            self.levels_table.setItem(idx, 8, QTableWidgetItem(band))

    def on_start_dry(self) -> None:
        self.status_card["State"].setText("DRY VIEW")
        self.dry_view_active = True
        self._log("[GRID] dry view started")
        self._log("[GRID] LIVE locked in v0.8.1 — dry-view only")
        self._log("[GRID] live locked")
        self._refresh_dry_bands()

    def on_stop(self) -> None:
        self.status_card["State"].setText("STOPPED")
        self.dry_view_active = False

    def save_settings(self) -> None:
        settings = self._collect_settings()
        GRID_SETTINGS_STORE.save(settings)
        self._log("[GRID] settings saved")

    def load_settings(self) -> None:
        settings = GRID_SETTINGS_STORE.load()
        settings.live_enabled = False
        self._apply_settings(settings)
        self._log("[GRID] settings loaded")
