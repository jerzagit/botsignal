# Historical replay feasibility (Phase B/E)

Estimates assume XAUUSD, broker session ~5.5 trading days/week, continuous
M15/H1/H4 without optimizing storage.

## Row counts (approx.)

| Horizon | M15 (~96/day × 22 days/mo) | H1 | H4 |
|---------|----------------------------|----|----|
| 6 months | ~12,700 | ~3,200 | ~800 |
| 12 months | ~25,400 | ~6,400 | ~1,600 |

Weekends/holidays reduce counts; gaps are recorded, not repaired.

## Dataset size on disk

CSV ~80–120 bytes/row →:

| Horizon | Total rows (3 TFs) | Approx size |
|---------|--------------------|-------------|
| 6 months | ~16.7k | **2–3 MB** |
| 12 months | ~33.4k | **4–6 MB** |

Negligible for local SSD.

## Download duration (MT5 `copy_rates_range`, once per TF)

Typically seconds to low minutes for 6–12 months if the terminal is warm and
history is cached. Cold terminals may take longer while the broker backfills.

## Replay duration (decision-only, in-memory)

Measured ~480 M15 bars ≈ **0.5 s** on a typical Windows desktop after
look-ahead logging was kept O(1). Extrapolating linearly:

| Horizon | M15 evaluations | Expected replay time |
|---------|-----------------|----------------------|
| 6 months | ~13k | **~5–15 s** |
| 12 months | ~25k | **~10–30 s** |

## Memory

Current implementation loads full M15/H1/H4 series into RAM.

| Horizon | Approx RAM for candles |
|---------|------------------------|
| 6 months | **< 10 MB** |
| 12 months | **< 20 MB** |

**Acceptable** for Phase B/E. No chunking needed yet.

## Recommendation

Keep full in-memory load through at least 12 months. Revisit only if multi-year
or multi-symbol universes are added.
