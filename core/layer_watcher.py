"""
core/layer_watcher.py
DCA-style layered entry watcher.

Places layers as price dips progressively deeper into/below the entry zone:
  L1 → price enters zone (standard proximity guard)
  L2 → price drops LAYER2_PIPS from L1 entry
  L3 → price drops 2×LAYER2_PIPS from L1 entry
  LN → price drops (N-1)×LAYER2_PIPS from L1 entry

Layer count is DYNAMIC:
  actual_layers = min(LAYER_COUNT, int(total_lot / MIN_LOT))
  → $200 account → 3 layers, $500 → 5, $1000+ → 7  (with LAYER_COUNT=7)

TP assignment:
  Upper layers (L1 … LN-1) → cycle through signal TPs in order
  Deepest layer (LN)       → furthest TP (free ride position)

Exit:
  When all upper layers close at TP → move deepest to breakeven.
  When deepest also closes → session complete.
  When all stopped → session ended.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field

import MetaTrader5 as mt5

from core.config import (
    YOUR_CHAT_ID, SIGNAL_EXPIRY, WATCH_INTERVAL_SECS,
    MT5_SYMBOL_SUFFIX, SL_PIP_SIZE, ENTRY_MAX_DISTANCE_PIPS,
    LAYER_COUNT, LAYER2_PIPS, MIN_LOT,
    SL_MIN_PIPS, TP_ENFORCE_PIPS,
)
from core.mt5   import mt5_connect, execute_trade, set_breakeven
from core.risk  import calculate_lot
from core.state import pending
from core.db    import upsert_signal

log = logging.getLogger(__name__)

# Registry exported for dashboard/future use
layer_sessions: dict[str, "LayerSession"] = {}


@dataclass
class LayerSession:
    signal:        object
    signal_id:     str
    actual_layers: int
    lot_per_layer: float
    effective_tps: list
    tickets: list = field(default_factory=list)   # int|None per layer
    entries: list = field(default_factory=list)   # float|None per layer
    state:   str  = "WAIT_L1"


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
    return effective_tps[-1]   # deepest → furthest TP (free ride)


def _layer_trigger_price(signal, l1_entry: float, layer_idx: int) -> float:
    """
    Absolute price that should trigger layer N (0-indexed).
    L1 (idx=0) uses proximity guard — no separate trigger.
    L2 (idx=1): l1_entry ∓ LAYER2_PIPS for buy/sell.
    LN (idx=N): l1_entry ∓ N×LAYER2_PIPS.
    """
    pip_dist = layer_idx * LAYER2_PIPS * SL_PIP_SIZE
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


# ── Main watcher coroutine ────────────────────────────────────────────────────

async def watch_layered_entry(signal, signal_id: str, bot):
    """
    DCA-style layered entry — runs as an asyncio background task.
    Places N layers as price dips deeper, then monitors for TP → BE.
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

    total_lot, lot_explanation = calculate_lot(signal)
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
    if LAYER2_PIPS > 0 and sl_pips_signal > 0:
        # How many DCA steps fit strictly inside the SL distance?
        safe_steps    = int((sl_pips_signal - 1) / LAYER2_PIPS)  # -1 pip buffer
        max_by_sl     = 1 + safe_steps                           # L1 + safe steps
        if actual_layers > max_by_sl:
            log.info(
                f"LayerWatcher [{signal_id}]: SL cap "
                f"{actual_layers}→{max_by_sl} layers "
                f"(SL={sl_pips_signal:.0f}p, step={LAYER2_PIPS}p)"
            )
            actual_layers = max(1, max_by_sl)

    lot_per_layer = max(MIN_LOT, round(total_lot / actual_layers, 2))

    session = LayerSession(
        signal=signal,
        signal_id=signal_id,
        actual_layers=actual_layers,
        lot_per_layer=lot_per_layer,
        effective_tps=effective_tps,
        tickets=[None] * actual_layers,
        entries=[None] * actual_layers,
    )
    layer_sessions[signal_id] = session

    log.info(
        f"LayerWatcher [{signal_id}]: "
        f"actual_layers={actual_layers} lot_per_layer={lot_per_layer} "
        f"total_lot={total_lot} layer2_pips={LAYER2_PIPS}"
    )

    next_idx = 0   # index of the next layer to place

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

        placed_tickets = [t for t in session.tickets if t is not None]

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

            # ── MONITORING: all layers placed → check for BE trigger ──────────
            if next_idx >= actual_layers:
                session.state = "MONITORING"

                # Find actual deepest layer (highest index with a ticket)
                deepest_idx = max(
                    (i for i in range(actual_layers) if session.tickets[i] is not None),
                    default=None
                )
                if deepest_idx is None:
                    await asyncio.sleep(WATCH_INTERVAL_SECS)
                    continue

                deepest_ticket = session.tickets[deepest_idx]
                upper_tickets  = [
                    session.tickets[i]
                    for i in range(deepest_idx)
                    if session.tickets[i] is not None
                ]

                still_open_set = set(still_open)

                if deepest_ticket not in still_open_set:
                    # Deepest layer also closed — all done
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
                        # All upper layers closed at TP → move deepest to BE
                        be_result = await asyncio.get_event_loop().run_in_executor(
                            None, set_breakeven, deepest_ticket
                        )
                        pending.pop(signal_id, None)
                        session.state = "DONE"
                        await _notify(bot, (
                            f"🔒 *L1–L{deepest_idx} TP secured → L{deepest_idx+1} free ride!*\n"
                            f"`{signal.symbol} {signal.direction.upper()}`\n"
                            f"#{deepest_ticket} moved to breakeven ♻️\n"
                            f"_{be_result}_"
                        ))
                        return

                await asyncio.sleep(WATCH_INTERVAL_SECS)
                continue

        # ── Layer placement phase ─────────────────────────────────────────────
        if next_idx < actual_layers:

            # Hard deadline check: no entry at all → expired
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
                    # L1: standard proximity guard (enter zone)
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

                    should_place = (
                        price <= trigger if signal.direction == "buy"
                        else price >= trigger
                    )

            if should_place:
                layer_num  = next_idx + 1
                own_tix    = [t for t in session.tickets[:next_idx] if t is not None]
                tp_val     = _tp_for_layer(next_idx, actual_layers, effective_tps)

                result = await asyncio.get_event_loop().run_in_executor(
                    None,
                    execute_trade,
                    signal,
                    signal_id,
                    lot_per_layer,      # lot_override
                    own_tix,            # own_tickets (exempt from stack guard)
                    tp_val,             # tp_override → single-order mode
                    next_idx > 0,       # skip_proximity for L2+
                    'layered_dca',      # entry_mode
                    layer_num,          # layer_num
                )

                if "Trade Executed" in result:
                    ticket = _get_latest_ticket(signal_id, exclude=own_tix)
                    session.tickets[next_idx] = ticket
                    session.entries[next_idx] = price
                    next_idx += 1

                    upsert_signal(signal_id, signal, status="executed")

                    if next_idx < actual_layers:
                        nxt_trigger = _layer_trigger_price(
                            signal, session.entries[0], next_idx
                        )
                        next_msg = (
                            f"\n📍 L{next_idx+1} triggers @ `{nxt_trigger:.2f}` "
                            f"({LAYER2_PIPS}p deeper)"
                        )
                    else:
                        next_msg = "\n🎯 All layers active — monitoring TPs"

                    await _notify(bot, (
                        f"📍 *Layer {layer_num}/{actual_layers} placed @ `{price:.2f}`*\n"
                        f"`{signal.symbol} {signal.direction.upper()}`\n"
                        f"Lot: `{lot_per_layer}` | TP: `{tp_val}` | #{ticket}"
                        + next_msg
                    ))

                elif "spread too wide" in result or "Market closed" in result:
                    log.info(
                        f"LayerWatcher [{signal_id}]: "
                        f"L{layer_num} spread wide or market closed — retrying next tick"
                    )
                    # No message — retry automatically next interval

                else:
                    # Guard blocked this layer
                    if next_idx == 0:
                        # L1 blocked — fatal, session over
                        pending.pop(signal_id, None)
                        upsert_signal(signal_id, signal, status="blocked")
                        session.state = "DONE"
                        await _notify(bot, (
                            f"🚫 *Layer 1 blocked — session ended*\n\n{result}"
                        ))
                        return
                    else:
                        # L2+ blocked — skip this layer slot, try next
                        log.info(
                            f"LayerWatcher [{signal_id}]: "
                            f"L{layer_num} blocked (skipped): {result.splitlines()[0]}"
                        )
                        session.tickets[next_idx] = None   # mark slot as skipped
                        next_idx += 1
                        await _notify(bot, (
                            f"⚠️ *L{layer_num}/{actual_layers} skipped (guard)*\n"
                            f"`{signal.symbol}` — watching for L{next_idx+1}"
                        ))

        await asyncio.sleep(WATCH_INTERVAL_SECS)
