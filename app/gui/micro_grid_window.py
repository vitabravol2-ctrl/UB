from __future__ import annotations

import threading
from dataclasses import asdict

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QHeaderView

from app.core.config import CONFIG
from app.core.crash_logger import GridCrashLogger
from app.core.grid_config import GRID_SETTINGS_STORE
from app.core.grid_engine import GridEngine
from app.core.grid_runtime import GridRuntime
from app.core.session_logger import SessionLogger
from app.core.grid_trade_adapter import GridTradeAdapter
from app.core.market_rest import MarketREST
from app.core.market_state import MarketState
from app.core.market_ws import MarketWSClient
from app.gui.grid_settings_dialog import GridSettingsDialog
from app.gui.styles import main_qss
from app.gui.widgets import build_status_badge, build_terminal_card, update_badge


class MicroGridWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UB Micro Grid / BTCU")
        self.resize(1500, 900)
        self.setStyleSheet(main_qss())
        self.grid_engine = GridEngine(CONFIG.tick_size_default)
        self.trade_adapter = GridTradeAdapter(live_enabled=False)
        self.market_state = MarketState()
        self.market_ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol)
        self.market_rest = MarketREST()
        self.settings = GRID_SETTINGS_STORE.load()
        self.rows = []
        self.grid_runtime = GridRuntime(log_callback=lambda m: self.append_log(f"[GRID][ERROR] {m}"))
        self.session_logger = SessionLogger(symbol="BTCU")
        self.crash_logger = GridCrashLogger()
        self.crash_logger.install()

        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        top = QHBoxLayout(); layout.addLayout(top)
        top.addWidget(QLabel("UB Micro Grid / BTCU")); self.mode_badge = build_status_badge("DRY", "info"); top.addWidget(self.mode_badge)
        self.live_badge = build_status_badge("LIVE LOCKED", "warn"); top.addWidget(self.live_badge); top.addStretch(1)
        self.settings_btn = QPushButton("Settings"); self.calculate_btn = QPushButton("Calculate"); self.start_stop_btn = QPushButton("START"); self.refresh_btn = QPushButton("Refresh")
        for b in [self.settings_btn, self.calculate_btn, self.start_stop_btn, self.refresh_btn]: top.addWidget(b)

        cards = QHBoxLayout(); layout.addLayout(cards)
        c1,self.conn = build_terminal_card("Connection",[("WS","LOST"),("REST","N/A"),("API","NOT SET"),("Source","NONE")])
        c2,self.market = build_terminal_card("Market",[("Bid","N/A"),("Ask","N/A"),("Spread U","N/A"),("Spread ticks","N/A"),("Mid","N/A"),("Source","NONE"),("Last update age","N/A")])
        c3,self.grid_status = build_terminal_card("Grid Status",[("State","IDLE"),("Levels","0"),("Valid","0"),("Invalid","0"),("Near Market","0"),("Active Orders","0 locked")])
        c4,self.bal = build_terminal_card("Balances",[("BTC free","0"),("BTC locked","0"),("U free","0"),("U locked","0"),("Exposure U","0")])
        [cards.addWidget(c) for c in [c1,c2,c3,c4]]

        center = QHBoxLayout(); layout.addLayout(center,1)
        self.levels_table = QTableWidget(0,10); center.addWidget(self.levels_table,4)
        self.levels_table.setHorizontalHeaderLabels(["Level","Price","Side","Order U","Qty BTC","Target Sell","Expected PnL","Band","Status","Reason"])
        self.levels_table.setAlternatingRowColors(True); self.levels_table.setSelectionMode(QAbstractItemView.NoSelection); self.levels_table.verticalHeader().setVisible(False)
        self.levels_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); self.levels_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.ResizeToContents)

        right = QVBoxLayout(); center.addLayout(right,2)
        right.addWidget(QLabel("Grid Map")); self.grid_map = QTextEdit(); self.grid_map.setReadOnly(True); right.addWidget(self.grid_map)
        sb,self.summary = build_terminal_card("Summary",[("Range","N/A"),("Step U","N/A"),("Levels","0"),("Budget / Level","0"),("Total Budget","0"),("Valid Levels","0"),("Invalid Levels","0"),("Total Expected PnL","0"),("Tick Size","N/A"),("Step Size","N/A"),("Min Qty","N/A"),("Min Notional","N/A"),("LIVE","LOCKED")]); right.addWidget(sb)
        right.addWidget(QLabel("Logs")); self.log_box = QTextEdit(); self.log_box.setReadOnly(True); right.addWidget(self.log_box)

        self.settings_btn.clicked.connect(self.open_settings); self.calculate_btn.clicked.connect(self.on_calculate); self.start_stop_btn.clicked.connect(self.on_start_stop); self.refresh_btn.clicked.connect(self._refresh_info)
        self.market_ws.signals.book.connect(self._on_ws_book); self.market_ws.signals.status.connect(self._on_ws_status); self.market_ws.start()
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(lambda: self._safe_call("timer callbacks", self._rest_poll)); self.rest_timer.start(3000)
        self.balance_timer = QTimer(self); self.balance_timer.timeout.connect(lambda: self._safe_call("balance refresh", self._balances_refresh)); self.balance_timer.start(7000)
        self._log("[GRID] terminal opened"); self._log(f"[GRID] session log path={self.session_logger.path}"); self._refresh_info()

    def closeEvent(self, event):
        self._log("[GRID] terminal closed")
        return super().closeEvent(event)

    def _safe_call(self, action: str, fn, *args):
        self.crash_logger.last_gui_action = action
        self.crash_logger.last_runtime_state = self.grid_runtime.state
        self.crash_logger.last_market_snapshot = vars(self.market_state.snapshot)
        self.crash_logger.last_settings = asdict(self.settings)
        self.crash_logger.last_grid_summary = {k: v.text() for k, v in self.summary.items()}
        try:
            return fn(*args)
        except Exception as exc:
            self.append_log(f"[GRID][ERROR] {action}: {exc}")
            return None

    def append_log(self, msg: str) -> None:
        self.session_logger.log("GRID", msg.replace("[GRID] ", ""))
        self.log_box.setPlainText("\n".join(self.session_logger.get_gui_lines()))

    def _log(self, msg: str) -> None:
        self.append_log(msg)

    def open_settings(self)->None:
        def _inner():
            dlg = GridSettingsDialog(self); dlg.apply_settings(self.settings)
            if dlg.exec():
                self.settings = dlg.collect_settings()
                if self.settings.live_enabled and self.settings.budget_u > self.settings.max_live_budget_u:
                    self.settings.live_enabled = False
                    self._log("[GRID] RISK_LIVE_BUDGET_TOO_HIGH")
                GRID_SETTINGS_STORE.save(self.settings)
                self._log("[GRID] settings applied")
                self.on_calculate()
        self._safe_call("settings apply", _inner)

    def on_start_stop(self)->None:
        if self.grid_runtime.state in {"LIVE_RUNNING", "DRY_VIEW"}:
            return self._safe_call("stop_live", self._stop_runtime)
        return self._safe_call("start_live", self._start_runtime)

    def _start_runtime(self):
        self.on_calculate()
        if not self.rows:
            self._log("[GRID] start blocked: empty grid levels")
            self.grid_status["State"].setText("IDLE")
            return
        if self.market_state.snapshot.bid <= 0 or self.market_state.snapshot.ask <= 0:
            self._log("[GRID] start blocked: no market data")
            self.grid_status["State"].setText("IDLE")
            return

        self.grid_runtime.live_enabled = self.settings.live_enabled
        if self.settings.live_enabled:
            if QMessageBox.question(self, "Confirm LIVE", "LIVE mode will place real orders.") != QMessageBox.StandardButton.Yes:
                self._log("[GRID] live start canceled by user")
                return
            self.grid_runtime.arm_live(True)
            self.grid_runtime.start_live()
        else:
            self.grid_runtime.start_dry()
        self.grid_status["State"].setText(self.grid_runtime.state)
        self.start_stop_btn.setText("STOP")
        self.start_stop_btn.setStyleSheet("background:#2f9e44;")

    def _stop_runtime(self):
        self.grid_runtime.stop()
        self.grid_status["State"].setText(self.grid_runtime.state)
        self.start_stop_btn.setText("START")
        self.start_stop_btn.setStyleSheet("")

    def _refresh_info(self)->None:
        self._safe_call("market callbacks", self._load_filters); self._safe_call("balance refresh", self._balances_refresh); self._load_api_status(); self._load_open_orders()

    def _load_api_status(self)->None:
        def _worker() -> None:
            try:
                self.conn["API"].setText(self.trade_adapter.load_api())
            except Exception as exc:
                self.conn["API"].setText("ERROR")
                self._log(f"[GRID] api status failed: {exc}")

        threading.Thread(target=_worker, daemon=True).start()
    def _load_open_orders(self)->None:
        def _worker() -> None:
            try:
                self._log(f"[GRID] open orders read n={len(self.trade_adapter.get_open_orders())}")
            except Exception as exc:
                self._log(f"[GRID] open orders skipped: {exc}")

        threading.Thread(target=_worker, daemon=True).start()
    def _load_filters(self)->None:
        f = self.trade_adapter.load_filters()
        self.grid_engine.set_filters(float(f["tickSize"]), float(f["stepSize"]), float(f["minQty"]), float(f["minNotional"]))

    def _balances_refresh(self)->None:
        b = self.trade_adapter.refresh_balances()
        self.bal["BTC free"].setText(f"{b['BTC']['free']:.6f}")
    def _on_ws_status(self, status:str)->None: self.conn["WS"].setText(status); update_badge(self.mode_badge,"info","DRY VIEW")
    def _on_ws_book(self,bid:float,ask:float,ts:int)->None:
        self._safe_call("market callbacks", self._on_ws_book_inner, bid, ask, ts)

    def _on_ws_book_inner(self,bid:float,ask:float,ts:int)->None:
        self.market_state.snapshot.bid=bid; self.market_state.snapshot.ask=ask; self.market_state.snapshot.source="WS"; self.market_state.last_ws_ms=ts

    def _rest_poll(self)->None: pass
    def on_calculate(self)->None:
        def _inner():
            s=self.settings
            self.rows=self.grid_engine.calculate_levels(s.upper_price,s.lower_price,s.budget_u,s.levels,s.profit_ticks)
            self.grid_runtime.validate_inputs(levels=self.rows, market=self.market_state.snapshot, balances={}, filters={})
        self._safe_call("calculate_grid", _inner)
