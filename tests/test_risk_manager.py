"""Tests for risk manager"""
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from portfolio.risk_manager import RiskManager

def test_empty_signals():
    rm = RiskManager()
    result = rm.filter({}, {}, {}, 0, {}, 0, 0.2)
    assert result == {}

def test_single_position_cap():
    rm = RiskManager({"max_single_weight": 0.30})
    result = rm.filter({"000001": 0.5}, {}, {}, 0, {}, 0, 0.2)
    assert result["000001"] == pytest.approx(0.30, abs=0.01)

def test_total_position_limit():
    rm = RiskManager({"max_total_position": 0.95})
    result = rm.filter({"000001": 0.5}, {"000001": 0.6}, {}, 0, {}, 0, 0.2)
    assert len(result) == 0  # already over limit
def test_drawdown_circuit_breaker():
    rm = RiskManager({"max_drawdown": 0.25})
    result = rm.filter({"000001": 0.3}, {}, {}, 0, {}, 0.30, 0.2)
    assert result == {}

def test_volatility_adaptive():
    rm = RiskManager({"vol_adaptive": True, "vol_low": 0.15, "vol_high": 0.40})
    r1 = rm.filter({"000001": 0.3}, {}, {}, 0, {}, 0, 0.15)
    r2 = rm.filter({"000001": 0.3}, {}, {}, 0, {}, 0, 0.40)
    assert r1["000001"] == pytest.approx(0.30, abs=0.02)  # low vol -> full
    assert r2["000001"] < 0.30  # high vol -> reduced
