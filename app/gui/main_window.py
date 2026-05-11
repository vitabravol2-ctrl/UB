import time
from dataclasses import dataclass
from decimal import Decimal
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QHeaderView

from app.core.binance_account import BinanceAPIError, BinanceAccountClient
from app.core.config import CONFIG, SETTINGS_STORE
from app.core.logger import FileLogManager, format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.trade_math import TradeMathEngine
from app.core.market_ws import MarketWSClient
from app.gui.styles import main_qss
from app.gui.widgets import big_value, kv_card


@dataclass
class LiveOrder:
    orderId: int
    clientOrderId: str
    side: str
    price: float
    origQty: float
    executedQty: float
    remainingQty: float
    fillPercent: float
    status: str
    type: str
    timeInForce: str
    ageMs: int
    updateTime: int
    source: str


@dataclass
class FillRecord:
    time: int
    order_id: int
    side: str
    avg_price: float
    executed_qty: float
    quote_qty: float
    status: str


@dataclass
class TradeHistoryRow:
    time: int
    status: str
    buy_avg_price: float
    sell_avg_price: float
    qty: float
    pnl_u: float
    duration_ms: int
    buy_order_id: int
    sell_order_id: int


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = SETTINGS_STORE.load()
        self.setWindowTitle("UB v0.4.0 / BTCU Trading Cockpit")
        self.resize(1600, 900)
        self.setMinimumSize(1280, 760)
        self.setStyleSheet(main_qss())
        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol, self.settings.max_ws_age_ms)
        self.account = BinanceAccountClient()
        self.account.debug_api_logs = self.settings.debug_api_logs
        self.api_status = "NOT SET"
        self.last_log_line = ""
        self.logged_flags: set[str] = set()
        self.balances = {"BTC": {"free": 0.0, "locked": 0.0}, "U": {"free": 0.0, "locked": 0.0}}
        self.filters = {"loaded": False, "fallback": False, "tickSize": 0.0, "stepSize": 0.0, "minQty": 0.0, "minNotional": 0.0}
        self.orders_data = []
        self.canceled_attempts = {"canceled_buy": 0, "canceled_sell": 0, "timeout_buy": 0, "timeout_sell": 0}
        self.runtime_active = False
        self.fsm_state = "IDLE"
        self.active_order = {}
        self.position_qty = 0.0
        self.position_entry_avg = 0.0
        self.position_buy_order_id = 0
        self.position_sell_order_id = 0
        self.position_state = "FLAT"
        self.buy_filled_qty = 0.0
        self.avg_entry = 0.0
        self.avg_exit = 0.0
        self.realized_u = 0.0
        self.entry_started_ms = 0
        self.exit_started_ms = 0
        self.reprice_done = False
        self.sell_reprice_count = 0
        self.last_sell_reprice_ms = 0
        self.exit_mode = "NORMAL"
        self.trade_math = TradeMathEngine()
        self.last_plan_status = ""
        self.ready_since_ms = 0
        self.plan_stable_ms = 400
        self.plan_ready_streak = 0
        self.last_plan_log_ms = 0
        self.last_plan_log_key = ""
        self.order_retry_blocked_until_ms = 0
        self.sync_open_orders: list[dict] = []
        self.live_orders: list[LiveOrder] = []
        self.last_sync_log_ms = 0
        self.last_open_orders_n = -1
        self.last_active_status = ""
        self.last_open_orders_sync_ms = 0
        self.active_orders_signature = ""
        self.session_started_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self.closed_cycles = 0
        self.wins = 0
        self.losses = 0
        self.session_realized_pnl = 0.0
        self.last_pnl = 0.0
        self.canceled_buys = 0
        self.sell_timeouts = 0
        self.file_logs = FileLogManager()
        self.pending_gui_logs = {"trade": [], "system": []}
        self.summary_signature = ""
        self.last_ws_live_log_ms = 0

        root = QWidget(); self.setCentralWidget(root); self.main_layout = QVBoxLayout(root)
        self.top_status = QLabel(); self.top_status.setObjectName("topStatus"); self.main_layout.addWidget(self.top_status)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(8); self.grid.setVerticalSpacing(8); self.main_layout.addLayout(self.grid, 1)
        self._build_cards(); self._build_controls(); self._build_logs()

        self.ws.signals.book.connect(self.on_ws_book); self.ws.signals.status.connect(self.on_ws_status); self.ws.signals.log.connect(self.log)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick); self.timer.start(300)
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(self.fetch_rest); self.rest_timer.start(self.settings.rest_poll_ms)
        self.account_timer = QTimer(self); self.account_timer.timeout.connect(self.refresh_account_data); self.account_timer.start(self.settings.balances_poll_ms)
        self.active_sync_timer = QTimer(self); self.active_sync_timer.timeout.connect(self.sync_active_order); self.active_sync_timer.start(self.settings.active_order_poll_ms)
        self.on_test_connection(silent=True)

    def _build_cards(self) -> None:
        conn, self.conn = kv_card("CONNECTION", [("API", "NOT SET"), ("REST", "N/A"), ("WS", "OPTIONAL LOST"), ("Источник", "NONE"), ("Обновление", "0 ms")])
        self.conn_box = conn
        self.grid.addWidget(conn, 0, 0)

        bid_box, self.bid_v = big_value("BID", "N/A"); ask_box, self.ask_v = big_value("ASK", "N/A"); spr_box, self.spr_v = big_value("SPREAD", "N/A")
        self.bid_box = bid_box; self.ask_box = ask_box; self.spr_box = spr_box
        self.grid.addWidget(bid_box, 0, 1); self.grid.addWidget(ask_box, 0, 2); self.grid.addWidget(spr_box, 0, 3)

        spread, self.spread = kv_card("SPREAD ENGINE", [("Статус", "BAD"), ("Spread", "N/A"), ("Capture", "N/A"), ("Lifetime", "0ms"), ("Источник", "NONE"), ("Обновление", "--")])
        self.spread_box = spread
        plan, self.plan = kv_card("TRADE PLAN", [("Status", "NO_DATA"), ("Entry", "N/A"), ("Exit", "N/A"), ("Qty BTC", "0"), ("Order U", "0"), ("Profit U", "N/A"), ("Age", "0ms")])
        plan.setMinimumHeight(320)
        self.plan_box = plan
        runtime, self.runtime = kv_card("RUNTIME", [("LIVE", "OFF"), ("FSM", "IDLE"), ("Mode", "ANALYTICS"), ("Active order", "none"), ("Position state", "FLAT"), ("Position qty", "0"), ("Entry avg", "0"), ("Треб. подтверждение", "YES"), ("Авто-отмена", "YES")])
        self.runtime_box = runtime
        risk, self.risk = kv_card("RISK", [("Order size U", "0"), ("Max exposure U", "0"), ("panic", "ON")])
        self.risk_box = risk
        bal, self.bal = kv_card("BALANCES", [("BTC свободно", "0"), ("BTC lock", "0"), ("U свободно", "0"), ("U lock", "0"), ("Max buy", "0 BTC"), ("Max sell", "0 BTC")])
        self.grid.addWidget(spread, 1, 0); self.grid.addWidget(plan, 1, 1); self.grid.addWidget(runtime, 1, 2); self.grid.addWidget(bal, 1, 3); self.grid.addWidget(risk, 2, 0, 1, 1)

        summary, self.summary = kv_card("SESSION RESULT", [("Started at", self.session_started_at), ("Position state", "FLAT"), ("Position qty", "0"), ("Entry avg", "0"), ("Closed cycles", "0"), ("Wins", "0"), ("Losses", "0"), ("Realized PnL", "0"), ("Last PnL", "0"), ("Winrate", "0%"), ("Canceled buys", "0"), ("Sell timeouts", "0"), ("SELL reprices", "0"), ("Current exit mode", "NORMAL"), ("Active order", "none")])
        summary.setMinimumHeight(190)
        self.grid.addWidget(summary, 2, 1, 1, 3)

        self.compact_status = QLabel("")
        self.compact_status.setObjectName("topStatus")
        self.main_layout.addWidget(self.compact_status)

    def _build_controls(self) -> None:
        row = QHBoxLayout()
        self.settings_btn = QPushButton("НАСТРОЙКИ"); self.settings_btn.setProperty("kind", "neutral"); self.settings_btn.clicked.connect(self.open_settings_dialog); row.addWidget(self.settings_btn)
        self.start_stop_btn = QPushButton("START"); self.start_stop_btn.setProperty("kind", "start"); self.start_stop_btn.clicked.connect(self.toggle_runtime); row.addWidget(self.start_stop_btn)
        self.cancel_btn = QPushButton("ОТМЕНИТЬ ВСЁ"); self.cancel_btn.setProperty("kind", "danger"); self.cancel_btn.clicked.connect(self.cancel_all); row.addWidget(self.cancel_btn)
        self.main_layout.addLayout(row)

    def _build_logs(self) -> None:
        self.log_tabs = QTabWidget()
        self.trade_logs = QTextEdit(); self.trade_logs.setReadOnly(True); self.trade_logs.document().setMaximumBlockCount(300)
        self.system_logs = QTextEdit(); self.system_logs.setReadOnly(True); self.system_logs.document().setMaximumBlockCount(300)
        self.log_tabs.addTab(self.trade_logs, "Торговля")
        self.log_tabs.addTab(self.system_logs, "Система")
        self.log_tabs.setMinimumHeight(180)
        self.main_layout.addWidget(self.log_tabs)

    def open_settings_dialog(self) -> None:
        d = QDialog(self); d.setWindowTitle("Настройки UB"); d.setModal(True); d.resize(760, 620)
        lay = QVBoxLayout(d)
        tabs = QTabWidget(); lay.addWidget(tabs)
        self.settings_inputs = {}

        labels = {"order_size_u": "Размер сделки U", "max_exposure_u": "Макс. экспозиция U", "max_daily_loss": "Макс. дневной убыток U", "max_open_lots": "Max open lots", "panic_exit": "Panic exit", "live_enabled": "LIVE enabled", "require_confirmation": "Require confirmation", "auto_cancel_on_stop": "Auto cancel on stop", "max_live_exposure_u": "Live max exposure U", "open_orders_poll_ms": "openOrders interval ms", "all_orders_poll_ms": "allOrders interval ms", "balances_poll_ms": "balances interval ms", "debug_api_logs": "API debug logs"}

        account_tab = QWidget(); account_form = QFormLayout(account_tab)
        api_key_input = QLineEdit(); api_secret_input = QLineEdit(); api_secret_input.setEchoMode(QLineEdit.Password)
        show_secret = QCheckBox("Показать secret")
        show_secret.toggled.connect(lambda v: api_secret_input.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        test_btn = QPushButton("Проверить API"); test_btn.clicked.connect(self.on_test_connection)
        save_api_btn = QPushButton("Сохранить ключи"); save_api_btn.clicked.connect(lambda: self._save_api_fields(api_key_input.text(), api_secret_input.text(), show_secret, api_secret_input))
        account_form.addRow("API key", api_key_input); account_form.addRow("API secret", api_secret_input); account_form.addRow("", show_secret); account_form.addRow(test_btn, save_api_btn); account_form.addRow("Статус", QLabel(self.api_status))
        tabs.addTab(account_tab, "Аккаунт")

        tab_map = [("Harvest", ["min_spread", "entry_offset", "exit_offset", "target_capture", "stop_loss", "max_hold_ms"]), ("Risk", ["order_size_u", "max_exposure_u", "max_daily_loss", "max_open_lots", "panic_exit", "max_live_exposure_u"]), ("Data", ["rest_poll_ms", "open_orders_poll_ms", "all_orders_poll_ms", "balances_poll_ms", "debug_api_logs", "ws_optional_enabled", "max_ws_age_ms"]), ("Safety", ["live_enabled", "require_confirmation", "auto_cancel_on_stop", "entry_timeout_ms", "exit_timeout_ms", "panic_reprice_once", "aggressive_exit_offset", "max_sell_reprices"])]
        for title, fields in tab_map:
            w = QWidget(); f = QFormLayout(w)
            for key in fields:
                v = getattr(self.settings, key)
                inp = QCheckBox() if isinstance(v, bool) else QLineEdit(str(v))
                if isinstance(inp, QCheckBox): inp.setChecked(v)
                self.settings_inputs[key] = inp
                f.addRow(labels.get(key, key), inp)
            if title == "Data":
                source_name = "WS" if self.state.ws_status == "OK" else self.state.snapshot.source
                pair_info = QLabel(f"Pair info: tick={self._fmt(float(self.filters.get('tickSize', 0.0)), 5)} | step={self._fmt(float(self.filters.get('stepSize', 0.0)), 5)} | minNotional={self._fmt(float(self.filters.get('minNotional', 0.0)), 2)} | source={source_name}")
                pair_info.setWordWrap(True)
                f.addRow("Pair Info", pair_info)
            tabs.addTab(w, title)

        btns = QHBoxLayout(); save = QPushButton("SAVE"); close = QPushButton("CLOSE"); save.clicked.connect(lambda: self._save_settings_dialog(d)); close.clicked.connect(d.close); btns.addWidget(save); btns.addWidget(close); lay.addLayout(btns)
        d.exec()

    def _save_api_fields(self, key: str, secret: str, show: QCheckBox, secret_input: QLineEdit) -> None:
        self.account.save_api_keys(key, secret)
        secret_input.clear(); show.setChecked(False)
        self.log("INFO", f"API keys loaded key={self.account._mask_key(self.account.api_key)}")
        self.on_test_connection(silent=True)

    def _save_settings_dialog(self, dialog: QDialog) -> None:
        for key, widget in self.settings_inputs.items():
            old = getattr(self.settings, key)
            if isinstance(widget, QCheckBox): setattr(self.settings, key, widget.isChecked())
            elif isinstance(old, int): setattr(self.settings, key, int(float(widget.text())))
            elif isinstance(old, float): setattr(self.settings, key, float(widget.text()))
        SETTINGS_STORE.save(self.settings); self.rest_timer.setInterval(self.settings.rest_poll_ms); self.account_timer.setInterval(self.settings.balances_poll_ms); self.account.debug_api_logs = self.settings.debug_api_logs; self.log("OK", "settings saved")
        dialog.close()

    def toggle_runtime(self) -> None:
        self.runtime_active = not self.runtime_active
        if self.runtime_active:
            self.ws.start(); self.start_stop_btn.setText("STOP"); self.start_stop_btn.setProperty("kind", "stop"); self.log("OK", "START")
            if self.position_state == "EXIT_FAILED" and self.position_qty > 0:
                self.log("WARNING", f"[EXEC] RECOVER EXIT position_qty={self.position_qty:.6f}")
                self.fsm_state = "PLACE_SELL"
        else: self.ws.stop(); self.start_stop_btn.setText("START"); self.start_stop_btn.setProperty("kind", "start"); self.cancel_all(); self.log("WARNING", "STOP")
        self.start_stop_btn.style().polish(self.start_stop_btn)

    def cancel_all(self) -> None:
        self.log("WARNING", "cancel all requested")
        if self.active_order.get("orderId"):
            try:
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            except Exception:
                pass
            self.sync_active_order(force=True)
        self.active_order = {}
        self.fsm_state = "IDLE"
        self.runtime_active = False
        if self.position_qty > 0:
            self.position_state = "EXIT_FAILED"
    def refresh_orders_manual(self) -> None:
        self.sync_active_order(force=True)

    def _to_live_order(self, order: dict, source: str) -> LiveOrder:
        now = int(time.time() * 1000)
        orig_qty = float(order.get("origQty", 0.0) or 0.0)
        exec_qty = float(order.get("executedQty", 0.0) or 0.0)
        remaining = max(orig_qty - exec_qty, 0.0)
        fill_pct = 100.0 if orig_qty <= 0 else min(max((exec_qty / orig_qty) * 100.0, 0.0), 100.0)
        update_time = int(order.get("updateTime") or order.get("time") or now)
        return LiveOrder(int(order.get("orderId", 0) or 0), str(order.get("clientOrderId", "")), str(order.get("side", "")), float(order.get("price", 0.0) or 0.0), orig_qty, exec_qty, remaining, fill_pct, str(order.get("status", "UNKNOWN")), str(order.get("type", "LIMIT")), str(order.get("timeInForce", "")), max(now - int(order.get("time", update_time) or update_time), 0), update_time, source)

    def sync_active_order(self, force: bool = False) -> None:
        if self.api_status != "OK":
            return
        try:
            now_ms = int(time.time() * 1000)
            if force or now_ms - self.last_open_orders_sync_ms >= self.settings.open_orders_poll_ms:
                self.sync_open_orders = self.account.get_open_orders(CONFIG.binance_symbol)
                self.last_open_orders_sync_ms = now_ms
            open_orders_n = len(self.sync_open_orders)
            if force:
                self.last_sync_log_ms = int(time.time() * 1000)
            self.last_open_orders_n = open_orders_n
            if self.active_order.get("orderId"):
                st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                status = st.get("status", "UNKNOWN")
                self.active_order["state"] = status
                self.last_active_status = status
                if status == "FILLED":
                    self._apply_filled_from_sync(st)
                elif status in {"CANCELED", "REJECTED", "EXPIRED"}:
                    self.log("WARNING", "[SYNC] desync fixed")
                    self.active_order = {}
                    self.fsm_state = "DONE"
            self._rebuild_live_orders()
        except Exception:
            pass

    def _apply_filled_from_sync(self, order_status: dict) -> None:
        side = self.active_order.get("side")
        self.log("OK", "[SYNC] order filled detected")
        if side == "BUY":
            self.log("OK", "[SYNC] BUY filled on exchange")
            self.position_qty = float(order_status.get("executedQty", 0.0) or 0.0)
            if self.position_qty <= 0:
                self.log("WARNING", "[EXEC] BLOCK reason=buy_filled_zero_qty")
                self.position_state = "FLAT"
                self.fsm_state = "ERROR"
                self.active_order = {}
                return
            self.buy_filled_qty = self.position_qty
            buy_quote = float(order_status.get("cummulativeQuoteQty", 0.0) or 0.0)
            self.avg_entry = (buy_quote / self.position_qty) if self.position_qty > 0 else float(self.active_order.get("price", 0.0))
            self.position_entry_avg = self.avg_entry
            self.position_buy_order_id = int(self.active_order.get("orderId", 0) or 0)
            self.position_state = "POSITION_OPEN"
            self.fsm_state = "PLACE_SELL"
        elif side == "SELL":
            self.log("OK", "[SYNC] SELL filled on exchange")
            sell_qty = float(order_status.get("executedQty", 0.0) or 0.0)
            sell_quote = float(order_status.get("cummulativeQuoteQty", 0.0) or 0.0)
            self.avg_exit = (sell_quote / sell_qty) if sell_qty > 0 else float(self.active_order.get("price", 0.0))
            self.realized_u = sell_quote - (self.position_entry_avg * sell_qty)
            self.log("OK", f"[EXEC] REALIZED pnl={self.realized_u:+.6f}")
            self._apply_session_pnl(self.realized_u)
            self.position_qty = max(self.position_qty - sell_qty, 0.0)
            self.position_state = "FLAT" if self.position_qty <= 0 else "POSITION_OPEN"
            self.fsm_state = "DONE"
        self.active_order = {}

    def _rebuild_live_orders(self) -> None:
        rows: list[LiveOrder] = [self._to_live_order(o, "openOrders") for o in self.sync_open_orders]
        if self.active_order.get("orderId") and int(self.active_order["orderId"]) not in {x.orderId for x in rows}:
            side = str(self.active_order.get("side", ""))
            state = str(self.active_order.get("state", "NEW"))
            if state in {"NEW", "PARTIALLY_FILLED"}:
                rows.append(self._to_live_order({
                    "orderId": self.active_order.get("orderId"),
                    "side": side,
                    "price": self.active_order.get("price", 0.0),
                    "origQty": self.active_order.get("qty", 0.0),
                    "executedQty": 0.0,
                    "status": state,
                    "type": self.active_order.get("type", "LIMIT"),
                    "time": self.active_order.get("create_ms", int(time.time() * 1000)),
                    "updateTime": int(time.time() * 1000),
                }, "active_order"))
        self.live_orders = sorted([o for o in rows if o.status in {"NEW", "PARTIALLY_FILLED"}], key=lambda x: x.updateTime, reverse=True)

    def cancel_order_by_id(self, order_id: int) -> None:
        try:
            self.account.cancel_order(CONFIG.binance_symbol, order_id)
            self.log("OK", f"[EXEC] CANCEL orderId={order_id}")
            self.sync_active_order(force=True); self.refresh_account_data()
        except Exception as exc:
            self.log("ERROR", f"[SYNC] cancel failed orderId={order_id} err={exc}")

    def on_ws_book(self, bid: float, ask: float, ts: int) -> None: self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "WS"; self.state.last_ws_ms = ts
    def on_ws_status(self, status: str) -> None:
        self.state.ws_status = status

    def _apply_session_pnl(self, pnl: float) -> None:
        self.session_realized_pnl += pnl
        self.closed_cycles += 1
        self.last_pnl = pnl
        if pnl > 0:
            self.wins += 1
        elif pnl < 0:
            self.losses += 1

    def on_test_connection(self, silent: bool = False) -> None:
        status = self.account.test_account_connection(); self.api_status = status.status
        if status.status == "OK":
            if not silent: self.log("OK", "API connected")
            self.refresh_account_data(load_filters=True)
        elif not silent:
            self.log("ERROR", f"API error {status.message}")

    def refresh_account_data(self, load_filters: bool = False) -> None:
        if self.api_status != "OK": return
        self.balances = self.account.get_account_balances()
        if load_filters or not self.filters.get("loaded"):
            self.filters = self.account.get_exchange_filters(CONFIG.binance_symbol)
            self.log("WARNING" if self.filters.get("fallback") else "OK", "filters loaded fallback" if self.filters.get("fallback") else "filters loaded")

    def fetch_rest(self) -> None:
        try:
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.binance_symbol)
            if self.state.rest_status == "ERROR": self.log("OK", "REST restored")
            self.state.last_rest_ms = ts; self.state.rest_status = "OK"; self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "REST"
        except Exception:
            if self.state.rest_status != "ERROR": self.log("ERROR", "REST lost")
            self.state.rest_status = "ERROR"

    def on_tick(self) -> None: self._refresh_ui()
    def _fmt(self, v: float, n: int = 6) -> str: return f"{v:.{n}f}".rstrip("0").rstrip(".") if v else "0"


    def _inc_canceled_attempt(self, key: str) -> None:
        if key in self.canceled_attempts:
            self.canceled_attempts[key] += 1

    def _normalize_qty(self, qty: float) -> float:
        step = float(self.filters.get("stepSize", 0.0) or 0.0)
        if step <= 0:
            return qty
        return int(qty / step) * step

    def _refresh_ui(self) -> None:
        bid = self.state.snapshot.bid; ask = self.state.snapshot.ask; spread = self.state.snapshot.spread
        spread_state = "BAD" if spread is None else ("HOT" if spread >= self.settings.min_spread + 0.02 else ("READY" if spread >= self.settings.min_spread else "WATCH"))
        ws_age = self.state.age_ms(self.state.last_ws_ms)
        ws_ok = ws_age is not None and ws_age <= self.settings.max_ws_age_ms and self.state.ws_status == "CONNECTED"
        ws_text = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        self.conn["API"].setText(self.api_status); self.conn["REST"].setText(self.state.rest_status); self.conn["WS"].setText(ws_text); self.conn["Источник"].setText("WS" if ws_ok else self.state.snapshot.source); self.conn["Обновление"].setText(f"{self.account.time_offset_ms} ms")
        self.bid_v.setText("N/A" if bid is None else f"{bid:.2f}"); self.ask_v.setText("N/A" if ask is None else f"{ask:.2f}"); self.spr_v.setText("N/A" if spread is None else f"{spread:.2f}")
        self.spread["Статус"].setText(spread_state); self.spread["Spread"].setText("N/A" if spread is None else f"{spread:.2f}")
        cap = (spread - self.settings.entry_offset - self.settings.exit_offset) if spread is not None else None
        self.spread["Capture"].setText("N/A" if cap is None else f"{cap:.2f}")
        age_ms = max(int(time.time() * 1000) - self.state.snapshot.updated_ms, 0)
        self.spread["Lifetime"].setText(f"{age_ms}ms" if age_ms < 1000 else f"{age_ms/1000:.1f}s")
        self.spread["Источник"].setText(self.state.snapshot.source); self.spread["Обновление"].setText(time.strftime("%H:%M:%S"))
        self.runtime["LIVE"].setText("ON" if self.settings.live_enabled else "OFF")
        self.runtime["FSM"].setText(self.fsm_state)
        self.runtime["Mode"].setText("LIVE SINGLE" if self.settings.live_enabled else "ANALYTICS")
        self.runtime["Position state"].setText(self.position_state)
        self.runtime["Position qty"].setText(self._fmt(self.position_qty, 6))
        self.runtime["Entry avg"].setText(self._fmt(self.position_entry_avg, 6))
        if self.active_order.get("orderId"):
            self.runtime["Active order"].setText(f"{self.active_order.get('side','-')} {self._fmt(float(self.active_order.get('qty',0.0)),6)} @ {self._fmt(float(self.active_order.get('price',0.0)),2)} {self.active_order.get('state','NEW')}")
        else:
            self.runtime["Active order"].setText("none")
        plan = self.trade_math.build_plan(self.state, self.settings, self.filters, self.balances, self.api_status)
        now_ms = int(time.time() * 1000)
        plan_status = plan.status
        if plan_status in {"READY", "HOT"}:
            self.plan_ready_streak += 1
            if self.ready_since_ms == 0:
                self.ready_since_ms = now_ms
            ready_age = now_ms - self.ready_since_ms
            if ready_age < self.plan_stable_ms and self.plan_ready_streak < 2:
                plan_status = "WARMUP"
        else:
            self.ready_since_ms = 0
            self.plan_ready_streak = 0
            ready_age = 0

        self.plan["Status"].setText(plan_status)
        self.plan["Entry"].setText("N/A" if plan.entry_price is None else f"{plan.entry_price:.2f}")
        self.plan["Exit"].setText("N/A" if plan.exit_price is None else f"{plan.exit_price:.2f}")
        self.plan["Qty BTC"].setText(self._fmt(plan.qty_btc, 6))
        self.plan["Order U"].setText(self._fmt(plan.order_size_u, 2))
        self.plan["Age"].setText(f"{ready_age}ms")
        self.plan["Profit U"].setText("N/A" if plan.expected_profit_u is None else self._fmt(plan.expected_profit_u, 6))
        plan_key = f"{plan.status}:{self._fmt(plan.order_size_u,2)}:{self._fmt(plan.qty_btc,6)}:{self._fmt(plan.required_u or 0.0,2)}"
        if self.runtime_active and self.fsm_state == "DONE":
            self.fsm_state = "WAIT_READY"
        if self.runtime_active and self.fsm_state == "ERROR" and now_ms >= self.order_retry_blocked_until_ms:
            self.fsm_state = "DONE"
        if self.runtime_active and self.fsm_state == "IDLE":
            self.log("INFO", f"[EXEC] LIVE {'ON' if self.settings.live_enabled else 'OFF'}")
            self.log("INFO", "[EXEC] WAIT READY")
            self.fsm_state = "WAIT_READY"
        market_valid = ws_ok or self.state.rest_status == "OK"
        if self.runtime_active and self.fsm_state == "WAIT_READY" and self.settings.live_enabled and plan.status in {"READY", "HOT"} and market_valid and plan.balance_ok and plan.filters_ok and (plan.required_u or 0.0) <= self.settings.max_live_exposure_u:
            if self.active_order.get("orderId") or self.position_qty > 0 or self.fsm_state in {"WAIT_BUY_FILL", "PLACE_SELL", "WAIT_SELL_FILL", "SELL_TIMEOUT", "ERROR_POSITION"}:
                self.log("WARNING", "[EXEC] BLOCK reason=position_open_no_new_buy")
                self.fsm_state = "DONE"
            elif self.active_order.get("orderId"):
                self.log("WARNING", "[EXEC] BLOCK reason=active_order")
                self.fsm_state = "DONE"
            elif (plan.required_u or 0.0) > self.settings.max_live_exposure_u:
                self.log("WARNING", f"[EXEC] BLOCK reason=required_u_gt_max_exposure_u required_u={(plan.required_u or 0.0):.4f} max_exposure_u={self.settings.max_live_exposure_u:.4f}")
                self.fsm_state = "DONE"
            else:
                try:
                    o = self.account.place_limit_order(CONFIG.binance_symbol, "BUY", float(plan.entry_price), float(plan.qty_btc))
                    now = int(time.time() * 1000)
                    self.active_order = {"orderId": o.get("orderId"), "side": "BUY", "price": float(plan.entry_price), "qty": float(plan.qty_btc), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                    self.position_state = "BUY_PENDING"
                    self.entry_started_ms = now
                    self.log("OK", f"[EXEC] BUY ORDER SENT orderId={self.active_order['orderId']} price={plan.entry_price:.2f} qty={plan.qty_btc:.6f}")
                    self.log("OK", f"[EXEC] PLACE BUY price={plan.entry_price:.2f} qty={plan.qty_btc:.6f}")
                    self.fsm_state = "WAIT_BUY_FILL"
                except BinanceAPIError as exc:
                    code = exc.binance_code
                    msg = exc.binance_msg or ""
                    self.log("ERROR", f"[EXEC] BUY REJECTED status={exc.status_code} code={code} msg={msg}")
                    self.log("ERROR", f"[EXEC] BUY REJECTED body={exc.response_text}")
                    lower_msg = msg.lower()
                    if any(k in lower_msg for k in ["permission", "api-key", "api key", "not allowed", "unauthorized"]):
                        self.runtime["Mode"].setText("ERROR: API permission")
                    elif any(k in lower_msg for k in ["invalid symbol", "unknown order", "unsupported", "invalid", "filter", "minnotional", "minqty"]):
                        self.runtime["Mode"].setText("ERROR: order unsupported for BTCU")
                    self.active_order = {}
                    self.order_retry_blocked_until_ms = int(time.time() * 1000) + 10_000
                    self.log("WARNING", "[EXEC] STOP retry storm prevented")
                    self.fsm_state = "ERROR"
        elif self.runtime_active and self.fsm_state == "WAIT_BUY_FILL" and self.active_order.get("orderId"):
            now = int(time.time() * 1000)
            st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            self.active_order["state"] = st.get("status", "NEW")
            if st.get("status") == "FILLED":
                buy_qty = float(Decimal(str(st.get("executedQty", "0"))))
                if buy_qty <= 0:
                    self.log("WARNING", "[EXEC] BLOCK reason=buy_filled_zero_qty")
                    self.position_state = "FLAT"
                    self.fsm_state = "ERROR"
                    self.active_order = {}
                    return
                self.position_qty = buy_qty
                self.buy_filled_qty = buy_qty
                buy_quote = float(st.get("cummulativeQuoteQty", 0.0) or 0.0)
                self.avg_entry = (buy_quote / buy_qty) if buy_qty > 0 else float(self.active_order.get("price", 0.0))
                self.position_entry_avg = self.avg_entry
                self.position_buy_order_id = int(self.active_order["orderId"])
                self.position_state = "POSITION_OPEN"
                self.log("OK", f"[EXEC] BUY FILLED id={int(self.active_order['orderId'])}")
                self.sell_reprice_count = 0
                self.last_sell_reprice_ms = 0
                self.exit_mode = "NORMAL"
                self.fsm_state = "PLACE_SELL"
            elif now - self.entry_started_ms >= self.settings.entry_timeout_ms:
                order_id = int(self.active_order["orderId"])
                self.log("WARNING", f"[EXEC] BUY TIMEOUT orderId={order_id}")
                self.log("WARNING", f"[EXEC] CANCEL BUY orderId={order_id}")
                try:
                    self.account.cancel_order(CONFIG.binance_symbol, order_id)
                    self.log("OK", "[EXEC] BUY CANCELED confirmed")
                except BinanceAPIError as exc:
                    self.log("WARNING", f"[EXEC] BUY CANCEL response status={exc.status_code} code={exc.binance_code} msg={exc.binance_msg}")
                final = self.account.get_order(CONFIG.binance_symbol, order_id)
                final_status = final.get("status", "UNKNOWN")
                self.log("INFO", f"[EXEC] BUY FINAL STATUS status={final_status} orderId={order_id}")
                executed_qty = float(final.get("executedQty", 0.0) or 0.0)
                if final_status == "FILLED":
                    self.position_qty = executed_qty
                    self.buy_filled_qty = executed_qty
                    self.avg_entry = float(self.active_order.get("price", 0.0))
                    self.log("OK", "[EXEC] BUY FILLED during cancel")
                    self.log("OK", f"[EXEC] BUY FILLED id={int(self.active_order['orderId'])}")
                    self.fsm_state = "PLACE_SELL"
                else:
                    self._inc_canceled_attempt("timeout_buy")
                    self._inc_canceled_attempt("canceled_buy")
                    self.canceled_buys += 1
                    self.active_order = {}
                    self.log("WARNING", "[EXEC] BLOCK reason=buy_not_filled")
                    self.fsm_state = "DONE"
        elif self.runtime_active and self.fsm_state == "PLACE_SELL":
            if self.active_order.get("orderId") and self.active_order.get("side") == "SELL":
                self.log("WARNING", "[EXEC] BLOCK duplicate_sell_prevented")
                self.fsm_state = "WAIT_SELL_FILL"
                return
            sell_qty = float(Decimal(str(self.position_qty)))
            min_qty = float(self.filters.get("minQty", 0.0) or 0.0)
            if sell_qty <= 0:
                self.log("WARNING", "[EXEC] BLOCK reason=no_position_to_sell")
                self.fsm_state = "ERROR"
            elif min_qty > 0 and sell_qty < min_qty:
                self.log("WARNING", f"[EXEC] BLOCK reason=sell_qty_invalid qty={sell_qty:.8f} minQty={min_qty:.8f}")
                self.fsm_state = "ERROR"
            else:
                self.log("OK", f"[EXEC] PLACE SELL price={plan.exit_price:.2f} qty={sell_qty:.6f}")
                o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(plan.exit_price), float(sell_qty))
                now = int(time.time() * 1000)
                self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(plan.exit_price), "qty": float(sell_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                self.position_sell_order_id = int(self.active_order["orderId"])
                self.position_state = "SELL_PENDING"
                self.exit_mode = "NORMAL" if self.sell_reprice_count == 0 else "AGGRESSIVE"
                self.log("OK", f"[EXEC] SELL ORDER SENT orderId={self.active_order['orderId']}")
                self.exit_started_ms = now
                self.fsm_state = "WAIT_SELL_FILL"
        elif self.runtime_active and self.fsm_state == "WAIT_SELL_FILL" and self.active_order.get("orderId"):
            now = int(time.time() * 1000)
            st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            self.active_order["state"] = st.get("status", "NEW")
            if st.get("status") == "FILLED":
                sell_qty = float(st.get("executedQty", 0.0) or 0.0)
                sell_quote = float(st.get("cummulativeQuoteQty", 0.0) or 0.0)
                self.avg_exit = (sell_quote / sell_qty) if sell_qty > 0 else float(self.active_order.get("price", 0.0))
                self.realized_u = sell_quote - (self.position_entry_avg * sell_qty)
                self.log("OK", f"[EXEC] SELL FILLED id={int(self.active_order['orderId'])}")
                remaining = max(self.position_qty - sell_qty, 0.0)
                self.log("OK", f"[EXEC] SELL FILLED qty={sell_qty:.6f} remaining={remaining:.6f}")
                self.log("OK", f"[EXEC] REALIZED pnl={self.realized_u:+.6f}")
                self._apply_session_pnl(self.realized_u)
                self.active_order = {}
                self.buy_filled_qty = 0.0
                if sell_qty >= self.position_qty:
                    self.position_qty = 0.0
                    self.position_state = "FLAT"
                    self.fsm_state = "DONE"
                else:
                    self.position_qty = remaining
                    self.position_state = "POSITION_OPEN"
                    self.fsm_state = "PLACE_SELL"
            elif now - self.exit_started_ms >= self.settings.exit_timeout_ms:
                self.log("WARNING", "[EXEC] SELL TIMEOUT")
                if now - self.last_sell_reprice_ms < 1000:
                    self.fsm_state = "WAIT_SELL_FILL"
                elif self.sell_reprice_count >= int(self.settings.max_sell_reprices):
                    self.log("ERROR", "[EXEC] EXIT FAILED max_reprices_reached")
                    self.log("ERROR", "[EXEC] EXIT FAILED")
                    self.exit_mode = "FAILED"
                    self.position_state = "EXIT_FAILED"
                    self.fsm_state = "SELL_TIMEOUT"
                else:
                    old_price = float(self.active_order.get("price", 0.0) or 0.0)
                    order_id = int(self.active_order["orderId"])
                    self.log("WARNING", f"[EXEC] CANCEL SELL orderId={order_id}")
                    self.account.cancel_order(CONFIG.binance_symbol, order_id)
                    self._inc_canceled_attempt("timeout_sell")
                    self._inc_canceled_attempt("canceled_sell")
                    self.sell_timeouts += 1
                    bid_now = float(self.state.snapshot.bid or 0.0)
                    ask_now = float(self.state.snapshot.ask or 0.0)
                    aggressive = float(self.settings.aggressive_exit_offset)
                    new_price = max(bid_now + 0.01, ask_now - aggressive)
                    sell_qty = float(Decimal(str(self.position_qty)))
                    self.log("WARNING", f"[EXEC] SELL REPRICE old={old_price:.2f} new={new_price:.2f}")
                    o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(sell_qty))
                    self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(new_price), "qty": float(sell_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                    self.position_sell_order_id = int(self.active_order["orderId"])
                    self.sell_reprice_count += 1
                    self.last_sell_reprice_ms = now
                    self.exit_started_ms = now
                    self.exit_mode = "AGGRESSIVE"
                    self.position_state = "SELL_PENDING"
                    self.log("WARNING", f"[EXEC] SELL REPRICE count={self.sell_reprice_count}")
                    self.log("OK", "[EXEC] EXIT RECOVERED")

        self.risk["Order size U"].setText(self._fmt(self.settings.order_size_u, 2))
        self.risk["Max exposure U"].setText(self._fmt(self.settings.max_live_exposure_u, 2))
        self.risk["panic"].setText("ON" if self.settings.panic_exit else "OFF")

        self.compact_status.setText("")

        u_free = float(self.balances.get("U", {}).get("free", 0.0))
        btc_free = float(self.balances.get("BTC", {}).get("free", 0.0))
        max_buy = (u_free / plan.entry_price) if plan.entry_price else 0.0
        self.bal["BTC свободно"].setText(self._fmt(btc_free, 6))
        self.bal["BTC lock"].setText(self._fmt(float(self.balances.get("BTC", {}).get("locked", 0.0)), 6))
        self.bal["U свободно"].setText(self._fmt(u_free, 6))
        self.bal["U lock"].setText(self._fmt(float(self.balances.get("U", {}).get("locked", 0.0)), 6))
        self.bal["Max buy"].setText(f"{self._fmt(max_buy, 6)} BTC")
        self.bal["Max sell"].setText(f"{self._fmt(btc_free, 6)} BTC")

        self.conn_box.setProperty("state", "api-ok" if self.api_status == "OK" else ("api-error" if self.api_status == "ERROR" else "api-notset"))
        self.spread_box.setProperty("state", spread_state.lower())
        plan_state = "danger"
        if plan_status == "READY":
            plan_state = "ready"
        elif plan_status == "HOT":
            plan_state = "hot"
        elif plan_status == "WARNING":
            plan_state = "warning"
        self.plan_box.setProperty("state", plan_state)
        self.risk_box.setProperty("state", "warning" if self.position_state != "FLAT" else "safe")
        self.spr_v.setStyleSheet("color:#22C55E;" if spread_state == "HOT" else "")
        for w in [self.conn_box, self.spread_box, self.plan_box, self.risk_box]: w.style().unpolish(w); w.style().polish(w)

        log_key = f"{plan_status}|{plan.reason}"
        should_log = False
        if plan_status in {"READY", "HOT"} and plan_status != self.last_plan_status:
            should_log = True
        elif plan_status in {"BALANCE_LOW", "FILTER_FAIL"} and plan_status != self.last_plan_status:
            should_log = True
        elif plan_status not in {"READY", "HOT"} and plan.reason != self.last_plan_log_key.split("|", 1)[-1] if self.last_plan_log_key else True:
            should_log = True
        if should_log and (now_ms - self.last_plan_log_ms >= 2000 or log_key != self.last_plan_log_key):
            self.last_plan_log_ms = now_ms
            self.last_plan_log_key = log_key
            self.last_plan_status = plan_status
            if plan_status in {"READY", "HOT"}:
                self.log("OK", f"[PLAN] {plan_status} entry={plan.entry_price:.2f} exit={plan.exit_price:.2f} profit={plan.expected_profit_u:.6f} age={ready_age}ms")
            elif plan_status in {"BALANCE_LOW", "FILTER_FAIL"}:
                self.log("WARNING", f"[EXEC] BLOCK reason={plan_status.lower()}")

        winrate = (self.wins / self.closed_cycles * 100.0) if self.closed_cycles else 0.0
        active_order_text = "none"
        if self.active_order.get("orderId"):
            active_order_text = f"{self.active_order.get('side','-')}#{self.active_order.get('orderId')}"
        summary_sig = f"{self.position_state}:{self.position_entry_avg:.6f}:{self.closed_cycles}:{self.wins}:{self.losses}:{self.session_realized_pnl:.6f}:{self.last_pnl:.6f}:{winrate:.2f}:{self.canceled_buys}:{self.sell_timeouts}:{self.sell_reprice_count}:{self.exit_mode}:{active_order_text}:{self.position_qty:.6f}"
        if summary_sig != self.summary_signature:
            self.summary_signature = summary_sig
            self.summary["Started at"].setText(self.session_started_at)
            self.summary["Position state"].setText(self.position_state)
            self.summary["Position qty"].setText(self._fmt(self.position_qty, 6))
            self.summary["Entry avg"].setText(self._fmt(self.position_entry_avg, 6))
            self.summary["Realized PnL"].setText(f"{self.session_realized_pnl:+.6f}")
            self.summary["Winrate"].setText(f"{winrate:.2f}%")
            self.summary["Closed cycles"].setText(str(self.closed_cycles))
            self.summary["Wins"].setText(str(self.wins))
            self.summary["Losses"].setText(str(self.losses))
            self.summary["Canceled buys"].setText(str(self.canceled_buys))
            self.summary["Last PnL"].setText(f"{self.last_pnl:+.6f}")
            self.summary["Sell timeouts"].setText(str(self.sell_timeouts))
            self.summary["SELL reprices"].setText(str(self.sell_reprice_count))
            self.summary["Current exit mode"].setText(self.exit_mode)
            self.summary["Active order"].setText(active_order_text)

        rest_txt = "OK" if self.state.rest_status == "OK" else "ERROR"
        ws_txt = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        self.top_status.setText(f"BTC/U | WS ● {ws_txt} | REST ● {rest_txt} | API ● {self.api_status} | {'HOT' if spread_state=='HOT' else 'READY'}")

    def _flush_gui_logs(self) -> None:
        for key, widget in (("trade", self.trade_logs), ("system", self.system_logs)):
            if not self.pending_gui_logs[key]:
                continue
            sb = widget.verticalScrollBar()
            at_bottom = sb.value() >= sb.maximum() - 4
            cursor = widget.textCursor(); cursor.movePosition(QTextCursor.End)
            for color, line in self.pending_gui_logs[key]:
                fmt = QTextCharFormat(); fmt.setForeground(QColor(color)); cursor.setCharFormat(fmt); cursor.insertText(line + "\n")
            self.pending_gui_logs[key].clear()
            if at_bottom:
                widget.setTextCursor(cursor)

    def log(self, tag: str, message: str) -> None:
        important = ("[EXEC] PLACE BUY", "[EXEC] BUY FILLED", "[EXEC] PLACE SELL", "[EXEC] SELL FILLED", "[EXEC] REALIZED", "[EXEC] TIMEOUT", "[EXEC] CANCEL", "[SYNC] desync fixed", "[ERROR]")
        if ("[SYNC] openOrders" in message or "[SYNC] active order status" in message):
            return
        if "[WS] LIVE" in message:
            now_ms = int(time.time() * 1000)
            if now_ms - self.last_ws_live_log_ms < 10_000 and "reconnect" not in message.lower():
                return
            self.last_ws_live_log_ms = now_ms
        if message.startswith("[") and not any(k in message for k in important) and not message.startswith("[PLAN]"):
            if not message.startswith("[EXEC] BLOCK"):
                return
        line = format_log(tag, message)
        if line.split("] ", 1)[-1] == self.last_log_line:
            return
        self.last_log_line = line.split("] ", 1)[-1]
        color = {"INFO": "#CBD5E1", "OK": "#22C55E", "WARNING": "#FACC15", "ERROR": "#EF4444"}.get(tag, "#CBD5E1")
        trade_keys = ("[EXEC]", "REALIZED", "PLACE BUY", "BUY FILLED", "PLACE SELL", "SELL FILLED", "CANCEL", "TIMEOUT")
        bucket = "trade" if any(k in message for k in trade_keys) else "system"
        self.pending_gui_logs[bucket].append((color, line))
        self._flush_gui_logs()
        if bucket == "trade":
            self.file_logs.write_trade(line.replace(f"[{tag}]", "[EXEC]"))
        else:
            self.file_logs.write_system(line)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop(); super().closeEvent(event)
