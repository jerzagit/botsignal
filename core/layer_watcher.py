"""
core/layer_watcher.py
DCA-style layered entry watcher.

Places layers as price dips progressively deeper into/below the entry zone:
  L1 -> price enters zone (standard proximity guard)
  L2 -> price drops gap_pips from L1 entry (dynamic: sl_pips × L2_GAP_RATIO)
  L3 -> price drops 2×gap_pips from L1 entry
  LN -> price drops (N-1)×gap_pips from L1 entry

Runway guard: skip layer if trigger is < L2_MIN_RUNWAY_PIPS from SL.

Layer count is DYNAMIC:
  actual_layers = min(LAYER_COUNT, int(total_lot / MIN_LOT))
  -> $200 account -> 3 layers, $500 -> 5, $1000+ -> 7  (with LAYER_COUNT=7)

TP splitting (dynamic sub-splitting):
  Each layer is split into up to MAX_SUB_SPLITS sub-orders (default 4).
  tp_split = min(int(lot_per_layer / MIN_LOT), MAX_SUB_SPLITS)
  Sub-orders cycle through signal TPs: TP1, TP2, TP1, TP2
  Auto-scales: $200->2-3 splits, $500->4, $1000+->4 (capped)

Exit:
  When all upper layers' sub-orders close at TP -> move deepest to breakeven.
  When deepest also closes -> session complete.
  When all stopped -> session ended.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import MetaTrader5 as mt5

from core.config import (
    YOUR_CHAT_ID, SIGNAL_EXPIRY, WATCH_INTERVAL_SECS,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, ENTRY_MAX_DISTANCE_PIPS,
    LAYER_COUNT, LAYER2_PIPS, MIN_LOT, MAX_SUB_SPLITS,
    SL_MIN_PIPS, TP_ENFORCE_PIPS, L2_GAP_RATIO, L2_MIN_RUNWAY_PIPS,
    L1_LOT_RATIO, PROFIT_LOCK_ENABLED, PROFIT_LOCK_PIPS, PROFIT_LOCK_TP_PIPS,
    TRAIL_ENABLED, TRAIL_PIPS,
)
from core.mt5   import mt5_connect, execute_trade, set_breakeven, modify_sl_tp
from core.risk  import calculate_lot
from core.state import pending
from core.db    import upsert_signal

log = logging.getLogger(__name__)

# Registry exported for dashboard/future use
layer_sessions: dict[str, "LayerSession"] = {}


@dataclass
class LayerSession:
    signal:         object
    signal_id:      str
    actual_layers:  int
    lots_per_layer: list          # total lot per layer  [L1_lot, L2_lot, ...]
    effective_tps:  list
    sub_lots:       list          # sub-order lot per layer  [L1_sub, L2_sub, ...]
    tp_splits:      list          # sub-order count per layer [L1_splits, L2_splits, ...]
    tickets:        list = field(default_factory=list)   # list[list[int]] — sub-tickets per layer
    entries:        list = field(default_factory=list)   # float|None per layer
    locked_tickets: set  = field(default_factory=set)   # tickets already profit-locked
    trail_prices:   dict = field(default_factory=dict)  # ticket → best price seen (for trailing)
    state:          str  = "WAIT_L1"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _adjusted_tps(signal) -> list:
    """Mirror auto-TP logic from execute_trade so pre-calc TPs are consistent."""
    sl_distance  = abs(signal.entry_mid - signal.sl)
    sl_pips_calc = sl_distance / SL_PIP_SIZE
    effective    = list(signal.tps)
    if sl_pips_calc < SL_MIN_PIPS:
        min_tp_pts = TP_ENFORCE_PIPS * SL_PIP_SIZE
        new_tps = []
        for t in effective:
            tp_dist = abs(t - signal.entry_mid)
            if tp_dist < min_tp_pts:
                adjusted = round(
                    signal.entry_mid + min_tp_pts if signal.direction == "buy"
                    else signal.entry_mid - min_tp_pts, 2
                )
                new_tps.append(adjusted)
            else:
                new_tps.append(t)
        effective = new_tps
    return effective


def _get_price(symbol: str, direction: str):
    """Lightweight price fetch without disturbing caller's MT5 session."""
    if not mt5_connect():
        return None
    tick = mt5.symbol_info_tick(symbol)
    mt5.shutdown()
    if tick is None:
        return None
    return tick.ask if direction == "buy" else tick.bid


