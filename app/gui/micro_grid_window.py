from __future__ import annotations

import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget, QHeaderView

from app.core.config import CONFIG
from app.core.grid_config import GRID_SETTINGS_STORE, GridSettings
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
        self.dry_view_active = False
        self.grid_runtime = GridRuntime()
        self.session_logger = SessionLogger(symbol="BTCU")

        root = QWidget(); self.setCentralWidget(root); layout = QVBoxLayout(root)
        top = QHBoxLayout(); layout.addLayout(top)
        top.addWidget(QLabel("UB Micro Grid / BTCU")); self.mode_badge = build_status_badge("DRY VIEW", "info"); top.addWidget(self.mode_badge)
        self.live_badge = build_status_badge("LIVE LOCKED", "warn"); top.addWidget(self.live_badge); top.addStretch(1)
        self.settings_btn = QPushButton("Settings"); self.calculate_btn = QPushButton("Calculate"); self.start_btn = QPushButton("Start Dry"); self.live_start_btn = QPushButton("Start LIVE Small"); self.pause_btn = QPushButton("Pause Grid"); self.stop_btn = QPushButton("Stop"); self.stop_cancel_btn = QPushButton("Stop & Cancel Grid"); self.emergency_btn = QPushButton("Emergency Cancel"); self.refresh_btn = QPushButton("Refresh"); self.cancel_btn = QPushButton("Cancel Grid Orders")
        self.cancel_btn.setEnabled(False)
        for b in [self.settings_btn,self.calculate_btn,self.start_btn,self.live_start_btn,self.pause_btn,self.stop_btn,self.stop_cancel_btn,self.emergency_btn,self.refresh_btn,self.cancel_btn]: top.addWidget(b)

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
        self.levels_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents); self.levels_table.horizontalHeader().setSectionResizeMode(9, QHeaderView.Stretch)

        right = QVBoxLayout(); center.addLayout(right,2)
        right.addWidget(QLabel("Grid Map")); self.grid_map = QTextEdit(); self.grid_map.setReadOnly(True); right.addWidget(self.grid_map)
        sb,self.summary = build_terminal_card("Summary",[("Range","N/A"),("Step U","N/A"),("Levels","0"),("Budget / Level","0"),("Total Budget","0"),("Valid Levels","0"),("Invalid Levels","0"),("Total Expected PnL","0"),("Tick Size","N/A"),("Step Size","N/A"),("Min Qty","N/A"),("Min Notional","N/A"),("LIVE","LOCKED")]); right.addWidget(sb)
        right.addWidget(QLabel("Logs")); self.log_box = QTextEdit(); self.log_box.setReadOnly(True); right.addWidget(self.log_box)
        self.status_line = QLabel("WS age | REST age | filters | last action | LIVE locked"); self.statusBar().addWidget(self.status_line,1)

        self.settings_btn.clicked.connect(self.open_settings); self.calculate_btn.clicked.connect(self.on_calculate); self.start_btn.clicked.connect(self.on_start_dry); self.live_start_btn.clicked.connect(self.on_start_live_small); self.pause_btn.clicked.connect(self.on_pause); self.stop_btn.clicked.connect(self.on_stop); self.stop_cancel_btn.clicked.connect(self.on_stop_cancel); self.emergency_btn.clicked.connect(self.on_emergency_cancel); self.refresh_btn.clicked.connect(self._refresh_info)
        self.market_ws.signals.book.connect(self._on_ws_book); self.market_ws.signals.status.connect(self._on_ws_status); self.market_ws.start()
        self.rest_timer = QTimer(self); self.rest_timer.timeout.connect(self._rest_poll); self.rest_timer.start(3000)
        self.balance_timer = QTimer(self); self.balance_timer.timeout.connect(self._balances_refresh); self.balance_timer.start(7000)
        self._log("[GRID] terminal started"); self._refresh_info()

    def append_log(self, msg: str) -> None:
        line = self.session_logger.log("GRID", msg.replace("[GRID] ", ""))
        self.log_box.setPlainText("\n".join(self.session_logger.get_gui_lines()))

    def _log(self,msg:str)->None: self.append_log(msg)
    def open_settings(self)->None:
        self._log("[GRID] settings dialog opened"); dlg=GridSettingsDialog(self); dlg.apply_settings(self.settings)
        if dlg.exec(): self.settings=dlg.collect_settings(); self._log("[GRID] settings applied")

    def _refresh_info(self)->None:
        self._load_filters(); self._balances_refresh(); self._load_api_status(); self._load_open_orders()

    def _load_api_status(self)->None:
        def work():
            status=self.trade_adapter.load_api(); self.conn["API"].setText(status)
        threading.Thread(target=work,daemon=True).start()

    def _load_open_orders(self)->None:
        def work():
            try: n=len(self.trade_adapter.get_open_orders()); self._log(f"[GRID] open orders read n={n}")
            except Exception: pass
        threading.Thread(target=work,daemon=True).start()

    def _load_filters(self)->None:
        try:
            f=self.trade_adapter.load_filters(); self.grid_engine.set_filters(float(f['tickSize']),float(f['stepSize']),float(f['minQty']),float(f['minNotional']))
            self.summary["Tick Size"].setText(str(f['tickSize'])); self.summary["Step Size"].setText(str(f['stepSize'])); self.summary["Min Qty"].setText(str(f['minQty'])); self.summary["Min Notional"].setText(str(f['minNotional']))
            self._log(f"[GRID] filters loaded tickSize={f['tickSize']} stepSize={f['stepSize']}")
        except Exception: pass

    def _balances_refresh(self)->None:
        def work():
            try:
                b=self.trade_adapter.refresh_balances(); self.bal["BTC free"].setText(f"{b['BTC']['free']:.6f}"); self.bal["BTC locked"].setText(f"{b['BTC']['locked']:.6f}"); self.bal["U free"].setText(f"{b['U']['free']:.2f}"); self.bal["U locked"].setText(f"{b['U']['locked']:.2f}"); self.bal["Exposure U"].setText(f"{b['U']['locked']:.2f}"); self._log(f"[GRID] balances updated BTC={b['BTC']['free']:.6f} U={b['U']['free']:.2f}")
            except Exception: pass
        threading.Thread(target=work,daemon=True).start()

    def _on_ws_status(self, status:str)->None: self.conn["WS"].setText(status); update_badge(self.mode_badge,"info","DRY VIEW")
    def _on_ws_book(self,bid:float,ask:float,ts:int)->None:
        self.market_state.snapshot.bid=bid; self.market_state.snapshot.ask=ask; self.market_state.snapshot.source="WS"; self.market_state.last_ws_ms=ts
        spread=ask-bid; mid=(ask+bid)/2
        self.market["Bid"].setText(f"{bid:.2f}"); self.market["Ask"].setText(f"{ask:.2f}"); self.market["Spread U"].setText(f"{spread:.2f}"); self.market["Spread ticks"].setText(f"{spread/max(self.grid_engine.tick_size,1e-9):.1f}"); self.market["Mid"].setText(f"{mid:.2f}"); self.market["Source"].setText("WS")
        if self.dry_view_active: self._refresh_dry_bands()

    def _rest_poll(self)->None: pass
    def on_calculate(self)->None:
        s=self.settings
        self.rows=self.grid_engine.calculate_levels(s.upper_price,s.lower_price,s.budget_u,s.levels,s.profit_ticks)
        self.levels_table.setRowCount(len(self.rows))
        valid=0
        for r,row in enumerate(self.rows):
            band="WAITING"; status="VALID" if row.valid else row.reason
            if row.valid: valid+=1
            vals=[str(row.level),f"{row.price:.2f}",row.side,f"{row.order_u:.4f}",f"{row.qty_btc:.8f}",f"{row.target_sell:.2f}",f"{row.expected_pnl:.8f}",band,status,row.reason]
            for c,v in enumerate(vals):
                item=QTableWidgetItem(v)
                if "INVALID" in status: item.setForeground(QColor("#ff7b7b"))
                elif "NEAR" in band: item.setForeground(QColor("#f8da6b"))
                elif status=="VALID": item.setForeground(QColor("#8fe3a2"))
                self.levels_table.setItem(r,c,item)
        invalid=len(self.rows)-valid
        self.grid_status["Levels"].setText(str(len(self.rows))); self.grid_status["Valid"].setText(str(valid)); self.grid_status["Invalid"].setText(str(invalid))
        self.summary["Range"].setText(f"{s.lower_price:.2f} .. {s.upper_price:.2f}"); self.summary["Step U"].setText(f"{(s.upper_price-s.lower_price)/(s.levels-1):.4f}"); self.summary["Levels"].setText(str(s.levels)); self.summary["Budget / Level"].setText(f"{s.budget_u/s.levels:.2f}"); self.summary["Total Budget"].setText(f"{s.budget_u:.2f}"); self.summary["Valid Levels"].setText(str(valid)); self.summary["Invalid Levels"].setText(str(invalid)); self.summary["Total Expected PnL"].setText(f"{sum(x.expected_pnl for x in self.rows):.6f}")
        self._log(f"[GRID] grid calculated levels={len(self.rows)} valid={valid} invalid={invalid}")

    def _refresh_dry_bands(self)->None:
        bid=self.market_state.snapshot.bid; ask=self.market_state.snapshot.ask
        if bid is None or ask is None: return
        mid=(bid+ask)/2; spread=ask-bid; near=0; lines=[f"▲ Upper {self.settings.upper_price:.2f}"]
        for i,row in enumerate(self.rows):
            if abs(row.price-mid)<=max(2*spread,2*self.grid_engine.tick_size): band="NEAR_MARKET"; near+=1
            elif row.price>ask: band="ABOVE_MARKET"
            else: band="BELOW_MARKET"
            self.levels_table.setItem(i,7,QTableWidgetItem(band)); lines.append(f"│ Level {row.level} {band}")
        lines.append(f"● Market {mid:.2f}"); lines.append(f"▼ Lower {self.settings.lower_price:.2f}"); self.grid_map.setPlainText("\n".join(lines)); self.grid_status["Near Market"].setText(str(near))

    def on_start_dry(self)->None: self.dry_view_active=True; self.grid_status["State"].setText("DRY_VIEW"); self._log("[GRID] dry view started"); self._log("[GRID] live locked")
    def on_stop(self)->None: self.dry_view_active=False; self.grid_status["State"].setText("STOPPED")
    def on_start_live_small(self)->None:
        details = f"symbol={CONFIG.binance_symbol}\nbudget_u={self.settings.budget_u:.2f}\nlevels={self.settings.levels}\nmax_exposure_u={self.settings.max_exposure_u:.2f}\nestimated order size={self.settings.budget_u/max(self.settings.levels,1):.2f}"
        msg = f"LIVE Grid will place real Binance orders. Use small budget only.\n\n{details}"
        if QMessageBox.question(self, "Confirm LIVE", msg) == QMessageBox.StandardButton.Yes:
            self.grid_runtime.live_enabled = True
            self.grid_runtime.arm_live(True)
            self.grid_runtime.start_live()
            self.grid_status["State"].setText(self.grid_runtime.state)
            self.summary["LIVE"].setText(self.grid_runtime.state)
            self._log("[GRID] live start confirmation accepted")

    def on_pause(self)->None:
        self.grid_runtime.pause(); self.grid_status["State"].setText("PAUSED"); self._log("[GRID] paused")

    def on_stop_cancel(self)->None:
        self.grid_runtime.stop(); self.trade_adapter.cancel_grid_orders(); self._log("[GRID] stop & cancel grid")

    def on_emergency_cancel(self)->None:
        self.trade_adapter.cancel_grid_orders(); self._log("[GRID] emergency cancel")
