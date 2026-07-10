"""백테스트 엔진·성과지표 검증 — 합성 가격 (네트워크 불필요)."""

import numpy as np
import pandas as pd
import pytest
from quantlab_shared.backtest.costs import CostModel
from quantlab_shared.backtest.engine import run_backtest
from quantlab_shared.backtest.metrics import max_drawdown

DATES = pd.bdate_range("2024-01-01", periods=30)
NO_COST = CostModel(transaction_tax=0.0, slippage=0.0)
COST = CostModel(transaction_tax=0.002, slippage=0.001)


def make_closes(**cols):
    return pd.DataFrame(cols, index=DATES)


def one_signal(date, direction=1):
    return pd.DataFrame([{"ticker": "A", "signal_date": date.date(), "direction": direction}])


def test_long_captures_rise():
    closes = make_closes(A=100 * 1.01 ** np.arange(30))
    res = run_backtest(one_signal(DATES[5]), closes, holding_days=5, cost=NO_COST)
    # 진입 익일부터 5일간 매일 +1%
    assert res.daily_net.iloc[6:11].round(10).eq(0.01).all()
    assert res.daily_net.iloc[12:].eq(0).all()  # 청산 후 현금
    assert res.trades["gross_ret"].iloc[0] == pytest.approx(1.01**5 - 1)
    assert res.metrics()["hit_ratio"] == 1.0


def test_short_captures_fall():
    closes = make_closes(A=100 * 0.99 ** np.arange(30))
    res = run_backtest(one_signal(DATES[5], direction=-1), closes, holding_days=5, cost=NO_COST)
    assert (res.daily_net.iloc[6:11] > 0).all()
    assert res.trades["gross_ret"].iloc[0] == pytest.approx(1 - 0.99**5)


def test_costs_charged_on_flat_prices():
    closes = make_closes(A=np.full(30, 100.0))
    res = run_backtest(one_signal(DATES[5]), closes, holding_days=5, cost=COST)
    # 가격 변동 0 → 순수익 = -왕복비용 (진입 매수 0.1% + 청산 매도 0.3%)
    assert res.daily_net.sum() == pytest.approx(-COST.round_trip)
    assert res.trades["net_ret"].iloc[0] == pytest.approx(-COST.round_trip)
    assert res.metrics()["hit_ratio"] == 0.0


def test_equal_weight_two_positions():
    closes = make_closes(A=100 * 1.02 ** np.arange(30), B=np.full(30, 50.0))
    sig = pd.DataFrame(
        [
            {"ticker": "A", "signal_date": DATES[5].date(), "direction": 1},
            {"ticker": "B", "signal_date": DATES[5].date(), "direction": 1},
        ]
    )
    res = run_backtest(sig, closes, holding_days=5, cost=NO_COST)
    # A +2%/일, B 0% → 균등가중 포트 +1%/일
    assert res.daily_net.iloc[7] == pytest.approx(0.01)


def test_long_only_filters_shorts():
    closes = make_closes(A=np.full(30, 100.0))
    sig = one_signal(DATES[5], direction=-1)
    res = run_backtest(sig, closes, holding_days=5, cost=NO_COST, long_only=True)
    assert len(res.trades) == 0
    assert res.daily_net.eq(0).all()


def test_max_drawdown():
    # +10% → -20%: MDD = -20%
    daily = pd.Series([0.10, -0.20])
    assert max_drawdown(daily) == pytest.approx(-0.20)
