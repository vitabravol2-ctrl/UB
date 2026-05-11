import time
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextOption
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget

from app.core.binance_account import BinanceAccountClient
from app.core.config import CONFIG, SETTINGS_STORE
from app.core.logger import format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
from app.gui.styles import main_qss
from app.gui.widgets import big_value, kv_card


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = SETTINGS_STORE.load()
        self.setWindowTitle("UB v0.1.7 / BTCU Microspread Terminal")
        self.resize(1400, 900)
        self.setStyleSheet(main_qss())
        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol, self.settings.max_ws_age_ms)
        self.account = BinanceAccountClient()
        self.api_status = "NOT SET"
        self.last_log_line = ""
        self.balances = {"BTC": {"free": 0.0, "locked": 0.0}, "U": {"free": 0.0, "locked": 0.0}}
        self.filters = {"loaded": False, "fallback": False, "tickSize": 0.0, "stepSize": 0.0, "minQty": 0.0, "minNotional": 0.0}
        self.orders_data = []
        self.runtime_active = False
        root = QWidget(); self.setCentralWidget(root); self.main_layout = QVBoxLayout(root)
        self.top_status = QLabel(); self.main_layout.addWidget(self.top_status)
        self.grid = QGridLayout(); self.main_layout.addLayout(self.grid, 1)
        self._build_cards(); self._build_controls(); self._build_logs()
        self.ws.signals.book.connect(self.on_ws_book); self.ws.signals.status.connect(self.on_ws_status); self.ws.signals.log.connect(self.log)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick); self.timer.start(300)
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(self.fetch_rest); self.rest_timer.start(self.settings.rest_poll_ms)
        self.account_timer = QTimer(self); self.account_timer.timeout.connect(self.refresh_account_data); self.account_timer.start(5000)

    def _build_cards(self) -> None:
        conn, self.conn = kv_card("CONNECTION", [("API", "NOT SET"), ("REST", "N/A"), ("WS", "OPTIONAL LOST"), ("Source", "NONE"), ("Offset", "0 ms")])
        self.grid.addWidget(conn, 0, 0)
        bid_box, self.bid_v = big_value("BID", "N/A"); ask_box, self.ask_v = big_value("ASK", "N/A"); spr_box, self.spr_v = big_value("SPREAD", "N/A")
        self.grid.addWidget(bid_box, 0, 1); self.grid.addWidget(ask_box, 0, 2); self.grid.addWidget(spr_box, 0, 3)
        spread, self.spread = kv_card("SPREAD ENGINE", [("Status", "IDLE"), ("Spread", "N/A"), ("Capture", "N/A"), ("Lifetime", "0ms"), ("Source", "NONE"), ("Update", "--")])
        runtime, self.runtime = kv_card("RUNTIME", [("LIVE", "OFF"), ("Require confirm", "YES"), ("Auto-cancel", "YES")])
        risk, self.risk = kv_card("RISK", [("lot_size", "0"), ("max_open_lots", "0"), ("max_daily_loss", "0"), ("max_exposure_u", "0"), ("panic_exit", "ON")])
        bal, self.bal = kv_card("BALANCES", [("BTC free", "0"), ("BTC locked", "0"), ("U free", "0"), ("U locked", "0"), ("Max buy", "0 BTC"), ("Max sell", "0 BTC")])
        fil, self.fil = kv_card("FILTERS", [("Filters", "NO"), ("tickSize", "0"), ("stepSize", "0"), ("minQty", "0"), ("minNotional", "0")])
        self.grid.addWidget(spread, 1, 0); self.grid.addWidget(runtime, 1, 1); self.grid.addWidget(risk, 1, 2); self.grid.addWidget(bal, 1, 3); self.grid.addWidget(fil, 2, 0, 1, 4)
        self.orders = QTableWidget(0, 7); self.orders.setHorizontalHeaderLabels(["Order ID", "Side", "Price", "Qty", "Filled", "Status", "Age"])
        box = QGroupBox("ORDERS (read-only)"); lay = QVBoxLayout(); lay.addWidget(self.orders); box.setLayout(lay)
        self.grid.addWidget(box, 3, 0, 1, 4)

    def _build_controls(self) -> None:
        row = QHBoxLayout(); self.settings_btn = QPushButton("НАСТРОЙКИ"); self.settings_btn.clicked.connect(self.open_settings_dialog); row.addWidget(self.settings_btn)
        self.start_stop_btn = QPushButton("START"); self.start_stop_btn.clicked.connect(self.toggle_runtime); row.addWidget(self.start_stop_btn)
        cancel_btn = QPushButton("ОТМЕНИТЬ ВСЁ"); cancel_btn.clicked.connect(self.cancel_all); row.addWidget(cancel_btn); self.main_layout.addLayout(row)

    def _build_logs(self) -> None:
        self.logs = QPlainTextEdit(); self.logs.setReadOnly(True); self.logs.setMaximumBlockCount(800); self.logs.setWordWrapMode(QTextOption.WrapAnywhere); self.main_layout.addWidget(self.logs, 1)

    def open_settings_dialog(self) -> None:
        d = QDialog(self); d.setWindowTitle("UB Settings"); d.setModal(True); d.resize(900, 700)
        lay = QVBoxLayout(d)
        g1 = QGroupBox("АККАУНТ BINANCE"); f1 = QFormLayout(g1)
        api_key_input = QLineEdit(); api_secret_input = QLineEdit(); api_secret_input.setEchoMode(QLineEdit.Password)
        show_secret = QCheckBox("show/hide"); show_secret.toggled.connect(lambda v: api_secret_input.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        test_btn = QPushButton("Проверить"); test_btn.clicked.connect(self.on_test_connection); save_btn = QPushButton("Сохранить"); save_btn.clicked.connect(lambda: self._save_api_fields(api_key_input.text(), api_secret_input.text(), show_secret, api_secret_input))
        f1.addRow("API key", api_key_input); f1.addRow("API secret", api_secret_input); f1.addRow("", show_secret); f1.addRow(test_btn, save_btn); f1.addRow("Status", QLabel(self.api_status))
        lay.addWidget(g1)
        self.settings_inputs = {}
        for title, fields in [("HARVEST SETTINGS", ["min_spread", "entry_offset", "exit_offset", "target_capture", "stop_loss", "max_hold_ms"]), ("RISK", ["lot_size", "max_open_lots", "max_daily_loss", "max_exposure_u", "panic_exit"]), ("LIVE SAFETY", ["live_enabled", "require_confirmation", "auto_cancel_on_stop"]), ("DATA", ["rest_poll_ms", "ws_optional_enabled", "max_ws_age_ms"])]:
            g = QGroupBox(title); f = QFormLayout(g)
            for key in fields:
                v = getattr(self.settings, key)
                if isinstance(v, bool):
                    w = QCheckBox(); w.setChecked(v)
                else:
                    w = QLineEdit(str(v))
                self.settings_inputs[key] = w
                f.addRow(key, w)
            lay.addWidget(g)
        btns = QHBoxLayout(); save = QPushButton("SAVE"); close = QPushButton("CLOSE"); save.clicked.connect(lambda: self._save_settings_dialog(d)); close.clicked.connect(d.close); btns.addWidget(save); btns.addWidget(close); lay.addLayout(btns)
        d.exec()

    def _save_api_fields(self, key: str, secret: str, show: QCheckBox, secret_input: QLineEdit) -> None:
        self.account.save_api_keys(key, secret)
        secret_input.clear(); show.setChecked(False)
        self.log("API", f"keys loaded key={self.account._mask_key(self.account.api_key)}")

    def _save_settings_dialog(self, dialog: QDialog) -> None:
        for key, widget in self.settings_inputs.items():
            old = getattr(self.settings, key)
            if isinstance(widget, QCheckBox):
                setattr(self.settings, key, widget.isChecked())
            elif isinstance(old, int):
                setattr(self.settings, key, int(float(widget.text())))
            elif isinstance(old, float):
                setattr(self.settings, key, float(widget.text()))
        SETTINGS_STORE.save(self.settings)
        self.rest_timer.setInterval(self.settings.rest_poll_ms)
        self.log("RUNTIME", "settings saved to config/settings.json")
        dialog.close()

    def toggle_runtime(self) -> None:
        self.runtime_active = not self.runtime_active
        if self.runtime_active: self.ws.start(); self.start_stop_btn.setText("STOP")
        else: self.ws.stop(); self.start_stop_btn.setText("START"); self.cancel_all()

    def cancel_all(self) -> None: self.log("ORDERS", "cancel all requested")
    def on_ws_book(self, bid: float, ask: float, ts: int) -> None: self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "WS"; self.state.last_ws_ms = ts
    def on_ws_status(self, status: str) -> None: self.state.ws_status = status
    def on_test_connection(self) -> None:
        status = self.account.test_account_connection(); self.api_status = status.status
        if status.status == "OK": self.log("API", "test account OK"); self.refresh_account_data(load_filters=True)
        else: self.log("API", f"account error {status.message}")

    def refresh_account_data(self, load_filters: bool = False) -> None:
        if self.api_status != "OK": return
        self.balances = self.account.get_account_balances()
        orders, issue = self.account.get_open_orders_safe(CONFIG.binance_symbol)
        self.orders_data = orders
        if issue: self.log("ORDERS", f"{CONFIG.binance_symbol} openOrders unsupported or empty")
        if load_filters or not self.filters.get("loaded"):
            self.filters = self.account.get_exchange_filters(CONFIG.binance_symbol)
            if self.filters.get("fallback"): self.log("FILTERS", "exchange filters unavailable -> using fallback defaults")

    def fetch_rest(self) -> None:
        try:
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.binance_symbol)
            self.state.last_rest_ms = ts; self.state.rest_status = "OK"; self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "REST"
        except Exception:
            self.state.rest_status = "ERROR"

    def on_tick(self) -> None: self._refresh_ui()

    def _fmt(self, v: float, n: int = 6) -> str:
        return f"{v:.{n}f}".rstrip("0").rstrip(".") if v else "0"

    def _refresh_ui(self) -> None:
        bid = self.state.snapshot.bid; ask = self.state.snapshot.ask; spread = self.state.snapshot.spread
        self.conn["API"].setText(self.api_status); self.conn["REST"].setText(self.state.rest_status); self.conn["WS"].setText("OK" if self.state.ws_status == "CONNECTED" else "OPTIONAL LOST"); self.conn["Source"].setText(self.state.snapshot.source); self.conn["Offset"].setText(f"{self.account.time_offset_ms} ms")
        self.bid_v.setText("N/A" if bid is None else f"{bid:.2f}"); self.ask_v.setText("N/A" if ask is None else f"{ask:.2f}"); self.spr_v.setText("N/A" if spread is None else f"{spread:.2f}")
        self.spread["Status"].setText("HOT" if spread else "IDLE"); self.spread["Spread"].setText("N/A" if spread is None else f"{spread:.2f}")
        cap = (spread - self.settings.entry_offset - self.settings.exit_offset) if spread is not None else None
        self.spread["Capture"].setText("N/A" if cap is None else f"{cap:.2f}")
        age_ms = max(int(time.time() * 1000) - self.state.snapshot.updated_ms, 0)
        self.spread["Lifetime"].setText(f"{age_ms}ms" if age_ms < 1000 else f"{age_ms/1000:.1f}s")
        self.spread["Source"].setText(self.state.snapshot.source); self.spread["Update"].setText(time.strftime("%H:%M:%S"))
        self.runtime["LIVE"].setText("ON" if self.settings.live_enabled else "OFF"); self.runtime["Require confirm"].setText("YES" if self.settings.require_confirmation else "NO"); self.runtime["Auto-cancel"].setText("YES" if self.settings.auto_cancel_on_stop else "NO")
        self.risk["lot_size"].setText(self._fmt(self.settings.lot_size, 6)); self.risk["max_open_lots"].setText(str(self.settings.max_open_lots)); self.risk["max_daily_loss"].setText(self._fmt(self.settings.max_daily_loss, 2)); self.risk["max_exposure_u"].setText(self._fmt(self.settings.max_exposure_u, 2)); self.risk["panic_exit"].setText("ON" if self.settings.panic_exit else "OFF")
        self.bal["BTC free"].setText(self._fmt(self.balances['BTC']['free'], 6)); self.bal["BTC locked"].setText(self._fmt(self.balances['BTC']['locked'], 6)); self.bal["U free"].setText(self._fmt(self.balances['U']['free'], 2)); self.bal["U locked"].setText(self._fmt(self.balances['U']['locked'], 2))
        max_buy = (self.balances["U"]["free"] / ask) if ask and ask > 0 else 0.0; max_sell = self.balances["BTC"]["free"]
        self.bal["Max buy"].setText(f"{self._fmt(max_buy, 6)} BTC"); self.bal["Max sell"].setText(f"{self._fmt(max_sell, 6)} BTC")
        self.fil["Filters"].setText("YES (fallback)" if self.filters.get("fallback") else ("YES" if self.filters["loaded"] else "NO"))
        for k in ["tickSize", "stepSize", "minQty", "minNotional"]: self.fil[k].setText(self._fmt(float(self.filters[k]), 6))
        self.orders.setRowCount(0)
        if not self.orders_data:
            self.orders.setRowCount(1); self.orders.setItem(0, 0, QTableWidgetItem("Нет открытых ордеров"))
        else:
            now = int(time.time() * 1000)
            for i, o in enumerate(self.orders_data):
                self.orders.insertRow(i); age = max((now - int(o.get("time", now))) // 1000, 0)
                vals = [str(o.get("orderId", "")), o.get("side", ""), str(o.get("price", "")), str(o.get("origQty", "")), str(o.get("executedQty", "")), o.get("status", ""), f"{age}s"]
                for c, v in enumerate(vals): self.orders.setItem(i, c, QTableWidgetItem(v))
        rest_txt = f"OK {max(int(time.time()*1000)-self.state.last_rest_ms,0)}ms" if self.state.last_rest_ms else self.state.rest_status
        ws_txt = "OK" if self.state.ws_status == "CONNECTED" else "OPTIONAL LOST"
        live_flag = "HOT" if self.runtime_active else "READY"
        self.top_status.setText(f"{CONFIG.display_symbol} | REST ● {rest_txt} | WS ● {ws_txt} | API ● {self.api_status} | {live_flag}")

    def log(self, tag: str, message: str) -> None:
        line = format_log(tag, message)
        if line.split("] ", 1)[-1] == self.last_log_line:
            return
        self.last_log_line = line.split("] ", 1)[-1]
        color = {"INFO": "#9CA3AF", "OK": "#22C55E", "WARNING": "#FACC15", "ERROR": "#EF4444"}.get(tag, "#9CA3AF")
        self.logs.setTextColor(QColor(color)); self.logs.appendPlainText(line)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop(); super().closeEvent(event)
