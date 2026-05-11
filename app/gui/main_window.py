import time
from dataclasses import dataclass
from decimal import Decimal
from collections import deque
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QHeaderView, QSizePolicy

from app.core.binance_account import BinanceAPIError, BinanceAccountClient
from app.core.config import CONFIG, SETTINGS_STORE
from app.core.logger import FileLogManager, format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.trade_math import TradeMathEngine
from app.core.market_ws import MarketWSClient
from app.gui.styles import main_qss
from app.gui.widgets import big_value, build_kv_card


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


@dataclass
class InventoryChunk:
    qty: float
    entry_price: float
    created_ms: int


class MarketHealthState:
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    DANGER = "DANGER"
    UNTRADEABLE = "UNTRADEABLE"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = SETTINGS_STORE.load()
        self.setWindowTitle("UB v0.7.6 / BTCU Trading Cockpit")
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
        self.inventory_chunks: list[InventoryChunk] = []
        self.position_entry_avg = 0.0
        self.position_buy_order_id = 0
        self.position_sell_order_id = 0
        self.position_state = "FLAT"
        self.buy_filled_qty = 0.0
        self.sell_reported_qty = 0.0
        self.sell_target_qty = 0.0
        self.buy_reported_qty = 0.0
        self.buy_reported_quote = 0.0
        self.avg_entry = 0.0
        self.avg_exit = 0.0
        self.realized_u = 0.0
        self.entry_started_ms = 0
        self.exit_started_ms = 0
        self.reprice_done = False
        self.sell_reprice_count = 0
        self.last_sell_reprice_ms = 0
        self.exit_mode = "NORMAL"
        self.panic_exit_final = False
        self.panic_exit_order_id = 0
        self.panic_exit_price = 0.0
        self.panic_exit_started_ms = 0
        self.panic_escalated_once = False
        self.last_panic_wait_log_ms = 0
        self.entry_exec_state = "ENTRY_WAITING"
        self.entry_reprice_count = 0
        self.last_entry_reprice_ms = 0
        self.entry_last_reason = "-"
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
        self.cycle_realized_pnl = 0.0
        self.cycle_has_fifo_close = False
        self.last_pnl = 0.0
        self.canceled_buys = 0
        self.sell_timeouts = 0
        self.file_logs = FileLogManager()
        self.gui_log_limit = 500
        self.pending_gui_logs = {"trade": [], "system": []}
        self.summary_signature = ""
        self.last_ws_live_log_ms = 0
        self.ws_tick_count = 0
        self.ws_raw_ticks_total = 0
        self.ws_tick_window_start_monotonic = time.monotonic()
        self.ws_ticks_per_sec = 0.0
        self.ws_dropped_ticks = 0
        self.last_ws_tick_monotonic = 0.0
        self.last_ws_bid = 0.0
        self.last_ws_ask = 0.0
        self.last_ws_state = "UNKNOWN"
        self.last_gui_refresh_monotonic = time.monotonic()
        self.gui_refresh_ms = 0
        self.last_position_open_block_log_ms = 0
        self.sell_recovery_in_progress = False
        self.sell_cancel_in_progress = False
        self.last_sell_place_ms = 0
        self.last_sell_place_signature = ""
        self.sell_hold_window_ms = 1500
        self.sell_hold_near_ticks = 2
        self.market_health_state = MarketHealthState.GOOD
        self.market_health_bid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_mid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_unstable_bid_ticks = abs(self.settings.max_negative_bid_delta)
        self.market_health_negative_mid_ticks = abs(self.settings.max_negative_mid_delta)
        self.market_health_min_spread_lifetime_ms = self.settings.min_spread_lifetime_ms
        self.recent_bids: deque[tuple[int, float]] = deque()
        self.recent_mids: deque[tuple[int, float]] = deque()
        self.last_spread_good_since_ms = 0
        self.last_health_log_ms = 0
        self.last_health_reason = ""
        self._last_health_update_ms = 0
        self.entry_guard_cooldown_until_ms = 0
        self.entry_guard_cooldown_reason = ""
        self.entry_guard_state = "WARMING"
        self.entry_guard_reason = "boot"
        self.entry_guard_last_block_log_ms = 0
        self.entry_guard_stable_count = 0
        self.last_plan_recompute_ms = 0
        self._cached_plan = None
        self.rest_fallback_count = 0
        self.stale_reason = ""

        root = QWidget(); self.setCentralWidget(root); self.main_layout = QVBoxLayout(root)
        self.top_status = QLabel(); self.top_status.setObjectName("topStatus"); self.main_layout.addWidget(self.top_status)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(10); self.grid.setVerticalSpacing(10); self.grid.setContentsMargins(0, 0, 0, 0); self.main_layout.addLayout(self.grid)
        self._build_cards(); self._build_controls(); self._build_logs(); self._configure_grid_layout()

        self.ws.signals.book.connect(self.on_ws_book); self.ws.signals.status.connect(self.on_ws_status); self.ws.signals.log.connect(self.log)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick); self.timer.start(250)
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(self.fetch_rest); self.rest_timer.start(self.settings.rest_poll_ms)
        self.account_timer = QTimer(self); self.account_timer.timeout.connect(self.refresh_account_data); self.account_timer.start(self.settings.balances_poll_ms)
        self.active_sync_timer = QTimer(self); self.active_sync_timer.timeout.connect(self.sync_active_order); self.active_sync_timer.start(max(self.settings.active_order_poll_ms, 1200))
        self._apply_runtime_settings()
        self.on_test_connection(silent=True)


    def _configure_grid_layout(self) -> None:
        for col in range(4):
            self.grid.setColumnStretch(col, 1)
            self.grid.setColumnMinimumWidth(col, 260)
        self.grid.setRowStretch(0, 0)
        self.grid.setRowStretch(1, 2)
        self.grid.setRowStretch(2, 2)
        self.grid.setRowStretch(3, 1)
        self.grid.setRowStretch(4, 0)
        self.grid.setRowStretch(5, 2)

    def _build_cards(self) -> None:
        conn, self.conn = build_kv_card("CONNECTION", [("API", "NOT SET"), ("REST", "N/A"), ("WS", "OPTIONAL LOST"), ("Source", "NONE"), ("Latency", "0 ms"), ("WS age", "N/A"), ("WS tps", "0.00"), ("GUI refresh", "0 ms")])
        self.conn_box = conn
        self.grid.addWidget(conn, 1, 0)

        spread, self.spread = build_kv_card("SPREAD ENGINE", [("Status", "BAD"), ("Spread", "N/A"), ("Capture", "N/A"), ("Lifetime", "0ms"), ("Source", "NONE"), ("Latency", "--")], compact=True)
        self.spread_box = spread
        plan, self.plan = build_kv_card("TRADE PLAN", [("Status", "NO_DATA"), ("Entry", "N/A"), ("Exit", "N/A"), ("Qty BTC", "0"), ("Order U", "0"), ("Profit U", "N/A"), ("Age", "0ms")], compact=True)
        self.plan_box = plan
        runtime, self.runtime = build_kv_card("RUNTIME", [("LIVE", "OFF"), ("FSM", "IDLE"), ("Mode", "ANALYTICS"), ("Position state", "FLAT"), ("Position qty", "0"), ("Entry avg", "0"), ("Market Health", "GOOD"), ("Entry Guard", "BALANCED"), ("Guard state", "WARMING"), ("Guard reason", "boot"), ("Stable snaps", "0/0"), ("Cooldown ms", "0"), ("Entry mode", "BALANCED"), ("BUY age", "0ms"), ("Entry reprices", "0"), ("Fill hint", "LOW"), ("Entry reason", "-"), ("Auto-confirm", "YES"), ("Auto-cancel", "YES")], compact=True)
        self.runtime_box = runtime
        risk, self.risk = build_kv_card("RISK", [("Order size U", "0"), ("Max exposure U", "0"), ("panic", "ON")], compact=True)
        self.risk_box = risk
        bal, self.bal = build_kv_card("BALANCES", [("BTC свободно", "0"), ("BTC lock", "0"), ("U свободно", "0"), ("U lock", "0"), ("Max buy", "0 BTC"), ("Max sell", "0 BTC")], compact=True)
        self.grid.addWidget(spread, 2, 0); self.grid.addWidget(plan, 2, 1); self.grid.addWidget(runtime, 2, 2); self.grid.addWidget(bal, 2, 3)

        summary_rows = [("Started", self.session_started_at), ("Position state", "FLAT"), ("Position qty", "0"), ("Entry avg", "0"), ("Closed cycles", "0"), ("Wins", "0"), ("Losses", "0"), ("Realized PnL", "0"), ("Last PnL", "0"), ("Winrate", "0%"), ("Canceled buys", "0"), ("Sell timeouts", "0"), ("Exit mode", "NORMAL")]
        summary, self.summary = build_kv_card("SESSION RESULT", summary_rows, compact=True, label_width=136, columns=3)
        self.grid.addWidget(summary, 1, 1, 1, 3)

        bid_box, self.bid_v = big_value("BID", "N/A", compact=True); ask_box, self.ask_v = big_value("ASK", "N/A", compact=True); spr_box, self.spr_v = big_value("SPREAD", "N/A", compact=True)
        self.bid_box = bid_box; self.ask_box = ask_box; self.spr_box = spr_box
        self.grid.addWidget(risk, 3, 0, 1, 1); self.grid.addWidget(bid_box, 3, 1); self.grid.addWidget(ask_box, 3, 2); self.grid.addWidget(spr_box, 3, 3)


    def _build_controls(self) -> None:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        self.settings_btn = QPushButton("НАСТРОЙКИ"); self.settings_btn.setProperty("kind", "neutral"); self.settings_btn.clicked.connect(self.open_settings_dialog); row.addWidget(self.settings_btn)
        self.start_stop_btn = QPushButton("START"); self.start_stop_btn.setProperty("kind", "start"); self.start_stop_btn.clicked.connect(self.toggle_runtime); row.addWidget(self.start_stop_btn)
        self.cancel_btn = QPushButton("ОТМЕНИТЬ ВСЁ"); self.cancel_btn.setProperty("kind", "danger"); self.cancel_btn.clicked.connect(self.cancel_all); row.addWidget(self.cancel_btn)
        for btn in (self.settings_btn, self.start_stop_btn, self.cancel_btn):
            btn.setMinimumHeight(54)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.controls_row = row
        self.grid.addLayout(self.controls_row, 4, 0, 1, 4)

    def _build_logs(self) -> None:
        self.log_tabs = QTabWidget()
        self.trade_logs = QTextEdit(); self.trade_logs.setReadOnly(True); self.trade_logs.document().setMaximumBlockCount(self.gui_log_limit)
        self.system_logs = QTextEdit(); self.system_logs.setReadOnly(True); self.system_logs.document().setMaximumBlockCount(self.gui_log_limit)
        self.log_tabs.addTab(self.trade_logs, "Торговля")
        self.log_tabs.addTab(self.system_logs, "Система")
        self.log_tabs.setMinimumHeight(220)
        self.log_tabs.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.grid.addWidget(self.log_tabs, 5, 0, 1, 4)

    def open_settings_dialog(self) -> None:
        d = QDialog(self); d.setWindowTitle("Настройки UB"); d.setModal(True); d.resize(760, 620)
        lay = QVBoxLayout(d)
        tabs = QTabWidget(); lay.addWidget(tabs)
        self.settings_inputs = {}

        labels = {
            "order_size_u": "Размер сделки U",
            "max_exposure_u": "Макс. экспозиция U",
            "max_daily_loss": "Макс. дневной убыток U",
            "max_open_lots": "Max open lots",
            "panic_exit": "Panic exit",
            "live_enabled": "LIVE enabled",
            "require_confirmation": "Require confirmation",
            "auto_cancel_on_stop": "Auto cancel on stop",
            "max_live_exposure_u": "Live max exposure U",
            "open_orders_poll_ms": "openOrders interval ms",
            "all_orders_poll_ms": "allOrders interval ms",
            "balances_poll_ms": "balances interval ms",
            "debug_api_logs": "API debug logs",
            "buy_timeout_ms": "BUY timeout ms",
            "buy_timeout_ms_fast": "BUY timeout fast ms",
            "entry_mode": "Entry mode (PASSIVE/BALANCED/AGGRESSIVE)",
            "entry_reprice_enabled": "Entry reprice enabled",
            "entry_reprice_cooldown_ms": "Entry reprice cooldown ms",
            "max_entry_reprices": "Max entry reprices",
            "entry_chase_ticks": "Entry chase ticks",
            "entry_cross_if_spread_ticks_above": "Cross entry if spread ticks above",
            "min_spread_after_entry_ticks": "Min spread after entry ticks",
            "sell_timeout_ms": "SELL timeout ms",
            "sell_reprice_cooldown_ms": "SELL reprice cooldown ms",
            "aggressive_exit_offset": "Aggressive exit offset",
            "max_sell_reprices": "Max sell reprices",
            "min_profit_ticks": "Min profit ticks",
            "take_profit_ticks": "Take profit ticks",
            "stop_loss_ticks": "Stop loss ticks",
            "guard_mode": "Guard mode (FAST/BALANCED/STRICT)",
        }

        account_tab = QWidget(); account_form = QFormLayout(account_tab)
        api_key_input = QLineEdit(); api_secret_input = QLineEdit(); api_secret_input.setEchoMode(QLineEdit.Password)
        show_secret = QCheckBox("Показать secret")
        show_secret.toggled.connect(lambda v: api_secret_input.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        test_btn = QPushButton("Проверить API"); test_btn.clicked.connect(self.on_test_connection)
        save_api_btn = QPushButton("Сохранить ключи"); save_api_btn.clicked.connect(lambda: self._save_api_fields(api_key_input.text(), api_secret_input.text(), show_secret, api_secret_input))
        account_form.addRow("API key", api_key_input); account_form.addRow("API secret", api_secret_input); account_form.addRow("", show_secret); account_form.addRow(test_btn, save_api_btn); account_form.addRow("Статус", QLabel(self.api_status))
        tabs.addTab(account_tab, "Аккаунт")

        tab_map = [("Harvest", ["min_spread", "entry_offset", "exit_offset", "target_capture", "stop_loss", "max_hold_ms"]), ("Risk", ["order_size_u", "max_exposure_u", "max_daily_loss", "max_open_lots", "panic_exit", "max_live_exposure_u"]), ("Data", ["rest_poll_ms", "open_orders_poll_ms", "all_orders_poll_ms", "balances_poll_ms", "debug_api_logs", "ws_optional_enabled", "max_ws_age_ms"]), ("Execution", ["entry_mode", "entry_reprice_enabled", "entry_reprice_cooldown_ms", "max_entry_reprices", "entry_chase_ticks", "entry_cross_if_spread_ticks_above", "min_spread_after_entry_ticks", "buy_timeout_ms_fast", "buy_timeout_ms", "sell_timeout_ms", "sell_reprice_cooldown_ms", "aggressive_exit_offset", "max_sell_reprices", "min_profit_ticks", "take_profit_ticks", "stop_loss_ticks"]), ("Safety", ["live_enabled", "require_confirmation", "auto_cancel_on_stop", "panic_reprice_once"]), ("Guard", ["guard_mode", "guard_enabled", "require_ws_for_buy", "max_ws_age_for_buy_ms", "min_spread_lifetime_ms", "stable_snapshots_required", "stable_snapshot_window_ms", "max_negative_mid_delta", "max_negative_bid_delta", "block_on_mid_negative", "block_on_bid_unstable", "block_on_snapshots_insufficient", "loss_cooldown_ms", "panic_cooldown_ms", "balance_safety_buffer_u", "block_log_throttle_ms", "health_log_throttle_ms"])]
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

        btns = QHBoxLayout(); save = QPushButton("SAVE"); export_btn = QPushButton("Export settings"); import_btn = QPushButton("Import settings"); close = QPushButton("CLOSE"); save.clicked.connect(lambda: self._save_settings_dialog(d)); export_btn.clicked.connect(self._export_settings); import_btn.clicked.connect(self._import_settings); close.clicked.connect(d.close); btns.addWidget(save); btns.addWidget(export_btn); btns.addWidget(import_btn); btns.addWidget(close); lay.addLayout(btns)
        d.exec()

    def _save_api_fields(self, key: str, secret: str, show: QCheckBox, secret_input: QLineEdit) -> None:
        self.account.save_api_keys(key, secret)
        secret_input.clear(); show.setChecked(False)
        self.log("INFO", f"API keys loaded key={self.account._mask_key(self.account.api_key)}")
        self._apply_runtime_settings()
        self.on_test_connection(silent=True)

    def _save_settings_dialog(self, dialog: QDialog) -> None:
        for key, widget in self.settings_inputs.items():
            old = getattr(self.settings, key)
            if isinstance(widget, QCheckBox): setattr(self.settings, key, widget.isChecked())
            elif isinstance(old, int): setattr(self.settings, key, int(float(widget.text())))
            elif isinstance(old, float): setattr(self.settings, key, float(widget.text()))
        SETTINGS_STORE.save(self.settings); self._apply_runtime_settings(); self.log("OK", "[SETTINGS] settings saved")
        dialog.close()

    def _apply_runtime_settings(self) -> None:
        self.rest_timer.setInterval(self.settings.rest_poll_ms)
        self.account_timer.setInterval(self.settings.balances_poll_ms)
        self.active_sync_timer.setInterval(self.settings.active_order_poll_ms)
        self.account.debug_api_logs = self.settings.debug_api_logs
        self.ws.max_ws_age_ms = self.settings.max_ws_age_ms
        self._apply_guard_mode_preset()
        self.market_health_bid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_mid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_unstable_bid_ticks = abs(self.settings.max_negative_bid_delta)
        self.market_health_negative_mid_ticks = abs(self.settings.max_negative_mid_delta)
        self.market_health_min_spread_lifetime_ms = self.settings.min_spread_lifetime_ms

    def _export_settings(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", "settings_export.json", "JSON (*.json)")
        if not path:
            return
        SETTINGS_STORE.export_settings_json(path)
        self.log("INFO", f"[SETTINGS] exported path={path}")

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", "", "JSON (*.json)")
        if not path:
            return
        try:
            imported = SETTINGS_STORE.import_settings_json(path)
        except Exception as exc:
            self.log("ERROR", f"[SETTINGS] import failed reason={exc}")
            return
        self.settings = imported
        self._apply_runtime_settings()
        self.log("INFO", f"[SETTINGS] imported path={path}")

    def toggle_runtime(self) -> None:
        self.runtime_active = not self.runtime_active
        if self.runtime_active:
            self.ws.start(); self.start_stop_btn.setText("STOP"); self.start_stop_btn.setProperty("kind", "stop"); self.log("OK", "START")
            self._repair_runtime_state()
            if self.position_qty > 0:
                self.log("WARNING", f"[EXEC] START resume exit qty={self.position_qty:.6f}")
                self.fsm_state = "PLACE_SELL"
        else: self.ws.stop(); self.start_stop_btn.setText("START"); self.start_stop_btn.setProperty("kind", "start"); self.cancel_all(); self.log("WARNING", "STOP")
        self.start_stop_btn.style().polish(self.start_stop_btn)

    def cancel_all(self) -> None:
        self.log("WARNING", "cancel all requested")
        if self.active_order.get("orderId"):
            try:
                if self.panic_exit_final and self.active_order.get("side") == "SELL":
                    self.log("WARNING", "[EXEC] STOP canceled panic order, position remains open")
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            except Exception:
                pass
            self.sync_active_order(force=True)
        if self.panic_exit_final:
            self.panic_exit_final = False
            self.panic_exit_order_id = 0
            self.panic_exit_price = 0.0
            self.panic_exit_started_ms = 0
            self.panic_escalated_once = False
            self.last_panic_wait_log_ms = 0
        self.active_order = {}
        self.fsm_state = "IDLE"
        self.runtime_active = False
        if self.position_qty > 0:
            self.position_state = "EXIT_FAILED"
            self.log("WARNING", f"[EXEC] STOP with open inventory qty={self.position_qty:.6f}")
        else:
            self.position_state = "FLAT"
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
            has_active_runtime = self.runtime_active or bool(self.active_order.get("orderId"))
            if not has_active_runtime and not force and now_ms - self.last_open_orders_sync_ms < 5000:
                return
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

    def _find_open_sell_order(self) -> dict:
        for order in self.sync_open_orders:
            if str(order.get("side", "")).upper() == "SELL":
                return order
        return {}

    def _adopt_open_sell_order(self, sell_order: dict) -> None:
        now = int(time.time() * 1000)
        order_id = int(sell_order.get("orderId", 0) or 0)
        if order_id <= 0:
            return
        price = float(sell_order.get("price", 0.0) or 0.0)
        qty = float(sell_order.get("origQty", 0.0) or 0.0)
        self.active_order = {
            "orderId": order_id,
            "side": "SELL",
            "price": price,
            "qty": qty,
            "create_ms": int(sell_order.get("time") or now),
            "state": str(sell_order.get("status", "NEW")),
            "type": str(sell_order.get("type", "LIMIT")),
        }
        self.position_sell_order_id = order_id
        self.position_state = "SELL_PENDING"
        self.fsm_state = "WAIT_SELL_FILL"
        self.log("INFO", f"[EXEC] ADOPT SELL orderId={order_id} qty={qty:.6f} price={price:.2f}")

    def _apply_filled_from_sync(self, order_status: dict) -> None:
        side = self.active_order.get("side")
        self.log("OK", "[SYNC] order filled detected")
        if side == "BUY":
            self.log("OK", "[SYNC] BUY filled on exchange")
            total_exec = float(order_status.get("executedQty", 0.0) or 0.0)
            if total_exec <= 0:
                self.log("WARNING", "[EXEC] BLOCK reason=buy_filled_zero_qty")
                self.position_state = "FLAT"
                self.fsm_state = "ERROR"
                self.active_order = {}
                return
            self._handle_buy_fill_update(order_status)
            self.position_entry_avg = self._recalc_entry_avg_from_chunks()
            self.position_state = "POSITION_OPEN" if self.position_qty > 0 else "FLAT"
            if self.position_qty > 0:
                self.fsm_state = "PLACE_SELL"
        elif side == "SELL":
            self.log("OK", "[SYNC] SELL filled on exchange")
            self._handle_sell_fill_update(order_status)
            self._recalc_position_from_chunks()
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

    def on_ws_book(self, bid: float, ask: float, ts: int) -> None:
        now_monotonic = time.monotonic()
        if self.last_ws_bid == bid and self.last_ws_ask == ask:
            self.ws_dropped_ticks += 1
            return
        self.state.snapshot.bid = bid
        self.state.snapshot.ask = ask
        self.state.snapshot.updated_ms = ts
        self.state.snapshot.source = "WS"
        self.state.last_ws_ms = ts
        self.state.last_ws_monotonic = now_monotonic
        self.ws_tick_count += 1
        self.ws_raw_ticks_total += 1
        elapsed = now_monotonic - self.ws_tick_window_start_monotonic
        if elapsed >= 1.0:
            self.ws_ticks_per_sec = self.ws_tick_count / elapsed
            self.ws_tick_count = 0
            self.ws_tick_window_start_monotonic = now_monotonic
        self.last_ws_tick_monotonic = now_monotonic
        self.last_ws_bid = bid
        self.last_ws_ask = ask
    def on_ws_status(self, status: str) -> None:
        self.state.ws_status = status

    def _apply_fifo_close_result(self, pnl_delta: float) -> None:
        epsilon = self._inventory_epsilon_qty()
        if abs(pnl_delta) <= epsilon:
            return
        self.session_realized_pnl += pnl_delta
        self.realized_u = pnl_delta
        self.cycle_realized_pnl += pnl_delta
        self.cycle_has_fifo_close = True
        self.log("OK", f"[EXEC] REALIZED pnl={pnl_delta:+.6f}")

    def _finalize_cycle_if_flat(self, active_sell_qty: float = 0.0) -> None:
        epsilon = self._inventory_epsilon_qty()
        no_chunks = len(self.inventory_chunks) == 0
        inventory_qty = self._recalc_position_from_chunks()
        is_flat = no_chunks and inventory_qty <= epsilon and active_sell_qty <= epsilon
        if not is_flat:
            return
        self._reset_sell_accounting("flat", reset_panic_order_id=True)
        cycle_pnl = self.cycle_realized_pnl
        if abs(cycle_pnl) <= epsilon and not self.cycle_has_fifo_close:
            self.log("INFO", "[EXEC] CYCLE SKIP no_fifo_pnl")
            return
        self.closed_cycles += 1
        if cycle_pnl > epsilon:
            self.wins += 1
        elif cycle_pnl < -epsilon:
            self.losses += 1
        self.last_pnl = cycle_pnl
        self.log("OK", f"[EXEC] CYCLE CLOSED pnl={cycle_pnl:+.6f}")
        if cycle_pnl < -epsilon:
            self._start_entry_guard_cooldown("loss_cycle")
        self.log("INFO", f"[EXEC] SESSION realized={self.session_realized_pnl:+.6f} wins={self.wins} losses={self.losses}")
        self.cycle_realized_pnl = 0.0
        self.cycle_has_fifo_close = False

    def _inventory_epsilon_qty(self) -> float:
        step = float(self.filters.get("stepSize", 0.0) or 0.0)
        return max(step * 1.5, 0.000001)

    def _reconcile_flat_balance(self) -> bool:
        epsilon = self._inventory_epsilon_qty()
        btc_free = float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0)
        btc_locked = float(self.balances.get("BTC", {}).get("locked", 0.0) or 0.0)
        return (btc_free + btc_locked) <= epsilon

    def _cleanup_inventory_if_drained(self) -> bool:
        epsilon = self._inventory_epsilon_qty()
        inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
        residual_qty = max(self.position_qty, inventory_qty, 0.0)
        if residual_qty > epsilon:
            return False
        self.inventory_chunks = []
        self.position_qty = 0.0
        self.position_entry_avg = 0.0
        self.active_order = {}
        self.position_state = "FLAT"
        self._reset_sell_accounting("inventory_drained", reset_panic_order_id=True)
        self.panic_exit_final = False
        self.panic_exit_order_id = 0
        self.panic_exit_price = 0.0
        self.panic_exit_started_ms = 0
        self.panic_escalated_once = False
        self.last_panic_wait_log_ms = 0
        self.log("INFO", f"[EXEC] INVENTORY DRAINED epsilon_cleanup qty={residual_qty:.6f}")
        if self._reconcile_flat_balance():
            self.position_state = "FLAT"
        return True

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
        ws_age = self.state.monotonic_age_ms(self.state.last_ws_monotonic)
        ws_ok = ws_age is not None and ws_age <= self.settings.max_ws_age_ms and self.state.ws_status == "CONNECTED"
        if ws_ok:
            return
        try:
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.binance_symbol)
            now_monotonic = time.monotonic()
            ws_fresh = self.state.last_ws_monotonic is not None and self.state.monotonic_age_ms(self.state.last_ws_monotonic) <= self.settings.max_ws_age_ms
            if ws_fresh:
                return
            if self.state.rest_status == "ERROR":
                self.log("OK", "REST restored")
            self.state.last_rest_ms = ts
            self.state.last_rest_monotonic = now_monotonic
            self.state.rest_status = "OK"
            self.state.snapshot.bid = bid
            self.state.snapshot.ask = ask
            self.state.snapshot.updated_ms = ts
            self.state.snapshot.source = "REST"
            self.rest_fallback_count += 1
        except Exception:
            if self.state.rest_status != "ERROR":
                self.log("ERROR", "REST lost")
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

    def _tick_size(self) -> float:
        tick = float(self.filters.get("tickSize", 0.0) or 0.0)
        return tick if tick > 0 else float(CONFIG.tick_size_default)

    def _round_price_down(self, price: object) -> float:
        tick = float(self.filters.get("tickSize", 0.0) or 0.0)
        if tick <= 0:
            tick = 0.01
        try:
            value = float(price)
        except (TypeError, ValueError):
            return 0.0
        if value <= 0:
            return 0.0
        try:
            return float(int(value / tick) * tick)
        except Exception:
            return value

    def _force_exit_price(self, bid_now: float, ask_now: float) -> float:
        tick = self._tick_size()
        aggressive = float(self.settings.aggressive_exit_offset)
        return max(bid_now + tick, ask_now - aggressive)

    def _is_market_near_sell_target(self, bid_now: float, target_price: float) -> bool:
        tick = self._tick_size()
        return bid_now > 0 and target_price > 0 and (target_price - bid_now) <= (tick * self.sell_hold_near_ticks)

    def _cap_soft_sell_reprice(self, old_price: float, candidate_price: float) -> float:
        tick = self._tick_size()
        if old_price <= 0 or tick <= 0:
            return candidate_price
        soft_floor = old_price - tick
        return max(candidate_price, soft_floor)

    def _remaining_to_sell(self) -> float:
        inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
        self.position_qty = inventory_qty
        epsilon = self._inventory_epsilon_qty()
        has_active_sell = bool(self.active_order.get("orderId")) and self.active_order.get("side") == "SELL"
        if inventory_qty > epsilon and not has_active_sell and self.sell_reported_qty >= inventory_qty:
            self.sell_reported_qty = 0.0
            self.log("WARNING", "[EXEC] SELL ACCOUNTING STALE RESET")
        remaining = max(inventory_qty - self.sell_reported_qty, 0.0)
        return 0.0 if remaining <= epsilon else remaining

    def _reset_sell_accounting(self, reason: str, reset_panic_order_id: bool = False) -> None:
        self.sell_reported_qty = 0.0
        self.sell_target_qty = 0.0
        self.position_sell_order_id = 0
        self.sell_reprice_count = 0
        self.sell_recovery_in_progress = False
        self.sell_cancel_in_progress = False
        if reset_panic_order_id:
            self.panic_exit_order_id = 0
        self.log("INFO", f"[EXEC] SELL ACCOUNTING RESET {reason}")

    def _recalc_position_from_chunks(self) -> float:
        epsilon = self._inventory_epsilon_qty()
        self.position_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
        if self.position_qty <= epsilon:
            self.position_qty = 0.0
        return self.position_qty

    def _recalc_entry_avg_from_chunks(self) -> float:
        total_qty = self._recalc_position_from_chunks()
        if total_qty <= 0:
            self.position_entry_avg = 0.0
            return 0.0
        weighted = sum(chunk.qty * chunk.entry_price for chunk in self.inventory_chunks if chunk.qty > 0)
        self.position_entry_avg = weighted / total_qty
        return self.position_entry_avg

    def _repair_runtime_state(self) -> None:
        inventory_qty = self._recalc_position_from_chunks()
        self._recalc_entry_avg_from_chunks()
        active_side = str(self.active_order.get("side", ""))
        has_active_order = bool(self.active_order.get("orderId"))
        if inventory_qty <= self._inventory_epsilon_qty():
            if self.position_state in {"POSITION_OPEN", "EXIT_FAILED"}:
                self.log("WARNING", "[EXEC] STATE REPAIR reason=zero_inventory_not_flat")
            self.position_state = "FLAT"
            if self.fsm_state == "BUY_PENDING" and not (has_active_order and active_side == "BUY"):
                self.log("WARNING", "[EXEC] STATE REPAIR reason=buy_pending_without_buy")
                self.fsm_state = "DONE"
        else:
            if self.position_state == "FLAT":
                self.log("WARNING", "[EXEC] STATE REPAIR reason=inventory_cannot_be_flat")
                self.position_state = "POSITION_OPEN"
            if self.position_state == "BUY_PENDING" and not (has_active_order and active_side == "BUY"):
                self.log("WARNING", "[EXEC] STATE REPAIR reason=buy_pending_with_inventory")
                self.position_state = "POSITION_OPEN"
            if self.fsm_state == "WAIT_READY" and not (has_active_order and active_side == "SELL"):
                self.log("INFO", f"[EXEC] EXIT RECOVERY inventory_no_sell qty={inventory_qty:.6f}")
                self.fsm_state = "PLACE_SELL"

    def _add_inventory_chunk(self, qty: float, entry_price: float, now_ms: int) -> None:
        if qty <= 0:
            return
        self.inventory_chunks.append(InventoryChunk(qty=qty, entry_price=entry_price, created_ms=now_ms))
        self.log("OK", f"[EXEC] CHUNK ADD qty={qty:.6f} entry={entry_price:.2f}")
        self.log("INFO", f"[EXEC] CHUNK COUNT n={len(self.inventory_chunks)}")
        self._recalc_entry_avg_from_chunks()

    def _consume_inventory_fifo(self, sell_qty: float, sell_price: float) -> float:
        epsilon = self._inventory_epsilon_qty()
        remaining = max(sell_qty, 0.0)
        realized = 0.0
        while remaining > epsilon and self.inventory_chunks:
            chunk = self.inventory_chunks[0]
            taken = min(chunk.qty, remaining)
            chunk_pnl = (sell_price - chunk.entry_price) * taken
            realized += chunk_pnl
            self.log("OK", f"[EXEC] FIFO CLOSE qty={taken:.6f} entry={chunk.entry_price:.2f} exit={sell_price:.2f} pnl={chunk_pnl:+.6f}")
            chunk.qty = max(chunk.qty - taken, 0.0)
            remaining -= taken
            if chunk.qty <= epsilon:
                self.log("INFO", "[EXEC] CHUNK DRAINED")
                self.inventory_chunks.pop(0)
        self._recalc_entry_avg_from_chunks()
        self._apply_fifo_close_result(realized)
        return realized

    def _sync_sell_target_qty(self) -> float:
        self.sell_target_qty = self._remaining_to_sell()
        return self.sell_target_qty

    def _panic_exit_final(self, now_ms: int, reason: str, sl_mode: bool = False) -> None:
        if self.panic_exit_final and self.panic_exit_order_id:
            self.log("WARNING", f"[EXEC] PANIC HOLD active orderId={self.panic_exit_order_id}")
            self.fsm_state = "WAIT_SELL_FILL"
            return
        bid_now = float(self.state.snapshot.bid or 0.0)
        ask_now = float(self.state.snapshot.ask or 0.0)
        if sl_mode:
            self.log("ERROR", "[EXEC] HARD SL triggered")
            self.log("WARNING", "[EXEC] PANIC EXIT sl_mode")
        else:
            self.log("WARNING", f"[EXEC] PANIC EXIT {reason}")
        new_price = self._force_exit_price(bid_now, ask_now)
        sell_qty = float(Decimal(str(self.position_qty)))
        self.log("WARNING", f"[EXEC] FORCE EXIT price={new_price:.2f}")
        self.log("OK", f"[EXEC] PLACE SELL price={new_price:.2f} qty={sell_qty:.6f}")
        try:
            o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(sell_qty))
        except Exception as exc:
            self.log("ERROR", f"[EXEC] PANIC SELL FAILED reason={exc}")
            self.fsm_state = "EXIT_FAILED"
            self.position_state = "EXIT_FAILED"
            return
        self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(new_price), "qty": float(sell_qty), "create_ms": now_ms, "state": "NEW", "type": "LIMIT"}
        self.position_sell_order_id = int(self.active_order["orderId"])
        self.last_sell_reprice_ms = now_ms
        self.exit_started_ms = now_ms
        self.exit_mode = "PANIC"
        self.panic_exit_final = True
        self._start_entry_guard_cooldown("panic_exit")
        self.panic_exit_order_id = int(self.active_order["orderId"])
        self.panic_exit_price = float(new_price)
        self.panic_exit_started_ms = now_ms
        self.panic_escalated_once = False
        self.last_panic_wait_log_ms = 0
        self.position_state = "SELL_PENDING"
        self.sell_reported_qty = 0.0
        self.log("WARNING", f"[EXEC] PANIC START price={new_price:.2f}")
        self.log("OK", f"[EXEC] SELL ORDER SENT orderId={self.active_order['orderId']}")
        self.fsm_state = "WAIT_SELL_FILL"

    def _handle_sell_fill_update(self, order: dict[str, object]) -> None:
        epsilon = self._inventory_epsilon_qty()
        executed_qty = float(order.get("executedQty", 0.0) or 0.0)
        sell_delta = max(executed_qty - self.sell_reported_qty, 0.0)
        if sell_delta <= epsilon:
            return
        sell_quote = float(order.get("cummulativeQuoteQty", 0.0) or 0.0)
        avg_sell_price = (sell_quote / executed_qty) if executed_qty > 0 else float(self.active_order.get("price", 0.0))
        self.avg_exit = avg_sell_price
        self._consume_inventory_fifo(sell_delta, avg_sell_price)
        self.sell_reported_qty = executed_qty
        self._finalize_cycle_if_flat()

    def _handle_buy_fill_update(self, order: dict[str, object]) -> None:
        epsilon = self._inventory_epsilon_qty()
        executed_qty = float(order.get("executedQty", 0.0) or 0.0)
        cummulative_quote_qty = float(order.get("cummulativeQuoteQty", 0.0) or 0.0)
        delta_qty = executed_qty - self.buy_reported_qty
        if delta_qty <= epsilon:
            return
        delta_quote = cummulative_quote_qty - self.buy_reported_quote
        avg_price = (delta_quote / delta_qty) if delta_qty > epsilon else float(self.active_order.get("price", 0.0))
        self.log("OK", f"[EXEC] BUY FILL UPDATE delta={delta_qty:.6f} avg={avg_price:.2f}")
        self._add_inventory_chunk(delta_qty, avg_price, int(time.time() * 1000))
        self.buy_filled_qty = executed_qty
        self.buy_reported_qty = executed_qty
        self.buy_reported_quote = cummulative_quote_qty
        self.avg_entry = self._recalc_entry_avg_from_chunks()
        self.position_buy_order_id = int(self.active_order.get("orderId", 0) or 0)
        self.position_state = "POSITION_OPEN" if self.position_qty > epsilon else "FLAT"
        self.log("OK", f"[EXEC] INVENTORY qty={self.position_qty:.6f}")
        if self.position_qty > epsilon and not (self.active_order.get("orderId") and self.active_order.get("side") == "SELL"):
            self.log("OK", f"[EXEC] SELL REQUIRED inventory={self.position_qty:.6f}")
            self.fsm_state = "PLACE_SELL"

    def _handle_sell_filled(self, st: dict[str, object], order_ref: int) -> None:
        self._handle_sell_fill_update(st)
        sell_qty = float(st.get("executedQty", 0.0) or 0.0)
        self.log("OK", f"[EXEC] SELL FILLED id={order_ref}")
        remaining = max(self.position_qty, 0.0)
        self.log("OK", f"[EXEC] SELL FILLED qty={sell_qty:.6f} remaining={remaining:.6f}")
        self.active_order = {}
        self.buy_filled_qty = 0.0
        self.sell_reported_qty = 0.0
        if remaining <= self._inventory_epsilon_qty() and self._cleanup_inventory_if_drained():
            self.fsm_state = "WAIT_READY" if self.runtime_active else "DONE"
        elif remaining <= 0:
            self.position_qty = 0.0
            self.position_state = "FLAT"
            if self.panic_exit_final:
                self.log("OK", "[EXEC] PANIC FILLED")
                self.log("INFO", "[EXEC] PANIC RESET")
                self.panic_exit_final = False
                self.panic_exit_order_id = 0
                self.panic_exit_price = 0.0
                self.panic_exit_started_ms = 0
                self.panic_escalated_once = False
                self.last_panic_wait_log_ms = 0
            self.fsm_state = "WAIT_READY" if self.runtime_active else "DONE"
        else:
            self.position_qty = remaining
            self.position_state = "POSITION_OPEN"
            self.fsm_state = "PLACE_SELL"
        self._finalize_cycle_if_flat()

    def handle_sell_timeout_recovery(self, now_ms: int) -> None:
        if self.sell_recovery_in_progress:
            self.log("INFO", "[EXEC] RECOVERY WAIT in_progress")
            return
        self.sell_recovery_in_progress = True
        try:
            if self.position_qty <= self._inventory_epsilon_qty():
                self.log("ERROR", "[EXEC] EXIT FAILED no_position_after_timeout")
                self.position_state = "EXIT_FAILED"
                self.fsm_state = "SELL_TIMEOUT"
                return
            order_id = int(self.active_order.get("orderId", 0) or 0)
            if self.panic_exit_final:
                self.log("WARNING", f"[EXEC] PANIC HOLD active orderId={order_id}")
                self.fsm_state = "WAIT_SELL_FILL"
                return
            old_price = float(self.active_order.get("price", 0.0) or 0.0)
            bid_now = float(self.state.snapshot.bid or 0.0)
            sl_ticks = max(int(getattr(self.settings, "stop_loss_ticks", 6)), 0)
            sl_price = float(self.position_entry_avg) - (self._tick_size() * sl_ticks)
            if self._is_market_near_sell_target(bid_now, old_price):
                hold_elapsed = now_ms - int(self.exit_started_ms or 0)
                if hold_elapsed < self.sell_hold_window_ms:
                    self.log("INFO", "[EXEC] SELL HOLD near target")
                    self.log("INFO", "[EXEC] RECOVERY WAIT market_near_target")
                    return
            self.log("WARNING", f"[EXEC] SELL TIMEOUT orderId={order_id}")
            if now_ms - self.last_sell_reprice_ms < int(self.settings.sell_reprice_cooldown_ms):
                self.log("ERROR", "[EXEC] EXIT FAILED sell_reprice_cooldown_active")
                self.position_state = "EXIT_FAILED"
                self.fsm_state = "EXIT_FAILED"
                return
            if order_id:
                if self.sell_cancel_in_progress:
                    self.log("INFO", "[EXEC] RECOVERY WAIT cancel_in_progress")
                    return
                self.sell_cancel_in_progress = True
                try:
                    self.log("WARNING", f"[EXEC] CANCEL SELL orderId={order_id}")
                    self.account.cancel_order(CONFIG.binance_symbol, order_id)
                finally:
                    self.sell_cancel_in_progress = False
                final = self.account.get_order(CONFIG.binance_symbol, order_id)
                final_status = str(final.get("status", "UNKNOWN"))
                self.log("INFO", f"[EXEC] SELL FINAL STATUS status={final_status} orderId={order_id}")
                if final_status == "FILLED":
                    self._handle_sell_filled(final, order_id)
                    return
                prev_sell_reported_qty = self.sell_reported_qty
                self._handle_sell_fill_update(final)
                sell_delta = max(self.sell_reported_qty - prev_sell_reported_qty, 0.0)
                if final_status == "PARTIALLY_FILLED" and self.position_qty > 0:
                    self.log("INFO", f"[EXEC] SELL REPLACE remaining={self.position_qty:.6f}")
                elif final_status not in {"CANCELED", "EXPIRED", "NEW", "PARTIALLY_FILLED"}:
                    self.log("ERROR", f"[EXEC] EXIT FAILED cancel_unexpected_status={final_status}")
                    self.position_state = "EXIT_FAILED"
                    self.fsm_state = "EXIT_FAILED"
                    return
            self._inc_canceled_attempt("timeout_sell")
            self._inc_canceled_attempt("canceled_sell")
            self.sell_timeouts += 1
            ask_now = float(self.state.snapshot.ask or 0.0)
            aggressive = float(self.settings.aggressive_exit_offset)
            tick = self._tick_size()
            min_profit_ticks = max(int(self.settings.min_profit_ticks), 0)
            safe_exit_price = bid_now + tick
            min_exit_price = float(self.position_entry_avg) + (tick * min_profit_ticks)
            self.log("INFO", "[EXEC] SELL TIMEOUT check fast_exit")
            if safe_exit_price >= min_exit_price:
                self.log("INFO", f"[EXEC] FAST SAFE EXIT price={safe_exit_price:.2f} entry={float(self.position_entry_avg):.2f} bid={bid_now:.2f}")
                candidate_price = safe_exit_price
            elif (bid_now + tick) >= float(self.position_entry_avg):
                candidate_price = max(bid_now + tick, float(self.position_entry_avg))
                self.log("INFO", f"[EXEC] BREAK EVEN EXIT price={candidate_price:.2f} entry={float(self.position_entry_avg):.2f}")
            elif bid_now > 0 and bid_now <= sl_price:
                self.log("WARNING", "[EXEC] FORCE EXIT hard_sl_triggered")
                self._panic_exit_final(now_ms, "hard_sl_timeout", sl_mode=True)
                return
            else:
                self.log("INFO", f"[EXEC] PANIC SKIP no_hard_sl bid={bid_now:.2f} entry={float(self.position_entry_avg):.2f}")
                if self.sell_reprice_count >= int(self.settings.max_sell_reprices):
                    self.fsm_state = "WAIT_SELL_FILL"
                    return
                candidate_price = max(bid_now + tick, ask_now - aggressive)
            new_price = self._cap_soft_sell_reprice(old_price, candidate_price)
            sell_qty = float(Decimal(str(self._sync_sell_target_qty())))
            min_notional = float(self.filters.get("minNotional", 0.0) or 0.0)
            if sell_qty <= self._inventory_epsilon_qty() or (bid_now > 0 and (sell_qty * bid_now) < min_notional):
                self.log("INFO", f"[EXEC] SKIP MICRO SELL epsilon={self._inventory_epsilon_qty():.6f}")
                self._cleanup_inventory_if_drained()
                self._finalize_cycle_if_flat()
                self.fsm_state = "WAIT_READY"
                return
            self.sell_reprice_count += 1
            self.log("WARNING", f"[EXEC] SELL REPRICE old={old_price:.2f} new={new_price:.2f} count={self.sell_reprice_count}")
            self.log("INFO", f"[EXEC] SELL REPLACE remaining={sell_qty:.6f}")
            self.log("OK", f"[EXEC] PLACE SELL price={new_price:.2f} qty={sell_qty:.6f}")
            o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(sell_qty))
            self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(new_price), "qty": float(sell_qty), "create_ms": now_ms, "state": "NEW", "type": "LIMIT"}
            self.position_sell_order_id = int(self.active_order["orderId"])
            self.last_sell_reprice_ms = now_ms
            self.exit_started_ms = now_ms
            if self.exit_mode != "PANIC":
                self.exit_mode = "AGGRESSIVE"
            self.position_state = "SELL_PENDING"
            self.log("OK", f"[EXEC] SELL ORDER SENT orderId={self.active_order['orderId']}")
            self.fsm_state = "WAIT_SELL_FILL"
        finally:
            self.sell_recovery_in_progress = False

    def _refresh_ui(self) -> None:
        now_monotonic = time.monotonic()
        self.gui_refresh_ms = max(int((now_monotonic - self.last_gui_refresh_monotonic) * 1000), 0)
        self.last_gui_refresh_monotonic = now_monotonic
        bid = self.state.snapshot.bid; ask = self.state.snapshot.ask; spread = self.state.snapshot.spread
        spread_state = "BAD" if spread is None else ("HOT" if spread >= self.settings.min_spread + 0.02 else ("READY" if spread >= self.settings.min_spread else "WATCH"))
        ws_age = self.state.monotonic_age_ms(self.state.last_ws_monotonic)
        ws_connected = self.state.ws_status == "CONNECTED"
        ws_stale = ws_age is None or ws_age > self.settings.max_ws_age_ms
        self.stale_reason = "NO_WS" if ws_age is None else ("WS_STALE" if ws_stale else "OK")
        ws_ok = ws_connected and not ws_stale
        ws_text = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        market_snapshot_source = "WS" if ws_ok else self.state.snapshot.source
        self.conn["API"].setText(self.api_status); self.conn["REST"].setText(self.state.rest_status); self.conn["WS"].setText(ws_text); self.conn["Source"].setText(market_snapshot_source); self.conn["Latency"].setText(f"{self.account.time_offset_ms} ms")
        self.conn["WS age"].setText("N/A" if ws_age is None else f"{ws_age} ms")
        self.conn["WS tps"].setText(f"{self.ws_ticks_per_sec:.2f}")
        self.conn["GUI refresh"].setText(f"{self.gui_refresh_ms} ms")
        self.bid_v.setText("N/A" if bid is None else f"{bid:.2f}"); self.ask_v.setText("N/A" if ask is None else f"{ask:.2f}"); self.spr_v.setText("N/A" if spread is None else f"{spread:.2f}")
        self.spread["Status"].setText(spread_state); self.spread["Spread"].setText("N/A" if spread is None else f"{spread:.2f}")
        cap = (spread - self.settings.entry_offset - self.settings.exit_offset) if spread is not None else None
        self.spread["Capture"].setText("N/A" if cap is None else f"{cap:.2f}")
        age_ms = ws_age if ws_ok and ws_age is not None else max(int(time.time() * 1000) - self.state.snapshot.updated_ms, 0)
        self.spread["Lifetime"].setText(f"{age_ms}ms" if age_ms < 1000 else f"{age_ms/1000:.1f}s")
        self.spread["Source"].setText(market_snapshot_source); self.spread["Latency"].setText(time.strftime("%H:%M:%S"))
        self.runtime["LIVE"].setText("ON" if self.settings.live_enabled else "OFF")
        self.runtime["FSM"].setText(self.fsm_state)
        self.runtime["Mode"].setText("LIVE SINGLE" if self.settings.live_enabled else "ANALYTICS")
        self.runtime["Position state"].setText(self.position_state)
        self.runtime["Position qty"].setText(self._fmt(self.position_qty, 6))
        self.runtime["Entry avg"].setText(self._fmt(self.position_entry_avg, 6))
        self.runtime["Market Health"].setText(self.market_health_state)
        self.runtime["Entry Guard"].setText(self.settings.guard_mode)
        self.runtime["Guard state"].setText(self.entry_guard_state)
        self.runtime["Guard reason"].setText(self.entry_guard_reason)
        self.runtime["Stable snaps"].setText(f"{self.entry_guard_stable_count}/{self.settings.stable_snapshots_required}")
        now_ms = int(time.time() * 1000)
        cooldown_left = max(self.entry_guard_cooldown_until_ms - now_ms, 0)
        self.runtime["Cooldown ms"].setText(str(cooldown_left))
        self.runtime["Entry mode"].setText(str(self.settings.entry_mode))
        active_buy_age_ms = 0
        if self.active_order.get("orderId") and self.active_order.get("side") == "BUY":
            active_buy_age_ms = max(now_ms - int(self.active_order.get("create_ms", now_ms) or now_ms), 0)
        self.runtime["BUY age"].setText(f"{active_buy_age_ms}ms")
        self.runtime["Entry reprices"].setText(str(self.entry_reprice_count))
        spread_ticks = int((float(spread or 0.0) / max(self._tick_size(), 1e-9))) if spread is not None else 0
        fill_hint = "HIGH" if spread_ticks >= max(int(self.settings.min_spread_after_entry_ticks), 1) + 2 else ("MED" if spread_ticks >= max(int(self.settings.min_spread_after_entry_ticks), 1) else "LOW")
        self.runtime["Fill hint"].setText(fill_hint)
        self.runtime["Entry reason"].setText(self.entry_last_reason)
        can_recompute_plan = self.runtime_active or self.position_qty > 0 or bool(self.active_order.get("orderId"))
        if can_recompute_plan and (now_ms - self.last_plan_recompute_ms >= 250):
            self._cached_plan = self.trade_math.build_plan(self.state, self.settings, self.filters, self.balances, self.api_status)
            self.last_plan_recompute_ms = now_ms
        plan = self._cached_plan if can_recompute_plan else None
        plan_status = plan.status if plan else "STOPPED"
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
        self.plan["Entry"].setText("N/A" if not plan or plan.entry_price is None else f"{plan.entry_price:.2f}")
        self.plan["Exit"].setText("N/A" if not plan or plan.exit_price is None else f"{plan.exit_price:.2f}")
        self.plan["Qty BTC"].setText(self._fmt(plan.qty_btc, 6) if plan else "0")
        self.plan["Order U"].setText(self._fmt(plan.order_size_u, 2) if plan else "0")
        self.plan["Age"].setText(f"{ready_age}ms")
        self.plan["Profit U"].setText("N/A" if not plan or plan.expected_profit_u is None else self._fmt(plan.expected_profit_u, 6))
        plan_key = f"{plan.status}:{self._fmt(plan.order_size_u,2)}:{self._fmt(plan.qty_btc,6)}:{self._fmt(plan.required_u or 0.0,2)}" if plan else "STOPPED"
        self._repair_runtime_state()
        if self.position_qty > 0 and not (self.active_order.get("orderId") and self.active_order.get("side") == "SELL") and self.runtime_active:
            if self.fsm_state != "PLACE_SELL":
                self.log("INFO", f"[EXEC] EXIT RECOVERY inventory_no_sell qty={self.position_qty:.6f}")
            self.fsm_state = "PLACE_SELL"
        if self.runtime_active and self.fsm_state == "DONE":
            self.fsm_state = "WAIT_READY"
        if self.runtime_active and self.fsm_state == "ERROR" and now_ms >= self.order_retry_blocked_until_ms:
            self.fsm_state = "DONE"
        if self.runtime_active and self.fsm_state == "IDLE":
            self.log("INFO", f"[EXEC] LIVE {'ON' if self.settings.live_enabled else 'OFF'}")
            self.log("INFO", "[EXEC] WAIT READY")
            self.fsm_state = "WAIT_READY"
        market_valid = ws_ok or self.state.rest_status == "OK"
        allow_buy = bool(plan) and self.settings.live_enabled and plan.status in {"READY", "HOT"} and market_valid and plan.filters_ok and (plan.required_u or 0.0) <= self.settings.max_live_exposure_u
        if self.runtime_active and self.fsm_state == "WAIT_READY" and allow_buy:
            if now_ms - self._last_health_update_ms >= 250:
                self._update_market_health(now_ms)
                self._last_health_update_ms = now_ms
            if self.position_qty > 0:
                if not plan.balance_ok:
                    self.log("WARNING", "[EXEC] BALANCE LOW ignored: exit priority")
                if now_ms - self.last_position_open_block_log_ms >= 3000:
                    self.log("WARNING", "[EXEC] BLOCK reason=position_open_no_new_buy")
                    self.last_position_open_block_log_ms = now_ms
                self.fsm_state = "DONE"
            elif self.balances.get("U", {}).get("free", 0.0) < float(plan.order_size_u or 0.0) * 1.01:
                free_u = float(self.balances.get("U", {}).get("free", 0.0) or 0.0)
                need_u = float(plan.order_size_u or 0.0) * 1.01
                self.log("WARNING", f"[EXEC] BLOCK reason=balance_low_preflight free={free_u:.8f} need={need_u:.8f}")
                self.fsm_state = "DONE"
            elif not plan.balance_ok:
                self.log("WARNING", "[EXEC] BLOCK reason=balance_low")
                self.fsm_state = "DONE"
            elif self.inventory_chunks:
                self.fsm_state = "DONE"
            elif self.active_order.get("orderId") or self.fsm_state in {"WAIT_BUY_FILL", "PLACE_SELL", "WAIT_SELL_FILL", "SELL_TIMEOUT", "ERROR_POSITION"}:
                self.fsm_state = "DONE"
            elif self.active_order.get("orderId"):
                self.log("WARNING", "[EXEC] BLOCK reason=active_order")
                self.fsm_state = "DONE"
            elif (plan.required_u or 0.0) > self.settings.max_live_exposure_u:
                self.log("WARNING", f"[EXEC] BLOCK reason=required_u_gt_max_exposure_u required_u={(plan.required_u or 0.0):.4f} max_exposure_u={self.settings.max_live_exposure_u:.4f}")
                self.fsm_state = "DONE"
            else:
                ok_to_buy, _ = self.final_pre_buy_check(plan, now_ms)
                if not ok_to_buy:
                    self.fsm_state = "DONE"
                else:
                    try:
                        o = self.account.place_limit_order(CONFIG.binance_symbol, "BUY", float(plan.entry_price), float(plan.qty_btc))
                        now = int(time.time() * 1000)
                        self._reset_sell_accounting("new_cycle", reset_panic_order_id=self.position_qty <= 1e-12)
                        self.entry_exec_state = "ENTRY_PLACED"
                        self.entry_reprice_count = 0
                        self.last_entry_reprice_ms = now
                        self.entry_last_reason = "placed"
                        self.active_order = {"orderId": o.get("orderId"), "side": "BUY", "price": float(plan.entry_price), "qty": float(plan.qty_btc), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                        self.buy_reported_qty = 0.0
                        self.buy_reported_quote = 0.0
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
            executed_qty = float(st.get("executedQty", 0.0) or 0.0)
            prev_buy_reported_qty = self.buy_reported_qty
            self._handle_buy_fill_update(st)
            buy_delta = max(self.buy_reported_qty - prev_buy_reported_qty, 0.0)
            if buy_delta > 0:
                self.log("OK", f"[EXEC] BUY PARTIAL delta={buy_delta:.6f} total={executed_qty:.6f}")
                self.log("OK", f"[EXEC] BUY REMAINING qty={max(float(self.active_order.get('qty', 0.0)) - executed_qty, 0.0):.6f}")
                self._sync_sell_target_qty()
            if st.get("status") == "FILLED":
                buy_qty = float(Decimal(str(executed_qty)))
                if buy_qty <= 0:
                    self.log("WARNING", "[EXEC] BLOCK reason=buy_filled_zero_qty")
                    self.position_state = "FLAT"
                    self.fsm_state = "ERROR"
                    self.active_order = {}
                    return
                self.buy_filled_qty = buy_qty
                self.position_entry_avg = self._recalc_entry_avg_from_chunks()
                self.position_buy_order_id = int(self.active_order["orderId"])
                self.position_state = "POSITION_OPEN"
                self.log("OK", f"[EXEC] BUY FILLED id={int(self.active_order['orderId'])}")
                self.entry_exec_state = "ENTRY_FILLED"
                self.sell_reprice_count = 0
                self.last_sell_reprice_ms = 0
                self.exit_mode = "NORMAL"
                self.panic_exit_final = False
                self.panic_exit_order_id = 0
                self.panic_exit_price = 0.0
                self.panic_exit_started_ms = 0
                self.panic_escalated_once = False
                self.last_panic_wait_log_ms = 0
                self.fsm_state = "PLACE_SELL"
            elif now - self.entry_started_ms >= int(self.settings.buy_timeout_ms):
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
                self._handle_buy_fill_update(final)
                if final_status == "FILLED":
                    self.buy_filled_qty = executed_qty
                    self.log("OK", "[EXEC] BUY FILLED during cancel")
                    self.log("OK", f"[EXEC] BUY FILLED id={int(self.active_order['orderId'])}")
                    self.fsm_state = "PLACE_SELL"
                elif executed_qty > 0:
                    self.log("WARNING", "[EXEC] BUY REMAINDER CANCELLED")
                    self.log("INFO", f"[EXEC] SELL CONTINUES inventory={self.position_qty:.6f}")
                    self.active_order = {}
                    self.position_state = "POSITION_OPEN" if self.position_qty > 0 else "FLAT"
                    if self.position_qty > 0 and not (self.active_order.get("side") == "SELL"):
                        self.fsm_state = "PLACE_SELL"
                    else:
                        self.fsm_state = "DONE"
                else:
                    self._inc_canceled_attempt("timeout_buy")
                    self._inc_canceled_attempt("canceled_buy")
                    self.canceled_buys += 1
                    self.active_order = {}
                    self.log("WARNING", "[EXEC] BLOCK reason=buy_not_filled")
                    self.fsm_state = "DONE"
            elif st.get("status") in {"NEW", "PARTIALLY_FILLED"}:
                bid_now = float(self.state.snapshot.bid or 0.0)
                tick = self._tick_size()
                spread_now = float(self.state.snapshot.spread or 0.0)
                spread_ticks = int(spread_now / max(tick, 1e-9))
                order_price = float(self.active_order.get("price", 0.0) or 0.0)
                age_ms = now - int(self.active_order.get("create_ms", now) or now)
                min_spread_ticks = max(int(self.settings.min_spread_after_entry_ticks), 0)
                if spread_ticks < min_spread_ticks:
                    self.entry_exec_state = "ENTRY_CANCELLED"
                    self.entry_last_reason = "spread_dead"
                    self.log("WARNING", f"[EXEC] ENTRY_CANCEL reason={self.entry_last_reason}")
                    self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                    self.active_order = {}
                    self.fsm_state = "DONE"
                    return
                if bid_now > 0 and order_price > 0 and bid_now < (order_price - tick):
                    self.entry_exec_state = "ENTRY_CANCELLED"
                    self.entry_last_reason = "mid_falling"
                    self.log("WARNING", f"[EXEC] ENTRY_CANCEL reason={self.entry_last_reason}")
                    self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                    self.active_order = {}
                    self.fsm_state = "DONE"
                    return
                can_reprice = bool(self.settings.entry_reprice_enabled) and self.entry_reprice_count < int(self.settings.max_entry_reprices)
                cooldown_ok = now - self.last_entry_reprice_ms >= int(self.settings.entry_reprice_cooldown_ms)
                if can_reprice and cooldown_ok and bid_now > order_price and age_ms >= int(self.settings.buy_timeout_ms_fast):
                    self.entry_exec_state = "ENTRY_REPRICE"
                    self.entry_reprice_count += 1
                    chase = max(int(self.settings.entry_chase_ticks), 0)
                    new_price = bid_now + tick * chase
                    if spread_ticks >= int(self.settings.entry_cross_if_spread_ticks_above):
                        new_price = min(float(self.state.snapshot.ask or new_price), new_price + tick)
                        self.entry_last_reason = "wide_spread_cross"
                    else:
                        self.entry_last_reason = "bid_moved_up"
                    self.log("INFO", f"[EXEC] ENTRY_REPRICE reason={self.entry_last_reason} old={order_price:.2f} new={new_price:.2f} count={self.entry_reprice_count}")
                    self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                    o = self.account.place_limit_order(CONFIG.binance_symbol, "BUY", float(new_price), float(self.active_order.get("qty", 0.0)))
                    self.active_order = {"orderId": o.get("orderId"), "side": "BUY", "price": float(new_price), "qty": float(self.active_order.get("qty", 0.0)), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                    self.last_entry_reprice_ms = now
                    self.log("OK", f"[EXEC] ENTRY_PLACE price={new_price:.2f} qty={float(self.active_order.get('qty', 0.0)):.6f}")
        elif self.runtime_active and self.fsm_state == "PLACE_SELL":
            epsilon_qty = self._inventory_epsilon_qty()
            if self.sell_recovery_in_progress or self.sell_cancel_in_progress:
                return
            if self.active_order.get("orderId") and self.active_order.get("side") == "SELL":
                self.log("WARNING", "[EXEC] BLOCK duplicate_sell_prevented")
                self.fsm_state = "WAIT_SELL_FILL"
                return
            self.sync_active_order(force=True)
            open_sell = self._find_open_sell_order()
            if open_sell:
                self._adopt_open_sell_order(open_sell)
                return
            inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
            sell_qty = float(Decimal(str(self._sync_sell_target_qty())))
            bid_now = float(self.state.snapshot.bid or 0.0)
            self.log("INFO", f"[EXEC] SELL CHECK inventory={inventory_qty:.6f} reported={self.sell_reported_qty:.6f} remaining={sell_qty:.6f} chunks={len(self.inventory_chunks)}")
            if sell_qty <= epsilon_qty and len(self.inventory_chunks) == 0 and self.active_order.get("side") == "BUY" and self.active_order.get("orderId"):
                buy_state = str(self.active_order.get("state", ""))
                if buy_state in {"FILLED", "PARTIALLY_FILLED"}:
                    sync_buy = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                    self._handle_buy_fill_update(sync_buy)
                    inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
                    sell_qty = float(Decimal(str(self._sync_sell_target_qty())))
                    self.log("INFO", f"[EXEC] SELL CHECK resync inventory={inventory_qty:.6f} remaining={sell_qty:.6f} chunks={len(self.inventory_chunks)}")
            if inventory_qty > epsilon_qty and sell_qty <= epsilon_qty and not (self.active_order.get('orderId') and self.active_order.get('side') == "SELL"):
                self.sell_reported_qty = 0.0
                self.log("WARNING", "[EXEC] SELL ACCOUNTING STALE RESET")
                sell_qty = float(Decimal(str(self._sync_sell_target_qty())))
            min_qty = float(self.filters.get("minQty", 0.0) or 0.0)
            btc_free = float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0)
            btc_locked = float(self.balances.get("BTC", {}).get("locked", 0.0) or 0.0)
            if sell_qty > epsilon_qty and btc_locked > epsilon_qty and (btc_free + epsilon_qty) < sell_qty:
                self.sync_active_order(force=True)
                open_sell = self._find_open_sell_order()
                if open_sell:
                    self._adopt_open_sell_order(open_sell)
                else:
                    self.log("WARNING", "[EXEC] SELL BLOCK locked_balance_no_order")
                return
            min_notional = float(self.filters.get("minNotional", 0.0) or 0.0)
            if sell_qty <= epsilon_qty or (bid_now > 0 and (sell_qty * bid_now) < min_notional):
                self.log("INFO", f"[EXEC] SKIP MICRO SELL epsilon={epsilon_qty:.6f}")
                self._cleanup_inventory_if_drained()
                self._finalize_cycle_if_flat()
                self.fsm_state = "DONE"
            elif sell_qty <= 0:
                if inventory_qty > 0:
                    self.log("WARNING", "[EXEC] SELL ACCOUNTING STALE RESET")
                    self.sell_reported_qty = 0.0
                    sell_qty = float(Decimal(str(self._sync_sell_target_qty())))
                if sell_qty <= 0:
                    self.log("WARNING", "[EXEC] EXIT RECOVERY waiting inventory_resync")
                    self.fsm_state = "DONE"
            elif min_qty > 0 and sell_qty < min_qty:
                self.log("WARNING", f"[EXEC] BLOCK reason=sell_qty_invalid qty={sell_qty:.8f} minQty={min_qty:.8f}")
                self.fsm_state = "ERROR"
            else:
                tick = self._tick_size()
                tp_ticks = max(int(getattr(self.settings, "take_profit_ticks", 3)), 0)
                min_profit_ticks = max(int(self.settings.min_profit_ticks), 0)
                tp_floor_price = float(self.position_entry_avg) + (tick * max(tp_ticks, min_profit_ticks))
                sell_price = max(float(plan.exit_price), tp_floor_price)
                now = int(time.time() * 1000)
                place_signature = f"{sell_qty:.8f}@{sell_price:.2f}"
                if self.last_sell_place_signature == place_signature and now - self.last_sell_place_ms < 1000:
                    return
                self.last_sell_place_signature = place_signature
                self.last_sell_place_ms = now
                self.log("OK", f"[EXEC] PLACE SELL price={sell_price:.2f} qty={sell_qty:.6f}")
                try:
                    o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(sell_price), float(sell_qty))
                except Exception as exc:
                    self.log("WARNING", f"[EXEC] SELL PLACE FAILED reason={exc}")
                    self.sync_active_order(force=True)
                    open_sell = self._find_open_sell_order()
                    if open_sell:
                        self._adopt_open_sell_order(open_sell)
                    return
                order_id = int(o.get("orderId", 0) or 0) if isinstance(o, dict) else 0
                if order_id <= 0:
                    self.log("WARNING", "[EXEC] SELL PLACE FAILED reason=empty_orderId")
                    self.sync_active_order(force=True)
                    open_sell = self._find_open_sell_order()
                    if open_sell:
                        self._adopt_open_sell_order(open_sell)
                    return
                self.active_order = {"orderId": order_id, "side": "SELL", "price": float(sell_price), "qty": float(sell_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                self.position_sell_order_id = order_id
                self.position_state = "SELL_PENDING"
                self.sell_reported_qty = 0.0
                self.exit_mode = "NORMAL" if self.sell_reprice_count == 0 else "AGGRESSIVE"
                self.log("OK", f"[EXEC] SELL ORDER SENT orderId={order_id}")
                self.exit_started_ms = now
                self.fsm_state = "WAIT_SELL_FILL"
        elif self.runtime_active and self.fsm_state == "WAIT_SELL_FILL" and self.active_order.get("orderId"):
            now = int(time.time() * 1000)
            panic_stale_ms = 20000
            tick = self._tick_size()
            bid_now = float(self.state.snapshot.bid or 0.0)
            sl_ticks = max(int(getattr(self.settings, "stop_loss_ticks", 6)), 0)
            sl_price = float(self.position_entry_avg) - (tick * sl_ticks)
            if not self.panic_exit_final and self.position_qty > 0 and bid_now > 0 and bid_now <= sl_price:
                self.log("INFO", "[EXEC] RECOVERY WAIT hard_sl_pending_recovery")
            st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            self.active_order["state"] = st.get("status", "NEW")
            prev_sell_reported_qty = self.sell_reported_qty
            self._handle_sell_fill_update(st)
            sell_delta = max(self.sell_reported_qty - prev_sell_reported_qty, 0.0)
            if sell_delta > 0:
                self.log("OK", f"[EXEC] SELL PARTIAL delta={sell_delta:.6f}")
                self.log("OK", f"[EXEC] INVENTORY remaining={self.position_qty:.6f}")
                if self.panic_exit_final and st.get("status") == "PARTIALLY_FILLED":
                    self.log("WARNING", f"[EXEC] PANIC PARTIAL filled={sell_delta:.6f} remaining={self.position_qty:.6f}")
            remaining_to_sell = self._sync_sell_target_qty()
            active_sell_qty = float(self.active_order.get("qty", 0.0) or 0.0)
            if (
                st.get("status") in {"NEW", "PARTIALLY_FILLED"}
                and not self.panic_exit_final
                and remaining_to_sell > (active_sell_qty + 1e-9)
                and not self.sell_recovery_in_progress
                and not self.sell_cancel_in_progress
            ):
                self.log("INFO", f"[EXEC] SELL UPSIZE old={active_sell_qty:.6f} new={remaining_to_sell:.6f}")
                self.handle_sell_timeout_recovery(now)
                return
            if st.get("status") == "FILLED":
                self._handle_sell_filled(st, int(self.active_order["orderId"]))
            elif now - self.exit_started_ms >= int(self.settings.sell_timeout_ms):
                if self.panic_exit_final:
                    panic_order_id = int(self.active_order.get("orderId", 0) or 0)
                    if now - self.last_panic_wait_log_ms >= 3000:
                        self.log("WARNING", f"[EXEC] PANIC WAIT still_open orderId={panic_order_id}")
                        self.last_panic_wait_log_ms = now
                    panic_started_ms = int(self.panic_exit_started_ms or self.exit_started_ms or now)
                    panic_stale = (now - panic_started_ms) > panic_stale_ms
                    if panic_stale:
                        if self.panic_escalated_once:
                            self.log("WARNING", f"[EXEC] PANIC HOLD escalated orderId={panic_order_id}")
                        elif self.position_qty > 0 and bid_now > 0 and bid_now < float(self.panic_exit_price):
                            self.log("WARNING", f"[EXEC] PANIC STALE detected orderId={panic_order_id}")
                            self.log("WARNING", f"[EXEC] PANIC ESCALATE old={float(self.panic_exit_price):.2f} new={bid_now:.2f} qty={self.position_qty:.6f}")
                            if self.sell_recovery_in_progress or self.sell_cancel_in_progress:
                                self.log("INFO", "[EXEC] RECOVERY WAIT panic_escalation_in_progress")
                                return
                            self.sell_recovery_in_progress = True
                            self.sell_cancel_in_progress = True
                            try:
                                self.account.cancel_order(CONFIG.binance_symbol, panic_order_id)
                                final = self.account.get_order(CONFIG.binance_symbol, panic_order_id)
                                final_status = str(final.get("status", "UNKNOWN"))
                                prev_sell_reported_qty = self.sell_reported_qty
                                self._handle_sell_fill_update(final)
                                sell_delta = max(self.sell_reported_qty - prev_sell_reported_qty, 0.0)
                                if sell_delta > 0 and final_status == "PARTIALLY_FILLED":
                                    self.log("WARNING", f"[EXEC] PANIC PARTIAL filled={sell_delta:.6f} remaining={self.position_qty:.6f}")
                                if final_status == "FILLED" or self.position_qty <= 0:
                                    self._handle_sell_filled(final, panic_order_id)
                                    return
                                new_price = self._round_price_down(bid_now)
                                sell_qty = float(Decimal(str(self.position_qty)))
                                o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(sell_qty))
                                self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(new_price), "qty": float(sell_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                                self.position_sell_order_id = int(self.active_order["orderId"])
                                self.panic_exit_order_id = int(self.active_order["orderId"])
                                self.panic_exit_price = float(new_price)
                                self.panic_exit_started_ms = now
                                self.panic_escalated_once = True
                                self.exit_started_ms = now
                                self.log("WARNING", f"[EXEC] PANIC ESCALATE placed orderId={self.panic_exit_order_id}")
                            finally:
                                self.sell_cancel_in_progress = False
                                self.sell_recovery_in_progress = False
                        elif now - self.last_panic_wait_log_ms >= 3000:
                            self.log("WARNING", "[EXEC] PANIC STALE hold market_not_worse")
                    else:
                        self.log("WARNING", f"[EXEC] PANIC HOLD active orderId={panic_order_id}")
                elif self.position_qty > 0:
                    self.handle_sell_timeout_recovery(now)
                else:
                    self.log("ERROR", "[EXEC] EXIT FAILED no_position_after_timeout")
                    self.position_state = "EXIT_FAILED"
                    self.fsm_state = "SELL_TIMEOUT"
        elif self.runtime_active and self.position_qty > 0 and not self.active_order.get("orderId") and self.position_state in {"SELL_PENDING", "EXIT_FAILED"}:
            self.log("WARNING", "[EXEC] WATCHDOG position open without sell -> recover")
            self.fsm_state = "PLACE_SELL"

        self.risk["Order size U"].setText(self._fmt(self.settings.order_size_u, 2))
        self.risk["Max exposure U"].setText(self._fmt(self.settings.max_live_exposure_u, 2))
        self.risk["panic"].setText("ON" if self.settings.panic_exit else "OFF")

        self.top_status.setText("")

        u_free = float(self.balances.get("U", {}).get("free", 0.0))
        btc_free = float(self.balances.get("BTC", {}).get("free", 0.0))
        max_buy = (u_free / plan.entry_price) if plan and plan.entry_price else 0.0
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

        plan_reason = plan.reason if plan else ""
        log_key = f"{plan_status}|{plan_reason}"
        should_log = False
        if plan_status in {"READY", "HOT"} and plan_status != self.last_plan_status:
            should_log = True
        elif plan_status in {"BALANCE_LOW", "FILTER_FAIL"} and plan_status != self.last_plan_status:
            should_log = True
        elif plan_status not in {"READY", "HOT"} and (plan_reason != self.last_plan_log_key.split("|", 1)[-1] if self.last_plan_log_key else True):
            should_log = True
        if plan and self.runtime_active and should_log and (now_ms - self.last_plan_log_ms >= 2000 or log_key != self.last_plan_log_key):
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
            self.summary["Started"].setText(self.session_started_at)
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
            self.summary["Exit mode"].setText(self.exit_mode)

        rest_txt = "OK" if self.state.rest_status == "OK" else "ERROR"
        ws_txt = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        self.top_status.setText(f"BTC/U | WS ● {ws_txt} | REST ● {rest_txt} | API ● {self.api_status} | {'HOT' if spread_state=='HOT' else 'READY'}")

    def _start_entry_guard_cooldown(self, reason: str) -> None:
        now_ms = int(time.time() * 1000)
        cooldown_ms = self.settings.panic_cooldown_ms if reason == "panic_exit" else self.settings.loss_cooldown_ms
        self.entry_guard_cooldown_until_ms = max(self.entry_guard_cooldown_until_ms, now_ms + cooldown_ms)
        self.entry_guard_cooldown_reason = reason

    def _mid_delta_ticks(self) -> float:
        tick = self._tick_size()
        if tick <= 0 or len(self.recent_mids) < 2:
            return 0.0
        return (self.recent_mids[-1][1] - self.recent_mids[0][1]) / tick

    def _bid_delta_ticks(self) -> float:
        tick = self._tick_size()
        if tick <= 0 or len(self.recent_bids) < 2:
            return 0.0
        return (self.recent_bids[-1][1] - self.recent_bids[0][1]) / tick

    def final_pre_buy_check(self, plan, now_ms: int):
        if now_ms - self._last_health_update_ms >= 150:
            self._update_market_health(now_ms)
            self._last_health_update_ms = now_ms
        if not self.settings.guard_enabled:
            return True, ""
        ws_age = self.state.monotonic_age_ms(self.state.last_ws_monotonic)
        spread = float((self.state.snapshot.ask or 0.0) - (self.state.snapshot.bid or 0.0))
        bid_delta = self._bid_delta_ticks()
        mid_delta = self._mid_delta_ticks()
        source = str(self.state.snapshot.source or "NONE")
        free_u = float(self.balances.get("U", {}).get("free", 0.0) or 0.0)
        need_u = float(plan.order_size_u or 0.0) + float(self.settings.balance_safety_buffer_u)
        stable_n = min(len(self.recent_bids), len(self.recent_mids))
        self.entry_guard_stable_count = stable_n

        reason = ""
        if self.settings.require_ws_for_buy and (ws_age is None or ws_age > self.settings.max_ws_age_for_buy_ms):
            reason = "ws_stale"
        elif self.settings.live_enabled and self.settings.ws_optional_enabled and source != "WS":
            reason = "rest_source_live_ws_required"
        elif spread >= self.settings.min_spread and self.last_spread_good_since_ms > 0 and (now_ms - self.last_spread_good_since_ms) < self.settings.min_spread_lifetime_ms:
            reason = "spread_too_young"
        elif self.settings.block_on_bid_unstable and bid_delta <= self.settings.max_negative_bid_delta:
            reason = "bid_unstable"
        elif self.settings.block_on_mid_negative and mid_delta <= self.settings.max_negative_mid_delta:
            reason = "mid_momentum_negative"
        elif self.settings.block_on_snapshots_insufficient and stable_n < self.settings.stable_snapshots_required:
            reason = "snapshots_insufficient"
        elif now_ms < self.entry_guard_cooldown_until_ms:
            reason = f"cooldown_{self.entry_guard_cooldown_reason or 'active'}"
        elif self.market_health_state not in {MarketHealthState.GOOD, MarketHealthState.EXCELLENT}:
            reason = "market_health_bad"
        elif free_u < need_u:
            reason = "balance_low_preflight"

        if reason:
            prev_reason = self.entry_guard_reason
            prev_state = self.entry_guard_state
            self.entry_guard_state = "BLOCKED"
            self.entry_guard_reason = reason
            force_log = reason != prev_reason or prev_state != "BLOCKED"
            if force_log or now_ms - self.entry_guard_last_block_log_ms >= self.settings.block_log_throttle_ms:
                self.entry_guard_last_block_log_ms = now_ms
                self.log("WARNING", f"[EXEC] BLOCK_BUY reason={reason} mode={self.settings.guard_mode} ws_age={ws_age} spread={spread:.2f} stable={stable_n}/{self.settings.stable_snapshots_required} mid_delta={mid_delta:.2f} bid_delta={bid_delta:.2f}")
            return False, reason
        self.entry_guard_state = "READY"
        self.entry_guard_reason = "ok"
        return True, ""

    def _update_market_health(self, now_ms: int) -> None:
        bid = self.state.snapshot.bid
        ask = self.state.snapshot.ask
        if bid is None or ask is None:
            self.market_health_state = MarketHealthState.DANGER
            return
        tick = self._tick_size()
        if tick <= 0:
            tick = 0.01
        mid = (bid + ask) / 2.0
        self.recent_bids.append((now_ms, bid))
        self.recent_mids.append((now_ms, mid))
        while self.recent_bids and now_ms - self.recent_bids[0][0] > self.market_health_bid_window_ms:
            self.recent_bids.popleft()
        while self.recent_mids and now_ms - self.recent_mids[0][0] > self.market_health_mid_window_ms:
            self.recent_mids.popleft()

        spread = ask - bid
        if spread >= self.settings.min_spread:
            if self.last_spread_good_since_ms == 0:
                self.last_spread_good_since_ms = now_ms
        else:
            self.last_spread_good_since_ms = 0

        reasons: list[str] = []
        hard_block_reason = ""
        state = MarketHealthState.EXCELLENT

        if len(self.recent_bids) >= 2:
            bid_delta_ticks = (self.recent_bids[-1][1] - self.recent_bids[0][1]) / tick
            if bid_delta_ticks <= -self.market_health_unstable_bid_ticks:
                reasons.append(f"unstable_bid delta_ticks={bid_delta_ticks:.2f}")
                state = MarketHealthState.UNTRADEABLE
            if bid_delta_ticks <= -2:
                hard_block_reason = "falling_market"

        if len(self.recent_mids) >= 2:
            mid_delta_ticks = (self.recent_mids[-1][1] - self.recent_mids[0][1]) / tick
            if mid_delta_ticks <= -self.market_health_negative_mid_ticks:
                reasons.append(f"negative_momentum ticks={mid_delta_ticks:.2f}")
                if state != MarketHealthState.UNTRADEABLE:
                    state = MarketHealthState.DANGER
            if mid_delta_ticks <= -3:
                hard_block_reason = "falling_market"

        spread_lifetime = 0 if self.last_spread_good_since_ms == 0 else now_ms - self.last_spread_good_since_ms
        if spread >= self.settings.min_spread and spread_lifetime < 500:
            hard_block_reason = "spread_too_young"
        if spread >= self.settings.min_spread and spread_lifetime < self.market_health_min_spread_lifetime_ms:
            reasons.append(f"unstable_spread lifetime={spread_lifetime}")
            if state == MarketHealthState.EXCELLENT:
                state = MarketHealthState.DANGER
        elif spread < self.settings.min_spread:
            state = MarketHealthState.DANGER if state == MarketHealthState.EXCELLENT else state

        if state == MarketHealthState.EXCELLENT and spread >= self.settings.min_spread and spread_lifetime >= 500:
            state = MarketHealthState.GOOD

        self.market_health_state = state
        if hard_block_reason == "spread_too_young":
            self.log("WARNING", f"[HEALTH] block spread_too_young lifetime={spread_lifetime}")
            self.market_health_state = MarketHealthState.DANGER
        elif hard_block_reason == "falling_market":
            bid_ticks = 0.0
            mid_ticks = 0.0
            if len(self.recent_bids) >= 2:
                bid_ticks = (self.recent_bids[-1][1] - self.recent_bids[0][1]) / tick
            if len(self.recent_mids) >= 2:
                mid_ticks = (self.recent_mids[-1][1] - self.recent_mids[0][1]) / tick
            self.log("WARNING", f"[HEALTH] block falling_market bid_ticks={bid_ticks:.2f} mid_ticks={mid_ticks:.2f}")
            self.market_health_state = MarketHealthState.DANGER
        if reasons:
            reason_key = "|".join(reasons)
            if reason_key != self.last_health_reason or now_ms - self.last_health_log_ms >= self.settings.health_log_throttle_ms:
                for reason in reasons:
                    self.log("WARNING", f"[HEALTH] {reason}")
                self.last_health_reason = reason_key
                self.last_health_log_ms = now_ms
        elif now_ms - self.last_health_log_ms >= 5000:
            self.log("INFO", f"[HEALTH] state={self.market_health_state}")
            self.last_health_reason = ""
            self.last_health_log_ms = now_ms

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

    def _should_show_in_gui(self, tag: str, message: str) -> bool:
        if "[EXEC]" in message or tag in {"ERROR", "WARNING"}:
            return True
        if message in {"START", "STOP"} or "[SETTINGS]" in message:
            return True
        if "[SYNC]" in message and ("desync fixed" in message or "filled detected" in message):
            return True
        return False

    def log(self, tag: str, message: str) -> None:
        line = format_log(tag, message)
        if line.split("] ", 1)[-1] == self.last_log_line:
            return
        self.last_log_line = line.split("] ", 1)[-1]
        self.file_logs.write_session(line)

        if not self._should_show_in_gui(tag, message):
            return

        color = {"INFO": "#CBD5E1", "OK": "#22C55E", "WARNING": "#FACC15", "ERROR": "#EF4444"}.get(tag, "#CBD5E1")
        trade_keys = ("[EXEC]", "REALIZED", "PLACE BUY", "BUY FILLED", "PLACE SELL", "SELL FILLED", "CANCEL", "TIMEOUT")
        bucket = "trade" if any(k in message for k in trade_keys) else "system"
        self.pending_gui_logs[bucket].append((color, line))
        self._flush_gui_logs()
        if bucket == "trade":
            self.file_logs.write_trade(line.replace(f"[{tag}]", "[EXEC]"))
        else:
            self.file_logs.write_system(line)


    def _apply_guard_mode_preset(self) -> None:
        mode = str(self.settings.guard_mode or "BALANCED").upper()
        self.settings.guard_mode = mode if mode in {"FAST", "BALANCED", "STRICT"} else "BALANCED"
        presets = {
            "FAST": dict(min_spread_lifetime_ms=100, stable_snapshots_required=1, max_negative_mid_delta=-8.0, max_negative_bid_delta=-12.0, loss_cooldown_ms=500, panic_cooldown_ms=1000, block_on_snapshots_insufficient=False),
            "BALANCED": dict(min_spread_lifetime_ms=300, stable_snapshots_required=3, max_negative_mid_delta=-4.0, max_negative_bid_delta=-7.0, loss_cooldown_ms=1500, panic_cooldown_ms=2500, block_on_snapshots_insufficient=True),
            "STRICT": dict(min_spread_lifetime_ms=500, stable_snapshots_required=5, max_negative_mid_delta=-2.0, max_negative_bid_delta=-4.0, loss_cooldown_ms=3000, panic_cooldown_ms=5000, block_on_snapshots_insufficient=True),
        }
        for k, v in presets[self.settings.guard_mode].items():
            setattr(self.settings, k, v)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop(); super().closeEvent(event)
