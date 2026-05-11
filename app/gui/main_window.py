import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QTextOption
from PySide6.QtWidgets import (
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

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
        self.setWindowTitle("UB v0.1.5 / BTCU Microspread Terminal")
        self.resize(1320, 820)
        self.setStyleSheet(main_qss())

        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.stream_symbol, CONFIG.binance_symbol, CONFIG.max_ws_age_ms)
        self.started_watch_ms = int(time.time() * 1000)
        self.runtime_active = False
        self._last_stale_log_ms = 0
        self._rest_bookticker_logged = False
        self._last_rest_ok_log_ms = 0
        self._had_rest_error = False
        self._ws_lost_logged = False
        self._spread_status = "BAD"
        self._spread_value: float | None = None
        self._spread_lifetime_start_ms = int(time.time() * 1000)

        root = QWidget()
        self.setCentralWidget(root)
        self.main_layout = QVBoxLayout(root)

        self.top_status = QLabel()
        self.top_status.setStyleSheet("font-size: 15px; font-weight: 800;")
        self.main_layout.addWidget(self.top_status)

        self.grid = QGridLayout()
        self.main_layout.addLayout(self.grid)

        self._build_cards()
        self._build_controls()
        self._build_logs()
        self._build_settings_panel()

        self.ws.signals.book.connect(self.on_ws_book)
        self.ws.signals.status.connect(self.on_ws_status)
        self.ws.signals.log.connect(self.log)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_tick)
        self.timer.start(300)

        self.rest_timer = QTimer(self)
        self.rest_timer.timeout.connect(self.fetch_rest)
        self.rest_timer.start(CONFIG.rest_poll_ms)

        self.log("BOOT", "UB v0.1.5 запущен")
        self.log(
            "CONFIG",
            f"display={CONFIG.display_symbol} binance={CONFIG.binance_symbol} stream={CONFIG.stream_symbol}",
        )
        self.log("BOOT", "WS optional diagnostic mode enabled")

    def _build_cards(self) -> None:
        conn, self.conn = kv_card("ПОДКЛЮЧЕНИЕ", [("WS", "OPTIONAL LOST"), ("REST", "N/A"), ("Источник", "NONE"), ("Возраст WS ms", "N/A"), ("Возраст REST ms", "N/A"), ("Data source", "NONE")])
        self.grid.addWidget(conn, 0, 0)

        bid_box, self.bid_v = big_value("BID", "N/A")
        ask_box, self.ask_v = big_value("ASK", "N/A")
        spr_box, self.spr_v = big_value("SPREAD", "N/A")
        self.grid.addWidget(bid_box, 0, 1)
        self.grid.addWidget(ask_box, 0, 2)
        self.grid.addWidget(spr_box, 0, 3)

        engine, self.engine = kv_card("СПРЕД", [("Статус", "BAD"), ("Спред", "N/A"), ("Захват", "0.00"), ("Время жизни", "0 ms"), ("Источник", "NONE"), ("Последнее обновление", "N/A")])
        fsm, self.fsm = kv_card("RUNTIME / FSM", [("Состояние", "IDLE"), ("Вход", "N/A"), ("Выход", "N/A"), ("Режим", "WATCH")])
        risk, _ = kv_card("РИСК", [("Риск", "LOW"), ("Паника", "READY"), ("Экспозиция", "LOW")])
        bal, _ = kv_card("БАЛАНСЫ", [("BTC", "N/A"), ("U", "N/A")])
        self.grid.addWidget(engine, 1, 0)
        self.grid.addWidget(fsm, 1, 1)
        self.grid.addWidget(risk, 1, 2)
        self.grid.addWidget(bal, 1, 3)

        self.orders = QTableWidget(0, 4)
        self.orders.setHorizontalHeaderLabels(["Вход", "Выход", "Qty", "Статус"])
        self.orders.setMaximumHeight(90)
        orders_box = QGroupBox("ОРДЕРА / ПОЗИЦИИ")
        o_lay = QVBoxLayout()
        o_lay.addWidget(self.orders)
        orders_box.setLayout(o_lay)
        self.grid.addWidget(orders_box, 2, 0, 1, 4)

    def _build_controls(self) -> None:
        row = QHBoxLayout()
        self.settings_btn = QPushButton("НАСТРОЙКИ")
        self.settings_btn.clicked.connect(self.toggle_settings)
        row.addWidget(self.settings_btn)

        self.start_stop_btn = QPushButton("START")
        self.start_stop_btn.setProperty("kind", "primary")
        self.start_stop_btn.clicked.connect(self.toggle_runtime)
        row.addWidget(self.start_stop_btn)

        cancel_btn = QPushButton("ОТМЕНИТЬ ВСЁ")
        cancel_btn.setProperty("kind", "danger")
        cancel_btn.clicked.connect(self.cancel_all)
        row.addWidget(cancel_btn)
        self.main_layout.addLayout(row)

    def _build_logs(self) -> None:
        self.logs = QPlainTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setMaximumBlockCount(500)
        self.logs.setMinimumHeight(280)
        self.logs.setWordWrapMode(QTextOption.WrapAnywhere)
        self.logs.setStyleSheet(
            "QPlainTextEdit {background-color: #111827; color: #E5E7EB; font-family: Consolas, 'Courier New', monospace; font-size: 11pt; border: 1px solid #374151; }"
        )
        self.main_layout.addWidget(self.logs)

    def _build_settings_panel(self) -> None:
        self.settings_dock = QDockWidget("НАСТРОЙКИ", self)
        self.settings_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        w = QWidget()
        lay = QVBoxLayout(w)
        strategy = QGroupBox("СТРАТЕГИЯ")
        s_form = QFormLayout()
        for key, val in [("min_spread", CONFIG.min_spread), ("entry_offset", CONFIG.entry_offset), ("exit_offset", CONFIG.exit_offset), ("target_capture", 0.0), ("stop_loss", 0.0), ("max_hold_ms", 5000)]:
            s_form.addRow(key, QLineEdit(str(val)))
        strategy.setLayout(s_form)
        risk = QGroupBox("РИСК")
        r_form = QFormLayout()
        for key, val in [("lot_size", 1), ("max_open_lots", 1), ("max_daily_loss", 100)]:
            r_form.addRow(key, QLineEdit(str(val)))
        risk.setLayout(r_form)
        mode = QGroupBox("РЕЖИМ")
        m_form = QFormLayout()
        m_form.addRow("LIVE", QLabel("OFF"))
        m_form.addRow("auto cancel on stop", QLabel("ON"))
        mode.setLayout(m_form)
        lay.addWidget(strategy)
        lay.addWidget(risk)
        lay.addWidget(mode)
        lay.addWidget(QPushButton("ПРИМЕНИТЬ"))
        self.settings_dock.setWidget(w)
        self.addDockWidget(Qt.RightDockWidgetArea, self.settings_dock)
        self.settings_dock.hide()

    def toggle_settings(self) -> None:
        self.settings_dock.setVisible(not self.settings_dock.isVisible())

    def toggle_runtime(self) -> None:
        if not self.runtime_active:
            self.runtime_active = True
            self.ws.start()
            self.fsm["Состояние"].setText("WATCH_SPREAD")
            self.start_stop_btn.setText("STOP")
            self.log("RUNTIME", "START WATCH")
            self.log("FSM", "IDLE -> WATCH_SPREAD")
            return
        self.runtime_active = False
        self.log("RUNTIME", "STOP")
        self.fsm["Состояние"].setText("IDLE")
        self.start_stop_btn.setText("START")
        self.ws.stop()
        self.cancel_all()
        self.log("FSM", "WATCH_SPREAD -> IDLE")

    def cancel_all(self) -> None:
        self.log("ORDERS", "cancel all requested")

    def on_ws_book(self, bid: float, ask: float, ts: int) -> None:
        self.state.snapshot.bid = bid
        self.state.snapshot.ask = ask
        self.state.snapshot.updated_ms = ts
        self.state.snapshot.source = "WS"
        self.state.last_ws_ms = ts

    def on_ws_status(self, status: str) -> None:
        self.state.ws_status = status

    def fetch_rest(self) -> None:
        try:
            if not self._rest_bookticker_logged:
                self.log("REST", f"bookTicker symbol={CONFIG.binance_symbol}")
                self._rest_bookticker_logged = True
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.binance_symbol)
            prev_status = self.state.rest_status
            self.state.last_rest_ms = ts
            self.state.rest_status = "OK"
            self.state.snapshot.bid = bid
            self.state.snapshot.ask = ask
            self.state.snapshot.updated_ms = ts
            self.state.snapshot.source = "REST"
            now_ms = int(time.time() * 1000)
            should_log = (
                prev_status != "OK"
                or self._had_rest_error
                or self._last_rest_ok_log_ms == 0
                or now_ms - self._last_rest_ok_log_ms >= 10000
            )
            if should_log:
                self.log("REST", f"OK bid={bid:.2f} ask={ask:.2f}")
                self._last_rest_ok_log_ms = now_ms
            self._had_rest_error = False
        except Exception as exc:
            if self.state.rest_status != "ERROR":
                self.log("REST", f"error: {exc}")
            self.state.rest_status = "ERROR"
            self._had_rest_error = True

    def on_tick(self) -> None:
        ws_age = self.state.age_ms(self.state.last_ws_ms)
        if ws_age is not None and ws_age > CONFIG.max_ws_age_ms and not self._ws_lost_logged:
            self.log("WS", "no BTCU bookTicker stream, running REST-first")
            self._ws_lost_logged = True
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        bid = self.state.snapshot.bid
        ask = self.state.snapshot.ask
        spread = self.state.snapshot.spread
        ws_age = self.state.age_ms(self.state.last_ws_ms)
        rest_age = self.state.age_ms(self.state.last_rest_ms)

        if self.state.ws_status == "CONNECTING":
            ws_display = "CONNECTING"
        elif self.state.ws_status == "ERROR":
            ws_display = "ERROR"
        elif ws_age is None:
            ws_display = "LOST"
        elif ws_age <= CONFIG.max_ws_age_ms:
            ws_display = "OK"
        else:
            ws_display = "STALE"

        ws_conn = "OPTIONAL LOST" if ws_display in {"LOST", "STALE", "ERROR", "CONNECTING"} else "OK"
        self.conn["WS"].setText(ws_conn)
        self.conn["REST"].setText(self.state.rest_status)
        self.conn["Источник"].setText(self.state.snapshot.source)
        self.conn["Data source"].setText(self.state.snapshot.source)
        self.conn["Возраст WS ms"].setText("N/A" if ws_age is None else str(ws_age))
        self.conn["Возраст REST ms"].setText("N/A" if rest_age is None else str(rest_age))

        self.bid_v.setText("N/A" if bid is None else f"{bid:.2f}")
        self.ask_v.setText("N/A" if ask is None else f"{ask:.2f}")
        self.spr_v.setText("N/A" if spread is None else f"{spread:.2f}")

        status = "BAD"
        capture = 0.0
        if spread is not None:
            capture = max(spread - CONFIG.entry_offset - CONFIG.exit_offset, 0.0)
            if spread >= CONFIG.min_spread * 1.5:
                status = "HOT"
            elif spread >= CONFIG.min_spread:
                status = "READY"
            elif spread > 0:
                status = "WATCH"

        now_ms = int(time.time() * 1000)
        if spread is None:
            self._spread_lifetime_start_ms = now_ms
            self._spread_value = None
            self._spread_status = status
        else:
            spread_changed = self._spread_value is None or abs(spread - self._spread_value) > max(0.01, 1.0)
            if status != self._spread_status or spread_changed:
                if status != self._spread_status:
                    self.log("SPREAD", f"status changed {self._spread_status} -> {status}")
                self._spread_lifetime_start_ms = now_ms
                self._spread_status = status
                self._spread_value = spread
                self.log("SPREAD", f"{status} spread={spread:.2f} capture={capture:.2f} source={self.state.snapshot.source}")

        lifetime_ms = max(now_ms - self._spread_lifetime_start_ms, 0)
        lifetime_txt = self._format_duration(lifetime_ms)
        self.engine["Статус"].setText(status)
        self.engine["Спред"].setText("N/A" if spread is None else f"{spread:.2f}")
        self.engine["Захват"].setText(f"{capture:.2f}")
        self.engine["Время жизни"].setText(lifetime_txt)
        self.engine["Источник"].setText(self.state.snapshot.source)
        self.engine["Последнее обновление"].setText("N/A" if rest_age is None else f"{rest_age} ms")

        runtime_txt = "WATCH" if self.runtime_active else "IDLE"
        ws_ok = ws_display == "OK"
        if self.state.rest_status == "OK" and ws_ok:
            mode_txt = "WS+REST"
        elif self.state.rest_status == "OK":
            mode_txt = "REST LIVE"
        else:
            mode_txt = "NO DATA"
        ws_txt = f"OK {ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        rest_txt = f"OK {rest_age}ms" if self.state.rest_status == "OK" and rest_age is not None else self.state.rest_status
        self.top_status.setText(f"{CONFIG.display_symbol} | REST ● {rest_txt} | WS ● {ws_txt} | {runtime_txt} | {mode_txt}")

    def _format_duration(self, duration_ms: int) -> str:
        if duration_ms < 1000:
            return f"{duration_ms} ms"
        if duration_ms < 10000:
            return f"{duration_ms / 1000:.1f} s"
        return f"{duration_ms / 1000:.1f} s"

    def log(self, tag: str, message: str) -> None:
        self.logs.appendPlainText(format_log(tag, message))
        self.logs.verticalScrollBar().setValue(self.logs.verticalScrollBar().maximum())

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop()
        super().closeEvent(event)
