import unittest
from unittest.mock import MagicMock, patch

from core.signal import Signal
from core.source_risk import apply_source_risk_bucket


def make_signal():
    return Signal(
        symbol="XAUUSD",
        direction="buy",
        entry_low=3200.0,
        entry_high=3200.0,
        sl=3195.0,
        tps=[3208.0],
        raw_text="xauusd buy @3200 sl 3195 tp 3208",
        source_id="alpha",
        source_name="Alpha",
        parser_profile="hafiz",
        telegram_chat_id="-1001",
        source_risk_percent=0.10,
    )


def make_account(equity=1000.0):
    account = MagicMock()
    account.equity = equity
    account.margin_free = equity
    return account


def symbol_info():
    info = MagicMock()
    info.volume_step = 0.01
    return info


class TestSourceRiskBucket(unittest.TestCase):
    def test_allows_when_source_bucket_has_room(self):
        with patch("core.source_risk._risk_per_lot", return_value=1000.0), \
             patch("core.source_risk._open_risk_by_source", return_value=({"alpha": 0.0}, 0.0)):
            result = apply_source_risk_bucket(make_signal(), make_account(), 0.05)

        self.assertTrue(result.allowed)
        self.assertEqual(result.lot, 0.05)

    def test_reduces_to_remaining_source_budget(self):
        with patch("core.source_risk._risk_per_lot", return_value=1000.0), \
             patch("core.source_risk._open_risk_by_source", return_value=({"alpha": 60.0}, 60.0)), \
             patch("core.source_risk.mt5.symbol_info", return_value=symbol_info()):
            result = apply_source_risk_bucket(make_signal(), make_account(), 0.10)

        self.assertTrue(result.allowed)
        self.assertEqual(result.lot, 0.04)
        self.assertIn("reduced", result.note)

    def test_blocks_when_remaining_budget_below_min_lot(self):
        with patch("core.source_risk._risk_per_lot", return_value=1000.0), \
             patch("core.source_risk._open_risk_by_source", return_value=({"alpha": 99.0}, 99.0)), \
             patch("core.source_risk.mt5.symbol_info", return_value=symbol_info()):
            result = apply_source_risk_bucket(make_signal(), make_account(), 0.10)

        self.assertFalse(result.allowed)
        self.assertIn("below MIN_LOT", result.reason)

    def test_other_source_does_not_consume_this_source_bucket(self):
        with patch("core.source_risk._risk_per_lot", return_value=1000.0), \
             patch("core.source_risk._open_risk_by_source", return_value=({"beta": 100.0}, 100.0)):
            result = apply_source_risk_bucket(make_signal(), make_account(), 0.10)

        self.assertTrue(result.allowed)
        self.assertEqual(result.lot, 0.10)


if __name__ == "__main__":
    unittest.main()
