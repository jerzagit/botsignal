# SignalBot — Complete Guard & Logic Reference

A precise, implementation-level description of every safety guard and trade-logic rule,
written so the exact same system can be reproduced in another bot.
All thresholds shown are the live production values.

---

## Table of contents

1. [Pipeline overview](#1-pipeline-overview)
2. [Pre-trade gates](#2-pre-trade-gates)
3. [`execute_trade()` guards, in order](#3-execute_trade-guards-in-order)
4. [Lot sizing formula](#4-lot-sizing-formula)
5. [Manual-trade extra checks](#5-manual-trade-extra-checks)
6. [Layered DCA runtime logic](#6-layered-dca-runtime-logic)
7. [Profit Lock](#7-profit-lock)
8. [Source risk buckets](#8-source-risk-buckets)
9. [Config reference](#9-config-reference)

---

## 1. Pipeline overview

```
Signal arrives (Telegram source, read via user account / MTProto listener)
  → Parse: direction, entry zone (low–high), SL, one or more TPs
  → Per-source risk bucket check (each source has its own budget; global cap too)
  → Lot sizing: risk % × equity ÷ SL distance  (see §4)
  → Guards (see §3) — any veto = skip / retry next cycle
  → Execute:
      • Layered DCA mode: up to N layers placed progressively as price moves deeper
      • Standard mode: split into equal positions cycling the TP list
  → Monitor:
      • outcome poller every 60 s (win/loss detection)
      • Profit Lock every 60 s (breakeven + TP extension)
      • layer watcher: places L2+ when price dips, hands deepest layer to breakeven
  → Record: MySQL (signals / trades / guard_events) + local JSON log
```

Design principles:
* **Fail-safe:** nothing auto-closes on crash — SL/TP live broker-side.
* **Every block is logged** to `guard_events(guard_name, signal_id, symbol, direction, reason, value_actual, value_required)`.
* **One asyncio event loop** runs all watchers; every blocking broker call runs in an executor thread.

---

## 2. Pre-trade gates

Checked before `execute_trade()` is ever called.

| Gate | Rule | Live setting | Behaviour |
|---|---|---|---|
| **Startup cooldown** | Commands within `STARTUP_COOLDOWN` seconds of boot are rejected | 60 s | prevents acting on stale queued messages after restart |
| **Replayed-update filter** | Telegram update ID must be newer than bot start time | always on | drops messages queued while the bot was down |
| **Duplicate-update claim** | each update ID processed at most once (`data/processed_updates.json`) | always on | idempotency across handler retries |
| **Manual cooldown** | per symbol+direction lockout between manual commands | 60 s | bypass with `force` argument |

---

## 3. `execute_trade()` guards, in order

Each guard below runs sequentially inside the trade executor. First failure aborts.
`L1` = first DCA layer, `L2+` = deeper layers. Manual commands may skip some gates.

### GUARD 0 — Session filter
```
if SESSION_FILTER_ENABLED and not manual:
    in_session = SESSION_START_HOUR_UTC <= utc_hour < SESSION_END_HOUR_UTC
    block if outside window
```
Live: **07:00–21:00 UTC** (London + NY only). Manual trades bypass this.

### GUARD X — Daily loss circuit breaker
```
if realized_loss_today >= MAX_DAILY_LOSS_USD:   # $50
    block ALL new trading until midnight reset
```

### GUARD DB — Database availability
```
signals : TRADE_REQUIRES_DB=true      → MySQL must be online
manual  : MANUAL_TRADE_REQUIRES_DB=false → optional
```
Fails closed: no untracked trades are allowed when tracking is required.

### GUARD 1 — Account reachable
MT5 IPC must return account info; otherwise abort.

### GUARD 2 — Margin level
```
if used_margin > 0 and margin_level < MIN_MARGIN_LEVEL (300%): block
margin_level = equity / used_margin × 100
skipped entirely when there are no open positions (used_margin == 0)
```

### GUARD 3 — Same-direction stack (mode: REDUCE)
```
existing_same_direction = open positions, same symbol + same type
excluded: own session tickets (this DCA session's layers)
          positions whose SL == entry (breakeven = free trade)
          positions not belonging to tracked channel trades
at_risk  = those still carrying risk (SL != entry)

STACK_MODE=reduce (live):
    new_lot = calculated_lot − sum(existing_at_risk_lot)
    if new_lot <= 0 → skip ("budget already used")
STACK_MODE=block would reject instead
```
This is why a fresh `/goldbuynow` on top of existing buys shrank 0.07 → 0.02 lots.

### GUARD 3b — DCA layer limit
```
own_layers >= MAX_DCA_LAYERS_PER_SYMBOL (3) → block further layers
```

### GUARD 4 — Auto-TP override + Reward:Risk ratio
```
sl_pips = |entry_mid − SL| / PIP_SIZE            # PIP_SIZE = 0.1 for gold
if sl_pips < SL_MIN_PIPS (50):
    for each TP closer than TP_ENFORCE_PIPS (70):
        TP = entry_mid ± 70×PIP_SIZE             # push out to minimum
tp_distance = |TP1_effective − entry_mid|
rr_ratio    = tp_distance / sl_distance
if rr_ratio < MIN_RR_RATIO (1.4): block
```
Note the RR uses **entry_mid**, never bid/ask.

### GUARD 5 — Spread
```
spread_pips = (ask − bid) / PIP_SIZE
if spread_pips > MAX_SPREAD_PIPS (5, live): block → retry next cycle
manual path: warn only, do not block
```

### GUARD 6 — Entry proximity
```
distance_pts  = max(0, max(entry_low − price, price − entry_high))
distance_pips = distance_pts / PIP_SIZE
if distance_pips > ENTRY_MAX_DISTANCE_PIPS (5): skip
skipped by design for L2+ (deeper entries are intentionally outside the zone)
```

### GUARD 7 — Lot calculation
```
lot = calculate_lot(...)                       # §4
if lot == 0: block ("cannot afford even MIN_LOT")
stack-reduce path additionally enforces new_lot ≥ MIN_LOT
```

### GUARD 8 — Source risk bucket
Per-source budget check (see §8). If the remaining bucket < MIN_LOT → blocked;
partial room → lot reduced automatically.

---

## 4. Lot sizing formula

From `core/risk.py`, exact:

```python
free_margin   = account.margin_free
equity        = account.equity                  # risk is % of EQUITY
sl_distance   = abs(entry_mid − sl)              # price units
sl_pips       = sl_distance / SL_PIP_SIZE        # SL_PIP_SIZE = 0.1 (gold)
sl_in_ticks   = sl_distance / tick_size
risk_per_lot  = sl_in_ticks × tick_value         # $ loss for 1.0 lot if SL hits

risk_amount   = equity × risk_percent            # 5% channel / 2.5% manual
raw_lot       = risk_amount / risk_per_lot

lot = clamp(raw_lot, MIN_LOT=0.01, MAX_LOT=5.00)
lot = round(round(lot / volume_step) × volume_step, 2)

if raw_lot < MIN_LOT: use MIN_LOT anyway (+ warning)
if lot × risk_per_lot > free_margin: FAIL (guard 7)
```

Worked example (live account):
```
equity=$1382.98  risk=2.5%  → $34.57
SL 50 pips → $5.00 move · tick_value $1/tick(0.01) → risk_per_lot=$500
raw = 34.57 / 500 = 0.0691 → round to step → 0.07 lots
```

Worst case loss if SL hit = `risk_percent × equity`, regardless of how many layers/splits.

---

## 5. Manual-trade extra checks

For `/goldbuynow` / `/goldsellnow` style instant entries:

1. **H1+H4 trend check** — both timeframes analysed (EMA9/21 + RSI14);
   if trend opposes the requested direction → command **cancelled** before execution.
2. **Fib retracement guard** — last opposite H1 swing must have pulled back into
   the 0–38.2% zone; otherwise warn (or block when configured).
3. **Fixed pip geometry** — SL/TP set from constants, not from a signal:
   `SL 50p · TP1 50p · TP2 80p`.
4. **SL/TP re-anchoring at fill** — because price can drift between command and
   execution, SL/TP distances are recomputed relative to the actual fill price
   before order_send, preventing "invalid stops" rejections.

---

## 6. Layered DCA runtime logic

When `LAYER_MODE=true` a signal becomes a *session* handled by the layer watcher:

```
total_lot     = calculate_lot() once at signal arrival (uses entry_mid)
actual_layers = min(LAYER_COUNT=7, int(total_lot / MIN_LOT))
layer_gap     = SL_pips × L2_GAP_RATIO (0.40)     # dynamic; fixed LAYER2_PIPS if ratio=0
max_by_sl     = 1 + int((sl_pips − 1) / layer_gap) # SL safety cap: no trigger may cross SL
actual_layers = min(actual_layers, max_by_sl)

L1 lot = total_lot × L1_LOT_RATIO (0.30)
L2+ lot = remaining 70% shared equally
per-layer sub-splits: tp_split = min(int(layer_lot / MIN_LOT), MAX_SUB_SPLITS=4)
sub-orders cycle the signal's TP list (TP1, TP2, TP1, …)
```

Runtime rules per cycle (30 s):
* **L1 fires** when price enters the zone (normal proximity guard applies).
* **L2+ fires** when price is ≥ k×gap beyond previous fill (deeper = better price).
* **Runway guard:** a layer trigger within `L2_MIN_RUNWAY_PIPS` (25 pips) of SL is skipped.
* A runtime re-check each tick also refuses triggers at/beyond SL.
* **Guard behaviour differs by layer:**
  * L1 blocked by non-spread guard → whole session ends (same as standard).
  * L1 spread-blocked → wait and retry next interval.
  * L2+ spread-blocked → retry; blocked by other guard → skip that layer slot, keep watching.
* **Breakeven hand-off:** when every upper layer's sub-orders close at TP,
  all deepest-layer orders get SL → entry (risk-free ride).

Risk preservation:
```
all layers SL hit  → total loss = RISK_PERCENT × equity   (unchanged)
only L1 filled     → exposure ≈ total_lot × 30% only
```

---

## 7. Profit Lock

Independent post-entry monitor, runs every 60 s on bot-placed positions only
(magic number `20250101`):

```
if position_profit_pips >= PROFIT_LOCK_PIPS (50):
    SL → entry price (breakeven)
    if current TP closer than PROFIT_LOCK_TP_PIPS (100) from entry:
        TP → entry ± 100×PIP_SIZE
```

Rules:
* Never tightens a TP that is already further away.
* Skips positions already at breakeven (no repeated modifications).
* With TP splitting each sub-order is evaluated independently — short TPs
  (< 50p) usually close naturally first; long TPs get upgraded by the lock.

---

## 8. Source risk buckets

Multiple signal sources each get an isolated budget so one noisy channel cannot
consume another's risk:

```
SOURCE_RISK_MODE=reduce        # partial bucket left → shrink lot; below MIN_LOT → block
SOURCE_CONFLICT_MODE=allow     # opposing sources may both trade
MAX_TOTAL_OPEN_RISK=0.10       # global ceiling across all sources combined
```

Guards evaluate per-source remaining budget, then the global cap, then normal
margin guards. Manual trades draw from their own separate bucket.

---

## 9. Config reference

Live production values:

| Variable | Value | Purpose |
|---|---|---|
| `ENV_MODE` | live | selects LIVE_* credential set |
| `RISK_PERCENT` | 0.05 | channel-signal risk per trade |
| `MANUAL_RISK_PERCENT` | 0.025 | manual-command risk (separate bucket) |
| `MAX_TOTAL_OPEN_RISK` | 0.10 | global open-risk ceiling |
| `MIN_MARGIN_LEVEL` | 300 | guard 2 threshold (%) |
| `MAX_SPREAD_PIPS` | 5 (live) | guard 5 threshold (pips) |
| `ENTRY_MAX_DISTANCE_PIPS` | 5 | guard 6 threshold (pips) |
| `MIN_RR_RATIO` | 1.4 | guard 4 threshold |
| `SL_MIN_PIPS` / `TP_ENFORCE_PIPS` | 50 / 70 | auto-TP override |
| `BLOCK_SAME_DIRECTION_STACK` / `STACK_MODE` | true / reduce | guard 3 behaviour |
| `MAX_DCA_LAYERS_PER_SYMBOL` | 3 | guard 3b limit |
| `MAX_DAILY_LOSS_USD` | 50 | circuit breaker |
| `SESSION_FILTER_ENABLED` + hours | true · 07–21 UTC | guard 0 window |
| `TRADE_REQUIRES_DB` / `MANUAL_TRADE_REQUIRES_DB` | true / false | DB gate scope |
| `MIN_LOT` / `MAX_LOT` | 0.01 / 5.00 | lot clamps |
| `SL_PIP_SIZE` | 0.1 | gold pip definition |
| `LAYER_MODE` | true | layered DCA on |
| `LAYER_COUNT` | 7 (dynamic) | max layers |
| `L2_GAP_RATIO` / `LAYER2_PIPS` | 0.40 / 35 | layer spacing |
| `L2_MIN_RUNWAY_PIPS` | 25 | runway guard |
| `L1_LOT_RATIO` | 0.30 | weighted first layer |
| `MAX_SUB_SPLITS` | 4 | sub-orders per layer |
| `PROFIT_LOCK_ENABLED/_PIPS/_TP_PIPS` | true / 50 / 100 | profit lock |
| `GOLD_SL/TP1/TP2_PIPS` | 50 / 50 / 80 | manual geometry |
| `SIGNAL_EXPIRY` / `WATCH_INTERVAL_SECS` | 1800 / 30 | standard-mode window |
| `MANUAL_TRADE_COOLDOWN_SECS` | 60 | manual spam guard |
| `STARTUP_COOLDOWN` | 60 | post-restart gate |

Magic number: **20250101** marks all bot-placed orders (used by poller & profit lock).

---

*Generated from production code (`core/mt5.py`, `core/risk.py`, `core/layer_watcher.py`,
`core/notifier.py`, `bot.py`) — thresholds match `.env` as deployed.*
