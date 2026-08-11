import unittest

from core.parsers import parse_with_profile


class TestTradeWhatYouSeeParser(unittest.TestCase):
    def test_rejects_tp_labels_without_prices(self):
        text = """Gold Sell Now @ 4058-8 - 4068.8

Tp 1
TP 2
Tp 2

SL 4073"""

        self.assertIsNone(parse_with_profile("tradewhatyousee", text))

    def test_parses_sell_with_range_and_real_tp_prices(self):
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

    def test_parses_buy_with_real_tp_prices(self):
        text = """Gold Buy Now @ 4058.8

TP 4065
TP 4070

SL 4053"""

        signal = parse_with_profile("tradewhatyousee", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, "buy")
        self.assertEqual(signal.entry_low, 4058.8)
        self.assertEqual(signal.entry_high, 4058.8)
        self.assertEqual(signal.sl, 4053.0)
        self.assertEqual(signal.tps, [4065.0, 4070.0])

    def test_parses_signal_with_decorative_emoji_noise(self):
        text = """🔥📊 TradeWhatYouSee VIP

🟡 Gold Sell Now @ 4058.8 — 4068.8

🎯 TP 4050
✅ TP 4042.5

🛑 SL 4073"""

        signal = parse_with_profile("tradewhatyousee", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertEqual(signal.direction, "sell")
        self.assertEqual(signal.entry_low, 4058.8)
        self.assertEqual(signal.entry_high, 4068.8)
        self.assertEqual(signal.sl, 4073.0)
        self.assertEqual(signal.tps, [4050.0, 4042.5])


if __name__ == "__main__":
    unittest.main()
