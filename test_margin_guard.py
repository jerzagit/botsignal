"""
test_margin_guard.py
Unit tests for ALL execute_trade() guards:
  - Margin level guard       (MIN_MARGIN_LEVEL = 300%)
  - Same-direction stack     (BLOCK_SAME_DIRECTION_STACK = true)
  - Reward:Risk ratio        (MIN_RR_RATIO = 1.0)
  - Spread guard             (MAX_SPREAD_PIPS = 3)
  - Entry proximity guard    (ENTRY_MAX_DISTANCE_PIPS = 50)

All MT5 calls are mocked — no live connection required.

Run: python -m pytest test_margin_guard.py -v
 or: python test_margin_guard.py
"""

import sys, os
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest
from unittest.mock import patch, MagicMock

from dotenv import load_dotenv
load_dotenv()

from core.signal import Signal
from core.mt5    import execute_trade


# ── Shared test signal (Hafiz-style XAUUSD BUY) ───────────────────────────────
def make_signal():
    return Signal(
        symbol     = "XAUUSD",
        direction  = "buy",
        entry_low  = 3200.00,
        entry_high = 3200.00,
        sl         = 3195.00,   # 50 pips SL
        tps        = [3208.00], # 80 pips TP
        raw_text   = "xauusd buy @3200\nsl 3195\ntp 3208\nTrade At Your Own Risk",
    )


def _mock_mt5_ok(account_mock, existing_positions=None, tick_override=None):
    """Patch all MT5 internals so execute_trade() can run without real MT5."""
    tick = tick_override if tick_override is not None else _tick()
    patches = {
        "core.mt5.mt5.initialize":         MagicMock(return_value=True),
        "core.mt5.mt5.login":              MagicMock(return_value=True),
        "core.mt5.mt5.shutdown":           MagicMock(),
        "core.mt5.mt5.account_info":       MagicMock(return_value=account_mock),
        "core.mt5.mt5.positions_get":      MagicMock(return_value=existing_positions or []),
        "core.mt5.mt5.symbol_info":        MagicMock(return_value=_symbol_info()),
        "core.mt5.mt5.symbol_select":      MagicMock(return_value=True),
        "core.mt5.mt5.symbol_info_tick":   MagicMock(return_value=tick),
        "core.mt5.mt5.order_send":         MagicMock(return_value=_order_result()),
        "core.mt5.mt5.ORDER_TYPE_BUY":     0,
        "core.mt5.mt5.ORDER_TYPE_SELL":    1,
        "core.mt5.mt5.TRADE_ACTION_DEAL":  1,
        "core.mt5.mt5.ORDER_TIME_GTC":     1,
        "core.mt5.mt5.ORDER_FILLING_IOC":  1,
        "core.mt5.mt5.TRADE_RETCODE_DONE": 10009,
        # risk.py also calls account_info + symbol_info
        "core.risk.mt5.account_info":      MagicMock(return_value=account_mock),
        "core.risk.mt5.symbol_info":       MagicMock(return_value=_symbol_info()),
    }
    return patches


def _account(margin_level, margin=500.0, free_margin=4500.0):
    a = MagicMock()
    a.margin       = margin
    a.margin_free  = free_margin
    a.equity       = free_margin + margin
    a.margin_level = margin_level
    return a


def _symbol_info():
    s = MagicMock()
    s.visible              = True
    s.trade_tick_size      = 0.01
    s.trade_tick_value     = 1.0
    s.trade_contract_size  = 100.0
    s.volume_step          = 0.01
    return s


def _tick():
    t = MagicMock()
    t.ask = 3200.50
    t.bid = 3200.30
    return t


def _order_result():
    r = MagicMock()
    r.retcode = 10009   # TRADE_RETCODE_DONE
    r.order   = 99999
    return r


# ══════════════════════════════════════════════════════════════════════════════