def _positions_open(tickets: list) -> list:
    """Return the subset of given tickets still open in MT5."""
    if not tickets:
        return []
    if not mt5_connect():
        return tickets   # fail-safe: assume open
    open_set = {p.ticket for p in (mt5.positions_get() or [])}
    mt5.shutdown()
    return [t for t in tickets if t in open_set]


def _tp_for_layer(idx: int, actual_layers: int, effective_tps: list) -> float:
    """
    Assign TPs across layers.
    Upper layers cycle through TPs in order; deepest layer gets furthest TP.
    """
    if not effective_tps:
        return 0.0
    if actual_layers == 1 or idx < actual_layers - 1:
        return effective_tps[idx % len(effective_tps)]
    return effective_tps[-1]   # deepest -> furthest TP (free ride)


def _tp_for_sub_order(tp_idx: int, tp_split: int, effective_tps: list) -> float:
    """
    Distribute sub-order TPs in thirds:
      1×TP1 (secure quick),  ceil((N-1)/2)×TP2 (medium),  rest=Runner (no TP).

    | splits | TP1 | TP2 | Runner |
    |--------|-----|-----|--------|
    |   1    |  1  |  0  |   0    |
    |   2    |  1  |  1  |   0    |
    |   3    |  1  |  1  |   1    |
    |   4    |  1  |  2  |   1    |
    """
    if not effective_tps:
        return 0.0
    if tp_split <= 1:
        return effective_tps[0]
    if tp_split == 2:
        return effective_tps[min(tp_idx, len(effective_tps) - 1)]
    # 3+ splits: 1×TP1, ceil((N-1)/2)×TP2, rest=runner
    tp2_count = -(-(tp_split - 1) // 2)   # ceil division
    if tp_idx == 0:
        return effective_tps[0]                               # TP1
    elif tp_idx <= tp2_count:
        return effective_tps[min(1, len(effective_tps) - 1)]  # TP2
    else:
        return 0.0                                            # Runner (no TP)


def _effective_gap_pips(signal) -> int:
    """
    Calculate layer gap in pips.
    If L2_GAP_RATIO > 0: gap = sl_pips × ratio (dynamic, adapts to SL size)
    Else: fall back to fixed LAYER2_PIPS (legacy).
    """
    if L2_GAP_RATIO > 0:
        sl_pips = abs(signal.entry_mid - signal.sl) / SL_PIP_SIZE
        return max(1, int(sl_pips * L2_GAP_RATIO))
    return LAYER2_PIPS


def _layer_trigger_price(signal, l1_entry: float, layer_idx: int) -> float:
    """
    Absolute price that should trigger layer N (0-indexed).
    L1 (idx=0) uses proximity guard — no separate trigger.
    L2+: l1_entry ∓ N × gap_pips (dynamic or fixed).
    """
    gap_pips = _effective_gap_pips(signal)
    pip_dist = layer_idx * gap_pips * SL_PIP_SIZE
    return (l1_entry - pip_dist) if signal.direction == "buy" else (l1_entry + pip_dist)


def _get_latest_ticket(signal_id: str, exclude: list = None) -> int | None:
    """DB: newest ticket for this signal not already in exclude list."""
    exclude = set(exclude or [])
    try:
        from core.db import get_conn
        conn = get_conn()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT ticket FROM trades WHERE signal_id = %s ORDER BY id DESC",
                (signal_id,)
            )
            rows = cur.fetchall()
        conn.close()
        for row in rows:
            t = row["ticket"]
            if t not in exclude:
                return t
    except Exception as e:
        log.warning(f"_get_latest_ticket failed: {e}")
    return None


