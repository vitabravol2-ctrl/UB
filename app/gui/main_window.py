import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import QCheckBox, QDockWidget, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.core.binance_account import BinanceAccountClient
from app.core.config import CONFIG
from app.core.logger import format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
from app.gui.styles import main_qss
from app.gui.widgets import big_value, kv_card


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UB v0.1.6 / BTCU Microspread Terminal")
        self.resize(1320, 860)
        self.setStyleSheet(main_qss())
        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol, CONFIG.max_ws_age_ms)
        self.account = BinanceAccountClient()
        self.api_status = "NOT SET"
        self.balances = {"BTC": {"free": 0.0, "locked": 0.0}, "U": {"free": 0.0, "locked": 0.0}}
        self.filters = {"loaded": False, "tickSize": 0.0, "stepSize": 0.0, "minQty": 0.0, "minNotional": 0.0}
        self.orders_data = []
        self.runtime_active = False
        root = QWidget(); self.setCentralWidget(root); self.main_layout = QVBoxLayout(root)
        self.top_status = QLabel(); self.main_layout.addWidget(self.top_status)
        self.grid = QGridLayout(); self.main_layout.addLayout(self.grid)
        self._build_cards(); self._build_controls(); self._build_logs(); self._build_settings_panel()
        self.ws.signals.book.connect(self.on_ws_book); self.ws.signals.status.connect(self.on_ws_status); self.ws.signals.log.connect(self.log)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick); self.timer.start(300)
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(self.fetch_rest); self.rest_timer.start(CONFIG.rest_poll_ms)
        self.account_timer = QTimer(self); self.account_timer.timeout.connect(self.refresh_account_data); self.account_timer.start(5000)

    def _build_cards(self) -> None:
        conn, self.conn = kv_card("ПОДКЛЮЧЕНИЕ", [("API", "NOT SET"), ("REST", "N/A"), ("WS", "OPTIONAL LOST"), ("Источник", "NONE"), ("Time offset", "0 ms")])
        self.grid.addWidget(conn, 0, 0)
        bid_box, self.bid_v = big_value("BID", "N/A"); ask_box, self.ask_v = big_value("ASK", "N/A"); spr_box, self.spr_v = big_value("SPREAD", "N/A")
        self.grid.addWidget(bid_box, 0, 1); self.grid.addWidget(ask_box, 0, 2); self.grid.addWidget(spr_box, 0, 3)
        bal, self.bal = kv_card("БАЛАНСЫ", [("BTC free", "0"), ("BTC locked", "0"), ("U free", "0"), ("U locked", "0"), ("Max buy", "0 BTC"), ("Max sell", "0 BTC")])
        fil, self.fil = kv_card("FILTERS", [("Filters", "NO"), ("tickSize", "0"), ("stepSize", "0"), ("minQty", "0"), ("minNotional", "0")])
        self.grid.addWidget(bal, 1, 0, 1, 2); self.grid.addWidget(fil, 1, 2, 1, 2)
        self.orders = QTableWidget(0, 7); self.orders.setHorizontalHeaderLabels(["Order ID", "Side", "Price", "Qty", "Filled", "Status", "Age"])
        box = QGroupBox("ОРДЕРА / POSITIONS (read-only)"); lay = QVBoxLayout(); lay.addWidget(self.orders); box.setLayout(lay)
        self.grid.addWidget(box, 2, 0, 1, 4)

    def _build_controls(self) -> None:
        row = QHBoxLayout(); self.settings_btn = QPushButton("НАСТРОЙКИ"); self.settings_btn.clicked.connect(self.toggle_settings); row.addWidget(self.settings_btn)
        self.start_stop_btn = QPushButton("START"); self.start_stop_btn.clicked.connect(self.toggle_runtime); row.addWidget(self.start_stop_btn)
        cancel_btn = QPushButton("ОТМЕНИТЬ ВСЁ"); cancel_btn.clicked.connect(self.cancel_all); row.addWidget(cancel_btn); self.main_layout.addLayout(row)

    def _build_settings_panel(self) -> None:
        self.settings_dock = QDockWidget("НАСТРОЙКИ", self); self.settings_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        w = QWidget(); lay = QVBoxLayout(w)
        account = QGroupBox("АККАУНТ BINANCE"); form = QFormLayout()
        self.api_key_input = QLineEdit(); self.api_secret_input = QLineEdit(); self.api_secret_input.setEchoMode(QLineEdit.Password)
        self.show_secret = QCheckBox("show secret"); self.show_secret.toggled.connect(lambda v: self.api_secret_input.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        self.test_api_btn = QPushButton("Проверить"); self.test_api_btn.clicked.connect(self.on_test_connection)
        self.save_api_btn = QPushButton("Сохранить в .env"); self.save_api_btn.clicked.connect(self.on_save_api)
        form.addRow("API key", self.api_key_input); form.addRow("API secret", self.api_secret_input); form.addRow("", self.show_secret); form.addRow(self.test_api_btn, self.save_api_btn)
        account.setLayout(form)
        mode = QGroupBox("LIVE SAFETY"); m = QFormLayout(); m.addRow("LIVE", QLabel("OFF")); m.addRow("REQUIRE_CONFIRMATION", QLabel("TRUE")); m.addRow("AUTO_CANCEL_ON_STOP", QLabel("TRUE")); mode.setLayout(m)
        lay.addWidget(account); lay.addWidget(mode); self.settings_dock.setWidget(w); self.addDockWidget(Qt.RightDockWidgetArea, self.settings_dock); self.settings_dock.hide()

    def _build_logs(self) -> None:
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setWordWrapMode(QTextOption.WrapAnywhere); self.main_layout.addWidget(self.logs)

    def toggle_settings(self) -> None: self.settings_dock.setVisible(not self.settings_dock.isVisible())
    def toggle_runtime(self) -> None:
        self.runtime_active = not self.runtime_active
        if self.runtime_active: self.ws.start(); self.start_stop_btn.setText("STOP")
        else: self.ws.stop(); self.start_stop_btn.setText("START"); self.cancel_all()
    def cancel_all(self) -> None: self.log("ORDERS", "cancel all requested")
    def on_ws_book(self, bid: float, ask: float, ts: int) -> None: self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "WS"; self.state.last_ws_ms = ts
    def on_ws_status(self, status: str) -> None: self.state.ws_status = status

    def on_save_api(self) -> None:
        self.account.save_api_keys(self.api_key_input.text(), self.api_secret_input.text())
        self.api_secret_input.clear(); self.show_secret.setChecked(False)
        self.log("API", f"keys loaded key={self.account._mask_key(self.account.api_key)}")

    def on_test_connection(self) -> None:
        status = self.account.test_account_connection(); self.api_status = status.status
        if status.status == "OK":
            self.log("TIME", f"server offset={self.account.time_offset_ms}ms")
            self.log("API", "test account OK")
            self.refresh_account_data(load_filters=True)
        else:
            self.log("API", f"account error {status.message}")

    def refresh_account_data(self, load_filters: bool = False) -> None:
        if self.api_status != "OK":
            return
        try:
            self.balances = self.account.get_account_balances(); self.orders_data = self.account.get_open_orders(CONFIG.binance_symbol)
            self.log("BALANCE", f"BTC free={self.balances['BTC']['free']} locked={self.balances['BTC']['locked']}")
            self.log("BALANCE", f"U free={self.balances['U']['free']} locked={self.balances['U']['locked']}")
            self.log("ORDERS", f"open orders n={len(self.orders_data)}")
            if load_filters or not self.filters.get("loaded"):
                self.filters = self.account.get_exchange_filters(CONFIG.binance_symbol)
                self.log("FILTERS", f"tickSize={self.filters['tickSize']} stepSize={self.filters['stepSize']} minQty={self.filters['minQty']} minNotional={self.filters['minNotional']}")
        except Exception as exc:
            self.api_status = "ERROR"; self.log("API", f"account error {exc}")

    def fetch_rest(self) -> None:
        try:
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.binance_symbol)
            self.state.last_rest_ms = ts; self.state.rest_status = "OK"; self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "REST"
        except Exception:
            self.state.rest_status = "ERROR"

    def on_tick(self) -> None: self._refresh_ui()

    def _refresh_ui(self) -> None:
        bid = self.state.snapshot.bid; ask = self.state.snapshot.ask; spread = self.state.snapshot.spread
        self.conn["API"].setText(self.api_status); self.conn["REST"].setText(self.state.rest_status); self.conn["WS"].setText("OPTIONAL LOST" if self.state.ws_status != "CONNECTED" else "OK"); self.conn["Источник"].setText(self.state.snapshot.source); self.conn["Time offset"].setText(f"{self.account.time_offset_ms} ms")
        self.bid_v.setText("N/A" if bid is None else f"{bid:.2f}"); self.ask_v.setText("N/A" if ask is None else f"{ask:.2f}"); self.spr_v.setText("N/A" if spread is None else f"{spread:.2f}")
        self.bal["BTC free"].setText(str(self.balances["BTC"]["free"])); self.bal["BTC locked"].setText(str(self.balances["BTC"]["locked"])); self.bal["U free"].setText(str(self.balances["U"]["free"])); self.bal["U locked"].setText(str(self.balances["U"]["locked"]))
        max_buy = (self.balances["U"]["free"] / ask) if ask and ask > 0 else 0.0; max_sell = self.balances["BTC"]["free"]
        self.bal["Max buy"].setText(f"{max_buy:.8f} BTC"); self.bal["Max sell"].setText(f"{max_sell:.8f} BTC")
        self.fil["Filters"].setText("YES" if self.filters["loaded"] else "NO")
        for k in ["tickSize", "stepSize", "minQty", "minNotional"]: self.fil[k].setText(str(self.filters[k]))
        self.orders.setRowCount(0)
        if not self.orders_data:
            self.orders.setRowCount(1); self.orders.setItem(0, 0, QTableWidgetItem("Нет открытых ордеров")); return
        now = int(time.time() * 1000)
        for i, o in enumerate(self.orders_data):
            self.orders.insertRow(i)
            age = max((now - int(o.get("time", now))) // 1000, 0)
            vals = [str(o.get("orderId", "")), o.get("side", ""), str(o.get("price", "")), str(o.get("origQty", "")), str(o.get("executedQty", "")), o.get("status", ""), f"{age}s"]
            for c, v in enumerate(vals): self.orders.setItem(i, c, QTableWidgetItem(v))
        self.top_status.setText(f"{CONFIG.display_symbol} | API {self.api_status} | REST {self.state.rest_status} | LIVE OFF")

    def log(self, tag: str, message: str) -> None:
        self.logs.appendPlainText(format_log(tag, message))

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop(); super().closeEvent(event)
