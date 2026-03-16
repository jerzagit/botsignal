"""
DCA Layered Entry Simulation  — matches exact bot logic
Signal: BUY XAUUSD zone 5081-5085, SL 5079, TP 5090, $1000 account
"""
import sys
sys.stdout.reconfigure(encoding="utf-8")

# ── Signal ──────────────────────────────────────────────────────────────────
ZONE_HIGH   = 5085.0
ZONE_LOW    = 5081.0
SL          = 5079.0
TP_HAFIZ    = 5090.0
DIRECTION   = "buy"

# ── Account & Config (.env) ──────────────────────────────────────────────────
ACCOUNT       = 1000.0
RISK_PCT      = 0.10
MIN_LOT       = 0.01
VOL_STEP      = 0.01
MAX_LAYERS    = 7
LAYER2_PIPS   = 35
PIP_SIZE      = 0.1        # 1 pip = $0.1 price unit (XAUUSD)
PIP_VALUE     = 10.0       # $10 per pip per 1.0 standard lot (100 oz × $0.1)
SL_MIN_PIPS   = 50
TP_ENFORCE    = 70
MIN_RR        = 1.4

SEP  = "=" * 66
SEP2 = "-" * 66

# ────────────────────────────────────────────────────────────────────────────
# Step 1 — RR guard + auto-TP  (uses entry_mid — same as execute_trade)
# ────────────────────────────────────────────────────────────────────────────
entry_mid   = round((ZONE_LOW + ZONE_HIGH) / 2, 2)   # 5083.0

sl_pips_mid = round((entry_mid - SL) / PIP_SIZE, 1)  # 40 pips (from MID to SL)

auto_tp = sl_pips_mid < SL_MIN_PIPS                  # 40 < 50 → YES
if auto_tp:
    eff_tp   = round(entry_mid + TP_ENFORCE * PIP_SIZE, 2)   # 5083 + 7.0 = 5090
    tp_note  = f"SL {sl_pips_mid:.0f}p < {SL_MIN_PIPS}p floor → auto-set to {TP_ENFORCE}p from mid"
else:
    eff_tp   = TP_HAFIZ
    tp_note  = "Hafiz TP used as-is"

tp_pips_mid = round((eff_tp - entry_mid) / PIP_SIZE, 1)   # 70 pips (from MID to TP)
rr_mid      = round(tp_pips_mid / sl_pips_mid, 2)          # 1.75
rr_ok       = rr_mid >= MIN_RR

# ────────────────────────────────────────────────────────────────────────────
# Step 2 — Lot calculation  (uses entry_mid SL — same as risk.py)
# ────────────────────────────────────────────────────────────────────────────
risk_amount  = ACCOUNT * RISK_PCT                           # $100
risk_per_lot = sl_pips_mid * PIP_VALUE                      # 40p × $10 = $400/lot
total_lot    = round(risk_amount / risk_per_lot, 2)         # 0.25 lots

# ────────────────────────────────────────────────────────────────────────────
# Step 3 — Layer count  (SL cap also uses entry_mid SL pips)
# ────────────────────────────────────────────────────────────────────────────
by_margin  = min(MAX_LAYERS, max(1, int(total_lot / MIN_LOT)))
safe_steps = int((sl_pips_mid - 1) / LAYER2_PIPS)            # uses MID sl pips
max_by_sl  = max(1, 1 + safe_steps)
actual_lay = min(by_margin, max_by_sl)
lot_each   = int(total_lot / actual_lay / VOL_STEP) * VOL_STEP

# ────────────────────────────────────────────────────────────────────────────
# Step 4 — Actual execution prices  (market orders at current ask)
#   L1: price just entered zone at ZONE_HIGH  → ask = 5085
#   L2: price dipped 35p from L1 entry        → ask = 5085 − 3.5 = 5081.5
# ────────────────────────────────────────────────────────────────────────────
eff_tps = [eff_tp]   # single TP signal

def tp_for_layer(idx, total):
    if idx == total - 1: return eff_tps[-1]
    return eff_tps[idx % len(eff_tps)]