async def _notify(bot, text: str):
    try:
        await bot.send_message(
            chat_id=YOUR_CHAT_ID,
            text=text,
            parse_mode="Markdown"
        )
    except Exception as e:
        log.warning(f"LayerWatcher _notify failed: {e}")


async def _check_profit_lock(session: "LayerSession", bot) -> None:
    """
    For every open sub-order not yet locked:
      if profit >= PROFIT_LOCK_PIPS → move SL to breakeven, tighten TP to PROFIT_LOCK_TP_PIPS.
    Sends one Telegram notification per batch of locks triggered this cycle.
    """
    if not PROFIT_LOCK_ENABLED:
        return

    placed_tickets = [t for sub in session.tickets for t in sub]
    if not placed_tickets:
        return

    unlocked = [t for t in placed_tickets if t not in session.locked_tickets]
    if not unlocked:
        return

    if not mt5_connect():
        return

    positions = {p.ticket: p for p in (mt5.positions_get() or [])}
    mt5.shutdown()

    signal = session.signal
    locked_this_cycle = []

    for ticket in unlocked:
        pos = positions.get(ticket)
        if pos is None:
            continue

        profit_pips = (
            (pos.price_open - pos.price_current) / SL_PIP_SIZE
            if signal.direction == "sell"
            else (pos.price_current - pos.price_open) / SL_PIP_SIZE
        )

        if profit_pips < PROFIT_LOCK_PIPS:
            continue

        new_sl = pos.price_open
        if PROFIT_LOCK_TP_PIPS > 0:
            new_tp = round(
                pos.price_open - PROFIT_LOCK_TP_PIPS * SL_PIP_SIZE
                if signal.direction == "sell"
                else pos.price_open + PROFIT_LOCK_TP_PIPS * SL_PIP_SIZE,
                2
            )
        else:
            new_tp = pos.tp

        result = modify_sl_tp(ticket, new_sl=new_sl, new_tp=new_tp)
        if "❌" not in result:
            session.locked_tickets.add(ticket)
            locked_this_cycle.append((ticket, profit_pips, new_tp))
            log.info(
                f"ProfitLock [{session.signal_id}]: #{ticket} "
                f"{profit_pips:.0f}p profit → SL=BE TP={new_tp}"
            )
        else:
            log.warning(f"ProfitLock [{session.signal_id}]: #{ticket} modify failed: {result}")

    if locked_this_cycle:
        for ticket, profit_pips, new_tp in locked_this_cycle:
            pos = positions.get(ticket)
            if pos is not None:
                session.trail_prices[ticket] = pos.price_current

        lines = "\n".join(
            f"  `#{t}` — `{p:.0f}p` profit → TP `{tp}`"
            for t, p, tp in locked_this_cycle
        )
        await _notify(bot, (
            f"🔒 *Profit Lock — {len(locked_this_cycle)} position(s) secured!*\n"
            f"`{signal.symbol} {signal.direction.upper()}`\n"
            f"{lines}\n"
            f"_SL moved to breakeven | TP tightened to {PROFIT_LOCK_TP_PIPS}p_"
        ))