class TestMarginLevelGuard(unittest.TestCase):
    """Threshold is now 300% (down from 1000% — professional standard)."""

    def test_blocked_below_300(self):
        """145% margin level — must be blocked."""
        account = _account(margin_level=145.0, margin=500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(make_signal(), "mg_01")
        self.assertIn("Trade blocked", result)
        self.assertIn("145.0%", result)
        print(f"\n[BLOCKED 145%]  {result.splitlines()[0]}")

    def test_blocked_at_299(self):
        """299.9% — just under threshold — must be blocked."""
        account = _account(margin_level=299.9, margin=500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(make_signal(), "mg_02")
        self.assertIn("Trade blocked", result)
        print(f"\n[BLOCKED 299%]  {result.splitlines()[0]}")

    def test_allowed_at_300(self):
        """Exactly 300% — boundary — must be allowed."""
        account = _account(margin_level=300.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(make_signal(), "mg_03")
        self.assertNotIn("Trade blocked", result)
        self.assertIn("Trade Executed", result)
        print(f"\n[ALLOWED 300%]  {result.splitlines()[0]}")

    def test_allowed_above_300(self):
        """2000% — well above threshold — must be allowed."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(make_signal(), "mg_04")
        self.assertIn("Trade Executed", result)
        print(f"\n[ALLOWED 2000%] {result.splitlines()[0]}")

    def test_guard_skipped_no_open_trades(self):
        """margin=0 (no open trades) → guard bypassed → first trade always allowed."""
        account = _account(margin_level=0.0, margin=0.0, free_margin=5000.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(make_signal(), "mg_05")
        self.assertIn("Trade Executed", result)
        print(f"\n[NO TRADES]     {result.splitlines()[0]}")

    def test_blocked_when_account_info_none(self):
        """MT5 returns None for account — must block."""
        patches = _mock_mt5_ok(None)
        patches["core.mt5.mt5.account_info"] = MagicMock(return_value=None)
        with _apply_patches(patches):
            result = execute_trade(make_signal(), "mg_06")
        self.assertIn("❌", result)
        print(f"\n[NO ACCT INFO]  {result.splitlines()[0]}")


class TestSameDirectionStackGuard(unittest.TestCase):
    """Block stacking same direction on same symbol."""

    def _make_existing_buy(self):
        pos = MagicMock()
        pos.type = 0   # ORDER_TYPE_BUY
        return pos

    def test_blocked_when_buy_already_open(self):
        """Already have a BUY on XAUUSD — new BUY must be blocked."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account, existing_positions=[self._make_existing_buy()])):
            result = execute_trade(make_signal(), "stack_01")
        self.assertIn("Stacking same direction", result)
        print(f"\n[STACK BLOCKED] {result.splitlines()[0]}")

    def test_allowed_when_no_existing_position(self):
        """No open positions — new BUY allowed."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account, existing_positions=[])):
            result = execute_trade(make_signal(), "stack_02")
        self.assertIn("Trade Executed", result)
        print(f"\n[STACK OK]      {result.splitlines()[0]}")

    def test_allowed_when_existing_is_opposite_direction(self):
        """Have a SELL open — new BUY is fine (hedging, not stacking)."""
        sell_pos = MagicMock(); sell_pos.type = 1  # ORDER_TYPE_SELL
        account  = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account, existing_positions=[sell_pos])):
            result = execute_trade(make_signal(), "stack_03")
        self.assertIn("Trade Executed", result)
        print(f"\n[OPPOSITE OK]   {result.splitlines()[0]}")


class TestRRRatioGuard(unittest.TestCase):
    """Minimum reward:risk ratio check."""

    def _signal_with_tp(self, tp):
        return Signal(
            symbol="XAUUSD", direction="buy",
            entry_low=3200.0, entry_high=3200.0,
            sl=3195.0,   # 50 pip SL
            tps=[tp],
            raw_text=f"xauusd buy @3200 sl 3195 tp {tp}",
        )

    def test_blocked_when_rr_below_1(self):
        """TP=30 pips, SL=50 pips → 0.6:1 ratio → blocked."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(self._signal_with_tp(3203.0), "rr_01")
        self.assertIn("reward:risk", result)
        print(f"\n[RR BLOCKED]    {result.splitlines()[0]}")

    def test_allowed_when_rr_equals_1(self):
        """TP=50 pips, SL=50 pips → 1.0:1 → exactly at minimum → allowed."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(self._signal_with_tp(3205.0), "rr_02")
        self.assertIn("Trade Executed", result)
        print(f"\n[RR 1:1 OK]     {result.splitlines()[0]}")

    def test_allowed_when_rr_above_1(self):
        """TP=80 pips, SL=50 pips → 1.6:1 → allowed."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        with _apply_patches(_mock_mt5_ok(account)):
            result = execute_trade(self._signal_with_tp(3208.0), "rr_03")
        self.assertIn("Trade Executed", result)
        print(f"\n[RR 1.6:1 OK]   {result.splitlines()[0]}")


class TestSpreadGuard(unittest.TestCase):
    """Block entry when broker spread is too wide."""

    def _tick_with_spread(self, spread_pts):
        t = MagicMock()
        t.ask = 3200.50
        t.bid = round(3200.50 - spread_pts, 2)
        return t

    def test_blocked_when_spread_4_pips(self):
        """Spread = 4 pips (0.4 pts) → above 3 pip max → blocked."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        wide_tick = self._tick_with_spread(0.40)   # 4 pips
        with _apply_patches(_mock_mt5_ok(account, tick_override=wide_tick)):
            result = execute_trade(make_signal(), "sp_01")
        self.assertIn("spread too wide", result)
        print(f"\n[SPREAD BLOCKED] {result.splitlines()[0]}")

    def test_allowed_when_spread_2_pips(self):
        """Spread = 2 pips (0.2 pts) → normal → allowed."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        norm_tick = self._tick_with_spread(0.20)   # 2 pips
        with _apply_patches(_mock_mt5_ok(account, tick_override=norm_tick)):
            result = execute_trade(make_signal(), "sp_02")
        self.assertIn("Trade Executed", result)
        print(f"\n[SPREAD OK]      {result.splitlines()[0]}")

    def test_blocked_at_exactly_3_point_1_pips(self):
        """Spread = 3.1 pips → just over threshold → blocked."""
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        over_tick = self._tick_with_spread(0.31)   # 3.1 pips
        with _apply_patches(_mock_mt5_ok(account, tick_override=over_tick)):
            result = execute_trade(make_signal(), "sp_03")
        self.assertIn("spread too wide", result)
        print(f"\n[SPREAD 3.1 BLK] {result.splitlines()[0]}")


# ── Helper: apply multiple patches as a single context manager ─────────────────

from contextlib import contextmanager

@contextmanager
def _apply_patches(patch_dict):
    patchers = [patch(target, val) for target, val in patch_dict.items()]
    for p in patchers:
        p.start()
    try:
        yield
    finally:
        for p in patchers:
            p.stop()


class TestEntryProximityGuard(unittest.TestCase):
    """
    Tests for the entry proximity guard.
    Signal entry zone: 3200.00 (single price).
    Current price varies per test. 50 pips = 5.0 pts for XAUUSD.
    """

    BASE_SIGNAL = dict(
        symbol="XAUUSD", direction="buy",
        entry_low=3200.00, entry_high=3200.00,
        sl=3195.00, tps=[3208.00],
        raw_text="xauusd buy @3200 sl 3195 tp 3208",
    )

    def _run(self, ask_price):
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        tick = MagicMock()
        tick.ask = ask_price
        tick.bid = ask_price - 0.20

        fake_order = MagicMock()
        fake_order.retcode = 10009
        fake_order.order   = 77777

        patches = _mock_mt5_ok(account)
        patches["core.mt5.mt5.symbol_info_tick"] = MagicMock(return_value=tick)
        patches["core.mt5.mt5.order_send"]       = MagicMock(return_value=fake_order)

        with _apply_patches(patches):
            return execute_trade(Signal(**self.BASE_SIGNAL), "prox_test")

    # ── Should EXECUTE ────────────────────────────────────────────────────────
    def test_executes_when_price_inside_zone(self):
        """Price exactly at entry → 0 pips distance → execute."""
        result = self._run(ask_price=3200.00)
        self.assertIn("Trade Executed", result)
        print(f"\n[INSIDE ZONE]   {result.splitlines()[0]}")

    def test_executes_when_price_within_50_pips(self):
        """Price 30 pips above entry → within range → execute."""
        result = self._run(ask_price=3203.00)   # 30 pips above 3200
        self.assertIn("Trade Executed", result)
        print(f"\n[30 PIPS OUT]   {result.splitlines()[0]}")

    def test_executes_at_exactly_50_pips(self):
        """Price exactly 50 pips away → boundary → execute."""
        result = self._run(ask_price=3205.00)   # exactly 50 pips above 3200
        self.assertIn("Trade Executed", result)
        print(f"\n[50 PIPS EXACT] {result.splitlines()[0]}")

    # ── Should BLOCK ──────────────────────────────────────────────────────────
    def test_blocked_when_price_51_pips_away(self):
        """Price 51 pips above entry → just over threshold → block."""
        result = self._run(ask_price=3205.10)   # 51 pips above 3200
        self.assertIn("price too far", result)
        print(f"\n[51 PIPS OUT]   {result.splitlines()[0]}")

    def test_blocked_when_price_200_pips_away(self):
        """Price 200 pips above entry → signal came way too early → block."""
        result = self._run(ask_price=3220.00)   # 200 pips above 3200
        self.assertIn("price too far", result)
        self.assertIn("200", result)
        print(f"\n[200 PIPS OUT]  {result.splitlines()[0]}")

    def test_blocked_when_price_below_entry(self):
        """Price 100 pips BELOW entry zone → also too far → block."""
        result = self._run(ask_price=3190.00)   # 100 pips below 3200
        self.assertIn("price too far", result)
        print(f"\n[100 PIPS LOW]  {result.splitlines()[0]}")

    def test_range_entry_inside_zone(self):
        """Range entry 3198–3202: price at 3200 is inside → execute."""
        sig = Signal(
            symbol="XAUUSD", direction="buy",
            entry_low=3198.00, entry_high=3202.00,
            sl=3193.00, tps=[3210.00],
            raw_text="xauusd buy @3198-3202 sl 3193 tp 3210",
        )
        account = _account(margin_level=2000.0, margin=500.0, free_margin=9500.0)
        tick = MagicMock(); tick.ask = 3200.00; tick.bid = 3199.80
        fake_order = MagicMock(); fake_order.retcode = 10009; fake_order.order = 66666
        patches = _mock_mt5_ok(account)
        patches["core.mt5.mt5.symbol_info_tick"] = MagicMock(return_value=tick)
        patches["core.mt5.mt5.order_send"]       = MagicMock(return_value=fake_order)
        with _apply_patches(patches):
            result = execute_trade(sig, "range_test")
        self.assertIn("Trade Executed", result)
        print(f"\n[RANGE IN ZONE] {result.splitlines()[0]}")


class TestMarginGuardRealMT5(unittest.TestCase):
    """
    Connects to your LIVE MT5 account to read the real margin level.
    order_send is mocked so NO real trade is placed.
    """

    @classmethod
    def setUpClass(cls):
        """Connect once, grab real account info + live price, then disconnect."""
        from core.mt5 import mt5_connect
        import MetaTrader5 as mt5
        from core.config import MT5_SYMBOL_SUFFIX

        if not mt5_connect():
            raise unittest.SkipTest("MT5 not available — skipping live tests")

        cls.account = mt5.account_info()
        symbol = "XAUUSD" + MT5_SYMBOL_SUFFIX
        cls.tick   = mt5.symbol_info_tick(symbol)
        cls.sym    = mt5.symbol_info(symbol)
        mt5.shutdown()

        if cls.account is None or cls.tick is None:
            raise unittest.SkipTest("Could not read account/price from MT5")

        ml = cls.account.margin_level if cls.account.margin > 0 else float("inf")
        print(f"\n  [LIVE MT5] Account #{cls.account.login} | "
              f"Margin level: {ml:.1f}% | "
              f"Free margin: ${cls.account.margin_free:,.2f}")

    def _live_signal(self):
        entry = self.tick.ask
        return Signal(
            symbol     = "XAUUSD",
            direction  = "buy",
            entry_low  = entry,
            entry_high = entry,
            sl         = round(entry - 5.0, 2),
            tps        = [round(entry + 8.0, 2)],
            raw_text   = f"xauusd buy @{entry} sl {entry-5} tp {entry+8}",
        )

    def test_real_guards_reflect_live_account(self):
        """
        Uses real MT5 account data. Mocks only order_send so nothing is placed.
        Evaluates all active guards against actual account state and asserts
        the result is consistent — either correctly blocked or correctly executed.
        """
        import MetaTrader5 as _mt5
        from core.config import MIN_MARGIN_LEVEL, BLOCK_SAME_DIRECTION_STACK, \
                                 MT5_SYMBOL_SUFFIX, MIN_RR_RATIO, SL_PIP_SIZE
        from core.mt5 import mt5_connect

        if not mt5_connect():
            self.skipTest("MT5 not available")

        account   = _mt5.account_info()
        symbol    = "XAUUSD" + MT5_SYMBOL_SUFFIX
        positions = _mt5.positions_get(symbol=symbol) or []
        tick      = _mt5.symbol_info_tick(symbol)
        _mt5.shutdown()

        if account is None or tick is None:
            self.skipTest("Could not read live account/price")

        signal = self._live_signal()
        margin_level  = account.margin_level if account.margin > 0 else float("inf")
        same_dir_open = [p for p in positions if p.type == 0]  # BUY positions
        spread_pips   = (tick.ask - tick.bid) / SL_PIP_SIZE

        print(f"\n  Margin level : {margin_level:.1f}%  (min: {MIN_MARGIN_LEVEL:.0f}%)")
        print(f"  Open BUYs    : {len(same_dir_open)}")
        print(f"  Spread       : {spread_pips:.1f} pips")

        # Determine expected outcome
        if account.margin > 0 and margin_level < MIN_MARGIN_LEVEL:
            expected_block, reason = True, "margin level"
        elif BLOCK_SAME_DIRECTION_STACK and same_dir_open:
            expected_block, reason = True, f"{len(same_dir_open)} open BUY(s) — stack guard"
        else:
            expected_block, reason = False, "all guards passed"

        print(f"  Expected     : {'BLOCKED' if expected_block else 'ALLOWED'} ({reason})")

        fake_order = MagicMock(); fake_order.retcode = 10009; fake_order.order = 88888
        with patch("core.mt5.mt5.order_send", return_value=fake_order), \
             patch("core.mt5._log_trade"), \
             patch("core.db.record_trade"):
            result = execute_trade(signal, "live_test_01")

        print(f"  Result       : {result.splitlines()[0]}")

        if expected_block:
            self.assertNotIn("Trade Executed", result)
        else:
            self.assertIn("Trade Executed", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
