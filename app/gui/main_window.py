import time
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
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
        self.setWindowTitle("UB v0.1.1 / BTCU Microspread Terminal")
        self.resize(1320, 820)
        self.setStyleSheet(main_qss())

        self.state = MarketState()
        self.rest = MarketREST()
        self.ws = MarketWSClient(CONFIG.symbol)
        self.started_watch_ms = int(time.time() * 1000)
        self.runtime_active = False

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

        self.log("BOOT", "UB v0.1.1 запущен")

    def _build_cards(self) -> None:
        conn, self.conn = kv_card("ПОДКЛЮЧЕНИЕ", [("WS", "CONNECTING"), ("REST", "N/A"), ("Возраст WS ms", "N/A"), ("Возраст REST ms", "N/A")])
        self.grid.addWidget(conn, 0, 0)

        bid_box, self.bid_v = big_value("BID", "N/A")
        ask_box, self.ask_v = big_value("ASK", "N/A")
        spr_box, self.spr_v = big_value("SPREAD", "N/A")
        self.grid.addWidget(bid_box, 0, 1)
        self.grid.addWidget(ask_box, 0, 2)
        self.grid.addWidget(spr_box, 0, 3)

        engine, self.engine = kv_card("СПРЕД", [("Статус", "BAD"), ("Спред", "N/A"), ("Захват", "0.00"), ("Время жизни", "0")])
        fsm, self.fsm = kv_card("RUNTIME / FSM", [("Состояние", "IDLE"), ("Вход", "N/A"), ("Выход", "N/A"), ("Режим", "WATCH")])
        risk, _ = kv_card("РИСК", [("Риск", "LOW"), ("Паника", "READY"), ("Экспозиция", "LOW")])
        bal, _ = kv_card("БАЛАНСЫ", [("BTC", "N/A"), ("USDT", "N/A")])
        self.grid.addWidget(engine, 1, 0)
        self.grid.addWidget(fsm, 1, 1)
        self.grid.addWidget(risk, 1, 2)
        self.grid.addWidget(bal, 1, 3)

        self.orders = QTableWidget(0, 4)
        self.orders.setHorizontalHeaderLabels(["Вход", "Выход", "Qty", "Статус"])
        self.orders.setMaximumHeight(120)
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
        self.logs = QTableWidget(0, 1)
        self.logs.setHorizontalHeaderLabels(["ЛОГИ"])
        self.logs.setMaximumHeight(170)
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

        apply_btn = QPushButton("ПРИМЕНИТЬ")

        lay.addWidget(strategy)
        lay.addWidget(risk)
        lay.addWidget(mode)
        lay.addWidget(apply_btn)
        self.settings_dock.setWidget(w)
        self.addDockWidget(Qt.RightDockWidgetArea, self.settings_dock)
        self.settings_dock.hide()

    def toggle_settings(self) -> None:
        self.settings_dock.setVisible(not self.settings_dock.isVisible())

    def toggle_runtime(self) -> None:
        if not self.runtime_active:
            self.runtime_active = True
            self.started_watch_ms = int(time.time() * 1000)
            self.ws.start()
            self.fsm["Состояние"].setText("WATCH_SPREAD")
            self.start_stop_btn.setText("STOP")
            self.log("FSM", "IDLE -> WATCH_SPREAD")
            return

        self.runtime_active = False
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
            bid, ask, ts = self.rest.fetch_book_ticker(CONFIG.symbol)
            self.state.last_rest_ms = ts
            self.state.rest_status = "OK"
            ws_age = self.state.age_ms(self.state.last_ws_ms)
            if ws_age is None or ws_age > CONFIG.max_ws_age_ms:
                self.log("WS", "stale")
                self.state.snapshot.bid = bid
                self.state.snapshot.ask = ask
                self.state.snapshot.updated_ms = ts
                self.state.snapshot.source = "REST"
        except Exception as exc:
            self.state.rest_status = "ERROR"
            self.log("REST", f"error: {exc}")

    def on_tick(self) -> None:
        self._refresh_ui()

    def _refresh_ui(self) -> None:
        bid = self.state.snapshot.bid
        ask = self.state.snapshot.ask
        spread = self.state.snapshot.spread
        ws_age = self.state.age_ms(self.state.last_ws_ms)
        rest_age = self.state.age_ms(self.state.last_rest_ms)

        ws_ok = ws_age is not None and ws_age <= CONFIG.max_ws_age_ms
        self.state.ws_status = "OK" if ws_ok else "LOST"

        self.conn["WS"].setText(self.state.ws_status)
        self.conn["REST"].setText(self.state.rest_status)
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

        self.engine["Статус"].setText(status)
        self.engine["Спред"].setText("N/A" if spread is None else f"{spread:.2f}")
        self.engine["Захват"].setText(f"{capture:.2f}")
        self.engine["Время жизни"].setText(str(max(int(time.time() * 1000) - self.started_watch_ms, 0)))

        if bid is not None and ask is not None:
            self.fsm["Вход"].setText(f"{bid + CONFIG.entry_offset:.2f}")
            self.fsm["Выход"].setText(f"{ask - CONFIG.exit_offset:.2f}")

        runtime_txt = "WATCH" if self.runtime_active else "IDLE"
        spread_txt = "FALLBACK" if not ws_ok and self.state.rest_status == "OK" else f"SPREAD {status}"
        ws_txt = f"{ws_age}ms" if ws_ok and ws_age is not None else "LOST"
        self.top_status.setText(f"{CONFIG.symbol} | WS ● {ws_txt} | REST ● {self.state.rest_status} | {runtime_txt} | {spread_txt}")

    def log(self, tag: str, message: str) -> None:
        text = format_log(tag, message)
        row = self.logs.rowCount()
        self.logs.insertRow(row)
        self.logs.setItem(row, 0, QTableWidgetItem(text))
        if self.logs.rowCount() > 120:
            self.logs.removeRow(0)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self.ws.stop()
        super().closeEvent(event)