async def _trail_stops(session: "LayerSession", bot) -> None:
    """
    For each locked ticket: if price has moved TRAIL_PIPS further in profit direction
    since last check, trail the SL to stay TRAIL_PIPS behind current price.
    Only moves SL in the profitable direction — never backwards.
    """
    if not TRAIL_ENABLED or not session.trail_prices:
        return

    if not mt5_connect():
        return

    positions = {p.ticket: p for p in (mt5.positions_get() or [])}
    mt5.shutdown()

    signal    = session.signal
    trail_pts = TRAIL_PIPS * SL_PIP_SIZE
    trailed   = []

    for ticket, best_price in list(session.trail_prices.items()):
        pos = positions.get(ticket)
        if pos is None:
            session.trail_prices.pop(ticket, None)
            continue

        current = pos.price_current

        if signal.direction == "sell":
            if current < best_price - trail_pts:
                new_sl = round(current + trail_pts, 2)
                if new_sl >= pos.sl:   # never move SL backwards
                    continue
                result = modify_sl_tp(ticket, new_sl=new_sl)
                if "❌" not in result:
                    session.trail_prices[ticket] = current
                    trailed.append((ticket, new_sl))
                    log.info(f"Trail [{session.signal_id}]: #{ticket} price={current} → SL={new_sl}")
        else:
            if current > best_price + trail_pts:
                new_sl = round(current - trail_pts, 2)
                if new_sl <= pos.sl:   # never move SL backwards
                    continue
                result = modify_sl_tp(ticket, new_sl=new_sl)
                if "❌" not in result:
                    session.trail_prices[ticket] = current
                    trailed.append((ticket, new_sl))
                    log.info(f"Trail [{session.signal_id}]: #{ticket} price={current} → SL={new_sl}")

    if trailed:
        lines = "\n".join(f"  `#{t}` → SL `{sl}`" for t, sl in trailed)
        await _notify(bot, (
            f"📈 *Trailing Stop updated — {len(trailed)} position(s)*\n"
            f"`{signal.symbol} {signal.direction.upper()}`\n"
            f"{lines}\n"
            f"_Trailing {TRAIL_PIPS}p behind price_"
        ))


# ── Main watcher coroutine ────────────────────────────────────────────────────

