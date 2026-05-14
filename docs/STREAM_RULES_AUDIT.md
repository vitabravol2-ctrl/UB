# UB Stream Conveyor — Audit Against 100 Trading Tree Rules

Date: 2026-05-14
Scope: static code audit of stream runtime logic.
Primary files: `app/core/grid_runtime.py`.

## Summary

- ✅ Implemented: core stream lifecycle, per-stream identity/state/order fields, supervisor tick, buy-capacity fill logic, safe-sell wait/recovery states, terminal-exit polling primitives, and stop diagnostics.
- ⚠️ Partial: rule naming/telemetry alignment and some recovery invariants are present but not fully enforced at runtime boundaries.
- ❌ Missing/Not provable by static check: strict chunk ownership/duplication guarantees and full end-to-end PnL/cycle accounting linkage from SELL fill to STREAM_PNL counters.

## Block A (Rules 1-20): Stream model and lifecycle contract

- ✅ 1-10: Per-stream independent fields and IDs exist in `GridLevel`; stream list is configured by `stream_count` and each stream has dedicated active order/chunk fields.
- ⚠️ 11-16: Lifecycle approximates chain (`WAIT_BUY` -> `BUY_PLACED` -> `SELL_*` -> `RECYCLE` -> `WAIT_BUY`), but explicit `BUY_FILLED/CHUNK_CREATED/SELL_FILLED/FIFO_CLOSE/STREAM_PNL` states are represented indirectly by logs and external handlers, not explicit state enum values.
- ✅ 17-20: Contract validator checks invalid states, missing order IDs/chunks, and chunk-live-without-plan violations.

## Block B (Rules 21-30): Scheduler independence and non-blocking behavior

- ✅ 21-23: Capacity fill computes buy slots only from BUY states and WAIT_BUY availability.
- ✅ 24-29: SELL/EXITING/BALANCE_WAIT/PAUSED_ERROR streams do not block global WAIT_BUY detection; supervisor/capacity logic is aggregate and non-blocking.
- ✅ 30: No early return in stream supervisor loops for single-stream errors.

## Block C (Rules 31-40): Tick ordering

- ⚠️ 31-40: `grid_runtime.py` includes supervisor + capacity phases, but full canonical order (market snapshot -> supervisor -> capacity -> buy statuses -> sell statuses -> terminal exits -> balance waits -> recycle -> GUI -> compact logs) is orchestrated outside this file; not fully provable from this module alone.

## Block D (Rules 41-50): Supervisor truth and counters

- ✅ 41-47: Supervisor iterates all streams each tick, repairs WAIT_START timeout, recycles on cooldown, wakes balance waits, and logs pool status.
- ⚠️ 48-50: No strict proof here that `stream_closed_cycles == STREAM_PNL count` and `wins + losses == cycles`; GUI/legacy counter decoupling is not fully verifiable in this module.

## Block E (Rules 51-60): Chunk + safe-sell semantics

- ⚠️ 51-53: Stream fields and active chunk linkage exist, but true chunk structure/ownership enforcement occurs outside this file.
- ✅ 54-60: `handle_sell_qty_not_safe` implements safe-qty gate, active-sell lock handling, and transition to `SELL_BALANCE_WAIT` with explicit next action (`wake_balance_wait_streams` -> `SELL_RETRY`).

## Block F (Rules 61-70): Retry vs terminal-exit discipline

- ✅ 61-67: Retry planner distinguishes normal retry vs terminal mode; terminal start is one-time guarded by `terminal_exit_started`.
- ✅ 68-70: Terminal order ID is tracked and polled via status-wait method; logic avoids repeated start if already active.

## Block G (Rules 71-80): Terminal timing + preserved inventory

- ⚠️ 71-72: Timeout primitives exist (`terminal_exit_should_reprice`), but explicit split between `terminal_exit_timeout_ms` and `terminal_exit_force_after_ms` is not modeled as separate runtime policies in this file.
- ⚠️ 73-79: Preservation / orphan / global-guard boundaries are partially visible via diagnostics and contract checks, but full invariant enforcement likely lives in inventory/risk modules.
- ✅ 80: This runtime file does not implement global-guard ownership over stream inventory.

## Block H (Rules 81-90): STOP reporting and duplication guards

- ✅ 81-85: STOP diagnostics include inventory/free/locked BTC, open chunks, active sells, terminal streams.
- ⚠️ 86-90: Direct duplicate prevention is partial here (registry helper and per-stream fields); one-stream-one-active-order constraints are not exhaustively hard-failed in every transition inside this file alone.

## Block I (Rules 91-100): Config scale, logging, architecture laws

- ✅ 91: `order_size_u` is used as per-stream entry budget source.
- ⚠️ 92-94: No explicit hard check/warning for `stream_max_inventory_u` safety envelope in this file.
- ✅ 95: Analysis lab separation exists in separate modules (`app/analysis/*`).
- ✅ 96-98: Trading runtime functions are not GUI-dependent and critical contract events are logged.
- ⚠️ 99-100: Core chain intent is preserved, but full proof of "stuck order never stops conveyor" and strict SELL->PNL->RECYCLE accounting requires integration-level validation across runtime + adapters + accounting.

## Recommended next actions

1. Introduce explicit stream state constants for `BUY_FILLED`, `CHUNK_CREATED`, `SELL_FILLED`, `FIFO_CLOSE`, `STREAM_PNL`.
2. Add hard invariant checks for duplicate BUY/SELL per stream/chunk in transition methods.
3. Add explicit counters and assertions for rules 48-49 (`closed_cycles`, `wins`, `losses`).
4. Encode terminal timing policy with two independent parameters (`timeout_ms`, `force_after_ms`).
5. Add integration tests for canonical tick order and non-blocking behavior under one stuck stream.
