"""Unit tests for authoritative geometry math."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from backtest.geometry_math import (
    compute_geometry,
    INVALID_GEOMETRY,
    sl_tighter_than_current,
    tp_further_than_current,
)


def test_sell_candidate1_current_rr():
    g = compute_geometry("SELL", 4504.15, 4541.56, 4467.07, min_rr=1.4)
    assert g["geometry_valid"]
    assert g["risk"] == 37.41
    assert g["reward"] == 37.08
    assert g["rr"] == 0.9912
    assert g["required_tp_at_min_rr"] == 4451.776
    assert g["passes_min_rr"] is False
    assert g["target_exceeds_min_rr"] is False
    assert round(g["target_shortfall_price"], 3) == 15.294


def test_sell_next_h1_rr():
    g = compute_geometry("SELL", 4504.15, 4541.56, 4464.78, min_rr=1.4)
    assert g["rr"] == 1.0524
    assert g["passes_min_rr"] is False


def test_buy_synthetic_passes_14():
    g = compute_geometry("BUY", 100, 90, 115, min_rr=1.4)
    assert g["risk"] == 10
    assert g["reward"] == 15
    assert g["rr"] == 1.5
    assert g["passes_min_rr"] is True


def test_invalid_sell_sl():
    g = compute_geometry("SELL", 100, 95, 90)
    assert not g["geometry_valid"]
    assert g["status"] == INVALID_GEOMETRY


def test_invalid_buy_sl():
    g = compute_geometry("BUY", 100, 105, 110)
    assert not g["geometry_valid"]


def test_invalid_sell_tp():
    g = compute_geometry("SELL", 100, 110, 105)
    assert not g["geometry_valid"]


def test_invalid_buy_tp():
    g = compute_geometry("BUY", 100, 90, 95)
    assert not g["geometry_valid"]


def test_sell_sl_tighter_flag():
    assert sl_tighter_than_current("SELL", 4504.15, 4541.56, 4540.81)
    assert not sl_tighter_than_current("SELL", 4504.15, 4541.56, 4542.86)


def test_candidate2_current_rr():
    g = compute_geometry("SELL", 4076.31, 4148.25, 4050.35, min_rr=1.4)
    assert g["risk"] == 71.94
    assert g["reward"] == 25.96
    assert g["rr"] == 0.3609
