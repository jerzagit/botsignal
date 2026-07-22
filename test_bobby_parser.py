import unittest

from core.parsers import parse_with_profile


class TestBobbyParser(unittest.TestCase):
    def test_parses_sell_signal(self):
        text = """Gold sell now @4078

SL:4090

Tp-4072
Tp-4066"""

        signal = parse_with_profile("bobby", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertEqual(signal.direction, "sell")
        self.assertEqual(signal.entry_low, 4078.0)
        self.assertEqual(signal.entry_high, 4078.0)
        self.assertEqual(signal.sl, 4090.0)
        self.assertEqual(signal.tps, [4072.0, 4066.0])

    def test_parses_buy_signal(self):
        text = """Gold Buy Now @4016

S:4004

Tp-4021
Tp-4026"""

        signal = parse_with_profile("bobby", text)

        self.assertIsNotNone(signal)
        self.assertEqual(signal.symbol, "XAUUSD")
        self.assertEqual(signal.direction, "buy")
        self.assertEqual(signal.entry_low, 4016.0)
        self.assertEqual(signal.entry_high, 4016.0)
        self.assertEqual(signal.sl, 4004.0)
        self.assertEqual(signal.tps, [4021.0, 4026.0])

    def test_rejects_without_sl(self):
        text = """Gold sell now @4078
Tp-4072"""

        self.assertIsNone(parse_with_profile("bobby", text))

    def test_rejects_sell_when_sl_is_below_entry(self):
        text = """Gold sell now @40178

SL:4090

Tp-4072
Tp-4066"""

        self.assertIsNone(parse_with_profile("bobby", text))


if __name__ == "__main__":
    unittest.main()
