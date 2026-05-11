# UB v0.1.0 — GUI + Market Monitor

UB (BTCU Spread Harvester) v0.1.0 is a **watch-only desktop cockpit** built with PySide6.

## Important
- v0.1.0 **does not trade**.
- No order placement.
- LIVE mode is hard-disabled (`LIVE OFF`).
- No AI, no indicators, no autotrading.

## Features in v0.1.0
- Dark cockpit GUI.
- Binance WS `bookTicker` for `BTCU`.
- REST fallback via `/api/v3/ticker/bookTicker`.
- Live bid/ask/spread/capture estimate display.
- WS/REST connection status + age metrics.
- Runtime/FSM, risk, balances, orders blocks as placeholders for v0.1.1 extension.
- Safe Qt signal-based UI updates from background WS thread.

## Install
```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run
```bash
python main.py
```

## Windows quick start
```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```

If Binance doesn't provide BTCU data, the app stays alive and shows WS/REST error statuses in UI and logs.
