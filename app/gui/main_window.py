import time
import re
from dataclasses import dataclass
from decimal import Decimal
from collections import deque
from requests import RequestException
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QTextCursor, QTextCharFormat
from PySide6.QtWidgets import QCheckBox, QDialog, QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QPushButton, QScrollArea, QTabWidget, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QProgressBar, QHeaderView, QSizePolicy

from app.core.binance_account import BinanceAPIError, BinanceAccountClient
from app.core.config import CONFIG, SETTINGS_STORE
from app.core.logger import FileLogManager, format_log
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.trade_math import TradeMathEngine
from app.core.market_ws import MarketWSClient
from app.core.grid_runtime import GridRuntime
from app.core.price_ticks import add_ticks
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
    grid_level_id: int | None = None
    stream_id: int | None = None
    entry_order_id: int | None = None
    sell_order_id: int | None = None
    sell_placed_ms: int = 0
    sell_retry_count: int = 0
    state: str = "OPEN"


class MarketHealthState:
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    DANGER = "DANGER"
    UNTRADEABLE = "UNTRADEABLE"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = SETTINGS_STORE.load()
        self.setWindowTitle("UB v0.8.21 / BTCU Trading Cockpit")
        self.resize(1600, 900)
        self.setMinimumSize(1280, 760)
        self.setStyleSheet(main_qss())
        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol, self.settings.max_ws_age_ms)
        self.account = BinanceAccountClient()
        self.account.debug_api_logs = self.settings.debug_api_logs
        self.grid_runtime = GridRuntime(log_callback=lambda m: self.log("INFO", f"[EXEC] {m}"))
        self.grid_level_by_order_id: dict[int, int] = {}
        self.grid_order_ids: set[int] = set()
        self.grid_sell_order_meta: dict[int, tuple[int, int]] = {}
        self.last_grid_skip_single_entry_log_ms = 0
        self.grid_last_place_batch_ms = 0
        self.grid_order_error_until_ms = 0
        self.grid_order_error_count = 0
        self.grid_buy_paused = False
        self.grid_last_batch_size = 0
        self.stream_last_recenter_ms = 0
        self.api_status = "NOT SET"
        self.api_ready = False
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
        self.last_panic_wait_order_id = 0
        self.panic_hold_count = 0
        self.exit_block_reason = "-"
        self.sell_upsize_skip_logged = False
        self.max_hold_exit_triggered = False
        self.exit_stage = "EXIT_TP_MAKER"
        self.panic_ladder_step = 0
        self.last_exit_reason = "-"
        self.taker_exit_state = "IDLE"
        self.taker_exit_triggered = False
        self.taker_exit_order_id = 0
        self.taker_exit_sent_ms = 0
        self.last_taker_status_poll_ms = 0
        self.last_taker_exit_reason = "-"
        self.last_taker_reason = "-"
        self.last_taker_qty = 0.0
        self.last_taker_price = 0.0
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
        self.stream_wins = 0
        self.stream_losses = 0
        self.stream_closed_cycles = 0
        self.stream_realized_pnl = 0.0
        self.stream_last_pnl = 0.0
        self.stream_winrate = 0.0
        self.canceled_buys = 0
        self.sell_timeouts = 0
        self.file_logs = FileLogManager()
        self.gui_log_limit = 200
        self.gui_log_mode = "IMPORTANT"
        self.logs_visible = bool(getattr(self.settings, "gui_logs_visible_default", False))
        self.gui_append_skipped = 0
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
        self.last_sell_qty_clamp_sig = ""
        self.last_sell_qty_clamp_log_ms = 0
        self.last_exit_recovery_log_ms = 0
        self.runtime_halt_manual_check = False
        self.sell_hold_window_ms = 1500
        self.exit_blocked_no_sellable_count = 0
        self.phantom_reconcile_count = 0
        self.last_phantom_log_ms = 0
        self.sell_hold_near_ticks = 2
        self.market_health_state = MarketHealthState.GOOD
        self.market_health_bid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_mid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_unstable_bid_ticks = abs(self.settings.max_negative_bid_delta)
        self.market_health_negative_mid_ticks = abs(self.settings.max_negative_mid_delta)
        self.market_health_min_spread_lifetime_ms = self.settings.min_spread_lifetime_ms
        self.last_sell_qty_clamp_log_ms = 0
        self.last_exit_recovery_log_ms = 0
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
        self.last_stream_global_sell_skip_log_ms_by_reason: dict[str, int] = {}
        self.last_stream_repair_skip_log_ms = 0
        self.last_stream_stats_ui_log_ms = 0
        self.entry_guard_stable_count = 0
        self.last_plan_recompute_ms = 0
        self._cached_plan = None
        self.rest_fallback_count = 0
        self.stale_reason = ""
        self.last_sell_price = 0.0
        self.force_reprice_sell_next = False
        self.last_sell_far_cancel_price = 0.0
        self.sell_hold_recovery_started_ms = 0
        self.floor_hold_timeout_triggered = False
        self._floor_hold_disabled_logged = False
        self.smart_exit_active = False
        self.smart_exit_attempts = 0
        self.smart_exit_last_order_id = 0
        self.smart_exit_last_action_ts = 0
        self.last_sell_recovery_attempt_ms = 0
        self.last_watchdog_sync_ms_by_order: dict[int, int] = {}
        self.protected_sell_ignore_last_log_ms_by_order: dict[int, int] = {}
        self.place_sell_entered_ms = 0
        self.last_taker_guard_skip_log_ms_by_reason: dict[str, int] = {}
        self.last_taker_status_wait_log_ms = 0
        self.taker_status_error_count = 0

        root = QWidget(); self.setCentralWidget(root); self.main_layout = QVBoxLayout(root)
        self.top_status = QLabel(); self.top_status.setObjectName("topStatus"); self.main_layout.addWidget(self.top_status)
        self.grid = QGridLayout(); self.grid.setHorizontalSpacing(10); self.grid.setVerticalSpacing(10); self.grid.setContentsMargins(0, 0, 0, 0); self.main_layout.addLayout(self.grid)
        self._build_cards(); self._build_controls(); self._build_logs(); self._configure_grid_layout()

        self.ws.signals.book.connect(self.on_ws_book); self.ws.signals.status.connect(self.on_ws_status); self.ws.signals.log.connect(self.log)
        self.timer = QTimer(self); self.timer.timeout.connect(self.on_tick); self.timer.start(250)
        self.stats_timer = QTimer(self); self.stats_timer.timeout.connect(self._update_session_summary); self.stats_timer.start(500)
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
        runtime, self.runtime = build_kv_card("RUNTIME", [("LIVE", "OFF"), ("FSM", "IDLE"), ("Mode", "ANALYTICS"), ("Position state", "FLAT"), ("Position qty", "0"), ("Entry avg", "0"), ("Free BTC", "0"), ("Inventory BTC", "0"), ("Safe SELL qty", "0"), ("Market Health", "GOOD"), ("Entry Guard", "BALANCED"), ("Guard state", "WARMING"), ("Guard reason", "boot"), ("Stable snaps", "0/0"), ("Cooldown ms", "0"), ("Entry mode", "BALANCED"), ("BUY age", "0ms"), ("Entry reprices", "0"), ("Fill hint", "LOW"), ("Entry reason", "-"), ("Exit stage", "-"), ("SELL age", "0ms"), ("SELL reprices", "0"), ("Panic ladder", "0"), ("Panic age", "0ms"), ("Panic holds", "0"), ("Exit blocked", "-"), ("Taker exit", "OFF"), ("Taker reason", "-"), ("Taker qty/price", "-"), ("Last exit reason", "-"), ("GRID LEVELS", "0"), ("GRID ACTIVE BUYS", "0"), ("GRID ACTIVE SELLS", "0"), ("GRID FILLED LEVELS", "0"), ("GRID INVENTORY U", "0"), ("GRID BUY PAUSED", "NO"), ("GRID PLACEMENT QUEUE", "0"), ("GRID LAST BATCH SIZE", "0"), ("GRID BUDGET USED", "0"), ("GRID BUDGET FREE", "0"), ("Auto-confirm", "YES"), ("Auto-cancel", "YES")], compact=True)
        self.runtime_box = runtime
        risk, self.risk = build_kv_card("RISK", [("Order size U", "0"), ("Max exposure U", "0"), ("panic", "ON")], compact=True)
        self.risk_box = risk
        bal, self.bal = build_kv_card("BALANCES", [("BTC свободно", "0"), ("BTC lock", "0"), ("U свободно", "0"), ("U lock", "0"), ("Max buy", "0 BTC"), ("Max sell", "0 BTC")], compact=True)
        self.grid.addWidget(spread, 2, 0); self.grid.addWidget(plan, 2, 1); self.grid.addWidget(runtime, 2, 2); self.grid.addWidget(bal, 2, 3)

        summary_rows = [("Started", self.session_started_at), ("Position state", "FLAT"), ("Position qty", "0"), ("Entry avg", "0"), ("Free BTC", "0"), ("Inventory BTC", "0"), ("Safe SELL qty", "0"), ("Closed cycles", "0"), ("Wins", "0"), ("Losses", "0"), ("Realized PnL", "0"), ("Last PnL", "0"), ("Winrate", "0%"), ("Canceled buys", "0"), ("Sell timeouts", "0"), ("Exit mode", "NORMAL"), ("Phantom reconcile count", "0")]
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
        self.load_session_log_btn = QPushButton("LOAD SESSION LOG"); self.load_session_log_btn.setProperty("kind", "neutral"); self.load_session_log_btn.clicked.connect(self.load_session_log_summary); row.addWidget(self.load_session_log_btn)
        self.toggle_logs_btn = QPushButton("SHOW LOGS" if not self.logs_visible else "HIDE LOGS"); self.toggle_logs_btn.setProperty("kind", "neutral"); self.toggle_logs_btn.clicked.connect(self.toggle_logs_visibility); row.addWidget(self.toggle_logs_btn)
        for btn in (self.settings_btn, self.start_stop_btn, self.cancel_btn, self.load_session_log_btn, self.toggle_logs_btn):
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
        self.log_tabs.setVisible(self.logs_visible)

    def toggle_logs_visibility(self) -> None:
        self.logs_visible = not self.logs_visible
        self.log_tabs.setVisible(self.logs_visible)
        self.toggle_logs_btn.setText("HIDE LOGS" if self.logs_visible else "SHOW LOGS")
        self.file_logs.write_session(format_log("INFO", f"GUI_LOGS {'ON' if self.logs_visible else 'OFF'}"))

    def open_settings_dialog(self) -> None:
        d = QDialog(self); d.setWindowTitle("Настройки UB"); d.setModal(True); d.resize(760, 620)
        lay = QVBoxLayout(d)
        tabs = QTabWidget(); lay.addWidget(tabs)
        self.settings_inputs = {}

        labels = {
            "live_enabled": "LIVE enabled",
            "order_size_u": "Order size U",
            "max_exposure_u": "Max exposure U",
            "max_daily_loss": "Max daily loss U",
            "auto_cancel_on_stop": "Auto cancel on stop",
            "stream_count": "Stream count",
            "stream_range_ticks": "Stream range ticks",
            "stream_max_active_buys": "Stream max active buys",
            "stream_place_batch_size": "Stream place batch size",
            "stream_place_interval_ms": "Stream place interval ms",
            "stream_buy_max_distance_ticks": "Stream BUY max distance ticks",
            "stream_recenter_interval_ms": "Stream recenter interval ms",
            "stream_sell_first": "Stream sell first",
            "min_spread_ticks": "Min spread ticks",
            "entry_offset_ticks": "Entry offset ticks",
            "buy_timeout_ms": "BUY timeout ms",
            "buy_watchdog_ms": "BUY watchdog ms",
            "far_buy_ticks": "BUY far ticks",
            "entry_mode": "Entry mode",
            "entry_chase_ticks": "Entry chase ticks",
            "entry_cross_if_spread_ticks_above": "Cross if spread ticks above",
            "stream_target_ticks": "Stream target ticks",
            "stream_min_profit_ticks": "Stream min profit ticks",
            "stream_sell_timeout_ms": "Stream sell timeout ms",
            "stream_sell_retry_max": "Stream sell retry max",
            "stream_sell_retry_step_ticks": "Stream sell retry step ticks",
            "stop_loss_ticks": "Stop loss ticks",
            "stream_loss_cooldown_ms": "Stream loss cooldown ms",
            "require_ws_for_buy": "Require WS for buy",
            "ws_optional_enabled": "WS optional enabled",
            "max_ws_age_ms": "Max WS age ms",
            "guard_enabled": "Guard enabled",
            "min_spread_lifetime_ms": "Min spread lifetime ms",
            "block_on_mid_negative": "Block on mid negative",
            "block_on_bid_unstable": "Block on bid unstable",
            "gui_log_mode": "GUI log mode",
            "gui_logs_visible_default": "GUI logs visible by default",
            "compact_logs": "Compact logs",
            "runtime_diag_enabled": "Runtime diagnostics",
        }

        account_tab = QWidget(); account_form = QFormLayout(account_tab)
        api_key_input = QLineEdit(); api_secret_input = QLineEdit(); api_secret_input.setEchoMode(QLineEdit.Password)
        show_secret = QCheckBox("Показать secret")
        show_secret.toggled.connect(lambda v: api_secret_input.setEchoMode(QLineEdit.Normal if v else QLineEdit.Password))
        test_btn = QPushButton("Проверить API"); test_btn.clicked.connect(self.on_test_connection)
        save_api_btn = QPushButton("Сохранить ключи"); save_api_btn.clicked.connect(lambda: self._save_api_fields(api_key_input.text(), api_secret_input.text(), show_secret, api_secret_input))
        account_form.addRow("API key", api_key_input); account_form.addRow("API secret", api_secret_input); account_form.addRow("", show_secret); account_form.addRow(test_btn, save_api_btn); account_form.addRow("Статус", QLabel(self.api_status))
        tabs.addTab(account_tab, "Аккаунт")

        tab_map = [
            ("GENERAL", ["live_enabled", "order_size_u", "max_exposure_u", "max_daily_loss", "auto_cancel_on_stop"]),
            ("STREAM CONVEYOR", ["stream_count", "stream_range_ticks", "stream_max_active_buys", "stream_place_batch_size", "stream_place_interval_ms", "stream_buy_max_distance_ticks", "stream_recenter_interval_ms", "stream_sell_first"]),
            ("ENTRY", ["min_spread_ticks", "entry_offset_ticks", "buy_timeout_ms", "buy_watchdog_ms", "far_buy_ticks", "entry_mode", "entry_chase_ticks", "entry_cross_if_spread_ticks_above"]),
            ("EXIT", ["stream_target_ticks", "stream_min_profit_ticks", "stream_sell_timeout_ms", "stream_sell_retry_max", "stream_sell_retry_step_ticks", "stop_loss_ticks", "stream_loss_cooldown_ms"]),
            ("DATA / GUARD", ["require_ws_for_buy", "ws_optional_enabled", "max_ws_age_ms", "guard_enabled", "min_spread_lifetime_ms", "block_on_mid_negative", "block_on_bid_unstable"]),
            ("GUI / LOGS", ["gui_log_mode", "gui_logs_visible_default", "compact_logs", "runtime_diag_enabled"]),
        ]
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
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(w)
            tabs.addTab(scroll, title)

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
        self.active_sync_timer.setInterval(max(self.settings.active_order_poll_ms, 250))
        self.account.debug_api_logs = self.settings.debug_api_logs
        self.ws.max_ws_age_ms = self.settings.max_ws_age_ms
        print("SETTINGS_CLEANUP_ACTIVE")
        print(f"SETTINGS_MODE_STREAM_CONVEYOR count={max(int(getattr(self.settings, 'stream_count', 1)), 1)}")
        print(f"BUDGET_SOURCE order_size_u={float(getattr(self.settings, 'order_size_u', 0.0))} max_exposure_u={float(getattr(self.settings, 'max_exposure_u', 0.0))}")
        self._apply_guard_mode_preset()
        self.market_health_bid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_mid_window_ms = self.settings.stable_snapshot_window_ms
        self.market_health_unstable_bid_ticks = abs(self.settings.max_negative_bid_delta)
        self.market_health_negative_mid_ticks = abs(self.settings.max_negative_mid_delta)
        self.market_health_min_spread_lifetime_ms = self.settings.min_spread_lifetime_ms
        self.last_sell_qty_clamp_log_ms = 0
        self.last_exit_recovery_log_ms = 0
        self.gui_log_mode = str(getattr(self.settings, "gui_log_mode", "IMPORTANT") or "IMPORTANT").upper()
        if self.gui_log_mode not in {"FULL", "IMPORTANT", "OFF"}:
            self.gui_log_mode = "IMPORTANT"
        self.logs_visible = bool(getattr(self.settings, "gui_logs_visible_default", False)) if not hasattr(self, "toggle_logs_btn") else self.logs_visible
        self.gui_log_limit = 0 if self.gui_log_mode == "OFF" else (200 if self.gui_log_mode == "IMPORTANT" else 500)
        if self.logs_visible:
            for widget in (self.trade_logs, self.system_logs):
                widget.document().setMaximumBlockCount(self.gui_log_limit)
        self.log_tabs.setVisible(self.logs_visible)
        if hasattr(self, "toggle_logs_btn"):
            self.toggle_logs_btn.setText("HIDE LOGS" if self.logs_visible else "SHOW LOGS")

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
        self.settings.live_enabled = True
        self._apply_runtime_settings()
        self.log("INFO", f"[SETTINGS] imported path={path}")

    def toggle_runtime(self) -> None:
        self.log("INFO", f"START_CLICK received enabled={self.start_stop_btn.isEnabled()} api_ready={self.api_ready} running={self.runtime_active}")
        self.runtime_active = not self.runtime_active
        if self.runtime_active:
            self.settings.live_enabled = True
            self.log("INFO", f"START_SETTINGS live_enabled={self.settings.live_enabled} guard_mode={self.settings.guard_mode} entry_mode={self.settings.entry_mode} exit_engine={self.settings.exit_engine_enabled} order_size={self.settings.order_size_u}")
            self.log("INFO", f"START_PROFILE_STREAM order_size={self.settings.order_size_u} min_spread_ticks={self.settings.min_spread_ticks} guard_mode={self.settings.guard_mode} entry_mode={self.settings.entry_mode} stream_sell_timeout_ms={self.settings.stream_sell_timeout_ms} stream_target_ticks={self.settings.stream_target_ticks}")
            self.log("INFO", f"SETTINGS_MERGE_PRESERVE existing_values=true missing_added={getattr(SETTINGS_STORE, 'last_merge_missing_added', 0)}")
            if not self.api_ready:
                self.log("WARNING", "START_BLOCKED reason=api_not_ready")
                self.runtime_active = False
            else:
                self.ws.start(); self.start_stop_btn.setText("STOP"); self.start_stop_btn.setProperty("kind", "stop"); self.exit_blocked_no_sellable_count = 0; self.log("OK", "START")
                self.log("OK", "START_OK runtime_started")
                if (int(getattr(self.settings, "stream_count", 1)) >= 1) and self.filters.get("loaded") and float(self.state.snapshot.bid or 0.0) > 0:
                    stream_count = max(int(getattr(self.settings, "stream_count", 1)), 1)
                    self.log("INFO", f"[EXEC] STREAM_CONVEYOR_ACTIVE count={stream_count}")
                    self.log("INFO", "[EXEC] STREAM_SINGLE_MODE count=1" if stream_count == 1 else f"[EXEC] STREAM_MULTI_MODE count={stream_count}")
                    self.grid_runtime.configure_micro_grid(
                        float(self.state.snapshot.bid),
                        float(self.filters.get("tickSize", 0.0) or 0.0),
                        float(self.filters.get("stepSize", 0.0) or 0.0),
                        float(self.filters.get("minQty", 0.0) or 0.0),
                        float(self.filters.get("minNotional", 0.0) or 0.0),
                        self.settings,
                    )
                self._repair_runtime_state()
                if self.position_qty > 0:
                    self.log("WARNING", f"[EXEC] START_RESUME_EXIT qty={self.position_qty:.6f}")
                    self.fsm_state = "PLACE_SELL"
        else:
            self.ws.stop(); self.start_stop_btn.setText("START"); self.start_stop_btn.setProperty("kind", "start"); self.exit_blocked_no_sellable_count = 0; self.cancel_all(); self.log("WARNING", "STOP")
        self.start_stop_btn.setEnabled(True)
        self.start_stop_btn.style().polish(self.start_stop_btn)

    def cancel_all(self) -> None:
        self.log("WARNING", "cancel all requested")
        stream_order_ids = list(self.grid_order_ids) + list(self.grid_sell_order_meta.keys())
        if self.active_order.get("orderId") and self.active_order.get("side") == "BUY":
            try:
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            except Exception:
                pass
            self.sync_active_order(force=True)
        elif self.active_order.get("orderId"):
            try:
                if self.panic_exit_final and self.active_order.get("side") == "SELL":
                    self.log("WARNING", "[EXEC] STOP canceled panic order, position remains open")
                self.account.cancel_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            except Exception:
                pass
            self.sync_active_order(force=True)
        for order_id in stream_order_ids:
            try:
                if order_id in self.grid_order_ids or bool(getattr(self.settings, "auto_cancel_on_stop", True)):
                    self.account.cancel_order(CONFIG.binance_symbol, int(order_id))
            except Exception:
                pass
        self.refresh_account_data()
        if self.position_qty <= self._inventory_epsilon_qty():
            open_sell = self._find_open_sell_order()
            if open_sell:
                try:
                    self.account.cancel_order(CONFIG.binance_symbol, int(open_sell.get("orderId", 0)))
                except Exception:
                    pass
        if self.panic_exit_final:
            self.panic_exit_final = False
            self.panic_exit_order_id = 0
            self.panic_exit_price = 0.0
            self.panic_exit_started_ms = 0
            self.panic_escalated_once = False
            self.last_panic_wait_log_ms = 0
            self.max_hold_exit_triggered = False
        self.active_order = {}
        self.fsm_state = "IDLE"
        self.runtime_active = False
        if self.position_qty > 0:
            self.position_state = "EXIT_BLOCKED_OPEN_INVENTORY"
            self.exit_block_reason = "stop_open_inventory"
            self.fsm_state = "WAIT_MANUAL"
            self.log("WARNING", f"[EXEC] STOP_SAFE_MANUAL inventory={self.position_qty:.6f} active_order={self.active_order.get('orderId', 0)}")
            self.log("WARNING", f"[EXEC] STREAM_STOP_OPEN_INVENTORY qty={self.position_qty:.6f} locked={float(self.balances.get('BTC', {}).get('locked', 0.0) or 0.0):.6f} free={float(self.balances.get('BTC', {}).get('free', 0.0) or 0.0):.6f}")
        else:
            self.position_state = "FLAT"
            self._reconcile_position_state("stop")
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
        if self._is_stream_sell_order_id(order_id):
            self.log("INFO", f"[EXEC] STREAM_GLOBAL_SELL_SKIP reason=adopt_stream_sell orderId={order_id}")
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
        self.active_order["active_sell_meta"] = self._update_sell_protection_meta(price, float(self.state.snapshot.ask or 0.0), "adopt_open_sell")
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
        if self.smart_exit_active:
            self.log("OK", "[EXEC] SMART_EXIT_DONE_FLAT")
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
        return max(step * 1.5, self.settings.inventory_epsilon_qty)

    def _min_sellable_qty(self) -> float:
        epsilon = self._inventory_epsilon_qty()
        step = float(self.filters.get("stepSize", 0.0) or 0.0)
        min_qty = float(self.filters.get("minQty", 0.0) or 0.0)
        min_notional = float(self.filters.get("minNotional", 0.0) or 0.0)
        filters_loaded = bool(self.filters.get("loaded"))
        bid_now = float(self.state.snapshot.bid or 0.0)
        ask_now = float(self.state.snapshot.ask or 0.0)
        current_price = bid_now if bid_now > 0 else ask_now

        reason = ""
        if not filters_loaded:
            reason = "filters_not_loaded"
        elif current_price <= 0:
            reason = "price_not_available"

        if reason:
            now = int(time.time() * 1000)
            if now - int(getattr(self, "last_min_sellable_fallback_log_ms", 0) or 0) >= 3000:
                self.log("WARNING", f"[EXEC] MIN_SELLABLE_FALLBACK reason={reason}")
                self.last_min_sellable_fallback_log_ms = now
            return max(epsilon, step, min_qty, self.settings.min_sellable_qty_fallback)

        min_qty_by_notional = (min_notional / current_price) if min_notional > 0 else 0.0
        return max(min_qty, min_qty_by_notional, step, epsilon)

    def _reconcile_flat_balance(self) -> bool:
        epsilon = self._inventory_epsilon_qty()
        btc_free = float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0)
        btc_locked = float(self.balances.get("BTC", {}).get("locked", 0.0) or 0.0)
        return (btc_free + btc_locked) <= epsilon


    def _reconcile_position_state(self, reason: str) -> bool:
        epsilon = self._inventory_epsilon_qty()
        inventory_qty = self._recalc_position_from_chunks()
        safe_qty = self._safe_sell_qty(self._sync_sell_target_qty())
        btc_free = float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0)
        btc_locked = float(self.balances.get("BTC", {}).get("locked", 0.0) or 0.0)

        if inventory_qty <= epsilon and safe_qty <= epsilon:
            if (btc_free + btc_locked) > epsilon:
                self.log("WARNING", f"[EXEC] BALANCE_EXTERNAL_BTC free={btc_free:.6f} locked={btc_locked:.6f}")
            self.inventory_chunks = []
            self.position_qty = 0.0
            self.position_entry_avg = 0.0
            self.position_state = "FLAT"
            self.active_order = {}
            self._reset_sell_accounting(f"reconcile_{reason}", reset_panic_order_id=True)
            self.panic_exit_final = False
            self.panic_exit_order_id = 0
            self.panic_exit_price = 0.0
            self.panic_exit_started_ms = 0
            self.panic_escalated_once = False
            self.last_panic_wait_log_ms = 0
            self.max_hold_exit_triggered = False
            self.fsm_state = "WAIT_READY" if self.runtime_active else "IDLE"
            self.log("INFO", "[EXEC] RECONCILE_FLAT reason=inventory_empty_safe_qty_zero")
            return True
        return False

    def _maybe_reconcile_phantom_inventory(self, inventory_qty: float, sell_qty: float, btc_free: float, btc_locked: float, open_sell: dict | None = None) -> bool:
        epsilon = self._inventory_epsilon_qty()
        if inventory_qty <= epsilon or self.exit_blocked_no_sellable_count < 3:
            return False
        if not (btc_free <= epsilon or sell_qty <= epsilon):
            return False
        now = int(time.time() * 1000)
        if btc_locked > epsilon:
            self.position_state = "WAIT_EXCHANGE_UNLOCK"
            self.fsm_state = "WAIT_SELL_ORDER_STATUS"
            if now - self.last_phantom_log_ms >= 3000:
                self.log("WARNING", f"[EXEC] PHANTOM_RECONCILE_SKIPPED reason=locked_btc locked={btc_locked:.6f}")
                self.last_phantom_log_ms = now
            return False
        self.sync_active_order(force=True)
        open_sell = open_sell or self._find_open_sell_order()
        if open_sell or (self.active_order.get("orderId") and self.active_order.get("side") == "SELL"):
            if now - self.last_phantom_log_ms >= 3000:
                order_id = int((open_sell or {}).get("orderId", self.active_order.get("orderId", 0)) or 0)
                self.log("WARNING", f"[EXEC] PHANTOM_RECONCILE_SKIPPED reason=open_sell_exists orderId={order_id}")
                self.last_phantom_log_ms = now
            return False
        self.inventory_chunks = []
        self.position_qty = 0.0
        self.position_entry_avg = 0.0
        self.position_state = "FLAT"
        self.active_order = {}
        self._reset_sell_accounting("phantom_inventory_reconcile", reset_panic_order_id=True)
        self.panic_exit_final = False
        self.panic_exit_order_id = 0
        self.panic_exit_price = 0.0
        self.panic_exit_started_ms = 0
        self.panic_escalated_once = False
        self.last_panic_wait_log_ms = 0
        self.max_hold_exit_triggered = False
        self.fsm_state = "WAIT_READY" if self.runtime_active else "IDLE"
        self.phantom_reconcile_count += 1
        if now - self.last_phantom_log_ms >= 3000:
            self.log("WARNING", f"[EXEC] PHANTOM_INVENTORY_RECONCILE inventory={inventory_qty:.6f} free={btc_free:.6f} locked={btc_locked:.6f} safe={sell_qty:.6f} reason=exchange_no_sellable_btc")
            self.last_phantom_log_ms = now
        return True

    def _maybe_reconcile_micro_partial_dust(self, inventory_qty: float, reason: str = "below_min_sellable") -> bool:
        if not bool(getattr(self.settings, "micro_partial_reconcile_enabled", True)):
            return False
        epsilon = self._inventory_epsilon_qty()
        if inventory_qty <= epsilon:
            return False
        max_qty = max(float(getattr(self.settings, "micro_partial_max_qty", 0.0001) or 0.0001), epsilon)
        min_sellable_qty = self._min_sellable_qty()
        bid_now = float(self.state.snapshot.bid or 0.0)
        ask_now = float(self.state.snapshot.ask or 0.0)
        current_price = bid_now if bid_now > 0 else ask_now
        min_notional = float(self.filters.get("minNotional", 0.0) or 0.0)
        notional = inventory_qty * current_price if current_price > 0 else 0.0
        below_min_sellable = inventory_qty < min_sellable_qty
        below_min_notional = min_notional > 0 and notional < min_notional
        if inventory_qty <= max_qty and (below_min_sellable or below_min_notional):
            if self.smart_exit_active:
                self.log("INFO", f"[EXEC] SMART_EXIT_DUST_RECONCILE qty={inventory_qty:.6f} reason={reason}")
            self.inventory_chunks = []
            self.position_qty = 0.0
            self.position_entry_avg = 0.0
            self.active_order = {}
            self.position_state = "FLAT"
            self._reset_sell_accounting("micro_partial_dust_reconcile", reset_panic_order_id=True)
            self.panic_exit_final = False
            self.panic_exit_order_id = 0
            self.panic_exit_price = 0.0
            self.panic_exit_started_ms = 0
            self.panic_escalated_once = False
            self.last_panic_wait_log_ms = 0
            self.max_hold_exit_triggered = False
            self.fsm_state = "WAIT_READY" if self.runtime_active else "IDLE"
            self.log("INFO", f"[EXEC] MICRO_PARTIAL_DUST_RECONCILE qty={inventory_qty:.6f} notional={notional:.6f} min_sellable={min_sellable_qty:.6f} reason={reason}")
            return True
        return False

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
        self.max_hold_exit_triggered = False
        self.log("INFO", f"[EXEC] INVENTORY DRAINED epsilon_cleanup qty={residual_qty:.6f}")
        if self._reconcile_flat_balance():
            self.position_state = "FLAT"
        return True

    def on_test_connection(self, silent: bool = False) -> None:
        status = self.account.test_account_connection(); self.api_status = status.status
        self.api_ready = status.status == "OK"
        if status.status == "OK":
            if not silent: self.log("OK", "API connected")
            self.refresh_account_data(load_filters=True)
        elif not silent:
            self.log("ERROR", f"API error {status.message}")

    def refresh_account_data(self, load_filters: bool = False) -> None:
        self.api_ready = self.api_status == "OK"
        if not self.api_ready:
            return
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

    def _round_price_up(self, price: object) -> float:
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
            steps = int(value / tick)
            rounded = steps * tick
            if rounded < value:
                rounded = (steps + 1) * tick
            return float(rounded)
        except Exception:
            return value

    def _force_exit_price(self, bid_now: float, ask_now: float) -> float:
        tick = self._tick_size()
        aggressive = float(self.settings.aggressive_exit_offset)
        return max(bid_now + tick, ask_now - aggressive)

    def _is_market_near_sell_target(self, bid_now: float, target_price: float) -> bool:
        tick = self._tick_size()
        return bid_now > 0 and target_price > 0 and (target_price - bid_now) <= (tick * self.sell_hold_near_ticks)

    def _reset_sell_price_cache_after_far_cancel(self, canceled_price: float) -> None:
        self.active_order = {}
        self.position_sell_order_id = 0
        self.last_sell_price = 0.0
        self.force_reprice_sell_next = True
        self.last_sell_far_cancel_price = float(canceled_price or 0.0)
        self._cached_plan = None
        self.last_plan_recompute_ms = 0
        self.last_sell_place_signature = ""
        self.last_sell_place_ms = 0
        self.fetch_rest()

    def _watchdog_ready(self, order_id: int, now_ms: int) -> bool:
        if order_id <= 0:
            return False
        last_ms = int(self.last_watchdog_sync_ms_by_order.get(order_id, 0) or 0)
        if now_ms - last_ms < 1000:
            return False
        self.last_watchdog_sync_ms_by_order[order_id] = now_ms
        return True

    def _is_far_sell(self, order_price: float, ask_now: float) -> tuple[bool, int]:
        tick = self._tick_size()
        if tick <= 0 or order_price <= 0 or ask_now <= 0:
            return False, 0
        dist_ticks = int((order_price - ask_now) / tick)
        far_ticks = int(getattr(self.settings, "far_sell_ticks", 10))
        if self.panic_exit_final:
            far_ticks *= max(int(getattr(self.settings, "panic_far_sell_multiplier", 5)), 1)
        return dist_ticks > far_ticks, dist_ticks

    def _is_far_buy(self, order_price: float, bid_now: float) -> tuple[bool, int]:
        tick = self._tick_size()
        if tick <= 0 or order_price <= 0 or bid_now <= 0:
            return False, 0
        dist_ticks = int((bid_now - order_price) / tick)
        return dist_ticks > int(getattr(self.settings, "far_buy_ticks", 10)), dist_ticks

    def _min_allowed_sell_price(self) -> float:
        tick = self._tick_size()
        min_profit_ticks = max(int(getattr(self.settings, "min_profit_ticks", 1)), 0)
        return float(self.position_entry_avg) + (tick * min_profit_ticks)

    def _apply_sell_floor(self, candidate_price: float, reason: str) -> float:
        if self.smart_exit_active:
            return candidate_price
        if self.floor_hold_timeout_triggered:
            if not getattr(self, "_floor_hold_disabled_logged", False):
                self.log("WARNING", "[EXEC] FLOOR_HOLD_DISABLED_AFTER_TIMEOUT")
                self._floor_hold_disabled_logged = True
            return candidate_price
        floor_price = self._min_allowed_sell_price()
        if candidate_price < floor_price:
            self.log("WARNING", f"[EXEC] SELL_FLOOR_BLOCKED price={candidate_price:.2f} floor={floor_price:.2f} entry={float(self.position_entry_avg):.2f} reason=below_min_profit source={reason}")
            return floor_price
        return candidate_price

    def _is_floor_protected_sell(self, order_price: float) -> bool:
        floor_price = self._min_allowed_sell_price()
        tick = self._tick_size()
        return order_price > 0 and floor_price > 0 and abs(order_price - floor_price) <= max(tick * 0.5, 1e-9)

    def _update_sell_protection_meta(self, sell_price: float, ask_now: float, source: str) -> dict:
        if self.smart_exit_active:
            self.sell_hold_recovery_started_ms = 0
            return {"is_floor_protected": False, "is_hold_recovery": False, "hold_source": source}
        tick = self._tick_size()
        floor_price = self._min_allowed_sell_price()
        is_floor_protected = self._is_floor_protected_sell(sell_price)
        dist_ticks = int((sell_price - ask_now) / tick) if tick > 0 and ask_now > 0 and sell_price > 0 else 0
        should_hold = bool(getattr(self.settings, "sell_floor_hold_enabled", True)) and is_floor_protected and (not self.panic_exit_final)
        if should_hold:
            if not self.sell_hold_recovery_started_ms:
                self.sell_hold_recovery_started_ms = int(time.time() * 1000)
                order_id = int(self.active_order.get("orderId", 0) or 0) if isinstance(self.active_order, dict) else 0
                self.log("WARNING", f"[EXEC] SELL_HOLD_RECOVERY_START orderId={order_id} entry={float(self.position_entry_avg):.2f} floor={floor_price:.2f} ask={ask_now:.2f}")
        else:
            self.sell_hold_recovery_started_ms = 0
        self.exit_blocked_no_sellable_count = 0
        return {"is_floor_protected": is_floor_protected, "is_hold_recovery": should_hold, "hold_source": source}

    def _is_protected_sell_order(self, order_price: float) -> bool:
        meta = self.active_order.get("active_sell_meta", {}) if isinstance(self.active_order, dict) else {}
        return bool(meta.get("is_floor_protected")) or bool(meta.get("is_hold_recovery")) or self._is_floor_protected_sell(order_price)

    def _should_block_protected_sell_escalation(self, now_ms: int, reason: str) -> bool:
        if self.smart_exit_active:
            return False
        if not isinstance(self.active_order, dict):
            return False
        if self.active_order.get("side") != "SELL":
            return False
        order_id = int(self.active_order.get("orderId", 0) or 0)
        if order_id <= 0:
            return False
        order_price = float(self.active_order.get("price", 0.0) or 0.0)
        if not self._is_protected_sell_order(order_price):
            return False
        if self.floor_hold_timeout_triggered:
            return False
        hold_started_ms = int(self.sell_hold_recovery_started_ms or self.active_order.get("create_ms", now_ms) or now_ms)
        hold_age_ms = max(now_ms - hold_started_ms, 0)
        hard_ms = int(getattr(self.settings, "protected_hold_hard_ms", getattr(self.settings, "sell_floor_hold_max_ms", 12000)))
        soft_ms = int(getattr(self.settings, "protected_hold_soft_ms", 5000))
        if hold_age_ms >= hard_ms:
            self.floor_hold_timeout_triggered = True
            self.log("WARNING", f"[EXEC] SMART_EXIT_BREAK_HOLD reason=hard_timeout age={hold_age_ms}")
            return False
        throttle_ms = 3000
        last_ms = int(getattr(self, "last_hold_skip_log_ms", 0) or 0)
        if now_ms - last_ms >= throttle_ms:
            self.log("INFO", f"[EXEC] HOLD_PROTECTED_SKIP_EXIT_ESCALATION reason={reason} orderId={order_id} hold_age={hold_age_ms} soft={soft_ms} hard={hard_ms}")
            self.last_hold_skip_log_ms = now_ms
        return True

    def _log_protected_sell_ignore(self, order_id: int, dist_ticks: int) -> None:
        now = int(time.time() * 1000)
        throttle_ms = int(getattr(self.settings, "protected_sell_ignore_log_throttle_ms", 5000))
        last_log_ms = int(self.protected_sell_ignore_last_log_ms_by_order.get(order_id, 0) or 0)
        if now - last_log_ms >= max(throttle_ms, 0):
            self.log("INFO", f"[EXEC] SELL_WATCHDOG_FAR_IGNORE orderId={order_id} reason=protected_sell dist_ticks={dist_ticks}")
            self.protected_sell_ignore_last_log_ms_by_order[order_id] = now

    def _should_break_protected_hold(self, now_ms: int, dist_ticks: int) -> tuple[bool, str, int]:
        if not bool(getattr(self.settings, "smart_exit_enabled", True)):
            return False, "", 0
        hold_started_ms = int(self.sell_hold_recovery_started_ms or self.active_order.get("create_ms", now_ms) or now_ms)
        hold_age_ms = max(now_ms - hold_started_ms, 0)
        soft_ms = int(getattr(self.settings, "protected_hold_soft_ms", 5000))
        hard_ms = int(getattr(self.settings, "protected_hold_hard_ms", getattr(self.settings, "sell_floor_hold_max_ms", 12000)))
        max_dist_ticks = max(int(getattr(self.settings, "protected_hold_max_dist_ticks", 1200)), 0)
        if hold_age_ms >= hard_ms:
            return True, "hard_timeout", hold_age_ms
        if hold_age_ms >= soft_ms and dist_ticks > max_dist_ticks:
            return True, "too_far", hold_age_ms
        return False, "", hold_age_ms

    def _cap_soft_sell_reprice(self, old_price: float, candidate_price: float) -> float:
        tick = self._tick_size()
        if old_price <= 0 or tick <= 0:
            return candidate_price
        soft_floor = old_price - tick
        return max(candidate_price, soft_floor)

    def _build_fresh_sell_price_after_far_cancel(self, ask_now: float, bid_now: float) -> tuple[float, int]:
        tick = self._tick_size()
        if tick <= 0 or ask_now <= 0:
            return 0.0, 0
        offset = max(float(getattr(self.settings, "exit_offset", 1.0)), 0.0)
        offset_ticks = int(round(offset / tick)) if tick > 0 else 0
        allow_ticks = max(2, offset_ticks)
        if self.taker_exit_triggered or self.exit_stage != "EXIT_TP_MAKER":
            panic_step = max(float(getattr(self.settings, "aggressive_exit_offset", 1.0)), tick)
            target_price = ask_now - panic_step
        else:
            target_price = ask_now - (tick * min(allow_ticks, 2))
        return self._round_price_down(max(target_price, tick)), allow_ticks

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
        self.taker_exit_triggered = False
        self.taker_exit_state = "IDLE"
        self.taker_exit_order_id = 0
        self.last_taker_exit_reason = "-"
        self.sell_upsize_skip_logged = False
        self.exit_block_reason = "-"
        self.sell_recovery_in_progress = False
        self.sell_cancel_in_progress = False
        self.sell_hold_recovery_started_ms = 0
        self.floor_hold_timeout_triggered = False
        self._floor_hold_disabled_logged = False
        self.smart_exit_active = False
        self.protected_sell_ignore_last_log_ms_by_order = {}
        self.exit_blocked_no_sellable_count = 0
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
        if bool((int(getattr(self.settings, "stream_count", 1)) >= 1)) and self._has_stream_owned_chunks_or_orders():
            now = int(time.time() * 1000)
            if now - int(self.last_stream_repair_skip_log_ms or 0) >= 2500:
                self.log("INFO", "[EXEC] STREAM_REPAIR_SKIP reason=stream_owned_inventory")
                self.last_stream_repair_skip_log_ms = now
            return
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
                if self._has_stream_owned_chunks_or_orders():
                    self.log("INFO", "[EXEC] STREAM_GLOBAL_SELL_SKIP reason=stream_owned_inventory")
                else:
                    self._log_exit_recovery_throttled(inventory_qty)
                    self.fsm_state = "PLACE_SELL"

    def _is_stream_owned_chunk(self, chunk: InventoryChunk) -> bool:
        return bool((int(getattr(self.settings, "stream_count", 1)) >= 1) and chunk.stream_id is not None)

    def _has_stream_owned_inventory(self) -> bool:
        eps = self._inventory_epsilon_qty()
        return any(self._is_stream_owned_chunk(chunk) and chunk.qty > eps for chunk in self.inventory_chunks)

    def _has_stream_owned_orders(self) -> bool:
        if not bool((int(getattr(self.settings, "stream_count", 1)) >= 1)):
            return False
        if any(int(order_id) > 0 for order_id in self.grid_sell_order_meta):
            return True
        active_order_id = int(self.active_order.get("orderId", 0) or 0)
        return self._is_stream_sell_order_id(active_order_id)

    def _has_stream_owned_chunks_or_orders(self) -> bool:
        return self._has_stream_owned_inventory() or self._has_stream_owned_orders()

    def _should_skip_global_sell_engine(self, reason: str) -> bool:
        if not bool((int(getattr(self.settings, "stream_count", 1)) >= 1)):
            return False
        if not self._has_stream_owned_chunks_or_orders():
            return False
        now = int(time.time() * 1000)
        last_ms = int(self.last_stream_global_sell_skip_log_ms_by_reason.get(reason, 0) or 0)
        if now - last_ms >= 2500:
            self.log("INFO", f"[EXEC] STREAM_GLOBAL_SELL_SKIP reason={reason}")
            self.last_stream_global_sell_skip_log_ms_by_reason[reason] = now
        return True

    def _is_stream_sell_order_id(self, order_id: int) -> bool:
        return bool(order_id > 0 and int(order_id) in self.grid_sell_order_meta)

    def _restore_orphan_stream_sells(self, now_ms: int, tick: float) -> None:
        for chunk in self.inventory_chunks:
            if not self._is_stream_owned_chunk(chunk):
                continue
            if chunk.qty <= self._inventory_epsilon_qty():
                continue
            has_meta_sell = bool(chunk.sell_order_id and int(chunk.sell_order_id) in self.grid_sell_order_meta)
            if has_meta_sell or chunk.state in {"STREAM_WAIT_SELL", "STREAM_SELL_PLACED"}:
                continue
            level_id = int(chunk.stream_id or chunk.grid_level_id or 0)
            if level_id <= 0:
                continue
            target_ticks = max(int(getattr(self.settings, "stream_target_ticks", 30)), 0)
            best_ask = float(self.state.snapshot.ask or 0.0)
            sell_price = self._round_price_up(add_ticks(chunk.entry_price, target_ticks, tick))
            if best_ask > 0:
                sell_price = max(sell_price, max(best_ask - tick, tick))
            try:
                repl = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(sell_price), float(chunk.qty))
            except (BinanceAPIError, RequestException, Exception) as exc:
                self._mark_stream_order_api_error("SELL", level_id, exc)
                self.log("WARNING", f"[EXEC] STREAM_SELL_UNLOCK_FAILED stream_id={level_id} chunk_id={id(chunk)} reason=placement_failed")
                continue
            new_id = int(repl.get("orderId", 0) or 0)
            if new_id <= 0:
                self.log("WARNING", f"[EXEC] STREAM_SELL_UNLOCK_FAILED stream_id={level_id} chunk_id={id(chunk)} reason=empty_order_id")
                continue
            chunk.sell_order_id = new_id
            chunk.sell_placed_ms = now_ms
            chunk.state = "STREAM_SELL_PLACED"
            self.grid_sell_order_meta[new_id] = (level_id, id(chunk))
            self.log("INFO", f"[EXEC] STREAM_SELL_ORPHAN_RESTORED stream_id={level_id} chunk_id={id(chunk)} order_id={new_id} price={sell_price:.2f} qty={chunk.qty:.6f}")

    def _add_inventory_chunk(self, qty: float, entry_price: float, now_ms: int) -> None:
        if qty <= 0:
            return
        grid_level_id = None
        if (int(getattr(self.settings, "stream_count", 1)) >= 1) and self.active_order.get("orderId"):
            grid_level_id = self.grid_level_by_order_id.get(int(self.active_order["orderId"]))
        self.inventory_chunks.append(InventoryChunk(qty=qty, entry_price=entry_price, created_ms=now_ms, grid_level_id=grid_level_id, stream_id=grid_level_id))
        self.log("OK", f"[EXEC] CHUNK ADD qty={qty:.6f} entry={entry_price:.2f}")
        if grid_level_id is not None:
            self.log("INFO", f"[EXEC] GRID_CHUNK_ADD level_id={grid_level_id} chunk_id={id(self.inventory_chunks[-1])}")
        self.log("INFO", f"[EXEC] CHUNK COUNT n={len(self.inventory_chunks)}")
        self._recalc_entry_avg_from_chunks()

    def _stream_counts(self) -> tuple[int, int, int]:
        active_buys = sum(1 for lvl in self.grid_runtime.levels if lvl.state == "BUY_PLACED" and int(lvl.active_buy_order_id or 0) > 0)
        sell_states = {"WAIT_SELL", "SELL_PLACED", "SELL_RETRY", "EXITING"}
        active_sells = 0
        for lvl in self.grid_runtime.levels:
            if lvl.state not in sell_states:
                continue
            if int(lvl.active_sell_order_id or 0) > 0:
                active_sells += 1
        waiting_streams = sum(1 for lvl in self.grid_runtime.levels if lvl.state == "WAIT_BUY" and int(lvl.active_buy_order_id or 0) <= 0)
        return active_buys, active_sells, waiting_streams

    def _poll_grid_orders(self, now_ms: int) -> None:
        if not self.runtime_active or not (int(getattr(self.settings, "stream_count", 1)) >= 1):
            return
        if now_ms < self.grid_order_error_until_ms:
            return
        tick = self._tick_size()
        self._restore_orphan_stream_sells(now_ms, tick)
        target_ticks = max(int(getattr(self.settings, "stream_target_ticks", 30)), 0)
        for level in self.grid_runtime.levels:
            order_id = int(level.active_buy_order_id or 0)
            if level.state != "BUY_PLACED" or order_id <= 0:
                continue
            try:
                st = self.account.get_order(CONFIG.binance_symbol, order_id)
            except (BinanceAPIError, RequestException, Exception) as exc:
                self._mark_stream_order_api_error("BUY", level.level_id, exc)
                continue
            status = str(st.get("status", "NEW"))
            if status == "NEW":
                recenter_interval_ms = max(int(getattr(self.settings, "stream_recenter_interval_ms", 3000)), 0)
                if recenter_interval_ms > 0 and now_ms - self.stream_last_recenter_ms >= recenter_interval_ms:
                    best_bid = float(self.state.snapshot.bid or 0.0)
                    tick = max(self._tick_size(), 1e-12)
                    max_distance_ticks = max(int(getattr(self.settings, "stream_buy_max_distance_ticks", 300)), 0)
                    if best_bid > 0 and max_distance_ticks > 0:
                        distance_ticks = int(round((best_bid - float(level.target_buy_price)) / tick))
                        has_level_inventory = any((c.grid_level_id == level.level_id and c.qty > self._inventory_epsilon_qty()) for c in self.inventory_chunks)
                        has_active_sell = any((lvl_id == level.level_id) for lvl_id, _ in self.grid_sell_order_meta.values())
                        if distance_ticks > max_distance_ticks and not has_level_inventory and not has_active_sell:
                            old_price = float(level.target_buy_price)
                            try:
                                self.account.cancel_order(CONFIG.binance_symbol, order_id)
                            except (BinanceAPIError, RequestException, Exception) as exc:
                                self._mark_stream_order_api_error("BUY", level.level_id, exc)
                                continue
                            self.grid_order_ids.discard(order_id)
                            self.grid_level_by_order_id.pop(order_id, None)
                            self.grid_runtime.recycle_level(level.level_id)
                            entry_offset_ticks = max(int(getattr(self.settings, "entry_offset_ticks", 1)), 0)
                            level.target_buy_price = self._round_price_down(max(best_bid - (tick * entry_offset_ticks), tick))
                            self.stream_last_recenter_ms = now_ms
                            self.log("INFO", f"[EXEC] STREAM_BUY_RECENTER old_price={old_price:.2f} new_price={float(level.target_buy_price):.2f} distance_ticks={distance_ticks}")
                            continue
            if status == "FILLED":
                fill_qty = float(st.get("executedQty", 0.0) or 0.0)
                fill_price = float(st.get("price", level.target_buy_price) or level.target_buy_price)
                self.grid_runtime.mark_buy_filled(level.level_id)
                self._add_inventory_chunk(fill_qty, fill_price, now_ms)
                chunk = self.inventory_chunks[-1] if self.inventory_chunks else None
                if chunk is None or fill_qty <= 0:
                    continue
                sell_price = add_ticks(fill_price, target_ticks, tick)
                self.log("INFO", f"[EXEC] STREAM_TARGET_USED ticks={target_ticks} sell_price={sell_price:.2f}")
                try:
                    sell_o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(sell_price), float(fill_qty))
                except (BinanceAPIError, RequestException, Exception) as exc:
                    chunk.state = "STREAM_WAIT_SELL"
                    level.state = "SELL_RETRY"
                    level.active_sell_order_id = None
                    level.active_chunk_id = id(chunk)
                    self._mark_stream_order_api_error("SELL", level.level_id, exc)
                    self.log("WARNING", f"[EXEC] STREAM_SELL_RETRY stream_id={level.level_id} chunk_id={id(chunk)} reason=placement_failed")
                    continue
                sell_order_id = int(sell_o.get("orderId"))
                chunk.entry_order_id = order_id
                chunk.sell_order_id = sell_order_id
                chunk.state = "STREAM_SELL_PLACED"
                chunk.sell_placed_ms = now_ms
                level.state = "SELL_PLACED"
                level.active_sell_order_id = sell_order_id
                level.active_chunk_id = id(chunk)
                self.grid_sell_order_meta[sell_order_id] = (level.level_id, id(chunk))
                self.log("INFO", f"[EXEC] STREAM_SELL_PLACED stream_id={level.level_id} chunk_id={id(chunk)} order_id={sell_order_id} price={sell_price:.2f} qty={fill_qty:.6f}")
            elif status in {"CANCELED", "EXPIRED", "REJECTED"}:
                level.active_buy_order_id = None
                self.grid_runtime.recycle_level(level.level_id)
                self.log("INFO", f"[EXEC] STREAM_SKIP stream_id={level.level_id} reason=BUY_CANCELED order_id={order_id} status={status}")
        for sell_order_id, (level_id, chunk_id) in list(self.grid_sell_order_meta.items()):
            try:
                st = self.account.get_order(CONFIG.binance_symbol, int(sell_order_id))
            except (BinanceAPIError, RequestException, Exception) as exc:
                self._mark_stream_order_api_error("SELL", level_id, exc)
                continue
            status = str(st.get("status", "NEW"))
            level = next((lv for lv in self.grid_runtime.levels if lv.level_id == level_id), None)
            if level is not None:
                level.active_sell_order_id = int(sell_order_id)
                level.active_chunk_id = chunk_id
                level.state = "SELL_PLACED" if status in {"NEW", "PARTIALLY_FILLED"} else level.state
            self.log("INFO", f"[EXEC] STREAM_SELL_STATUS stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id} status={status}")
            if status == "NEW":
                timeout_handled = False
                timeout_ms = max(int(getattr(self.settings, "stream_sell_timeout_ms", 12000)), 1)
                for chunk in self.inventory_chunks:
                    if id(chunk) != chunk_id:
                        continue
                    placed_ms = int(chunk.sell_placed_ms or chunk.created_ms or now_ms)
                    age_ms = max(now_ms - placed_ms, 0)
                    if age_ms < timeout_ms:
                        break
                    self.log("WARNING", f"[EXEC] STREAM_SELL_TIMEOUT stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id} age_ms={age_ms} timeout_ms={timeout_ms}")
                    try:
                        self.account.cancel_order(CONFIG.binance_symbol, int(sell_order_id))
                        self.log("INFO", f"[EXEC] STREAM_SELL_CANCEL_OLD stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id}")
                    except (BinanceAPIError, RequestException, Exception) as exc:
                        self._mark_stream_order_api_error("SELL", level_id, exc)
                        timeout_handled = True
                        break
                    self.grid_sell_order_meta.pop(sell_order_id, None)
                    chunk.sell_order_id = None
                    best_bid = float(self.state.snapshot.bid or 0.0)
                    best_ask = float(self.state.snapshot.ask or 0.0)
                    stop_loss_ticks = max(int(getattr(self.settings, "stop_loss_ticks", 0)), 0)
                    min_profit_ticks = max(int(getattr(self.settings, "stream_min_profit_ticks", 0)), 0)
                    retry_max = max(int(getattr(self.settings, "stream_sell_retry_max", 3)), 0)
                    retry_step_ticks = max(int(getattr(self.settings, "stream_sell_retry_step_ticks", 20)), 0)
                    emergency_or_manual_stop = not self.runtime_active
                    retry_available = chunk.sell_retry_count < retry_max
                    if retry_available and not emergency_or_manual_stop:
                        self.log("INFO", f"[EXEC] STREAM_STOP_LOSS_BLOCKED_RETRY_AVAILABLE retry_count={chunk.sell_retry_count} retry_max={retry_max} stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id}")
                        retry_price = self._round_price_up(chunk.entry_price + tick * min_profit_ticks)
                        if best_ask > 0:
                            retry_price = max(retry_price, max(best_ask - tick * retry_step_ticks, tick))
                        new_price = self._round_price_up(retry_price)
                        chunk.sell_retry_count += 1
                        self.log("INFO", f"[EXEC] STREAM_SELL_RETRY_PLACED stream_id={level_id} chunk_id={chunk_id} prev_order_id={sell_order_id} retry_count={chunk.sell_retry_count} price={new_price:.2f} qty={chunk.qty:.6f}")
                    else:
                        if chunk.sell_retry_count < retry_max and not emergency_or_manual_stop:
                            raise AssertionError("STREAM_STOP_LOSS_EXIT forbidden while retry is available")
                        stop_loss_price = self._round_price_down(max(chunk.entry_price - tick * stop_loss_ticks, tick))
                        new_price = self._round_price_down(max(best_bid, stop_loss_price, tick))
                        self.log("WARNING", f"[EXEC] STREAM_STOP_LOSS_EXIT stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id} stop_price={new_price:.2f} retry_count={chunk.sell_retry_count} retry_max={retry_max}")
                    try:
                        repl = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(chunk.qty))
                    except (BinanceAPIError, RequestException, Exception) as exc:
                        chunk.state = "STREAM_WAIT_SELL"
                        self._mark_stream_order_api_error("SELL", level_id, exc)
                        self.log("WARNING", f"[EXEC] STREAM_SELL_UNLOCK_FAILED stream_id={level_id} chunk_id={chunk_id} reason=placement_failed")
                        timeout_handled = True
                        break
                    new_id = int(repl.get("orderId", 0) or 0)
                    if new_id > 0:
                        chunk.sell_order_id = new_id
                        chunk.sell_placed_ms = now_ms
                        chunk.state = "STREAM_SELL_PLACED"
                        level = next((lv for lv in self.grid_runtime.levels if lv.level_id == level_id), None)
                        if level is not None:
                            level.state = "SELL_PLACED"
                            level.active_sell_order_id = new_id
                            level.active_chunk_id = chunk_id
                        self.grid_sell_order_meta[new_id] = (level_id, chunk_id)
                    else:
                        chunk.state = "STREAM_WAIT_SELL"
                        level = next((lv for lv in self.grid_runtime.levels if lv.level_id == level_id), None)
                        if level is not None:
                            level.state = "SELL_RETRY"
                            level.active_sell_order_id = None
                            level.active_chunk_id = chunk_id
                        self.log("WARNING", f"[EXEC] STREAM_SELL_UNLOCK_FAILED stream_id={level_id} chunk_id={chunk_id} reason=empty_order_id")
                    timeout_handled = True
                    break
                if timeout_handled:
                    continue

            if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                self.log("WARNING", f"[EXEC] STREAM_SELL_RETRY stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id} status={status}")
                best_ask = float(self.state.snapshot.ask or 0.0)
                for chunk in self.inventory_chunks:
                    if id(chunk) != chunk_id:
                        continue
                    retry_ticks = max(int(getattr(self.settings, "stream_target_ticks", 80)), 0)
                    retry_price = add_ticks(chunk.entry_price, retry_ticks, tick)
                    if best_ask > 0:
                        retry_price = max(retry_price, max(best_ask - tick, tick))
                    retry_price = self._round_price_up(retry_price)
                    self.log("INFO", f"[EXEC] STREAM_TARGET_USED ticks={retry_ticks} sell_price={retry_price:.2f}")
                    try:
                        retry_order = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(retry_price), float(chunk.qty))
                    except (BinanceAPIError, RequestException, Exception) as exc:
                        chunk.state = "STREAM_WAIT_SELL"
                        self._mark_stream_order_api_error("SELL", level_id, exc)
                        self.log("WARNING", f"[EXEC] STREAM_SELL_RETRY stream_id={level_id} chunk_id={chunk_id} reason=placement_failed")
                        break
                    retry_order_id = int(retry_order.get("orderId", 0) or 0)
                    if retry_order_id > 0:
                        chunk.sell_order_id = retry_order_id
                        chunk.state = "STREAM_SELL_PLACED"
                        chunk.sell_placed_ms = now_ms
                        level = next((lv for lv in self.grid_runtime.levels if lv.level_id == level_id), None)
                        if level is not None:
                            level.state = "SELL_PLACED"
                            level.active_sell_order_id = retry_order_id
                            level.active_chunk_id = chunk_id
                        self.grid_sell_order_meta[retry_order_id] = (level_id, chunk_id)
                        self.log("INFO", f"[EXEC] STREAM_SELL_RETRY_PLACED stream_id={level_id} chunk_id={chunk_id} order_id={retry_order_id} price={retry_price:.2f} qty={chunk.qty:.6f}")
                    break
                self.grid_sell_order_meta.pop(sell_order_id, None)
                continue
            if status != "FILLED":
                continue
            executed_qty = float(st.get("executedQty", 0.0) or 0.0)
            fill_price = float(st.get("price", 0.0) or 0.0)
            for idx, chunk in enumerate(self.inventory_chunks):
                if id(chunk) != chunk_id:
                    continue
                qty_to_close = min(max(chunk.qty, 0.0), executed_qty)
                if qty_to_close > 0:
                    self.log("INFO", f"[EXEC] STREAM_SELL_FILLED stream_id={level_id} chunk_id={chunk_id} order_id={sell_order_id}")
                    pnl = (fill_price - chunk.entry_price) * qty_to_close
                    self.log("OK", f"[EXEC] FIFO CLOSE qty={qty_to_close:.6f} entry={chunk.entry_price:.2f} exit={fill_price:.2f} pnl={pnl:+.6f}")
                    self.log("INFO", f"[EXEC] STREAM_PNL stream_id={level_id} pnl={pnl:+.6f}")
                    self.stream_realized_pnl += pnl
                    self.stream_last_pnl = pnl
                    eps = self._inventory_epsilon_qty()
                    self.stream_closed_cycles += 1
                    if pnl > eps:
                        self.stream_wins += 1
                    elif pnl < -eps:
                        self.stream_losses += 1
                        cooldown_ms = max(int(getattr(self.settings, "stream_loss_cooldown_ms", 5000)), 0)
                        if cooldown_ms > 0:
                            self.entry_guard_cooldown_until_ms = max(self.entry_guard_cooldown_until_ms, now_ms + cooldown_ms)
                            self.log("WARNING", f"[EXEC] STREAM_LOSS_COOLDOWN stream_id={level_id} cooldown_ms={cooldown_ms}")
                    self.stream_winrate = (self.stream_wins / self.stream_closed_cycles * 100.0) if self.stream_closed_cycles else 0.0
                self.inventory_chunks.pop(idx)
                self._recalc_entry_avg_from_chunks()
                self.grid_runtime.recycle_level(level_id)
                self.log("INFO", f"[EXEC] STREAM_RECYCLED stream_id={level_id}")
                break
            self.grid_sell_order_meta.pop(sell_order_id, None)

    def _mark_stream_order_api_error(self, side: str, stream_id: int | None, exc: Exception) -> None:
        now_ms = int(time.time() * 1000)
        cooldown_ms = max(int(getattr(self.settings, "stream_order_error_cooldown_ms", 1500)), 0)
        max_errors = max(int(getattr(self.settings, "stream_max_order_errors", 5)), 1)
        self.grid_order_error_count = min(self.grid_order_error_count + 1, max_errors)
        self.grid_order_error_until_ms = now_ms + cooldown_ms
        self.log("ERROR", f"[EXEC] STREAM_ORDER_API_ERROR side={side} stream_id={stream_id} error={exc}")

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
                drained_level_id = chunk.grid_level_id
                self.inventory_chunks.pop(0)
                if drained_level_id is not None and not any(c.grid_level_id == drained_level_id for c in self.inventory_chunks):
                    self.grid_runtime.recycle_level(drained_level_id)
        self._recalc_entry_avg_from_chunks()
        self._apply_fifo_close_result(realized)
        return realized

    def _sync_sell_target_qty(self) -> float:
        self.sell_target_qty = self._remaining_to_sell()
        return self.sell_target_qty

    def _safe_sell_qty(self, candidate_qty: float, *, refresh_balance: bool = False) -> float:
        if refresh_balance:
            self.refresh_account_data()
        inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
        free_btc = float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0)
        rounded_qty = max(self._normalize_qty(float(candidate_qty)), 0.0)
        safe_qty = min(inventory_qty, free_btc, rounded_qty)
        epsilon = self._inventory_epsilon_qty()
        if safe_qty <= epsilon:
            return 0.0
        if safe_qty + epsilon < rounded_qty or inventory_qty + epsilon < rounded_qty or free_btc + epsilon < rounded_qty:
            now = int(time.time() * 1000)
            clamp_sig = f"{inventory_qty:.8f}:{free_btc:.8f}:{safe_qty:.8f}"
            if clamp_sig != self.last_sell_qty_clamp_sig or now - self.last_sell_qty_clamp_log_ms >= 3000:
                stream_open_sells = len(self.grid_sell_order_meta)
                stream_locked_btc = float(self.balances.get("BTC", {}).get("locked", 0.0) or 0.0)
                if stream_open_sells > 0 and stream_locked_btc > epsilon:
                    self.log("INFO", f"[EXEC] STREAM LOCKED BTC locked={stream_locked_btc:.6f} STREAM OPEN SELLS={stream_open_sells}")
                else:
                    self.log("WARNING", f"[EXEC] SELL_QTY_CLAMP inventory={inventory_qty:.6f} free={free_btc:.6f} final={safe_qty:.6f}")
                self.last_sell_qty_clamp_sig = clamp_sig
                self.last_sell_qty_clamp_log_ms = now
        return safe_qty

    def _log_exit_recovery_throttled(self, qty: float) -> None:
        if self._should_skip_global_sell_engine("exit_recovery_inventory_no_sell"):
            return
        if self._is_taker_exit_active():
            self._log_taker_guard_skip("exit_recovery_inventory_no_sell")
            return
        now = int(time.time() * 1000)
        if now - self.last_exit_recovery_log_ms >= 3000:
            self.log("INFO", f"[EXEC] EXIT RECOVERY inventory_no_sell qty={qty:.6f}")
            self.last_exit_recovery_log_ms = now

    def _sell_far_cancel_unstick(self, now_ms: int, reason: str) -> None:
        action = "taker"
        if self._trigger_taker_exit(now_ms, reason):
            self.log("WARNING", f"[EXEC] SELL_FAR_CANCEL_UNSTICK action={action}")
            return
        action = "panic"
        if self.position_qty > self._inventory_epsilon_qty():
            self.trigger_panic_exit(reason)
            if self.fsm_state != "WAIT_MANUAL":
                self.log("WARNING", f"[EXEC] SELL_FAR_CANCEL_UNSTICK action={action}")
                return
        self.runtime_halt_manual_check = True
        self.fsm_state = "WAIT_MANUAL"
        self.log("ERROR", "[EXEC] SELL_FAR_CANCEL_UNSTICK action=wait_manual")

    def _handle_place_sell_stuck(self, now_ms: int) -> None:
        if self._should_skip_global_sell_engine("place_sell_stuck_recovery"):
            return
        if self._is_taker_exit_active():
            self._log_taker_guard_skip("place_sell_stuck_recovery")
            return
        cooldown_ms = int(getattr(self.settings, "place_sell_recovery_cooldown_ms", 2500))
        if now_ms - self.last_sell_recovery_attempt_ms < cooldown_ms:
            return
        self.last_sell_recovery_attempt_ms = now_ms
        inv = self._safe_sell_qty(self._sync_sell_target_qty(), refresh_balance=True)
        self.log("WARNING", f"[EXEC] PLACE_SELL_STUCK_RECOVERY qty={inv:.6f}")
        if inv <= self._inventory_epsilon_qty():
            return
        if self.active_order.get("orderId"):
            self.sync_active_order(force=True)
            return
        self.force_reprice_sell_next = True

    def _trigger_taker_exit(self, now_ms: int, reason: str) -> bool:
        if self._should_skip_global_sell_engine(f"taker_{reason}"):
            return False
        if not bool(getattr(self.settings, "taker_exit_enabled", True)) or self.panic_exit_final:
            return False
        if self.taker_exit_triggered and self.taker_exit_state in {"TRIGGERED", "ORDER_SENT"}:
            return False
        if self._should_block_protected_sell_escalation(now_ms, f"taker_{reason}"):
            return False
        order_id = int(self.active_order.get("orderId", 0) or 0)
        if order_id <= 0 or self.active_order.get("side") != "SELL":
            return False
        age_ms = max(now_ms - int(self.active_order.get("create_ms", now_ms) or now_ms), 0)
        self.log("WARNING", f"[EXEC] TAKER_EXIT_TRIGGER reason={reason} age={age_ms} qty={self.position_qty:.6f}")
        if self.smart_exit_active:
            self.log("WARNING", f"[EXEC] SMART_EXIT_TO_TAKER reason={reason}")
        self.log("WARNING", f"[EXEC] TAKER_EXIT_CANCEL_MAKER orderId={order_id}")
        self.account.cancel_order(CONFIG.binance_symbol, order_id)
        final = self.account.get_order(CONFIG.binance_symbol, order_id)
        self._handle_sell_fill_update(final)
        if str(final.get("status", "")) == "FILLED" or self.position_qty <= self._inventory_epsilon_qty():
            self._handle_sell_filled(final, order_id)
            return True
        self.refresh_account_data()
        sell_qty = self._safe_sell_qty(self._sync_sell_target_qty())
        min_sellable_qty = self._min_sellable_qty()
        if sell_qty <= self._inventory_epsilon_qty() or sell_qty < min_sellable_qty:
            self.sync_active_order(force=True)
            open_sell = self._find_open_sell_order()
            if open_sell:
                self._adopt_open_sell_order(open_sell)
                return False
            self.log("WARNING", f"[EXEC] TAKER_EXIT_FAILED reason=qty_below_min_sellable qty={sell_qty:.6f} min={min_sellable_qty:.6f}")
            self.last_taker_exit_reason = "qty_below_min_sellable"
            return False
        bid_now = float(self.state.snapshot.bid or 0.0)
        tick = self._tick_size()
        max_slip_ticks = max(int(getattr(self.settings, "taker_exit_max_slippage_ticks", 10)), 0)
        taker_price = self._round_price_down(max(bid_now - (tick * max_slip_ticks), tick))
        tif = "IOC" if bool(getattr(self.settings, "taker_exit_ioc", True)) else "GTC"
        self.taker_exit_state = "TRIGGERED"
        self.last_taker_reason = reason
        self.last_taker_exit_reason = reason
        self.last_taker_qty = sell_qty
        self.last_taker_price = taker_price
        self.taker_exit_triggered = True
        try:
            self.log("INFO", f"[EXEC] PLACE_SELL_SOURCE source=taker price={taker_price:.2f}")
            o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(taker_price), float(sell_qty), time_in_force=tif)
            new_id = int(o.get("orderId", 0) or 0)
            self.active_order = {"orderId": new_id, "side": "SELL", "price": float(taker_price), "qty": float(sell_qty), "create_ms": now_ms, "state": "NEW", "type": "LIMIT"}
            self.taker_exit_order_id = new_id
            self.taker_exit_sent_ms = now_ms
            self.last_taker_status_poll_ms = 0
            self.position_sell_order_id = new_id
            self.exit_started_ms = now_ms
            self.exit_mode = "TAKER"
            self.taker_exit_state = "ORDER_SENT"
            self.fsm_state = "WAIT_TAKER_EXIT_STATUS"
            self.log("WARNING", f"[EXEC] TAKER_EXIT_ORDER_SENT id={new_id} price={taker_price:.2f} qty={sell_qty:.6f}")
            return True
        except Exception as exc:
            self.taker_exit_state = "FAILED"
            self.last_taker_exit_reason = f"failed:{exc}"
            self.log("ERROR", f"[EXEC] TAKER_EXIT_FAILED reason={exc}")
            return False

    def _is_taker_exit_active(self) -> bool:
        return bool(self.taker_exit_order_id) and self.taker_exit_state in {"ORDER_SENT", "WAIT_STATUS"}

    def _sync_taker_exit_status(self, now_ms: int) -> None:
        order_id = int(self.taker_exit_order_id or 0)
        if order_id <= 0:
            self.fsm_state = "PLACE_SELL"
            return
        poll_ms = int(getattr(self.settings, "taker_status_poll_ms", 300))
        timeout_ms = int(getattr(self.settings, "taker_status_timeout_ms", 1500))
        sent_ms = int(self.taker_exit_sent_ms or now_ms)
        elapsed = max(now_ms - sent_ms, 0)
        if now_ms - int(self.last_taker_status_wait_log_ms or 0) >= 1000:
            self.log("INFO", f"[EXEC] TAKER_STATUS_WAIT orderId={order_id}")
            self.last_taker_status_wait_log_ms = now_ms
        if self.last_taker_status_poll_ms and now_ms - self.last_taker_status_poll_ms < poll_ms:
            return
        self.taker_exit_state = "WAIT_STATUS"
        self.last_taker_status_poll_ms = now_ms
        try:
            st = self.account.get_order(CONFIG.binance_symbol, order_id)
            self.taker_status_error_count = 0
        except Exception as exc:
            self.taker_status_error_count += 1
            self.log("ERROR", f"[EXEC] TAKER_STATUS_ERROR error={exc}")
            if elapsed >= timeout_ms:
                self._handle_taker_status_timeout(now_ms, order_id, timeout_reason="error_timeout")
            if self.taker_status_error_count >= 3:
                self.log("ERROR", f"[EXEC] TAKER_STATUS_FAILED_MANUAL orderId={order_id} errors={self.taker_status_error_count}")
                self.fsm_state = "WAIT_MANUAL"
            return
        status = str(st.get("status", "UNKNOWN"))
        self.log("INFO", f"[EXEC] TAKER_STATUS_SYNC orderId={order_id} status={status}")
        prev_sell_reported_qty = self.sell_reported_qty
        self._handle_sell_fill_update(st)
        sell_delta = max(self.sell_reported_qty - prev_sell_reported_qty, 0.0)
        remaining = self._safe_sell_qty(self._sync_sell_target_qty(), refresh_balance=True)
        if status == "FILLED" or self.position_qty <= self._inventory_epsilon_qty():
            self.log("OK", f"[EXEC] TAKER_STATUS_FILLED orderId={order_id}")
            self._handle_sell_filled(st, order_id)
            return
        if status == "PARTIALLY_FILLED":
            self.log("WARNING", f"[EXEC] TAKER_STATUS_PARTIAL orderId={order_id} filled={sell_delta:.6f} remaining={remaining:.6f}")
            self._consume_fifo(sell_delta)
            if remaining > self._min_sellable_qty():
                if self.smart_exit_active:
                    self.log("WARNING", f"[EXEC] SMART_EXIT_TAKER_PARTIAL_REMAINING qty={remaining:.6f}")
                    self._smart_exit_retry_aggressive(now_ms, "taker_partial_remaining")
                    return
                self.active_order = {}
                self.taker_exit_order_id = 0
                self.taker_exit_state = "IDLE"
                self.fsm_state = "PLACE_SELL"
                return
            self._maybe_reconcile_micro_partial_dust(remaining, reason="taker_partial_remaining")
            self._finalize_cycle_if_flat()
            self.fsm_state = "DONE"
            return
        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
            self.log("WARNING", f"[EXEC] TAKER_STATUS_FAILED status={status}")
            self._handle_taker_status_timeout(now_ms, order_id, timeout_reason=f"status_{status.lower()}")
            return
        if status == "NEW" and elapsed >= timeout_ms:
            self._handle_taker_status_timeout(now_ms, order_id, timeout_reason="new_timeout")
            return

    def _handle_taker_status_timeout(self, now_ms: int, order_id: int, timeout_reason: str = "timeout") -> None:
        self.log("ERROR", f"[EXEC] TAKER_STATUS_TIMEOUT orderId={order_id}")
        self.refresh_account_data()
        self.sync_active_order(force=True)
        try:
            latest = self.account.get_order(CONFIG.binance_symbol, order_id)
            latest_status = str(latest.get("status", "UNKNOWN"))
        except Exception as exc:
            latest = {}
            latest_status = "UNKNOWN"
            self.log("ERROR", f"[EXEC] TAKER_STATUS_ERROR error={exc}")
        if latest_status in {"FILLED", "PARTIALLY_FILLED"}:
            prev = self.sell_reported_qty
            self._handle_sell_fill_update(latest)
            delta = max(self.sell_reported_qty - prev, 0.0)
            if latest_status == "FILLED":
                self.log("OK", f"[EXEC] TAKER_STATUS_FILLED orderId={order_id}")
                self._handle_sell_filled(latest, order_id)
                return
            remaining = self._safe_sell_qty(self._sync_sell_target_qty(), refresh_balance=True)
            self.log("WARNING", f"[EXEC] TAKER_STATUS_PARTIAL orderId={order_id} filled={delta:.6f} remaining={remaining:.6f}")
            self._consume_fifo(delta)
            if remaining > self._min_sellable_qty():
                if self.smart_exit_active:
                    self.log("WARNING", f"[EXEC] SMART_EXIT_TAKER_PARTIAL_REMAINING qty={remaining:.6f}")
                    self.log("WARNING", "[EXEC] SMART_EXIT_TO_PANIC reason=taker_partial_timeout_remaining")
                    self.active_order = {}
                    self.taker_exit_order_id = 0
                    self.taker_exit_state = "IDLE"
                    self.trigger_panic_exit("smart_exit_taker_partial_timeout_remaining")
                    return
                self.active_order = {}
                self.taker_exit_order_id = 0
                self.taker_exit_state = "IDLE"
                self.fsm_state = "PLACE_SELL"
                return
            self._maybe_reconcile_micro_partial_dust(remaining, reason="taker_partial_timeout_remaining")
            self._finalize_cycle_if_flat()
            self.fsm_state = "DONE"
            return
        self.active_order = {}
        self.taker_exit_order_id = 0
        self.taker_exit_state = "IDLE"
        remaining = self._safe_sell_qty(self._sync_sell_target_qty(), refresh_balance=True)
        btc_locked = float(self.balances.get("BTC", {}).get("locked", 0.0) or 0.0)
        if remaining > self._min_sellable_qty():
            if self.smart_exit_active:
                self._smart_exit_retry_aggressive(now_ms, timeout_reason)
            else:
                self.fsm_state = "PLACE_SELL"
            return
        if remaining <= self._inventory_epsilon_qty() and btc_locked <= self._inventory_epsilon_qty():
            self._maybe_reconcile_phantom_inventory(self.position_qty, remaining, float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0), btc_locked)
            self._finalize_cycle_if_flat()
            self.fsm_state = "DONE"
            return
        if btc_locked > self._inventory_epsilon_qty():
            self.fsm_state = "WAIT_EXCHANGE_UNLOCK"
            return
        self.log("ERROR", f"[EXEC] TAKER_STATUS_FAILED_MANUAL orderId={order_id} reason={timeout_reason}")
        self.fsm_state = "WAIT_MANUAL"

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
        tick = self._tick_size()
        panic_step = max(float(getattr(self.settings, "aggressive_exit_offset", 1.0)), tick)
        new_price = self._round_price_down(max(ask_now - panic_step, tick))
        sell_qty = self._safe_sell_qty(self.position_qty, refresh_balance=True)
        if sell_qty <= self._inventory_epsilon_qty():
            self._cleanup_inventory_if_drained()
            self._reconcile_position_state("inferred_by_balance")
            self.log("OK", "[EXEC] EXIT_FILLED inferred_by_balance")
            self.fsm_state = "WAIT_READY" if self.runtime_active else "DONE"
            self.place_sell_entered_ms = 0
            return
        self.log("WARNING", f"[EXEC] FORCE EXIT price={new_price:.2f}")
        self.log("INFO", f"[EXEC] PLACE_SELL_SOURCE source=panic price={new_price:.2f}")
        self.log("OK", f"[EXEC] PLACE SELL price={new_price:.2f} qty={sell_qty:.6f}")
        try:
            o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(sell_qty))
        except Exception as exc:
            self.log("ERROR", f"[EXEC] PANIC SELL FAILED reason={exc}")
            self.fsm_state = "EXIT_FAILED"
            self.position_state = "EXIT_FAILED"
            return
        self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(new_price), "qty": float(sell_qty), "create_ms": now_ms, "state": "NEW", "type": "LIMIT"}
        self.smart_exit_last_order_id = int(self.active_order.get("orderId", 0) or 0)
        self.smart_exit_last_action_ts = now_ms
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


    def trigger_panic_exit(self, reason: str) -> None:
        epsilon = self._inventory_epsilon_qty()
        if self.position_qty <= epsilon:
            return
        if self.sell_recovery_in_progress or self.sell_cancel_in_progress:
            self.log("INFO", "[EXEC] RECOVERY WAIT panic_trigger_in_progress")
            return
        now_ms = int(time.time() * 1000)
        manual_or_hard_stop_reason = reason in {"manual_stop", "hard_stop", "hard_sl", "stop_loss", "sell_hold_timeout"}
        emergency_exchange_reason = "exchange_error" in reason
        if not manual_or_hard_stop_reason and not emergency_exchange_reason:
            if self._should_block_protected_sell_escalation(now_ms, f"panic_{reason}"):
                return
        order_id = int(self.active_order.get("orderId", 0) or 0)
        if self.panic_exit_final and order_id:
            self.fsm_state = "WAIT_SELL_FILL"
            return
        self.log("WARNING", f"[EXEC] EXIT_FAIL reason={reason} force_exit")
        if reason.startswith("smart_exit_") or self.smart_exit_active:
            self.smart_exit_active = True
            self.smart_exit_attempts += 1
            self.smart_exit_last_action_ts = now_ms
        self.last_exit_reason = reason
        if order_id and self.active_order.get("side") == "SELL":
            self.sell_cancel_in_progress = True
            try:
                self.log("WARNING", f"[EXEC] CANCEL SELL orderId={order_id} reason={reason}")
                self.account.cancel_order(CONFIG.binance_symbol, order_id)
            except Exception as exc:
                self.log("WARNING", f"[EXEC] CANCEL SELL FAILED reason={exc}")
            finally:
                self.sell_cancel_in_progress = False
            try:
                final = self.account.get_order(CONFIG.binance_symbol, order_id)
                self._handle_sell_fill_update(final)
                if str(final.get("status", "")) == "FILLED" or self.position_qty <= epsilon:
                    self._handle_sell_filled(final, order_id)
                    return
            except Exception as exc:
                self.log("WARNING", f"[EXEC] SELL FINAL STATUS FAILED reason={exc}")
        self._panic_exit_final(now_ms, reason)

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
        self.exit_blocked_no_sellable_count = 0
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
        self.exit_blocked_no_sellable_count = 0
        sell_qty = float(st.get("executedQty", 0.0) or 0.0)
        self.log("OK", f"[EXEC] SELL FILLED id={order_ref}")
        remaining = max(self.position_qty, 0.0)
        self.log("OK", f"[EXEC] SELL FILLED qty={sell_qty:.6f} remaining={remaining:.6f}")
        if self.exit_mode == "TAKER":
            self.log("OK", f"[EXEC] TAKER_EXIT_FILLED qty={sell_qty:.6f}")
        if self.smart_exit_active and remaining <= self._inventory_epsilon_qty():
            self.log("OK", "[EXEC] SMART_EXIT_DONE_FLAT")
            self.smart_exit_active = False
            self.smart_exit_attempts = 0
            self.smart_exit_last_order_id = 0
            self.smart_exit_last_action_ts = 0
        self.active_order = {}
        self.buy_filled_qty = 0.0
        self.sell_reported_qty = 0.0
        if remaining <= self._inventory_epsilon_qty() and self._cleanup_inventory_if_drained():
            self.fsm_state = "WAIT_READY" if self.runtime_active else "DONE"
            self.place_sell_entered_ms = 0
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
                self.max_hold_exit_triggered = False
            self.fsm_state = "WAIT_READY" if self.runtime_active else "DONE"
            self.place_sell_entered_ms = 0
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
                self.place_sell_entered_ms = 0
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
                self.log("WARNING", f"[EXEC] FAST_SAFE_EXIT_EXECUTE price={safe_exit_price:.2f} qty={self.position_qty:.6f}")
                self.sell_upsize_skip_logged = True
                if self._trigger_taker_exit(now_ms, "fast_safe_exit"):
                    return
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
                    self.place_sell_entered_ms = 0
                    return
                candidate_price = max(bid_now + tick, ask_now - aggressive)
            candidate_price = self._apply_sell_floor(float(candidate_price), "watchdog_reprice")
            new_price = self._cap_soft_sell_reprice(old_price, candidate_price)
            sell_qty = self._safe_sell_qty(self._sync_sell_target_qty(), refresh_balance=True)
            min_sellable_qty = self._min_sellable_qty()
            if sell_qty <= self._inventory_epsilon_qty() or sell_qty < min_sellable_qty:
                self.log("INFO", f"[EXEC] SKIP MICRO SELL epsilon={self._inventory_epsilon_qty():.6f}")
                self._cleanup_inventory_if_drained()
                self._finalize_cycle_if_flat()
                self.fsm_state = "WAIT_READY"
                return
            self.sell_reprice_count += 1
            self.log("WARNING", f"[EXEC] SELL REPRICE old={old_price:.2f} new={new_price:.2f} count={self.sell_reprice_count}")
            self.log("INFO", f"[EXEC] SELL REPLACE remaining={sell_qty:.6f}")
            self.log("INFO", f"[EXEC] PLACE_SELL_SOURCE source=plan price={new_price:.2f}")
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
        free_btc_runtime = float(self.balances.get("BTC", {}).get("free", 0.0) or 0.0)
        inventory_runtime = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
        safe_runtime = self._safe_sell_qty(self._sync_sell_target_qty())
        self.runtime["Free BTC"].setText(self._fmt(free_btc_runtime, 6))
        self.runtime["Inventory BTC"].setText(self._fmt(inventory_runtime, 6))
        self.runtime["Safe SELL qty"].setText(self._fmt(safe_runtime, 6))
        self.runtime["Market Health"].setText(self.market_health_state)
        self.runtime["Entry Guard"].setText(self.settings.guard_mode)
        self.runtime["Guard state"].setText(self.entry_guard_state)
        self.runtime["Guard reason"].setText(self.entry_guard_reason)
        self.runtime["Stable snaps"].setText(f"{self.entry_guard_stable_count}/{self.settings.stable_snapshots_required}")
        now_ms = int(time.time() * 1000)
        self._poll_grid_orders(now_ms)
        if self.runtime_active and self.fsm_state == "WAIT_TAKER_EXIT_STATUS":
            self._sync_taker_exit_status(now_ms)
            return
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
        self.runtime["Exit stage"].setText(self.exit_stage)
        active_sell_age_ms = 0
        if self.active_order.get("orderId") and self.active_order.get("side") == "SELL":
            active_sell_age_ms = max(now_ms - int(self.active_order.get("create_ms", now_ms) or now_ms), 0)
        self.runtime["SELL age"].setText(f"{active_sell_age_ms}ms")
        self.runtime["SELL reprices"].setText(str(self.sell_reprice_count))
        self.runtime["Panic ladder"].setText(str(self.panic_ladder_step))
        panic_age_ms = max(now_ms - int(self.panic_exit_started_ms), 0) if self.panic_exit_final and self.panic_exit_started_ms else 0
        self.runtime["Panic age"].setText(f"{panic_age_ms}ms")
        self.runtime["Panic holds"].setText(str(self.panic_hold_count))
        self.runtime["Exit blocked"].setText(self.exit_block_reason)
        self.runtime["Taker exit"].setText("ON" if self.settings.taker_exit_enabled else "OFF")
        self.runtime["Taker reason"].setText(self.last_taker_reason)
        self.runtime["Taker qty/price"].setText(f"{self.last_taker_qty:.6f}@{self.last_taker_price:.2f}" if self.last_taker_qty > 0 else "-")
        self.runtime["Last exit reason"].setText(self.last_exit_reason)
        inventory_u = inventory_runtime * float(self.state.snapshot.bid or 0.0)
        grid_queue = sum(1 for lvl in self.grid_runtime.levels if lvl.state == "WAIT_BUY" and lvl.active_buy_order_id is None)
        grid_stats = self.grid_runtime.grid_telemetry(inventory_u=inventory_u, buy_paused=self.grid_buy_paused, placement_queue=grid_queue, last_batch_size=self.grid_last_batch_size) if (int(getattr(self.settings, "stream_count", 1)) >= 1) else {}
        for key in ("GRID LEVELS", "GRID ACTIVE BUYS", "GRID ACTIVE SELLS", "GRID FILLED LEVELS", "GRID PLACEMENT QUEUE", "GRID LAST BATCH SIZE"):
            self.runtime[key].setText(str(int(grid_stats.get(key, 0))))
        self.runtime["GRID INVENTORY U"].setText(self._fmt(float(grid_stats.get("GRID INVENTORY U", 0.0) or 0.0), 2))
        self.runtime["GRID BUY PAUSED"].setText(str(grid_stats.get("GRID BUY PAUSED", "NO")))
        self.runtime["GRID BUDGET USED"].setText(self._fmt(float(grid_stats.get("GRID BUDGET USED", 0.0) or 0.0), 2))
        self.runtime["GRID BUDGET FREE"].setText(self._fmt(float(grid_stats.get("GRID BUDGET FREE", 0.0) or 0.0), 2))
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
            if self.smart_exit_active:
                self.log("WARNING", f"[EXEC] SMART_EXIT_ORPHAN_INVENTORY qty={self.position_qty:.6f}")
                if not self._trigger_taker_exit(now_ms, "smart_exit_inventory_no_sell"):
                    self._smart_exit_retry_aggressive(now_ms, "inventory_no_sell")
                return
            if self.fsm_state != "PLACE_SELL":
                self._log_exit_recovery_throttled(self.position_qty)
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
        allow_buy = bool(plan) and self.settings.live_enabled and plan.status in {"READY", "HOT"} and market_valid and plan.filters_ok and (plan.required_u or 0.0) <= self.settings.max_exposure_u
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
            elif self.active_order.get("orderId") or self.fsm_state in {"WAIT_BUY_FILL", "PLACE_SELL", "WAIT_SELL_FILL", "SELL_TIMEOUT", "ERROR_POSITION"}:
                self.fsm_state = "DONE"
            elif self.active_order.get("orderId"):
                self.log("WARNING", "[EXEC] BLOCK reason=active_order")
                self.fsm_state = "DONE"
            elif (plan.required_u or 0.0) > self.settings.max_exposure_u:
                self.log("WARNING", f"[EXEC] BLOCK reason=required_u_gt_max_exposure_u required_u={(plan.required_u or 0.0):.4f} max_exposure_u={self.settings.max_exposure_u:.4f}")
                self.fsm_state = "DONE"
            else:
                ok_to_buy, _ = self.final_pre_buy_check(plan, now_ms)
                if not ok_to_buy:
                    self.fsm_state = "DONE"
                else:
                    try:
                        buy_price = float(plan.entry_price)
                        buy_qty = float(plan.qty_btc)
                        active_level_id = None
                        if (int(getattr(self.settings, "stream_count", 1)) >= 1):
                            if now_ms - self.last_grid_skip_single_entry_log_ms >= 4000:
                                self.log("INFO", "[EXEC] STREAM_MODE_ACTIVE_SKIP_SINGLE_ENTRY")
                                self.last_grid_skip_single_entry_log_ms = now_ms
                        if (int(getattr(self.settings, "stream_count", 1)) >= 1):
                            if not self.grid_runtime.levels:
                                self.log("WARNING", "[EXEC] STREAM_SKIP reason=NO_STREAM_LEVELS")
                                self.fsm_state = "DONE"
                                return
                            free_u = float(self.balances.get("U", {}).get("free", 0.0) or 0.0)
                            bid_now = float(self.state.snapshot.bid or 0.0)
                            inventory_u = self.position_qty * bid_now
                            active_buys, active_sells, waiting_streams = self._stream_counts()
                            if active_sells == 0:
                                for sell_order_id, (sid, _chunk_id) in self.grid_sell_order_meta.items():
                                    self.log("ERROR", f"[EXEC] STREAM_STATE_MISMATCH reason=sell_order_not_counted stream_id={sid} order_id={sell_order_id}")
                            has_active_sell = bool(self.active_order.get("orderId") and self.active_order.get("side") == "SELL")
                            max_active_buys = max(int(getattr(self.settings, "stream_max_active_buys", 8)), 1)
                            batch_size = max(int(getattr(self.settings, "stream_place_batch_size", 3)), 1)
                            place_interval_ms = max(int(getattr(self.settings, "stream_place_interval_ms", 500)), 0)
                            self.grid_last_batch_size = 0
                            self.grid_buy_paused = False
                            if bool(getattr(self.settings, "stream_sell_first", True)) and (inventory_u > 0.0 or has_active_sell):
                                self.grid_buy_paused = True
                                unsold = next((c for c in self.inventory_chunks if c.qty > 0 and c.sell_order_id is None), None)
                                if unsold is not None:
                                    self.log("INFO", f"[EXEC] GRID_SELL_FIRST_PENDING chunk_id={id(unsold)} level_id={unsold.grid_level_id}")
                            if inventory_u > float(getattr(self.settings, "stream_pause_buy_inventory_u", 800.0)):
                                self.grid_buy_paused = True
                                self.log("WARNING", f"[EXEC] GRID_BUY_PAUSED_INVENTORY inventory_u={inventory_u:.2f}")
                            if inventory_u > float(getattr(self.settings, "stream_max_inventory_u", 1000.0)):
                                self.grid_buy_paused = True
                                self.log("WARNING", f"[EXEC] GRID_SELL_FIRST_MODE inventory_u={inventory_u:.2f}")
                            free_buy_slots = max(0, max_active_buys - active_buys)
                            interval_elapsed = (now_ms - self.grid_last_place_batch_ms) >= place_interval_ms
                            self.log("INFO", f"[EXEC] STREAM_BUY_CAPACITY active_buys={active_buys} active_sells={active_sells} waiting_streams={waiting_streams} max_active_buys={max_active_buys} free_buy_slots={free_buy_slots} batch_size={batch_size}")
                            placed_any = False
                            placed_count = 0
                            balance_safety_buffer_u = float(getattr(self.settings, "balance_safety_buffer_u", 0.0) or 0.0)
                            max_exposure_u = float(getattr(self.settings, "max_exposure_u", 0.0) or 0.0)
                            blocked_reason = "none"
                            if self.grid_buy_paused:
                                blocked_reason = "balance_low"
                            elif free_buy_slots <= 0:
                                blocked_reason = "no_capacity"
                            elif not interval_elapsed:
                                blocked_reason = "interval_not_elapsed"
                            elif waiting_streams <= 0:
                                blocked_reason = "no_waiting_stream"
                            else:
                                for level in self.grid_runtime.levels:
                                    if placed_count >= batch_size or active_buys >= max_active_buys:
                                        break
                                    if level.state != "WAIT_BUY" or level.active_buy_order_id is not None:
                                        continue
                                    pending_buy_exposure_u = sum(
                                        float(lvl.budget_u)
                                        for lvl in self.grid_runtime.levels
                                        if lvl.state == "BUY_PLACED" and lvl.active_buy_order_id is not None
                                    )
                                    current_exposure_u = max(inventory_u, 0.0) + pending_buy_exposure_u
                                    if current_exposure_u + float(level.budget_u) > max_exposure_u + 1e-12:
                                        blocked_reason = "exposure_limit"
                                        self.log(
                                            "WARNING",
                                            f"[EXEC] STREAM_BUY_BLOCKED reason=exposure_limit current_exposure_u={current_exposure_u:.2f} order_size_u={float(level.budget_u):.2f} max_exposure_u={max_exposure_u:.2f}",
                                        )
                                        continue
                                    if free_u + 1e-12 < (float(level.budget_u) + balance_safety_buffer_u):
                                        blocked_reason = "balance_low"
                                        self.log(
                                            "WARNING",
                                            f"[EXEC] STREAM_BUY_BLOCKED reason=balance_low free_u={free_u:.2f} required_u={(float(level.budget_u) + balance_safety_buffer_u):.2f}",
                                        )
                                        continue
                                    try:
                                        o = self.account.place_limit_order(CONFIG.binance_symbol, "BUY", float(level.target_buy_price), float(level.qty))
                                    except (BinanceAPIError, RequestException, Exception) as exc:
                                        level.state = "WAIT_BUY"
                                        level.active_buy_order_id = None
                                        self._mark_stream_order_api_error("BUY", level.level_id, exc)
                                        continue
                                    buy_order_id = int(o.get("orderId"))
                                    self.grid_level_by_order_id[buy_order_id] = level.level_id
                                    self.grid_order_ids.add(buy_order_id)
                                    self.grid_runtime.mark_buy_placed(level.level_id, buy_order_id)
                                    free_u -= level.budget_u
                                    active_buys += 1
                                    placed_count += 1
                                    placed_any = True
                                    self.log("OK", f"[EXEC] PLACE BUY price={float(level.target_buy_price):.2f} qty={float(level.qty):.6f}")
                                if placed_any:
                                    blocked_reason = "none"
                                elif blocked_reason == "none":
                                    blocked_reason = "no_waiting_stream"
                            self.log("INFO", f"[EXEC] STREAM_PLACEMENT_TICK active_buys={active_buys} active_sells={active_sells} waiting_streams={waiting_streams} free_buy_slots={free_buy_slots} placed={placed_count}")
                            if placed_count == 0:
                                self.log("INFO", f"[EXEC] STREAM_PLACEMENT_TICK reason={blocked_reason}")
                            if placed_any:
                                self.grid_last_place_batch_ms = now_ms
                                self.grid_last_batch_size = placed_count
                                self.fsm_state = "DONE"
                            else:
                                self.log("INFO", "[EXEC] GRID_NO_VALID_LEVELS_FOR_BUY")
                                self.fsm_state = "DONE"
                            return
                        o = self.account.place_limit_order(CONFIG.binance_symbol, "BUY", buy_price, buy_qty)
                        now = int(time.time() * 1000)
                        self._reset_sell_accounting("new_cycle", reset_panic_order_id=self.position_qty <= 1e-12)
                        self.entry_exec_state = "ENTRY_PLACED"
                        self.entry_reprice_count = 0
                        self.last_entry_reprice_ms = now
                        self.entry_last_reason = "placed"
                        self.active_order = {"orderId": o.get("orderId"), "side": "BUY", "price": buy_price, "qty": buy_qty, "create_ms": now, "state": "NEW", "type": "LIMIT"}
                        self.buy_reported_qty = 0.0
                        self.buy_reported_quote = 0.0
                        self.position_state = "BUY_PENDING"
                        self.entry_started_ms = now
                        self.log("OK", f"[EXEC] BUY ORDER SENT orderId={self.active_order['orderId']} price={buy_price:.2f} qty={buy_qty:.6f}")
                        self.log("OK", f"[EXEC] PLACE BUY price={buy_price:.2f} qty={buy_qty:.6f}")
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
            order_id = int(self.active_order["orderId"])
            order_age_ms = now - int(self.active_order.get("create_ms", now) or now)
            if order_age_ms >= int(getattr(self.settings, "buy_watchdog_ms", 2500)) and self._watchdog_ready(order_id, now):
                wd_status = str(st.get("status", "UNKNOWN"))
                self.log("INFO", f"[EXEC] BUY_WATCHDOG_SYNC orderId={order_id} status={wd_status}")
                if wd_status == "CANCELED":
                    self.active_order = {}
                    self.fsm_state = "DONE"
                    return
                if wd_status == "FILLED":
                    self._apply_filled_from_sync(st)
                    return
                if wd_status == "NEW":
                    bid_now = float(self.state.snapshot.bid or 0.0)
                    order_price = float(self.active_order.get("price", 0.0) or 0.0)
                    is_far, dist_ticks = self._is_far_buy(order_price, bid_now)
                    if is_far and order_id not in self.grid_order_ids:
                        self.log("WARNING", f"[EXEC] BUY_WATCHDOG_FAR_CANCEL orderId={order_id} price={order_price:.2f} bid={bid_now:.2f} dist_ticks={dist_ticks}")
                        self.account.cancel_order(CONFIG.binance_symbol, order_id)
                        self.active_order = {}
                        self.fsm_state = "DONE"
                        return
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
                filled_level_id = self.grid_level_by_order_id.get(int(self.active_order["orderId"]))
                if filled_level_id is not None:
                    self.grid_runtime.mark_buy_filled(filled_level_id)
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
                    if self._maybe_reconcile_micro_partial_dust(float(self.position_qty), reason="below_min_sellable"):
                        self.active_order = {}
                        return
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
                is_grid_buy = int(self.active_order.get("orderId", 0) or 0) in self.grid_order_ids
                can_reprice = (not is_grid_buy) and bool(self.settings.entry_reprice_enabled) and self.entry_reprice_count < int(self.settings.max_entry_reprices)
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
            if self._should_skip_global_sell_engine("place_sell"):
                self.fsm_state = "DONE"
                return
            now_ms = int(time.time() * 1000)
            if self._is_taker_exit_active():
                self._log_taker_guard_skip("smart_exit_wait_taker" if self.smart_exit_active else "place_sell")
                self.fsm_state = "WAIT_TAKER_EXIT_STATUS"
                return
            if self.place_sell_entered_ms <= 0:
                self.place_sell_entered_ms = now_ms
            if self.position_state == "SELL_PENDING" and not self.active_order.get("orderId"):
                if self._is_taker_exit_active():
                    self._log_taker_guard_skip("sell_pending_without_order_fix")
                    self.fsm_state = "WAIT_TAKER_EXIT_STATUS"
                    return
                self.position_state = "POSITION_OPEN"
                self.log("WARNING", "[EXEC] SELL_PENDING_WITHOUT_ORDER_FIXED")
            if self.runtime_halt_manual_check:
                return
            place_sell_stuck_ms = int(getattr(self.settings, "place_sell_stuck_ms", 1500))
            if now_ms - self.place_sell_entered_ms >= place_sell_stuck_ms:
                if self.active_order.get("orderId"):
                    self.sync_active_order(force=True)
                elif self.position_qty > self._inventory_epsilon_qty():
                    self._handle_place_sell_stuck(now_ms)
                self.place_sell_entered_ms = now_ms
            epsilon_qty = self._inventory_epsilon_qty()
            if self.sell_recovery_in_progress or self.sell_cancel_in_progress:
                return
            if self.active_order.get("orderId") and self.active_order.get("side") == "SELL":
                self.log("WARNING", "[EXEC] BLOCK duplicate_sell_prevented")
                self.fsm_state = "WAIT_SELL_FILL"
                self.place_sell_entered_ms = 0
                return
            self.sync_active_order(force=True)
            open_sell = self._find_open_sell_order()
            if open_sell:
                ask_now = float(self.state.snapshot.ask or 0.0)
                open_sell_price = float(open_sell.get("price", 0.0) or 0.0)
                is_far, dist_ticks = self._is_far_sell(open_sell_price, ask_now)
                if is_far:
                    order_id = int(open_sell.get("orderId", 0) or 0)
                    if self._is_stream_sell_order_id(order_id):
                        self.log("INFO", f"[EXEC] STREAM_GLOBAL_SELL_SKIP reason=sell_watchdog_far_cancel orderId={order_id}")
                        return
                    if self._is_protected_sell_order(open_sell_price):
                        self._log_protected_sell_ignore(order_id, dist_ticks)
                        self._adopt_open_sell_order(open_sell)
                        return
                    self.log("WARNING", f"[EXEC] SELL_WATCHDOG_FAR_CANCEL orderId={order_id} price={open_sell_price:.2f} ask={ask_now:.2f} dist_ticks={dist_ticks}")
                    self.account.cancel_order(CONFIG.binance_symbol, order_id)
                    final = self.account.get_order(CONFIG.binance_symbol, order_id)
                    final_status = str(final.get("status", "UNKNOWN"))
                    self.log("INFO", f"[EXEC] SELL_FAR_CANCEL_FINAL_STATUS status={final_status}")
                    self._reset_sell_price_cache_after_far_cancel(open_sell_price)
                    if final_status == "CANCELED":
                        self.active_order = {}
                        if self.position_qty > self._inventory_epsilon_qty():
                            self.position_state = "POSITION_OPEN"
                            self.fsm_state = "PLACE_SELL"
                    self.sync_active_order(force=True)
                else:
                    self._adopt_open_sell_order(open_sell)
                return
            inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
            sell_qty = self._safe_sell_qty(self._sync_sell_target_qty(), refresh_balance=True)
            bid_now = float(self.state.snapshot.bid or 0.0)
            self.log("INFO", f"[EXEC] SELL CHECK inventory={inventory_qty:.6f} reported={self.sell_reported_qty:.6f} remaining={sell_qty:.6f} chunks={len(self.inventory_chunks)}")
            if sell_qty <= epsilon_qty and len(self.inventory_chunks) == 0 and self.active_order.get("side") == "BUY" and self.active_order.get("orderId"):
                buy_state = str(self.active_order.get("state", ""))
                if buy_state in {"FILLED", "PARTIALLY_FILLED"}:
                    sync_buy = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
                    self._handle_buy_fill_update(sync_buy)
                    inventory_qty = max(sum(max(chunk.qty, 0.0) for chunk in self.inventory_chunks), 0.0)
                    sell_qty = self._safe_sell_qty(self._sync_sell_target_qty())
                    self.log("INFO", f"[EXEC] SELL CHECK resync inventory={inventory_qty:.6f} remaining={sell_qty:.6f} chunks={len(self.inventory_chunks)}")
            if inventory_qty > epsilon_qty and sell_qty <= epsilon_qty and not (self.active_order.get('orderId') and self.active_order.get('side') == "SELL"):
                self.sell_reported_qty = 0.0
                self.log("WARNING", "[EXEC] SELL ACCOUNTING STALE RESET")
                sell_qty = self._safe_sell_qty(self._sync_sell_target_qty())
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
            min_sellable_qty = self._min_sellable_qty()
            blocked_by_dust = sell_qty <= min_sellable_qty
            if inventory_qty > epsilon_qty and blocked_by_dust:
                if self._maybe_reconcile_micro_partial_dust(inventory_qty, reason="below_min_sellable"):
                    self._finalize_cycle_if_flat()
                    return
                if btc_locked > epsilon_qty and btc_free <= min_sellable_qty:
                    self.sync_active_order(force=True)
                    open_sell = self._find_open_sell_order()
                    if open_sell:
                        self._adopt_open_sell_order(open_sell)
                        self.fsm_state = "WAIT_SELL_ORDER_STATUS"
                        self.position_state = "WAIT_SELL_ORDER_STATUS"
                    else:
                        self.log("ERROR", f"[EXEC] BTC_LOCKED_WITHOUT_TRACKED_ORDER qty={btc_locked:.6f}")
                        self.position_state = "WAIT_EXCHANGE_UNLOCK"
                        self.fsm_state = "WAIT_MANUAL"
                        self.runtime_halt_manual_check = True
                    return
                if btc_free <= min_sellable_qty:
                    self.exit_blocked_no_sellable_count += 1
                    self.log("WARNING", f"[EXEC] EXIT_BLOCKED_NO_SELLABLE_QTY inventory={inventory_qty:.6f} free={btc_free:.6f} safe={sell_qty:.6f} reason=dust_or_locked_balance")
                    self.position_state = "EXIT_BLOCKED_NO_SELLABLE_QTY"
                    if self._maybe_reconcile_phantom_inventory(inventory_qty, sell_qty, btc_free, btc_locked):
                        self._finalize_cycle_if_flat()
                        return
                    self.fsm_state = "WAIT_MANUAL"
                    return
            if sell_qty <= epsilon_qty or sell_qty < min_sellable_qty:
                if len(self.inventory_chunks) > 0 and btc_free <= epsilon_qty and btc_locked > epsilon_qty:
                    self.position_state = "WAIT_EXCHANGE_UNLOCK"
                    self.fsm_state = "WAIT_SELL_ORDER_STATUS"
                    return
                if len(self.inventory_chunks) > 0 and btc_free <= epsilon_qty and btc_locked <= epsilon_qty:
                    self.log("INFO", "[EXEC] RECONCILE_FLAT reason=phantom_inventory_no_balance")
                    self._cleanup_inventory_if_drained()
                    self._reconcile_position_state("phantom_inventory_no_balance")
                    self._finalize_cycle_if_flat()
                    self.fsm_state = "DONE"
                    return
                self.log("INFO", f"[EXEC] SKIP MICRO SELL epsilon={epsilon_qty:.6f}")
                self._cleanup_inventory_if_drained()
                self._reconcile_position_state("sell_clamp_or_micro")
                self._finalize_cycle_if_flat()
                self.fsm_state = "DONE"
            elif sell_qty <= 0:
                if inventory_qty > 0:
                    self.log("WARNING", "[EXEC] SELL ACCOUNTING STALE RESET")
                    self.sell_reported_qty = 0.0
                    sell_qty = self._safe_sell_qty(self._sync_sell_target_qty())
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
                plan_exit_price = float(plan.exit_price) if plan and plan.exit_price is not None else 0.0
                target_exit = max(plan_exit_price, tp_floor_price)
                ask_now = float(self.state.snapshot.ask or 0.0)
                bid_now = float(self.state.snapshot.bid or 0.0)
                place_sell_source = "plan"
                if self.smart_exit_active:
                    self.log("WARNING", "[EXEC] SMART_EXIT_BLOCKED_PLAN_SELL")
                    if not self._trigger_taker_exit(int(time.time() * 1000), "smart_exit_blocked_plan_sell"):
                        self.log("WARNING", "[EXEC] SMART_EXIT_TO_PANIC reason=blocked_plan_sell")
                        self.trigger_panic_exit("smart_exit_blocked_plan_sell")
                    return
                if self.force_reprice_sell_next:
                    place_sell_source = "fresh_after_far_cancel"
                    if ask_now <= 0 or bid_now <= 0:
                        self.fetch_rest()
                        ask_now = float(self.state.snapshot.ask or 0.0)
                        bid_now = float(self.state.snapshot.bid or 0.0)
                    sell_price, allow_ticks = self._build_fresh_sell_price_after_far_cancel(ask_now, bid_now)
                    old_price = float(self.last_sell_far_cancel_price or self.last_sell_price or plan_exit_price)
                    self.log("INFO", f"[EXEC] SELL_REPRICE_FRESH_APPLIED old={old_price:.2f} new={sell_price:.2f} ask={ask_now:.2f} bid={bid_now:.2f}")
                    dist_ticks = int(abs(sell_price - ask_now) / max(tick, 1e-9)) if ask_now > 0 and sell_price > 0 else 0
                    self.log("INFO", f"[EXEC] SELL_REPRICE_VALIDATION old={old_price:.2f} new={sell_price:.2f} ask={ask_now:.2f} dist_ticks={dist_ticks} source=fresh_snapshot")
                    if sell_price <= 0 or dist_ticks > allow_ticks:
                        self.log("ERROR", f"[EXEC] SELL_REPRICE_FRESH_FAILED old={old_price:.2f} new={sell_price:.2f} ask={ask_now:.2f} reason=validation_failed")
                        self.force_reprice_sell_next = False
                        self._sell_far_cancel_unstick(int(time.time() * 1000), "fresh_reprice_validation_failed")
                        return
                    if old_price > 0 and abs(sell_price - old_price) < (tick * 0.5):
                        self.log("ERROR", f"[EXEC] SELL_REPRICE_FRESH_FAILED old={old_price:.2f} new={sell_price:.2f} reason=same_as_old")
                        self.force_reprice_sell_next = False
                        self._sell_far_cancel_unstick(int(time.time() * 1000), "fresh_reprice_same_as_old")
                        return
                    self.force_reprice_sell_next = False
                    still_far, dist_ticks = self._is_far_sell(sell_price, ask_now)
                    if still_far:
                        self.log("WARNING", f"[EXEC] SELL_SKIP_STILL_FAR_AFTER_REPRICE price={sell_price:.2f} ask={ask_now:.2f} dist_ticks={dist_ticks}")
                        self._sell_far_cancel_unstick(int(time.time() * 1000), "still_far_after_reprice")
                        return
                else:
                    sell_price = target_exit
                sell_price = self._apply_sell_floor(float(sell_price), place_sell_source)
                now = int(time.time() * 1000)
                place_signature = f"{sell_qty:.8f}@{sell_price:.2f}"
                if self.last_sell_place_signature == place_signature and now - self.last_sell_place_ms < 1000:
                    return
                self.last_sell_place_signature = place_signature
                self.last_sell_place_ms = now
                self.log("INFO", f"[EXEC] PLACE_SELL_SOURCE source={place_sell_source} price={sell_price:.2f}")
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
                self.active_order["active_sell_meta"] = self._update_sell_protection_meta(float(sell_price), ask_now, place_sell_source)
                self.position_sell_order_id = order_id
                self.last_sell_price = float(sell_price)
                self.position_state = "SELL_PENDING"
                self.sell_reported_qty = 0.0
                self.exit_mode = "NORMAL" if self.sell_reprice_count == 0 else "AGGRESSIVE"
                self.exit_stage = "EXIT_TP_MAKER"
                self.panic_ladder_step = 0
                self.max_hold_exit_triggered = False
                self.last_exit_reason = "tp_order_placed"
                self.log("OK", f"[EXEC] SELL ORDER SENT orderId={order_id}")
                self.exit_started_ms = now
                self.fsm_state = "WAIT_SELL_FILL"
                self.place_sell_entered_ms = 0
        elif self.runtime_active and self.fsm_state == "WAIT_SELL_FILL" and self.active_order.get("orderId"):
            now = int(time.time() * 1000)
            if self._smart_exit_poll_panic_order(now):
                return
            panic_stale_ms = 20000
            tick = self._tick_size()
            bid_now = float(self.state.snapshot.bid or 0.0)
            sl_ticks = max(int(getattr(self.settings, "stop_loss_ticks", 6)), 0)
            sl_price = float(self.position_entry_avg) - (tick * sl_ticks)
            st = self.account.get_order(CONFIG.binance_symbol, int(self.active_order["orderId"]))
            self.active_order["state"] = st.get("status", "NEW")
            order_id = int(self.active_order["orderId"])
            order_age_ms = now - int(self.active_order.get("create_ms", now) or now)
            if order_age_ms >= int(getattr(self.settings, "sell_watchdog_ms", 3000)) and self._watchdog_ready(order_id, now):
                wd_status = str(st.get("status", "UNKNOWN"))
                self.log("INFO", f"[EXEC] SELL_WATCHDOG_SYNC orderId={order_id} status={wd_status}")
                if wd_status == "FILLED":
                    self._handle_sell_filled(st, order_id)
                    return
                if wd_status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    self.active_order = {}
                    self.fsm_state = "PLACE_SELL" if self.position_qty > self._inventory_epsilon_qty() else "DONE"
                    return
                if wd_status in {"NEW", "PARTIALLY_FILLED"}:
                    ask_now = float(self.state.snapshot.ask or 0.0)
                    order_price = float(self.active_order.get("price", 0.0) or 0.0)
                    meta = self.active_order.get("active_sell_meta", {}) if isinstance(self.active_order, dict) else {}
                    if bool(meta.get("is_floor_protected")) and bool(meta.get("is_hold_recovery")) and not self.panic_exit_final:
                        hold_max_ms = int(getattr(self.settings, "sell_floor_hold_max_ms", 30000))
                        hold_started_ms = int(self.sell_hold_recovery_started_ms or self.active_order.get("create_ms", now))
                        hold_age_ms = max(now - hold_started_ms, 0)
                        if hold_age_ms >= hold_max_ms:
                            self.floor_hold_timeout_triggered = True
                            self.log("ERROR", f"[EXEC] SELL_HOLD_TIMEOUT_PANIC orderId={order_id} hold_age={hold_age_ms} max={hold_max_ms}")
                            self.log("WARNING", "[EXEC] FLOOR_HOLD_DISABLED_AFTER_TIMEOUT")
                            self._floor_hold_disabled_logged = True
                            self.account.cancel_order(CONFIG.binance_symbol, order_id)
                            final = self.account.get_order(CONFIG.binance_symbol, order_id)
                            final_status = str(final.get("status", "UNKNOWN"))
                            self.log("INFO", f"[EXEC] SELL_HOLD_TIMEOUT_FINAL_STATUS status={final_status}")
                            if final_status == "FILLED":
                                self._handle_sell_filled(final, order_id)
                                return
                            self.active_order = {}
                            self.trigger_panic_exit("sell_hold_timeout")
                            return
                    is_far, dist_ticks = self._is_far_sell(order_price, ask_now)
                    if is_far:
                        if self._is_stream_sell_order_id(order_id):
                            self.log("INFO", f"[EXEC] STREAM_GLOBAL_SELL_SKIP reason=sell_watchdog_far_cancel orderId={order_id}")
                            return
                        if self._is_protected_sell_order(order_price):
                            should_break, break_reason, hold_age_ms = self._should_break_protected_hold(now, dist_ticks)
                            if should_break:
                                self.floor_hold_timeout_triggered = True
                                self.smart_exit_active = True
                                self.log("WARNING", "[EXEC] SMART_EXIT_ACTIVE")
                                meta = self.active_order.get("active_sell_meta", {}) if isinstance(self.active_order, dict) else {}
                                if isinstance(meta, dict):
                                    meta["is_hold_recovery"] = False
                                    meta["is_floor_protected"] = False
                                self.log("WARNING", f"[EXEC] SMART_EXIT_BREAK_HOLD reason={break_reason} age={hold_age_ms} dist_ticks={dist_ticks}")
                                self.account.cancel_order(CONFIG.binance_symbol, order_id)
                                final = self.account.get_order(CONFIG.binance_symbol, order_id)
                                self._handle_sell_fill_update(final)
                                if str(final.get("status", "")) == "FILLED" or self.position_qty <= self._inventory_epsilon_qty():
                                    self._handle_sell_filled(final, order_id)
                                    return
                                if not self._trigger_taker_exit(now, f"smart_break_hold_{break_reason}"):
                                    self.log("WARNING", "[EXEC] SMART_EXIT_TO_PANIC reason=taker_trigger_failed_after_break_hold")
                                    self.trigger_panic_exit(f"smart_break_hold_{break_reason}_failed")
                                return
                            self._log_protected_sell_ignore(order_id, dist_ticks)
                            return
                        self.log("WARNING", f"[EXEC] SELL_WATCHDOG_FAR_CANCEL orderId={order_id} price={order_price:.2f} ask={ask_now:.2f} dist_ticks={dist_ticks}")
                        self.account.cancel_order(CONFIG.binance_symbol, order_id)
                        final = self.account.get_order(CONFIG.binance_symbol, order_id)
                        final_status = str(final.get("status", "UNKNOWN"))
                        self.log("INFO", f"[EXEC] SELL_FAR_CANCEL_FINAL_STATUS status={final_status}")
                        self._reset_sell_price_cache_after_far_cancel(order_price)
                        if final_status == "CANCELED":
                            self.active_order = {}
                            self.position_state = "POSITION_OPEN" if self.position_qty > self._inventory_epsilon_qty() else "FLAT"
                        self.fsm_state = "PLACE_SELL" if self.position_qty > self._inventory_epsilon_qty() else "DONE"
                        return
            prev_sell_reported_qty = self.sell_reported_qty
            self._handle_sell_fill_update(st)
            self.exit_blocked_no_sellable_count = 0
            sell_delta = max(self.sell_reported_qty - prev_sell_reported_qty, 0.0)
            if sell_delta > 0:
                self.log("OK", f"[EXEC] SELL PARTIAL delta={sell_delta:.6f}")
                self.log("OK", f"[EXEC] INVENTORY remaining={self.position_qty:.6f}")
                if self.panic_exit_final and st.get("status") == "PARTIALLY_FILLED":
                    self.log("WARNING", f"[EXEC] PANIC PARTIAL filled={sell_delta:.6f} remaining={self.position_qty:.6f}")
            remaining_to_sell = self._sync_sell_target_qty()
            spread_now = float((self.state.snapshot.ask or 0.0) - (self.state.snapshot.bid or 0.0))
            spread_ticks_now = int(spread_now / max(tick, 1e-9)) if spread_now > 0 else 0
            collapse_ticks = max(int(self.settings.min_spread_after_entry_ticks) - spread_ticks_now, 0)
            mid_delta = 0.0
            if len(self.recent_mids) >= 2:
                mid_delta = self.recent_mids[-1][1] - self.recent_mids[0][1]
            active_sell_qty = float(self.active_order.get("qty", 0.0) or 0.0)
            if st.get("status") in {"NEW", "PARTIALLY_FILLED"} and remaining_to_sell > (active_sell_qty + 1e-9):
                upsize_blocked = (
                    self.exit_stage != "EXIT_TP_MAKER"
                    or self.panic_exit_final
                    or self.max_hold_exit_triggered
                    or self.taker_exit_triggered
                    or (now - self.exit_started_ms >= int(self.settings.sell_timeout_ms))
                    or self.sell_recovery_in_progress
                    or self.sell_cancel_in_progress
                )
                if upsize_blocked:
                    if not self.sell_upsize_skip_logged:
                        self.log("INFO", "[EXEC] SELL_UPSIZE_SKIPPED reason=exit_or_panic_active")
                        self.sell_upsize_skip_logged = True
                else:
                    self.log("INFO", f"[EXEC] SELL UPSIZE old={active_sell_qty:.6f} new={remaining_to_sell:.6f}")
                    self.handle_sell_timeout_recovery(now)
                    return
            if st.get("status") == "FILLED":
                self.log("OK", "[EXEC] EXIT_FILLED")
                self._handle_sell_filled(st, int(self.active_order["orderId"]))
            elif self.position_qty > 0 and now - self.exit_started_ms >= int(getattr(self.settings, "taker_exit_force_flat_after_ms", 1800)):
                if self._should_block_protected_sell_escalation(now, "force_flat_after_ms") and not self.smart_exit_active:
                    return
                if not self._trigger_taker_exit(now, "force_flat_after_ms"):
                    if self.smart_exit_active:
                        self.log("WARNING", "[EXEC] SMART_EXIT_TO_PANIC reason=taker_force_flat_failed")
                    self.trigger_panic_exit("taker_force_flat_failed")
            elif self.position_qty > 0 and collapse_ticks >= int(getattr(self.settings, "taker_exit_spread_collapse_ticks", 3)):
                self._trigger_taker_exit(now, "spread_collapse")
            elif self.position_qty > 0 and mid_delta <= float(getattr(self.settings, "taker_exit_mid_negative_threshold", -40.0)):
                self._trigger_taker_exit(now, "mid_negative")
            elif self.position_qty > 0 and now - self.exit_started_ms >= int(getattr(self.settings, "taker_exit_after_ms", 900)):
                if self._should_block_protected_sell_escalation(now, "timeout_after_ms") and not self.smart_exit_active:
                    return
                self._trigger_taker_exit(now, "timeout_after_ms")
            elif self.position_qty > 0 and now - self.exit_started_ms >= int(self.settings.max_hold_ms):
                self.last_exit_reason = "max_hold_exceeded"
                self.exit_stage = "EXIT_CROSS"
                if not self.max_hold_exit_triggered:
                    self.max_hold_exit_triggered = True
                    self.trigger_panic_exit("max_hold_exceeded")
            elif now - self.exit_started_ms >= int(self.settings.sell_timeout_ms):
                if self.position_qty > 0 and not self.panic_exit_final and self.taker_exit_state not in {"ORDER_SENT"}:
                    if self._trigger_taker_exit(now, "sell_timeout_pre_panic"):
                        return
                if self.panic_exit_final:
                    panic_order_id = int(self.active_order.get("orderId", 0) or 0)
                    panic_interval_ms = int(getattr(self.settings, "panic_ladder_ms", 400))
                    panic_hold_max_ms = int(getattr(self.settings, "panic_hold_max_ms", 2500))
                    panic_age_ms = max(now - int(self.panic_exit_started_ms or now), 0)
                    if panic_order_id != self.last_panic_wait_order_id:
                        self.last_panic_wait_order_id = panic_order_id
                        self.last_panic_wait_log_ms = 0
                    if now - self.last_panic_wait_log_ms >= 2000:
                        self.log("WARNING", f"[EXEC] PANIC WAIT still_open orderId={panic_order_id}")
                        self.last_panic_wait_log_ms = now
                    if panic_age_ms >= panic_hold_max_ms:
                        self.log("ERROR", f"[EXEC] PANIC_HOLD_MAX_REACHED orderId={panic_order_id} age_ms={panic_age_ms}")
                    if now - self.last_sell_reprice_ms < panic_interval_ms and panic_age_ms < panic_hold_max_ms:
                        return
                    if self.sell_recovery_in_progress or self.sell_cancel_in_progress:
                        self.log("INFO", "[EXEC] RECOVERY WAIT panic_ladder_in_progress")
                        return
                    self.sell_recovery_in_progress = True
                    self.sell_cancel_in_progress = True
                    try:
                        try:
                            self.account.cancel_order(CONFIG.binance_symbol, panic_order_id)
                        except Exception as exc:
                            self.log("ERROR", f"[EXEC] EXIT_FAIL reason=panic_replace_failed cancel={exc}")
                            return
                        final = self.account.get_order(CONFIG.binance_symbol, panic_order_id)
                        final_status = str(final.get("status", "UNKNOWN"))
                        self._handle_sell_fill_update(final)
                        if final_status == "FILLED" or self.position_qty <= 0:
                            self._handle_sell_filled(final, panic_order_id)
                            return
                        if final_status not in {"CANCELED", "EXPIRED", "NEW", "PARTIALLY_FILLED"}:
                            self.log("ERROR", "[EXEC] EXIT_FAIL reason=panic_replace_failed")
                            return
                        step_ticks = max(int(getattr(self.settings, "panic_ladder_step_ticks", 1)), 1)
                        self.panic_ladder_step += 1
                        ladder_ticks = step_ticks * self.panic_ladder_step
                        ask_now = float(self.state.snapshot.ask or 0.0)
                        panic_step = max(float(getattr(self.settings, "aggressive_exit_offset", 1.0)), tick)
                        dynamic_step = panic_step + (tick * ladder_ticks)
                        new_price = self._round_price_down(max(ask_now - dynamic_step, tick))
                        sell_qty = self._safe_sell_qty(self.position_qty, refresh_balance=True)
                        if sell_qty <= self._inventory_epsilon_qty():
                            self._cleanup_inventory_if_drained()
                            self._reconcile_position_state("panic_ladder_inferred_by_balance")
                            self.log("OK", "[EXEC] EXIT_FILLED inferred_by_balance")
                            self.fsm_state = "WAIT_READY" if self.runtime_active else "DONE"
                            self.place_sell_entered_ms = 0
                            return
                        self.log("INFO", f"[EXEC] PLACE_SELL_SOURCE source=panic price={new_price:.2f}")
                        o = self.account.place_limit_order(CONFIG.binance_symbol, "SELL", float(new_price), float(sell_qty))
                        self.active_order = {"orderId": o.get("orderId"), "side": "SELL", "price": float(new_price), "qty": float(sell_qty), "create_ms": now, "state": "NEW", "type": "LIMIT"}
                        self.position_sell_order_id = int(self.active_order["orderId"])
                        self.panic_exit_order_id = int(self.active_order["orderId"])
                        self.panic_exit_price = float(new_price)
                        self.panic_exit_started_ms = now
                        self.panic_hold_count += 1
                        self.exit_started_ms = now
                        self.last_sell_reprice_ms = now
                        self.log("WARNING", f"[EXEC] PANIC ESCALATE placed orderId={self.panic_exit_order_id} step={self.panic_ladder_step}")
                    except Exception as exc:
                        self.log("ERROR", f"[EXEC] EXIT_FAIL reason=panic_replace_failed place={exc}")
                        self.exit_block_reason = f"panic_replace_failed:{exc}"
                        self.position_state = "EXIT_BLOCKED"
                        self.fsm_state = "WAIT_MANUAL"
                    finally:
                        self.sell_cancel_in_progress = False
                        self.sell_recovery_in_progress = False
                elif self.position_qty > 0:
                    age = now - int(self.active_order.get("create_ms", now))
                    step_ticks = max(int(getattr(self.settings, "exit_reprice_step_ticks", 1)), 1)
                    stage1 = int(getattr(self.settings, "exit_stage1_ms", 700))
                    stage2 = int(getattr(self.settings, "exit_stage2_ms", 1200))
                    stage3 = int(getattr(self.settings, "exit_stage3_ms", 1800))
                    cross_after = int(getattr(self.settings, "panic_cross_after_ms", 3500))
                    if bool(getattr(self.settings, "exit_engine_enabled", True)):
                        if age >= cross_after:
                            self.exit_stage = "EXIT_CROSS"
                        elif age >= stage3:
                            self.exit_stage = "EXIT_NEAR_BID"
                        elif age >= stage2:
                            self.exit_stage = "EXIT_TIGHTEN"
                        elif age >= stage1:
                            self.exit_stage = "EXIT_TP_MAKER"
                        self.log("INFO", f"[EXEC] EXIT_STAGE stage={self.exit_stage} age_ms={age}")
                        if self.sell_reprice_count < int(getattr(self.settings, "exit_max_reprices", 8)):
                            self.last_exit_reason = f"stage={self.exit_stage}"
                            self.log("INFO", f"[EXEC] EXIT_REPRICE reason={self.last_exit_reason}")
                            self.handle_sell_timeout_recovery(now)
                            return
                        if bool(getattr(self.settings, "panic_ladder_enabled", True)) and (now - self.last_sell_reprice_ms >= int(getattr(self.settings, "panic_ladder_ms", 400))):
                            self.panic_ladder_step += 1
                            self.last_exit_reason = "panic_ladder_step"
                            self.log("WARNING", f"[EXEC] EXIT_PANIC_STEP step={self.panic_ladder_step}")
                            self.handle_sell_timeout_recovery(now)
                            return
                        self.log("ERROR", "[EXEC] EXIT_FAIL reason=no_exit_action_available")
                    self.handle_sell_timeout_recovery(now)
                else:
                    self.log("ERROR", "[EXEC] EXIT FAILED no_position_after_timeout")
                    self.position_state = "EXIT_FAILED"
                    self.fsm_state = "SELL_TIMEOUT"
        elif self.runtime_active and self.fsm_state == "WAIT_TAKER_EXIT_STATUS":
            now_ms = int(time.time() * 1000)
            self._sync_taker_exit_status(now_ms)
        elif self.runtime_active and self.position_qty > 0 and not self.active_order.get("orderId") and self.position_state in {"SELL_PENDING", "EXIT_FAILED"}:
            self.log("WARNING", "[EXEC] WATCHDOG position open without sell -> recover")
            self.fsm_state = "PLACE_SELL"

        self.risk["Order size U"].setText(self._fmt(self.settings.order_size_u, 2))
        self.risk["Max exposure U"].setText(self._fmt(self.settings.max_exposure_u, 2))
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

        self._update_session_summary(now_ms)

        rest_txt = "OK" if self.state.rest_status == "OK" else "ERROR"
        ws_txt = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        self.top_status.setText(f"BTC/U | WS ● {ws_txt} | REST ● {rest_txt} | API ● {self.api_status} | {'HOT' if spread_state=='HOT' else 'READY'}")

    def _start_entry_guard_cooldown(self, reason: str) -> None:
        now_ms = int(time.time() * 1000)
        cooldown_ms = self.settings.panic_cooldown_ms if reason == "panic_exit" else self.settings.loss_cooldown_ms
        self.entry_guard_cooldown_until_ms = max(self.entry_guard_cooldown_until_ms, now_ms + cooldown_ms)
        self.entry_guard_cooldown_reason = reason

    def _update_session_summary(self, now_ms: int | None = None) -> None:
        if now_ms is None:
            now_ms = int(time.time() * 1000)
        use_stream_stats = bool((int(getattr(self.settings, "stream_count", 1)) >= 1))
        closed_cycles = self.stream_closed_cycles if use_stream_stats else self.closed_cycles
        wins = self.stream_wins if use_stream_stats else self.wins
        losses = self.stream_losses if use_stream_stats else self.losses
        realized_pnl = self.stream_realized_pnl if use_stream_stats else self.session_realized_pnl
        last_pnl = self.stream_last_pnl if use_stream_stats else self.last_pnl
        winrate = (wins / closed_cycles * 100.0) if closed_cycles else 0.0
        if use_stream_stats:
            self.stream_winrate = winrate
            if now_ms - self.last_stream_stats_ui_log_ms >= 5000:
                self.last_stream_stats_ui_log_ms = now_ms
                self.log("INFO", f"[EXEC] STREAM_STATS_UI cycles={closed_cycles} wins={wins} losses={losses} pnl={realized_pnl:+.6f} winrate={winrate:.2f}%")
        active_order_text = "none" if not self.active_order.get("orderId") else f"{self.active_order.get('side','-')}#{self.active_order.get('orderId')}"
        summary_sig = f"{self.position_state}:{self.position_entry_avg:.6f}:{closed_cycles}:{wins}:{losses}:{realized_pnl:.6f}:{last_pnl:.6f}:{winrate:.2f}:{self.canceled_buys}:{self.sell_timeouts}:{self.sell_reprice_count}:{self.exit_mode}:{self.phantom_reconcile_count}:{active_order_text}:{self.position_qty:.6f}"
        if summary_sig == self.summary_signature:
            return
        self.summary_signature = summary_sig
        self.summary["Started"].setText(self.session_started_at)
        self.summary["Position state"].setText(self.position_state)
        self.summary["Position qty"].setText(self._fmt(self.position_qty, 6))
        self.summary["Entry avg"].setText(self._fmt(self.position_entry_avg, 6))
        self.summary["Realized PnL"].setText(f"{realized_pnl:+.6f}")
        self.summary["Winrate"].setText(f"{winrate:.2f}%")
        self.summary["Closed cycles"].setText(str(closed_cycles))
        self.summary["Wins"].setText(str(wins))
        self.summary["Losses"].setText(str(losses))
        self.summary["Canceled buys"].setText(str(self.canceled_buys))
        self.summary["Last PnL"].setText(f"{last_pnl:+.6f}")
        self.summary["Sell timeouts"].setText(str(self.sell_timeouts))
        self.summary["Exit mode"].setText(self.exit_mode)
        self.summary["Phantom reconcile count"].setText(str(self.phantom_reconcile_count))

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
        stream_mode = bool((int(getattr(self.settings, "stream_count", 1)) >= 1))
        stream_guard_bypass = bool(
            stream_mode
            and getattr(self.settings, "ws_optional_enabled", False)
            and source == "REST"
            and self.state.rest_status == "OK"
            and self.api_status == "OK"
        )
        if stream_mode:
            spread_ticks = spread / max(self._tick_size(), 1e-12)
            max_inventory_u = float(getattr(self.settings, "stream_max_inventory_u", 1000.0))
            max_active_buys = max(int(getattr(self.settings, "stream_max_active_buys", 8)), 1)
            bid_now = float(self.state.snapshot.bid or 0.0)
            inventory_u = float(self.position_qty or 0.0) * bid_now
            active_buys = 1 if (self.active_order.get("orderId") and self.active_order.get("side") == "BUY") else 0

            if self.api_status != "OK" or self.state.rest_status != "OK":
                reason = "api_or_rest_unavailable"
            elif spread_ticks < float(self.settings.min_spread_ticks):
                reason = "min_spread_ticks"
            elif inventory_u > max_inventory_u:
                reason = "max_inventory_u"
            elif active_buys >= max_active_buys:
                reason = "max_active_buys"
            elif now_ms < self.entry_guard_cooldown_until_ms:
                reason = f"cooldown_{self.entry_guard_cooldown_reason or 'active'}"
            elif free_u < need_u:
                reason = "balance_low_preflight"
        else:
            if self.settings.require_ws_for_buy and (ws_age is None or ws_age > self.settings.max_ws_age_for_buy_ms):
                if stream_guard_bypass:
                    self.log("INFO", "[EXEC] STREAM_GUARD_BYPASS reason=ws_stale_rest_ok")
                else:
                    reason = "ws_stale"
            elif self.settings.live_enabled and self.settings.ws_optional_enabled and source != "WS":
                if stream_guard_bypass:
                    self.log("INFO", "[EXEC] STREAM_GUARD_BYPASS reason=ws_stale_rest_ok")
                else:
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
                if stream_guard_bypass and "stale_market_source" in str(self.market_health_reason):
                    self.log("INFO", "[EXEC] STREAM_GUARD_BYPASS reason=ws_stale_rest_ok")
                else:
                    reason = "market_health_bad"
            elif free_u < need_u:
                reason = "balance_low_preflight"

        if reason:
            if stream_mode:
                self.log("INFO", f"[EXEC] STREAM_GUARD_SIMPLE_BLOCK reason={reason}")
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
        if stream_mode:
            self.log("INFO", "[EXEC] STREAM_GUARD_SIMPLE_PASS")
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
        if not self.logs_visible:
            return
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
        if self.gui_log_mode == "OFF":
            return False
        if self.gui_log_mode == "FULL":
            return True
        important_exec = ("STREAM_PNL", "STREAM_SELL_FILLED", "STREAM_STOP_LOSS_EXIT", "STREAM_ORDER_API_ERROR")
        important_system = ("LIVE ON", "STOP", "CANCEL ALL", "SESSION RESULT")
        if "[EXEC]" in message and any(k in message for k in important_exec):
            return True
        if tag in {"ERROR", "WARNING"} and ("Binance" in message or "API" in message or "[EXEC]" in message):
            return True
        if any(k in message for k in important_system):
            return True
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
        now_ms = int(time.time() * 1000)
        if now_ms - getattr(self, "_last_gui_perf_log_ms", 0) >= 1000:
            self._last_gui_perf_log_ms = now_ms
            self.file_logs.write_session(format_log("INFO", f"GUI_PERF tick_ms={getattr(self, 'tick_ms', 0)} logs_visible={self.logs_visible} gui_log_mode={self.gui_log_mode} append_skipped={self.gui_append_skipped}"))

        if not self.logs_visible:
            self.gui_append_skipped += 1
            return

        if not self._should_show_in_gui(tag, message):
            return

        color = {"INFO": "#CBD5E1", "OK": "#22C55E", "WARNING": "#FACC15", "ERROR": "#EF4444"}.get(tag, "#CBD5E1")
        trade_keys = ("[EXEC]", "REALIZED", "PLACE BUY", "BUY FILLED", "PLACE SELL", "SELL FILLED", "CANCEL", "TIMEOUT")
        bucket = "trade" if any(k in message for k in trade_keys) else "system"
        self.pending_gui_logs[bucket].append((color, line))
        if self.gui_log_mode != "OFF":
            self._flush_gui_logs()
        if bucket == "trade":
            self.file_logs.write_trade(line.replace(f"[{tag}]", "[EXEC]"))
        else:
            self.file_logs.write_system(line)

    def load_session_log_summary(self) -> None:
        path = self.file_logs.session_path
        if not path.exists():
            self.log("WARNING", f"SESSION LOG NOT FOUND path={path}")
            return
        text = path.read_text(encoding="utf-8")
        pnl_values = [float(v) for v in re.findall(r"STREAM_PNL .*?pnl=([+-]?\d+(?:\.\d+)?)", text)]
        wins = sum(1 for v in pnl_values if v > 0)
        losses = sum(1 for v in pnl_values if v < 0)
        cycles = len(pnl_values)
        realized = sum(pnl_values)
        avg = (realized / cycles) if cycles else 0.0
        max_loss = min(pnl_values) if pnl_values else 0.0
        max_win = max(pnl_values) if pnl_values else 0.0
        stop_loss_count = len(re.findall(r"STREAM_STOP_LOSS_EXIT", text))
        api_errors = len(re.findall(r"STREAM_ORDER_API_ERROR|BinanceAPIError|RequestException|\\[ERROR\\].*API", text))
        winrate = (wins / cycles * 100.0) if cycles else 0.0
        self.log("OK", f"SESSION RESULT cycles={cycles} wins={wins} losses={losses} winrate={winrate:.2f}% realized={realized:+.6f} avg={avg:+.6f} max_loss={max_loss:+.6f} max_win={max_win:+.6f} stop_loss_count={stop_loss_count} api_errors={api_errors}")


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

    def _smart_exit_retry_aggressive(self, now_ms: int, reason: str) -> None:
        max_attempts = max(int(getattr(self.settings, "max_smart_exit_attempts", 8)), 1)
        self.smart_exit_active = True
        self.smart_exit_attempts += 1
        self.smart_exit_last_action_ts = now_ms
        if self.smart_exit_attempts > max_attempts:
            self.log("ERROR", f"[EXEC] SMART_EXIT_MAX_ATTEMPTS attempts={self.smart_exit_attempts} qty={self.position_qty:.6f}")
            self.fsm_state = "WAIT_MANUAL"
            return
        self.log("WARNING", f"[EXEC] SMART_EXIT_PANIC_RETRY reason={reason} attempt={self.smart_exit_attempts}")
        self.trigger_panic_exit(f"smart_exit_{reason}")

    def _smart_exit_poll_panic_order(self, now_ms: int) -> bool:
        if not self.smart_exit_active:
            return False
        order_id = int(self.active_order.get("orderId", 0) or self.panic_exit_order_id or 0)
        if order_id <= 0:
            return False
        try:
            st = self.account.get_order(CONFIG.binance_symbol, order_id)
        except Exception:
            return False
        status = str(st.get("status", "UNKNOWN"))
        filled = float(st.get("executedQty", 0.0) or 0.0)
        qty = float(st.get("origQty", 0.0) or self.active_order.get("qty", 0.0) or 0.0)
        age = max(now_ms - int(self.active_order.get("create_ms", now_ms) or now_ms), 0)
        self.log("INFO", f"[EXEC] SMART_EXIT_PANIC_STATUS id={order_id} status={status} filled={filled:.6f} remaining={max(qty-filled, 0.0):.6f} age={age}")
        hold_max = int(getattr(self.settings, "panic_hold_max_ms", 1200))
        if status in {"NEW", "OPEN", "PARTIALLY_FILLED"} and age > hold_max:
            self.log("WARNING", f"[EXEC] SMART_EXIT_PANIC_TIMEOUT_CANCEL id={order_id} age={age} max={hold_max}")
            self.account.cancel_order(CONFIG.binance_symbol, order_id)
            self.active_order = {}
            self.panic_exit_order_id = 0
            self._smart_exit_retry_aggressive(now_ms, "panic_timeout")
            return True
        if status in {"CANCELED", "EXPIRED", "REJECTED"}:
            self.active_order = {}
            self.panic_exit_order_id = 0
            self._smart_exit_retry_aggressive(now_ms, f"panic_{status.lower()}")
            return True
        return False

    def _log_taker_guard_skip(self, reason: str) -> None:
        if reason == "smart_exit_wait_taker_or_panic":
            return
        now = int(time.time() * 1000)
        last_ms = int(self.last_taker_guard_skip_log_ms_by_reason.get(reason, 0) or 0)
        if now - last_ms >= 3000:
            self.log("INFO", f"[EXEC] TAKER_STATUS_GUARD_SKIP reason={reason}")
            self.last_taker_guard_skip_log_ms_by_reason[reason] = now
