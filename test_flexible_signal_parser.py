import unittest

from core.parsers import parse_with_profile
from core.signal import parse_signal


class TestFlexibleSignalParser(unittest.TestCase):
    def test_hafiz_standard_sell_range(self):
        text = """xauusd sell @4076-4080
sl 4083
tp 4072
tp 4068"""

        signal = parse_signal(text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertEqual(signal.direction, "sell")
        self.assertEqual(signal.entry_low, 4076.0)
        self.assertEqual(signal.entry_high, 4080.0)
        self.assertEqual(signal.sl, 4083.0)
        self.assertEqual(signal.tps, [4072.0, 4068.0])

    def test_hafiz_standard_buy_reversed_range(self):
        text = """xauusd buy @4065-4061
sl 4058
tp 4070
tp 4075"""

        signal = parse_with_profile("hafiz", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "buy")
        self.assertEqual(signal.entry_low, 4061.0)
        self.assertEqual(signal.entry_high, 4065.0)
        self.assertEqual(signal.sl, 4058.0)
        self.assertEqual(signal.tps, [4070.0, 4075.0])

    def test_tradewhatyousee_gold_sell_now(self):
        text = """Gold Sell Now @ 4058.8 - 4068.8
TP 4050
TP 4042.5
SL 4073"""

        signal = parse_with_profile("tradewhatyousee", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertEqual(signal.direction, "sell")
        self.assertEqual(signal.entry_low, 4058.8)
        self.assertEqual(signal.entry_high, 4068.8)
        self.assertEqual(signal.sl, 4073.0)
        self.assertEqual(signal.tps, [4050.0, 4042.5])

    def test_tradewhatyousee_direction_first(self):
        text = """SELL GOLD NOW @ 4068.8
Target 4058
Target 4050
Stop Loss 4073"""

        signal = parse_with_profile("tradewhatyousee", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertEqual(signal.direction, "sell")
        self.assertEqual(signal.entry_low, 4068.8)
        self.assertEqual(signal.entry_high, 4068.8)
        self.assertEqual(signal.sl, 4073.0)
        self.assertEqual(signal.tps, [4058.0, 4050.0])

    def test_tradewhatyousee_noisy_buy_with_shorthand_entry(self):
        text = """TradeWhatYouSee VIP
Gold Buy Now @ 4058-8
TP1: 4065
TP2: 4070
SL: 4053"""

        signal = parse_with_profile("tradewhatyousee", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "buy")
        self.assertEqual(signal.entry_low, 4058.8)
        self.assertEqual(signal.entry_high, 4058.8)
        self.assertEqual(signal.sl, 4053.0)
        self.assertEqual(signal.tps, [4065.0, 4070.0])

    def test_rejects_placeholder_tp_labels(self):
        text = """Gold Sell Now @ 4058-8 - 4068.8
Tp 1
TP 2
SL 4073"""

        self.assertIsNone(parse_with_profile("tradewhatyousee", text))

    def test_rejects_wrong_side_sell_levels(self):
        text = """Gold Sell Now @ 4058.8
TP 4065
SL 4053"""

        self.assertIsNone(parse_with_profile("tradewhatyousee", text))


if __name__ == "__main__":
    unittest.main()