async def watch_layered_entry(signal, signal_id: str, bot,
                              entry_mode: str = "layered_dca",
                              skip_proximity: bool = False):
    """
    DCA-style layered entry — runs as an asyncio background task.
    Places N layers as price dips deeper, then monitors for TP -> BE.
    entry_mode: 'layered_dca' (signal-based) or 'mapped' (zone auto-entry).
    skip_proximity: if True, execute immediately without waiting for price to enter zone.
    """
    symbol   = signal.symbol + MT5_SYMBOL_SUFFIX
    deadline = signal.created_at + SIGNAL_EXPIRY
    zone_str = (
        f"{signal.entry_low}"
        if signal.entry_low == signal.entry_high
        else f"{signal.entry_low}–{signal.entry_high}"
    )

    log.info(
        f"LayerWatcher [{signal_id}]: starting "
        f"{signal.symbol} {signal.direction.upper()} zone={zone_str}"
    )

    # ── Pre-flight: total lot + dynamic layer count ───────────────────────────
    if not mt5_connect():
        await _notify(bot, f"❌ LayerWatcher [{signal_id}]: MT5 connect failed at startup")
        return

    # Manual trades use separate risk allocation (MANUAL_RISK_PERCENT)
    risk_override = None
    if entry_mode == "manual":
        from core.config import MANUAL_RISK_PERCENT
        risk_override = MANUAL_RISK_PERCENT

    total_lot, lot_explanation = calculate_lot(signal, risk_override=risk_override)
    effective_tps = _adjusted_tps(signal)
    mt5.shutdown()

    if total_lot == 0.0:
        await _notify(bot, f"❌ Lot calc failed — layered session aborted\n{lot_explanation}")
        return

    # Dynamic layer count: capped by LAYER_COUNT and by how many MIN_LOT slots exist
    actual_layers = min(LAYER_COUNT, max(1, int(total_lot / MIN_LOT)))

    # ── SL safety cap: ensure no layer trigger crosses the stop loss ──────────
    # For BUY: L(N) trigger = entry − N×LAYER2_PIPS — must stay > signal.sl
    # For SELL: L(N) trigger = entry + N×LAYER2_PIPS — must stay < signal.sl
    # Safe condition: N × LAYER2_PIPS < sl_pips  (strict — never touch SL)
    sl_pips_signal = abs(signal.entry_mid - signal.sl) / SL_PIP_SIZE
    effective_gap = _effective_gap_pips(signal)
    if effective_gap > 0 and sl_pips_signal > 0:
        # How many DCA steps fit strictly inside the SL distance?
        safe_steps    = int((sl_pips_signal - 1) / effective_gap)  # -1 pip buffer
        max_by_sl     = 1 + safe_steps                             # L1 + safe steps
        if actual_layers > max_by_sl:
            log.info(
                f"LayerWatcher [{signal_id}]: SL cap "
                f"{actual_layers}->{max_by_sl} layers "
                f"(SL={sl_pips_signal:.0f}p, gap={effective_gap}p)"
            )
            actual_layers = max(1, max_by_sl)

    # ── Weighted lot split: L1=30%, L2+= remaining 70% split equally ────────
    if actual_layers == 1:
        lots_per_layer = [total_lot]
    else:
        l1_lot  = max(MIN_LOT, round(total_lot * L1_LOT_RATIO, 2))
        remaining = max(MIN_LOT, round(total_lot - l1_lot, 2))
        ln_lot  = max(MIN_LOT, round(remaining / (actual_layers - 1), 2))
        lots_per_layer = [l1_lot] + [ln_lot] * (actual_layers - 1)

    # ── Dynamic sub-splitting per layer ──────────────────────────────────────
    sub_lots  = []
    tp_splits = []
    for lpl in lots_per_layer:
        max_affordable = max(1, int(lpl / MIN_LOT))
        ts = min(max_affordable, MAX_SUB_SPLITS)
        sub_lots.append(max(MIN_LOT, round(lpl / ts, 2)))
        tp_splits.append(ts)

    session = LayerSession(
        signal=signal,
        signal_id=signal_id,
        actual_layers=actual_layers,
        lots_per_layer=lots_per_layer,
        effective_tps=effective_tps,
        sub_lots=sub_lots,
        tp_splits=tp_splits,
        tickets=[[] for _ in range(actual_layers)],
        entries=[None] * actual_layers,
    )
    layer_sessions[signal_id] = session

    log.info(
        f"LayerWatcher [{signal_id}]: "
        f"actual_layers={actual_layers} lots_per_layer={lots_per_layer} "
        f"sub_lots={sub_lots} tp_splits={tp_splits} "
        f"total_lot={total_lot} layer_gap={effective_gap}p "
        f"(ratio={L2_GAP_RATIO}, sl={sl_pips_signal:.0f}p) "
        f"min_runway={L2_MIN_RUNWAY_PIPS}p L1_ratio={L1_LOT_RATIO}"
    )

    next_idx = 0   # index of the next layer to place
    stack_notified = False   # only send "waiting for stack" message once

    # ── Main loop ─────────────────────────────────────────────────────────────
    while True:
        # Allow external cancellation (close alerts etc.)
        if signal_id not in pending:
            log.info(f"LayerWatcher [{signal_id}]: cancelled externally — stopping")
            session.state = "DONE"
            return

        price = await asyncio.get_event_loop().run_in_executor(
            None, _get_price, symbol, signal.direction
        )

        placed_tickets = [t for sub in session.tickets for t in sub]

        # ── Profit lock + trailing stop ───────────────────────────────────────
        if placed_tickets:
            await _check_profit_lock(session, bot)
            await _trail_stops(session, bot)

        # ── Check for stop-outs on placed layers ──────────────────────────────
        if placed_tickets:
            still_open = await asyncio.get_event_loop().run_in_executor(
                None, _positions_open, placed_tickets
            )

            if not still_open:
                # Every layer was stopped out
                pending.pop(signal_id, None)
                upsert_signal(signal_id, signal, status="closed")
                session.state = "DONE"
                await _notify(bot, (
                    f"❌ *All {len(placed_tickets)} layer(s) stopped out*\n"
                    f"`{signal.symbol} {signal.direction.upper()}`\n"
                    f"_Session ended._"
                ))
                return

            # ── MONITORING: all layers placed -> check for BE trigger ──────────
            if next_idx >= actual_layers:
                session.state = "MONITORING"

                # Find actual deepest layer (highest index with sub-tickets)
                deepest_idx = max(
                    (i for i in range(actual_layers) if session.tickets[i]),
                    default=None
                )
                if deepest_idx is None:
                    await asyncio.sleep(WATCH_INTERVAL_SECS)
                    continue

                deepest_tickets = session.tickets[deepest_idx]
                upper_tickets   = [
                    t for i in range(deepest_idx)
                    for t in session.tickets[i]
                ]

                still_open_set = set(still_open)

                # Check if ALL deepest sub-tickets are closed
                deepest_open = [t for t in deepest_tickets if t in still_open_set]
                if not deepest_open:
                    # Deepest layer fully closed — all done
                    pending.pop(signal_id, None)
                    upsert_signal(signal_id, signal, status="closed")
                    session.state = "DONE"
                    await _notify(bot, (
                        f"✅ *All layers closed — session complete*\n"
                        f"`{signal.symbol} {signal.direction.upper()}`"
                    ))
                    return

                if upper_tickets:
                    upper_still_open = [t for t in upper_tickets if t in still_open_set]
                    if not upper_still_open:
                        # All upper layers closed at TP -> move ALL deepest sub-tickets to BE
                        be_results = []
                        for dt in deepest_open:
                            be_result = await asyncio.get_event_loop().run_in_executor(
                                None, set_breakeven, dt
                            )
                            be_results.append(f"#{dt}: {be_result}")
                        pending.pop(signal_id, None)
                        session.state = "DONE"
                        be_text = "\n".join(be_results)
                        await _notify(bot, (
                            f"🔒 *L1–L{deepest_idx} TP secured -> L{deepest_idx+1} free ride!*\n"
                            f"`{signal.symbol} {signal.direction.upper()}`\n"
                            f"{len(deepest_open)} position(s) moved to breakeven ♻️\n"
                            f"_{be_text}_"
                        ))
                        return

                await asyncio.sleep(WATCH_INTERVAL_SECS)
                continue

        # ── Layer placement phase ─────────────────────────────────────────────
        if next_idx < actual_layers:

            # Hard deadline check: no entry at all -> expired
            if time.time() >= deadline and next_idx == 0:
                pending.pop(signal_id, None)
                upsert_signal(signal_id, signal, status="expired")
                session.state = "DONE"
                await _notify(bot, (
                    f"⏰ *Layered session expired — no entry made*\n"
                    f"`{signal.symbol} {signal.direction.upper()}`\n"
                    f"_Watched {SIGNAL_EXPIRY // 60} min — price never reached zone._"
                ))
                return

            # Soft deadline: L1 placed but time ran out for further layers
            if time.time() >= deadline and next_idx > 0:
                log.info(
                    f"LayerWatcher [{signal_id}]: deadline reached, "
                    f"{next_idx}/{actual_layers} layers placed — monitoring"
                )
                next_idx = actual_layers   # skip to monitoring
                await asyncio.sleep(WATCH_INTERVAL_SECS)
                continue

            # Decide whether to trigger next layer
            should_place = False
            if price is not None:
                if next_idx == 0:
                    # L1: standard proximity guard (enter zone) or skip for immediate execution
                    if skip_proximity:
                        should_place = True
                    else:
                        dist = max(0.0, max(signal.entry_low - price, price - signal.entry_high))
                        should_place = (dist / SL_PIP_SIZE) <= ENTRY_MAX_DISTANCE_PIPS
                else:
                    # L2+: price dipped N×LAYER2_PIPS from L1 entry
                    trigger = _layer_trigger_price(signal, session.entries[0], next_idx)

                    # Runtime SL safety: trigger must not cross the stop loss
                    # (actual L1 fill may differ slightly from entry_mid)
                    beyond_sl = (
                        trigger <= signal.sl if signal.direction == "buy"
                        else trigger >= signal.sl
                    )
                    if beyond_sl:
                        log.info(
                            f"LayerWatcher [{signal_id}]: L{next_idx+1} trigger "
                            f"{trigger:.2f} at/beyond SL {signal.sl} — "
                            f"capping to {next_idx} layers"
                        )
                        next_idx = actual_layers   # jump to monitoring
                        await asyncio.sleep(WATCH_INTERVAL_SECS)
                        continue

                    # ── Runway guard: block if trigger too close to SL ──
                    runway_pips = abs(trigger - signal.sl) / SL_PIP_SIZE
                    if runway_pips < L2_MIN_RUNWAY_PIPS:
                        _lnum = next_idx + 1
                        log.info(
                            f"LayerWatcher [{signal_id}]: L{_lnum} runway "
                            f"{runway_pips:.0f}p < min {L2_MIN_RUNWAY_PIPS}p — "
                            f"skipping (trigger={trigger:.2f}, SL={signal.sl})"
                        )
                        session.tickets[next_idx] = []   # mark slot as skipped
                        next_idx += 1
                        await _notify(bot, (
                            f"⏭️ *L{_lnum}/{actual_layers} skipped — "
                            f"runway too short ({runway_pips:.0f}p < {L2_MIN_RUNWAY_PIPS}p)*\n"
                            f"`{signal.symbol} {signal.direction.upper()}`\n"
                            f"_Trigger {trigger:.2f} only {runway_pips:.0f}p from SL {signal.sl}_"
                        ))
                        await asyncio.sleep(WATCH_INTERVAL_SECS)
                        continue

                    should_place = (
                        price <= trigger if signal.direction == "buy"
                        else price >= trigger
                    )

            if should_place:
                layer_num  = next_idx + 1
                # Flatten all previous layers' sub-tickets for own_tickets
                own_tix    = [t for sub in session.tickets[:next_idx] for t in sub]

                # ── TP-split: place sub_lot × tp_split orders (one per TP) ─────
                layer_tickets = []
                layer_blocked = None   # first non-spread block message
                spread_retry  = False

                for tp_idx in range(session.tp_splits[next_idx]):
                    tp_val  = _tp_for_sub_order(tp_idx, session.tp_splits[next_idx], effective_tps)
                    all_own = own_tix + layer_tickets   # include already-placed sub-orders

                    _skip_rr = (entry_mode == "manual")
                    result = await asyncio.get_event_loop().run_in_executor(
                        None,
                        execute_trade,
                        signal,
                        signal_id,
                        session.sub_lots[next_idx],    # lot_override (sub-lot)
                        all_own,            # own_tickets (exempt from stack guard)
                        tp_val,             # tp_override -> single-order mode
                        next_idx > 0,       # skip_proximity for L2+
                        entry_mode,         # entry_mode ('layered_dca' or 'mapped')
                        layer_num,          # layer_num
                        _skip_rr,           # skip_rr_check for manual trades
                        entry_mode == "manual",  # skip_session for manual /buynow /sellnow
                    )

                    if "Trade Executed" in result:
                        ticket = _get_latest_ticket(signal_id, exclude=all_own)
                        if ticket:
                            layer_tickets.append(ticket)
                    elif "spread too wide" in result or "Market closed" in result:
                        spread_retry = True
                        break   # retry whole layer next cycle
                    else:
                        layer_blocked = result
                        break   # guard blocked — stop placing more sub-orders

                if spread_retry:
                    log.info(
                        f"LayerWatcher [{signal_id}]: "
                        f"L{layer_num} spread wide or market closed — retrying next tick"
                    )
                    # No message — retry automatically next interval

                elif layer_tickets:
                    # At least some sub-orders placed successfully
                    stack_notified = False   # reset for any future stack waits
                    session.tickets[next_idx] = layer_tickets
                    session.entries[next_idx] = price
                    next_idx += 1

                    upsert_signal(signal_id, signal, status="executed")

                    if next_idx < actual_layers:
                        nxt_trigger = _layer_trigger_price(
                            signal, session.entries[0], next_idx
                        )
                        gap_display = int(abs(nxt_trigger - session.entries[0]) / SL_PIP_SIZE)
                        nxt_runway = abs(nxt_trigger - signal.sl) / SL_PIP_SIZE
                        next_msg = (
                            f"\n📍 L{next_idx+1} triggers @ `{nxt_trigger:.2f}` "
                            f"({gap_display}p deeper, {nxt_runway:.0f}p runway)"
                        )
                    else:
                        next_msg = "\n🎯 All layers active — monitoring TPs"

                    # Build TP breakdown label: TP1 × N | TP2 × N | Runner × N
                    tp_counts = {}
                    for i in range(len(layer_tickets)):
                        tv = _tp_for_sub_order(i, session.tp_splits[next_idx - 1], effective_tps)
                        if tv == 0.0:
                            tp_counts["Runner"] = tp_counts.get("Runner", 0) + 1
                        else:
                            # Find which TP index this matches
                            tp_num = next(
                                (j + 1 for j, t in enumerate(effective_tps) if t == tv),
                                "?"
                            )
                            key = f"TP{tp_num}:`{tv}`"
                            tp_counts[key] = tp_counts.get(key, 0) + 1
                    tp_labels = " | ".join(
                        f"{k} x {v}" for k, v in tp_counts.items()
                    )
                    tix_str = ", ".join(f"#{t}" for t in layer_tickets)
                    await _notify(bot, (
                        f"📍 *Layer {layer_num}/{actual_layers} placed @ `{price:.2f}`*\n"
                        f"`{signal.symbol} {signal.direction.upper()}`\n"
                        f"Lot: `{session.sub_lots[next_idx - 1]} × {len(layer_tickets)}` | {tp_labels}\n"
                        f"Tickets: {tix_str}"
                        + next_msg
                    ))

                elif layer_blocked:
                    # Guard blocked — no sub-orders placed
                    if next_idx == 0:
                        # Stack guard on L1: retry each interval until clear or deadline
                        if "position(s) at risk" in layer_blocked:
                            log.info(
                                f"LayerWatcher [{signal_id}]: "
                                f"L1 stack blocked — retrying next tick"
                            )
                            if not stack_notified:
                                stack_notified = True
                                await _notify(bot, (
                                    f"⏳ *Layer 1 waiting — stack guard*\n"
                                    f"`{signal.symbol} {signal.direction.upper()}`\n"
                                    f"_Existing {signal.direction.upper()} position at risk. "
                                    f"Will auto-enter when it closes or hits breakeven._"
                                ))
                        else:
                            # Any other guard on L1 — fatal, session over
                            pending.pop(signal_id, None)
                            upsert_signal(signal_id, signal, status="blocked")
                            session.state = "DONE"
                            await _notify(bot, (
                                f"🚫 *Layer 1 blocked — session ended*\n\n{layer_blocked}"
                            ))
                            return
                    else:
                        # L2+ blocked — skip this layer slot, try next
                        log.info(
                            f"LayerWatcher [{signal_id}]: "
                            f"L{layer_num} blocked (skipped): {layer_blocked.splitlines()[0]}"
                        )
                        session.tickets[next_idx] = []   # mark slot as skipped
                        next_idx += 1
                        await _notify(bot, (
                            f"⚠️ *L{layer_num}/{actual_layers} skipped (guard)*\n"
                            f"`{signal.symbol}` — watching for L{next_idx+1}"
                        ))

        await asyncio.sleep(WATCH_INTERVAL_SECS)
