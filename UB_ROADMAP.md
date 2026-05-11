# UB / BTCU Spread Harvester — Техническое задание

## Цель
Создать простую desktop-программу для пары BTC/U на Binance:

- видим спред;
- быстро входим;
- быстро выходим;
- забираем часть спреда;
- повторяем цикл.

Ограничения:

- без AI;
- без индикаторов;
- без сложной теории.

---

## Этап 1 — UB v0.1.0 (GUI + подключение + просмотр рынка)

### Задача
Собрать базовый терминал.

### Нужно
- PySide6 GUI
- Binance API keys
- WS bookTicker
- REST fallback
- отображение bid / ask / spread
- лог событий
- статус соединения

### GUI-блоки
- Connection
- Market
- Spread
- Balances
- Runtime
- Logs

### Показываем
- symbol: BTCU
- bid
- ask
- spread U
- spread ticks
- WS age
- REST age
- connection status

> На этом этапе ордера не ставим.

---

## Этап 2 — UB v0.1.1 (Балансы + фильтры Binance)

### Задача
Подключить реальные данные аккаунта.

### Нужно
- баланс BTC
- баланс U
- open orders
- exchange filters
- tickSize
- stepSize
- minQty
- minNotional

### GUI (добавить)
- BTC free / locked
- U free / locked
- max buy
- max sell
- min order
- filters loaded: YES/NO

---

## Этап 3 — UB v0.1.2 (Ручные лимитные ордера)

### Задача
Без автоматики проверить безопасную постановку и отмену ордеров.

### Кнопки
- LIMIT BUY
- LIMIT SELL
- Cancel Selected
- Cancel All
- Refresh Orders

### Защита
- LIVE OFF по умолчанию
- подтверждение перед ордером
- проверка minNotional
- проверка stepSize
- проверка tickSize
- лог каждого действия

---

## Этап 4 — UB v0.2.0 (Spread Detector)

### Задача
Определять пригодность спреда.

### Метрики
- best_bid
- best_ask
- spread_u
- spread_ticks
- spread_lifetime_ms
- spread_min
- spread_max
- spread_avg
- book_age_ms
- trade_flow

### Условия good spread
- spread >= min_spread
- book_age < max_book_age
- spread_lifetime >= min_lifetime_ms
- bid/ask не схлопываются

### GUI
- SPREAD STATUS: BAD / WATCH / READY / HOT

---

## Этап 5 — UB v0.3.0 (Paper Trading)

### Задача
Проверить стратегию без реальных ордеров.

### Логика (пример)
- bid = 80000
- ask = 80007
- spread = 7
- paper BUY = bid + 1
- paper SELL = ask - 1
- profit = 5 U / BTC

### Считаем
- cycles
- wins
- losses
- avg profit
- max loss
- fill time
- missed opportunities
- spread capture %

---

## Этап 6 — UB v0.4.0 (Single Live Cycle)

### Задача
Один безопасный live-цикл.

### FSM
IDLE → WATCH_SPREAD → PLACE_ENTRY → WAIT_FILL → PLACE_EXIT → WAIT_EXIT → DONE

### Настройки
- lot_size
- min_spread
- entry_offset
- tp_offset
- sl_ticks
- max_hold_ms
- panic_exit

### Пример
- bid 80000
- ask 80007
- BUY 80001
- TP 80006
- SL 79998

---

## Этап 7 — UB v0.5.0 (Fast Exit / Panic Exit)

### Задача
Не зависать в позиции.

### Выходы
1. normal TP
2. inside-spread exit
3. IOC exit
4. market-like emergency exit

### Триггеры panic
- spread collapsed
- price moved against us
- position age > max_hold_ms
- book stale
- WS disconnected
- manual STOP

---

## Этап 8 — UB v0.6.0 (Nonstop Conveyor)

### Задача
Сделать непрерывную работу без зависания в одном ордере.

### Логика
- lot_1 entry
- lot_1 exit
- lot_2 waits
- lot_3 prepares

### Ограничения
- max_open_lots = 2–3
- max_exposure_btc
- max_exposure_u
- max_daily_loss
- max_stuck_time

---

## Этап 9 — UB v0.7.0 (Queue Avoidance)

### Задача
Минимизировать очереди.

### Методы
- entry = bid + 1
- exit = ask - 1
- reprice если цена ушла
- cancel если очередь плохая
- не ставить слишком крупный объём
- не входить в мёртвый стакан

### Метрики
- time_to_fill
- order_age
- price_moved
- queue_failed
- reprice_count
- cancel_count

---

## Этап 10 — UB v1.0.0 (Финальная версия)

### Что умеет
- видит спред по WS
- оценивает пригодность
- входит маленьким лотом
- быстро выходит
- не держит позицию долго
- не зависает в очереди
- ведёт PnL
- имеет panic exit
- работает конвейером

### Главное правило
Не ждать идеальный спред. Быстро забирать 2–5 U много раз.

### Базовые настройки старта
- symbol = BTCU
- min_spread = 7 U
- entry_offset = +1 U
- exit_offset = -1 U
- target_capture = 3–5 U
- stop_loss = 3–5 U
- max_hold_ms = 1500–3000
- lot_size = маленький
- max_open_lots = 1 (сначала)
- LIVE = OFF по умолчанию
