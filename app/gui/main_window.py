import time
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget

from app.core.binance_account import BinanceAccountClient
from app.core.config import CONFIG, SETTINGS_STORE
from app.core.logger import format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.trade_math import TradeMathEngine
from app.core.market_ws import MarketWSClient
from app.gui.styles import main_qss
from app.gui.widgets import big_value, kv_card


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = SETTINGS_STORE.load()
        self.setWindowTitle("UB v0.3.0 / BTCU Trading Cockpit")
        self.resize(1600, 900)
        self.setMinimumSize(1280, 760)
        self.setStyleSheet(main_qss())
        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol, self.settings.max_ws_age_ms)
        self.account = BinanceAccountClient()
        self.api_status = "NOT SET"
        self.last_log_line = ""
        self.logged_flags: set[str] = set()
        self.balances = {"BTC": {"free": 0.0, "locked": 0.0}, "U": {"free": 0.0, "locked": 0.0}}
        self.filters = {"loaded": False, "fallback": False, "tickSize": 0.0, "stepSize": 0.0, "minQty": 0.0, "minNotional": 0.0}
        self.orders_data = []
        self.runtime_active = False
        self.fsm_state = "IDLE"
        self.active_order = {}
        self.position_qty = 0.0
        self.avg_entry = 0.0
        self.avg_exit = 0.0
        self.realized_u = 0.0
        self.entry_started_ms = 0
        self.exit_started_ms = 0
        self.reprice_done = False
        self.trade_math = TradeMathEngine()
        self.last_plan_status = ""
        self.ready_since_ms = 0
        self.plan_stable_ms = 400
        self.plan_ready_streak = 0
        self.last_plan_log_ms = 0
        self.last_plan_log_key = ""

        root = QWidget(); self.setCentralWidget(root); self.main_layout = QVBoxLayout(root)
        self.top_status = QLabel(); self.top_status.setObjectName("topStatus"); self.main_layout.addWidget(self.top_status)
        self.grid = QGridLayout(); self.main_layout.addLayout(self.grid, 1)
        self._build_cards(); self._build_controls(); self._build_logs()

        self.ws.signals.book.connect(self.on_ws_book); self.ws.signals.status.connect(self.on_ws_status); self.ws.signals.log.connect(self.log)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick); self.timer.start(300)
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(self.fetch_rest); self.rest_timer.start(self.settings.rest_poll_ms)
        self.account_timer = QTimer(self); self.account_timer.timeout.connect(self.refresh_account_data); self.account_timer.start(5000)
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
        plan, self.plan = kv_card("TRADE PLAN", [("Status", "NO_DATA"), ("Reason", "Нет рыночных данных"), ("Entry BUY", "N/A"), ("Exit SELL", "N/A"), ("Stop", "N/A"), ("Capture/BTC", "N/A"), ("Lot", "0"), ("Required U", "N/A"), ("Balance OK", "NO"), ("Filters OK", "NO"), ("Ready age", "0ms"), ("Profit U", "N/A"), ("Loss U", "N/A"), ("R:R", "N/A")])
        plan.setMinimumHeight(320)
        self.plan_box = plan
        runtime, self.runtime = kv_card("RUNTIME", [("LIVE", "OFF"), ("ARM", "OFF"), ("FSM", "IDLE"), ("Треб. подтверждение", "YES"), ("Авто-отмена", "YES")])
        self.runtime_box = runtime
        risk, self.risk = kv_card("RISK", [("lot_size", "0"), ("max_open_lots", "0"), ("max_daily_loss", "0"), ("max_exposure_u", "0"), ("panic_exit", "ON")])
        self.risk_box = risk
        bal, self.bal = kv_card("BALANCES", [("BTC свободно", "0"), ("BTC lock", "0"), ("U свободно", "0"), ("U lock", "0"), ("Max buy", "0 BTC"), ("Max sell", "0 BTC")])
        fil, self.fil = kv_card("ФИЛЬТРЫ", [("Filters", "NO"), ("tickSize", "0"), ("stepSize", "0"), ("minQty", "0"), ("minNotional", "0")])
        self.grid.addWidget(spread, 1, 0); self.grid.addWidget(plan, 1, 1); self.grid.addWidget(runtime, 1, 2); self.grid.addWidget(risk, 1, 3); self.grid.addWidget(bal, 2, 0); self.grid.addWidget(fil, 2, 1, 1, 3)

        self.orders = QTableWidget(0, 8); self.orders.setHorizontalHeaderLabels(["Order ID", "Side", "Price", "Qty", "Filled", "Status", "Age", "Type"])
        box = QGroupBox("REAL LIVE ORDERS"); lay = QVBoxLayout(); lay.addWidget(self.orders); box.setLayout(lay)
        self.grid.addWidget(box, 3, 0, 1, 4)

    def _build_controls(self) -> None:
        row = QHBoxLayout()
        self.settings_btn = QPushButton("НАСТРОЙКИ"); self.settings_btn.setProperty("kind", "neutral"); self.settings_btn.clicked.connect(self.open_settings_dialog); row.addWidget(self.settings_btn)
        self.start_stop_btn = QPushButton("START"); self.start_stop_btn.setProperty("kind", "start"); self.start_stop_btn.clicked.connect(self.toggle_runtime); row.addWidget(self.start_stop_btn)
        self.cancel_btn = QPushButton("ОТМЕНИТЬ ВСЁ"); self.cancel_btn.setProperty("kind", "danger"); self.cancel_btn.clicked.connect(self.cancel_all); row.addWidget(self.cancel_btn)
        self.main_layout.addLayout(row)

    def _build_logs(self) -> None:
        self.logs = QTextEdit(); self.logs.setReadOnly(True); self.logs.document().setMaximumBlockCount(500); self.logs.setMinimumHeight(180); self.main_layout.addWidget(self.logs)

    def open_settings_dialog(self) -> None:
        d = QDialog(self); d.setWindowTitle("Настройки UB"); d.setModal(True); d.resize(760, 620)
        lay = QVBoxLayout(d)
        tabs = QTabWidget(); lay.addWidget(tabs)
        self.settings_inputs = {}

        account_tab = QWidget(); account_form = QFormLayout(account_tab)
        api_key_input = QLineEdit(); api_secret_input = QLineEdit(); api_secret_input.setEchoMode(QLineEdit.Password)
        show_secret = QCheckBox("Показать secret")
        show_secret.toggled.connect(lambda v: api_secret_input.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        test_btn = QPushButton("Проверить API"); test_btn.clicked.connect(self.on_test_connection)
        save_api_btn = QPushButton("Сохранить ключи"); save_api_btn.clicked.connect(lambda: self._save_api_fields(api_key_input.text(), api_secret_input.text(), show_secret, api_secret_input))
        account_form.addRow("API key", api_key_input); account_form.addRow("API secret", api_secret_input); account_form.addRow("", show_secret); account_form.addRow(test_btn, save_api_btn); account_form.addRow("Статус", QLabel(self.api_status))
        tabs.addTab(account_tab, "Аккаунт")

        tab_map = [("Harvest", ["min_spread", "entry_offset", "exit_offset", "target_capture", "stop_loss", "max_hold_ms"]), ("Risk", ["lot_size", "max_open_lots", "max_daily_loss", "max_exposure_u", "live_max_exposure_u", "panic_exit"]), ("Data", ["rest_poll_ms", "ws_optional_enabled", "max_ws_age_ms"]), ("Safety", ["live_enabled", "arm_live", "require_confirmation", "auto_cancel_on_stop", "entry_timeout_ms", "exit_timeout_ms", "panic_reprice_once"])]
        for title, fields in tab_map:
            w = QWidget(); f = QFormLayout(w)
            for key in fields:
                v = getattr(self.settings, key)
                inp = QCheckBox() if isinstance(v, bool) else QLineEdit(str(v))
                if isinstance(inp, QCheckBox): inp.setChecked(v)
                self.settings_inputs[key] = inp
                f.addRow(key, inp)
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
        SETTINGS_STORE.save(self.settings); self.rest_timer.setInterval(self.settings.rest_poll_ms); self.log("OK", "settings saved")
        dialog.close()

    def toggle_runtime(self) -> None:
        self.runtime_active = not self.runtime_active
        if self.runtime_active: self.ws.start(); self.start_stop_btn.setText("STOP"); self.start_stop_btn.setProperty("kind", "stop"); self.log("OK", "START")
        else: self.ws.stop(); self.start_stop_btn.setText("START"); self.start_stop_btn.setProperty("kind", "start"); self.cancel_all(); self.log("WARNING", "STOP")
        self.start_stop_btn.style().polish(self.start_stop_btn)

    def cancel_all(self) -> None:
        self.log("WARNING", "cancel all requested")
        if self.active_order.get("orderId"):
            try:
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            except Exception:
                pass
        self.active_order = {}
        self.fsm_state = "IDLE"
    def on_ws_book(self, bid: float, ask: float, ts: int) -> None: self.state.snapshot.bid = bid; self.state.snapshot.ask = ask; self.state.snapshot.updated_ms = ts; self.state.snapshot.source = "WS"; self.state.last_ws_ms = ts
    def on_ws_status(self, status: str) -> None:
        self.state.ws_status = status

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
        orders, _ = self.account.get_open_orders_safe(CONFIG.binance_symbol); self.orders_data = orders
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
        self.runtime["ARM"].setText("ON" if self.settings.arm_live else "OFF")
        self.runtime["FSM"].setText(self.fsm_state)
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
        self.plan["Reason"].setText(plan.reason)
        self.plan["Entry BUY"].setText("N/A" if plan.entry_price is None else f"{plan.entry_price:.2f}")
        self.plan["Exit SELL"].setText("N/A" if plan.exit_price is None else f"{plan.exit_price:.2f}")
        self.plan["Stop"].setText("N/A" if plan.stop_price is None else f"{plan.stop_price:.2f}")
        self.plan["Capture/BTC"].setText("N/A" if plan.capture_per_btc is None else f"{plan.capture_per_btc:.2f}")
        self.plan["Lot"].setText(self._fmt(plan.lot_size, 6))
        self.plan["Required U"].setText("N/A" if plan.required_u is None else self._fmt(plan.required_u, 6))
        self.plan["Balance OK"].setText("YES" if plan.balance_ok else "NO")
        self.plan["Filters OK"].setText("YES" if plan.filters_ok else "NO")
        self.plan["Ready age"].setText(f"{ready_age}ms")
        self.plan["Profit U"].setText("N/A" if plan.expected_profit_u is None else self._fmt(plan.expected_profit_u, 6))
        self.plan["Loss U"].setText("N/A" if plan.stop_loss_u is None else self._fmt(plan.stop_loss_u, 6))
        self.plan["R:R"].setText("N/A" if plan.risk_reward is None else self._fmt(plan.risk_reward, 4))

        if self.runtime_active and self.fsm_state == "IDLE":
            self.fsm_state = "WAIT_READY"
        if self.runtime_active and self.fsm_state == "WAIT_READY" and plan.status in {"READY", "HOT"} and plan.balance_ok and plan.filters_ok and ws_ok:
            if (plan.required_u or 0.0) > self.settings.live_max_exposure_u:
                self.log("WARNING", f"[EXEC] BLOCK exposure required={plan.required_u:.4f} > {self.settings.live_max_exposure_u:.4f}")
                self.fsm_state = "DONE"
            elif self.settings.live_enabled and self.settings.arm_live:
                o = self.account.place_limit_order(CONFIG.binance_symbol, "BUY", float(plan.entry_price), float(plan.lot_size))
                now = int(time.time() * 1000)
                self.active_order = {"orderId": o.get("orderId"), "side": "BUY", "price": float(plan.entry_price), "qty": float(plan.lot_size), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                self.entry_started_ms = now
                self.log("OK", f"[EXEC] PLACE BUY id={self.active_order['orderId']} p={plan.entry_price:.2f} q={plan.lot_size:.6f}")
                self.fsm_state = "WAIT_BUY_FILL"
            else:
                self.log("INFO", "[EXEC] analytics only (LIVE OFF or ARM OFF)")
                self.fsm_state = "DONE"
        elif self.runtime_active and self.fsm_state == "WAIT_BUY_FILL" and self.active_order.get("orderId"):
            now = int(time.time() * 1000)
            st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            self.active_order["state"] = st.get("status", "NEW")
            if st.get("status") == "FILLED":
                self.position_qty = float(st.get("executedQty", 0.0))
                self.avg_entry = float(self.active_order.get("price", 0.0))
                self.log("OK", f"[EXEC] BUY FILLED id={self.active_order['orderId']}")
                self.fsm_state = "PLACE_SELL"
            elif now - self.entry_started_ms >= self.settings.entry_timeout_ms:
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                self.log("WARNING", "[EXEC] TIMEOUT")
                self.fsm_state = "DONE"
        elif self.runtime_active and self.fsm_state == "PLACE_SELL":
            o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(plan.exit_price), float(self.position_qty))
            now = int(time.time() * 1000)
            self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(plan.exit_price), "qty": float(self.position_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
            self.exit_started_ms = now
            self.log("OK", f"[EXEC] PLACE SELL id={self.active_order['orderId']} p={plan.exit_price:.2f} q={self.position_qty:.6f}")
            self.fsm_state = "WAIT_SELL_FILL"
        elif self.runtime_active and self.fsm_state == "WAIT_SELL_FILL" and self.active_order.get("orderId"):
            now = int(time.time() * 1000)
            st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            self.active_order["state"] = st.get("status", "NEW")
            if st.get("status") == "FILLED":
                self.avg_exit = float(self.active_order.get("price", 0.0))
                self.realized_u = (self.avg_exit - self.avg_entry) * self.position_qty
                self.log("OK", f"[EXEC] SELL FILLED id={self.active_order['orderId']}")
                self.log("OK", f"[EXEC] REALIZED {self.realized_u:+.6f} U")
                self.fsm_state = "DONE"
            elif now - self.exit_started_ms >= self.settings.exit_timeout_ms:
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                if self.settings.panic_reprice_once and not self.reprice_done and self.state.snapshot.ask:
                    self.reprice_done = True
                    p = float(self.state.snapshot.ask - self.settings.exit_offset)
                    o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", p, float(self.position_qty))
                    self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": p, "qty": float(self.position_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                    self.exit_started_ms = now
                else:
                    self.log("WARNING", "[EXEC] TIMEOUT")
                    self.fsm_state = "DONE"

        self.fil["Filters"].setText("YES fallback" if self.filters.get("fallback") else ("YES" if self.filters["loaded"] else "NO"))
        for k in ["tickSize", "stepSize", "minQty", "minNotional"]: self.fil[k].setText(self._fmt(float(self.filters[k]), 6))

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
        self.risk_box.setProperty("state", "safe")
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
                self.log("WARNING", f"[PLAN] {plan_status} required={self._fmt(plan.required_u or 0.0, 2)} available={self._fmt(float(self.balances.get('U', {}).get('free', 0.0)), 2)} reason={plan.reason}")
            else:
                self.log("WARNING", f"[PLAN] {plan_status} reason={plan.reason}")

        rows = list(self.orders_data)
        if self.active_order:
            rows.append({"orderId": self.active_order.get("orderId"), "side": self.active_order.get("side"), "price": self.active_order.get("price", 0), "origQty": self.active_order.get("qty", 0), "executedQty": 0, "status": self.active_order.get("state", "NEW"), "time": self.active_order.get("create_ms", int(time.time() * 1000)), "type": self.active_order.get("type", "LIMIT")})
        self.orders.setRowCount(len(rows))
        now = int(time.time() * 1000)
        for i, o in enumerate(rows):
            vals = [str(o.get("orderId", "")), str(o.get("side", "")), self._fmt(float(o.get("price", 0.0)), 6), self._fmt(float(o.get("origQty", 0.0)), 6), self._fmt(float(o.get("executedQty", 0.0)), 6), str(o.get("status", "")), f"{max(now - int(o.get('time', now)), 0)}ms", str(o.get("type", "LIMIT"))]
            for j, v in enumerate(vals):
                self.orders.setItem(i, j, QTableWidgetItem(v))

        rest_txt = "OK" if self.state.rest_status == "OK" else "ERROR"
        ws_txt = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        self.top_status.setText(f"BTC/U | WS ● {ws_txt} | REST ● {rest_txt} | API ● {self.api_status} | {'HOT' if spread_state=='HOT' else 'READY'}")

    def log(self, tag: str, message: str) -> None:
        line = format_log(tag, message)
        if line.split("] ", 1)[-1] == self.last_log_line: return
        self.last_log_line = line.split("] ", 1)[-1]
        color = {"INFO": "#CBD5E1", "OK": "#22C55E", "WARNING": "#FACC15", "ERROR": "#EF4444"}.get(tag, "#CBD5E1")
        cursor = self.logs.textCursor(); cursor.movePosition(QTextCursor.End)
        fmt = QTextCharFormat(); fmt.setForeground(QColor(color)); cursor.setCharFormat(fmt); cursor.insertText(line + "\n")
        self.logs.setTextCursor(cursor)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop(); super().closeEvent(event)