layers = []
for i in range(actual_lay):
    exec_price = round(ZONE_HIGH - i * LAYER2_PIPS * PIP_SIZE, 2)   # actual ask
    tp         = tp_for_layer(i, actual_lay)
    tp_p       = round((tp - exec_price) / PIP_SIZE, 1)
    sl_p       = round((exec_price - SL)  / PIP_SIZE, 1)
    pnl_tp     = round(lot_each * tp_p * PIP_VALUE, 2)
    pnl_sl     = round(lot_each * sl_p * PIP_VALUE, 2)
    layers.append({"n": i+1, "exec": exec_price, "tp": tp,
                   "tp_p": tp_p, "sl_p": sl_p,
                   "pnl_tp": pnl_tp, "pnl_sl": pnl_sl})

# ─── Print: Signal breakdown ─────────────────────────────────────────────────
print(SEP)
print("  BUY XAUUSD  —  Layered DCA Analysis  ($1,000 account)")
print(SEP)
print(f"  Hafiz signal:  BUY {ZONE_LOW}–{ZONE_HIGH}  |  SL {SL}  |  TP {TP_HAFIZ}")
print()
print("  ★ How the bot reads this signal:")
print(f"    entry_mid  = ({ZONE_LOW} + {ZONE_HIGH}) / 2 = {entry_mid}")
print(f"    SL pips    = ({entry_mid} − {SL}) / {PIP_SIZE} = {sl_pips_mid} pips  [RR + lot guard uses this]")
print(f"    auto-TP    = {auto_tp}  ({tp_note})")
print(f"    eff TP     = {eff_tp}  ({tp_pips_mid} pips from mid)")
print(f"    RR check   = {tp_pips_mid}p / {sl_pips_mid}p = {rr_mid}  ({'PASS ✓' if rr_ok else 'FAIL ✗'}, min = {MIN_RR})")
print()
print("  ★ Lot & layers:")
print(f"    risk budget = ${ACCOUNT:,.0f} × {int(RISK_PCT*100)}% = ${risk_amount:.0f}")
print(f"    lot         = ${risk_amount:.0f} / ({sl_pips_mid}p × ${PIP_VALUE}/pip/lot) = {total_lot} lots")
print(f"    by margin   = min({MAX_LAYERS}, {int(total_lot/MIN_LOT)}) = {by_margin} layers allowed")
print(f"    SL cap      = min(7, {max_by_sl}) = {actual_lay} layers  (SL only {sl_pips_mid}p from mid)")
print(f"    lot/layer   = {total_lot} / {actual_lay} = {lot_each} lots each")
print()
print(f"  {'Lyr':<4} {'Exec price':<12} {'Trigger condition':<28} {'SL pips':>8} {'TP':>8} {'TP pips':>8}")
print(f"  {SEP2}")
for lay in layers:
    if lay["n"] == 1:
        trig = "price enters zone (immediate)"
    else:
        depth = (lay["n"]-1) * LAYER2_PIPS
        trig  = f"price dips {depth}p from L1 ({lay['exec']})"
    print(f"  L{lay['n']:<3} {lay['exec']:<12} {trig:<28} {lay['sl_p']:>6}p   {lay['tp']:>8} {lay['tp_p']:>6}p")

# ─── Scenario A ──────────────────────────────────────────────────────────────
l1 = layers[0]
print()
print(SEP)
print("  SCENARIO A  —  Zone respected immediately. Price goes straight to TP.")
print("                 L2 never fires.")
print(SEP)
print(f"  Price drops to {ZONE_HIGH} → enters zone")
print(f"  L1 placed  : {lot_each} lots @ {l1['exec']}  |  SL {SL}  |  TP {eff_tp}")
print(f"  Price rallies to {eff_tp}  ← TP hit")
print(f"  L2 trigger ({layers[1]['exec'] if actual_lay>1 else 'N/A'}) never reached.")
print()
print(f"  Profit = {lot_each} lots × {l1['tp_p']} pips × ${PIP_VALUE}/pip/lot")
print(f"         = ${l1['pnl_tp']:,.2f}")
print()
print(f"  (Worst case if SL hit: {lot_each}L × {l1['sl_p']}p × ${PIP_VALUE} = −${l1['pnl_sl']:,.2f})")

