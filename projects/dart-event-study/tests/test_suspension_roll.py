"""거래정지일 체결 밀기 검증 (v1.2 #7)."""

import numpy as np
import pandas as pd
import pytest
from quantlab_shared.backtest.costs import CostModel
from quantlab_shared.backtest.engine import run_backtest

DATES = pd.bdate_range("2024-01-01", periods=30)
NO_COST = CostModel(transaction_tax=0.0, slippage=0.0)


def setup(suspended_days: list[int]):
    """가격 100→매일+1원 선형, 지정일은 거래량 0(정지)."""
    closes = pd.DataFrame({"A": 100.0 + np.arange(30)}, index=DATES)
    vol = pd.DataFrame({"A": np.full(30, 1000.0)}, index=DATES)
    vol.iloc[suspended_days, 0] = 0
    return closes, vol


def sig(day, direction=1):
    return pd.DataFrame([{"ticker": "A", "signal_date": DATES[day].date(), "direction": direction}])


def test_entry_rolled_to_next_tradeable():
    closes, vol = setup(suspended_days=[5, 6])
    res = run_backtest(sig(5), closes, holding_days=3, cost=NO_COST, volumes=vol)
    tr = res.trades.iloc[0]
    assert tr["entry"] == DATES[7].date()  # 5,6 정지 → 7 진입
    assert tr["exit"] == DATES[10].date()  # 진입 기준 H=3
    assert tr["gross_ret"] == pytest.approx((110 - 107) / 107)


def test_exit_rolled_past_suspension():
    closes, vol = setup(suspended_days=[8])
    res = run_backtest(sig(5), closes, holding_days=3, cost=NO_COST, volumes=vol)
    tr = res.trades.iloc[0]
    assert tr["entry"] == DATES[5].date()
    assert tr["exit"] == DATES[9].date()  # 8 정지 → 9 청산
    assert tr["gross_ret"] == pytest.approx((109 - 105) / 105)


def test_entry_abandoned_beyond_roll_limit():
    closes, vol = setup(suspended_days=list(range(5, 12)))  # 7일 연속 정지 > roll_limit 5
    res = run_backtest(sig(5), closes, holding_days=3, cost=NO_COST, volumes=vol)
    assert len(res.trades) == 0


def test_no_volumes_preserves_legacy():
    closes, vol = setup(suspended_days=[5])
    res = run_backtest(sig(5), closes, holding_days=3, cost=NO_COST)  # volumes 미전달
    assert res.trades.iloc[0]["entry"] == DATES[5].date()  # 기존: 정지 무시하고 체결
