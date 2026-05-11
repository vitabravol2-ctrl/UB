# UB v0.1.6 — GUI + REST market + Binance account read-only

UB (BTCU Spread Harvester) v0.1.6 is a desktop cockpit with REST-first market data and **read-only Binance account connectivity**.

## Safety / scope (v0.1.6)
- No order placement.
- No auto-trading / no paper-trading.
- Cancel-all button is still a stub log only.
- LIVE trading is hard-disabled (`LIVE OFF`).

## What v0.1.6 adds
- Binance API keys via `.env`.
- API test status: `NOT SET` / `OK` / `ERROR`.
- Real balances (`BTC`, `U`: free/locked).
- Open orders read-only table.
- Exchange filters (`tickSize`, `stepSize`, `minQty`, `minNotional`).
- Time sync offset via `/api/v3/time` for signed requests.
- Max buy / max sell calculations shown in GUI.

## API setup
1. Copy env template:
```bash
cp .env.example .env
```
2. Put keys in `.env`:
```env
BINANCE_API_KEY=your_key
BINANCE_API_SECRET=your_secret
```
3. Never commit `.env` (already in `.gitignore`).

## Endpoints used
- `GET /api/v3/time`
- `GET /api/v3/account` (SIGNED)
- `GET /api/v3/openOrders?symbol=BTCU` (SIGNED)
- `GET /api/v3/exchangeInfo?symbol=BTCU`
- `GET /api/v3/ticker/bookTicker?symbol=BTCU`

## Install / run
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

## How to verify account connection
- Open `НАСТРОЙКИ` panel.
- Fill API key + secret.
- Click `Сохранить в .env`.
- Click `Проверить`.
- On success UI shows API `OK`, balances, filters, and open orders.