# ─── Scenario B ──────────────────────────────────────────────────────────────
if actual_lay >= 2:
    l2 = layers[1]
    scB_profit = round(l1["pnl_tp"] + l2["pnl_tp"], 2)
    scB_loss   = round(l1["pnl_sl"] + l2["pnl_sl"], 2)
    avg_entry  = round((l1["exec"] + l2["exec"]) / 2, 2)

    print()
    print(SEP)
    print("  SCENARIO B  —  Market goes sideways, dips to L2, then recovers to TP.")
    print(SEP)
    print(f"  Price drops to {ZONE_HIGH}  → enters zone")
    print(f"  L1 placed  : {lot_each} lots @ {l1['exec']}  |  SL {SL}  |  TP {eff_tp}")
    print(f"  Price stalls, drifts down {LAYER2_PIPS} more pips to {l2['exec']}")
    print(f"  L2 placed  : {lot_each} lots @ {l2['exec']}  |  SL {SL}  |  TP {eff_tp}")
    print(f"             → L2 entry is {l2['sl_p']} pips above SL  (safe, inside zone)")
    print(f"  Average entry across both layers: {avg_entry}")
    print()
    print(f"  Price reverses and rallies to {eff_tp}  ← both TP hit")
    print()
    print(f"  L1 profit  = {lot_each}L × {l1['tp_p']}p × ${PIP_VALUE}  =  ${l1['pnl_tp']:,.2f}")
    print(f"  L2 profit  = {lot_each}L × {l2['tp_p']}p × ${PIP_VALUE}  =  ${l2['pnl_tp']:,.2f}  ← bought cheaper!")
    print(f"  {'─'*44}")
    print(f"  TOTAL PROFIT  =  ${scB_profit:,.2f}")
    print()
    print(f"  Worst-case (ALL SL hit):")
    print(f"  L1 loss  =  {lot_each}L × {l1['sl_p']}p × ${PIP_VALUE}  =  −${l1['pnl_sl']:,.2f}")
    print(f"  L2 loss  =  {lot_each}L × {l2['sl_p']}p × ${PIP_VALUE}  =  −${l2['pnl_sl']:,.2f}")
    print(f"  {'─'*44}")
    print(f"  TOTAL MAX LOSS  =  −${scB_loss:,.2f}")
    print()
    print(f"  ★ Breakeven protection:")
    print(f"    → L1 TP hits {eff_tp}  (price rises to TP)")
    print(f"    → Bot IMMEDIATELY moves L2 SL to breakeven ({l2['exec']})")
    print(f"    → L2 is now RISK-FREE. Worst case = $0 loss on L2.")
    print(f"    → L2 TP still active at {l2['tp']}  →  potential +${l2['pnl_tp']:,.2f} for free")

    # ─── Summary table ───────────────────────────────────────────────────────
    print()
    print(SEP)
    print("  SUMMARY")
    print(SEP)
    r   = f"  {'Metric':<38}"
    sa  = f"{'Scenario A':>12}"
    sb  = f"{'Scenario B':>12}"
    print(r + sa + sb)
    print(f"  {'─'*62}")

    def row(label, a, b):
        print(f"  {label:<38}{str(a):>12}{str(b):>12}")

    row("Positions placed",        "1  (L1 only)",    "2  (L1 + L2)")
    row("Execution prices",         str(l1["exec"]),  f"{l1['exec']} + {l2['exec']}")
    row("Average entry",            str(l1["exec"]),   str(avg_entry))
    row("──────────────────────────────────────", "", "")
    row("Profit (TP hit)",          f"${l1['pnl_tp']:,.2f}", f"${scB_profit:,.2f}")
    row("Max loss (SL hit)",        f"−${l1['pnl_sl']:,.2f}", f"−${scB_loss:,.2f}")
    row("Reward:Risk ratio",         str(round(l1['pnl_tp']/l1['pnl_sl'],2)),
                                     str(round(scB_profit/scB_loss,2)))
    row("──────────────────────────────────────", "", "")
    row("Extra profit vs A",        "—",              f"+${scB_profit - l1['pnl_tp']:,.2f}")
    row("Extra risk vs A",          "—",              f"+${scB_loss   - l1['pnl_sl']:,.2f}")
    row("After L1 TP → L2 risk",   "—",              "$0  (breakeven)")

    print(SEP)
    xprofit = scB_profit - l1['pnl_tp']
    xrisk   = scB_loss   - l1['pnl_sl']
    print(f"  KEY INSIGHT: L2 earns +${xprofit:,.2f} extra profit")
    print(f"  for only +${xrisk:,.2f} extra risk  ({round(xrisk/xprofit*100,0):.0f}¢ of extra risk per $1 extra gain).")
    print(f"  Once L1 TP hits, L2 becomes a free ride  =  zero further risk.")
    print(SEP)
