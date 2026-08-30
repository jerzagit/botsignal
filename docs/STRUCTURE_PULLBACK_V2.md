# Structure Pullback V2 — Locked Rules

Internal id: `structure_pullback_v2`  
Status: experimental  
Required timeframes: M15, M30, H1, H4  
Default: **NO** (default remains `breakout_retest_v1`)

## Flow

```
H4 direction (EMA9/EMA21 + RSI14) → must be BULL (buy) or BEAR (sell)
        ↓
H1 structure (pivots L=2,R=2) → BULLISH / BEARISH
        ↓
M30 RBR demand or DBD supply zone (first retest only)
        ↓
Pullback into zone (after departure; departure ≠ touch)
        ↓
M15 confirmed structure-shift (close beyond swing; wick-only invalid)
        ↓
StrategyDecision enter @ M15 confirmation close
```

## Zone boundaries (body/wick hybrid)

**RBR demand:** distal = min(base.low); proximal = max(body_top)  
**DBD supply:** distal = max(base.high); proximal = min(body_bottom)

## Base (M30 ATR14)

- Length 1–4; prefer smallest valid  
- Each TR ≤ 0.80×ATR; body/range ≤ 0.60; range>0  
- Adjacent overlap required for 2+  
- Total width ≤ 1.20×ATR  

## Legs / departure / BOS

- Incoming rally/drop ≥ 1.0×ATR  
- Departure within 1–3 M30 bars ≥ 1.0×ATR from proximal with body/range ≥ 0.60  
- BOS: M30 close beyond prior confirmed swing (L=2,R=2); wick-only ≠ BOS  

## Freshness / invalidation

- First return after departure → touch_count=1  
- M30 close beyond distal → INVALIDATED  
- Leave zone ≥ 0.50×ATR without M15 confirm → CONSUMED  

## Entry / SL / TP

- Entry = M15 confirmation close  
- SL = distal ± 0.10×M30 ATR14  
- TP = latest confirmed H1 opposing swing beyond entry; else `no_structural_target`  
- Fib 38.2–61.8 = confluence metadata only (not required)  

## Safety

Plugin never calls MT5 `order_send`, Telegram, or DB. Guards/risk/execution unchanged.
