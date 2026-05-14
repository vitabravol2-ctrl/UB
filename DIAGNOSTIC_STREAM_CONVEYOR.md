# Stream Conveyor Diagnostic (baseline a87c2f1)

## Scope
Static code audit + targeted tests/log checks for Stream Conveyor lifecycle, accounting, and GUI counters.

## What was checked
- Stream creation, BUY placement gating, order sizing source.
- Per-stream lifecycle isolation and transitions.
- SELL placement, timeout/retry, and failure containment.
- FIFO/realized PnL accounting and cycle closure semantics.
- GUI counters (cycles/wins/losses/winrate/realized PnL) vs runtime update points.
- Legacy/global logic that can interfere with conveyor behavior.

## Findings

### 1) Input / stream creation
- ✅ `GridRuntime.configure_micro_grid` builds stream levels using `order_size_u` as budget source (`STREAM_BUDGET_SOURCE source=order_size_u`).
- ⚠️ BUY gating still uses `max_exposure_u` as a runtime limiter in placement loop (`STREAM_BUY_BLOCKED reason=exposure_limit ... max_exposure_u=...`). This is not position sizing, but it can block new BUYs even when stream is otherwise healthy.
- ✅ Tests validate level budgets are `order_size_u`.

### 2) Stream lifecycle independence
- ✅ Runtime model is per-level (`level.state`, `active_buy_order_id`, `active_sell_order_id`, `active_chunk_id`), with staggered activation and recycle per stream.
- ✅ Tests cover independent staggered activation and recycle release.
- ⚠️ No single strict enum/state check for exact path `WAIT_BUY -> BUY_PLACED -> BUY_FILLED -> SELL_PLACED -> SELL_FILLED -> PNL -> RECYCLE -> WAIT_BUY`; path is implemented across `GridRuntime` + `MainWindow._poll_grid_orders`, but with side paths (`SELL_RETRY`, `EXITING`).

### 3) Exit behavior
- ✅ On BUY fill, SELL is attempted immediately in the same polling tick.
- ✅ SELL timeout path cancels/replaces per stream with retry counters and repricing.
- ✅ Errors in one stream SELL path are contained (logged and level moved to retry/wait states) rather than stopping whole runtime.
- ⚠️ There are global sell/watchdog paths in `MainWindow` (non-stream FSM) that must keep skipping stream-owned inventory; code includes guards (`STREAM_GLOBAL_SELL_SKIP ...`), but this remains a sensitive integration point.

### 4) Accounting (FIFO, realized PnL, close semantics)
- ✅ Stream sell fill computes pnl once per closed chunk and increments stream counters once.
- ✅ FIFO close logging present at sell fill.
- ✅ Cycle increments are tied to sell filled branch (`status == FILLED` for stream sell order).
- ⚠️ Separate non-stream cycle accounting also exists in same class (`_apply_fifo_close_result`, `_finalize_cycle_if_flat`). Guarding by `use_stream_stats` in summary avoids mixed UI counters, but coexistence is a regression risk.

### 5) GUI/statistics consistency
- ✅ In stream mode UI summary reads stream-specific counters (`stream_closed_cycles`, `stream_wins`, `stream_losses`, `stream_realized_pnl`).
- ✅ Winrate computed from same stream counters.
- ⚠️ Potential mismatch can happen if stream fill is processed with zero qty (explicit mismatch log exists), or if ownership metadata desyncs (self-heal paths exist).

### 6) Logs / stuck states / legacy interference
- ✅ There is extensive lifecycle/stuck instrumentation (`STREAM_STATE_MISMATCH`, `STREAM_CONCURRENCY_AUDIT`, ownership audit, self-heal logs).
- ⚠️ Confirmed failing unit test in trade math suite:
  - `test_order_size_u_not_interpreted_as_btc` fails with plan status `CAPTURE_TOO_SMALL` instead of `READY/HOT`.
  - This indicates a baseline inconsistency in planning thresholds vs test expectation and can mask BUY eligibility diagnostics.

## Real discrepancies to address first (point fixes, no big features)
1. Reconcile trade-plan threshold logic with stream expectations (fix failing `test_order_size_u_not_interpreted_as_btc` or update expectation if intended).
2. Add/enable an invariant test that validates full per-stream lifecycle path including SELL retry branch and final recycle.
3. Add a targeted regression test proving one stream stuck in `SELL_RETRY/EXITING` does not reduce BUY placement capacity for unrelated waiting streams beyond configured `stream_max_active_buys`.
4. Add a small audit assertion test that GUI stream counters equal number of stream sell-filled closures from logs/events.

