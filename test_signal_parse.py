"""
test_signal_parse.py
Parse a sample Hafiz signal and show the result — NO trade execution.
Safe to run anytime, even with LIVE account.
"""

from core.signal import parse_signal

# Sample signals — copy-paste real signals from Telegram here
SAMPLES = [
    # Standard sell signal
    """xauusd sell @4076-4080
sl 4083
tp 4072
tp 4068
Trade At Your Own Risk
T.A.Y.O.R @AssistByHafizCarat""",

    # Standard buy signal
    """xauusd buy @4065-4061
sl 4058
tp 4070
tp 4075
Trade At Your Own Risk
T.A.Y.O.R @AssistByHafizCarat""",

    # Close alert
    """setup failed""",
]

for i, raw in enumerate(SAMPLES, 1):
    print(f"--- Sample {i} ---")
    print(raw)
    print("---")
    signal = parse_signal(raw)
    if signal:
        print(f"  Symbol:    {signal.symbol}")
        print(f"  Direction: {signal.direction.upper()}")
        print(f"  Entry:     {signal.entry_low} - {signal.entry_high}")
        print(f"  SL:        {signal.sl}")
        print(f"  TPs:       {signal.tps}")
    else:
        # Might be a close alert
        from core.signal import parse_close_alert
        close = parse_close_alert(raw)
        if close:
            print(f"  Close alert: reason={close.reason} symbol={close.symbol}")
        else:
            print("  Could not parse")
    print()

# ── Live test: paste a real Hafiz signal below ──
# RAW = """paste here"""
# signal = parse_signal(RAW)
# print(signal)
