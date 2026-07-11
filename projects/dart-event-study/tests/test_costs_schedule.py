"""날짜 의존 거래세 스케줄 + 수수료 검증 (v1.2 #3)."""

import numpy as np
import pandas as pd
import pytest
from quantlab_shared.backtest.costs import KOSPI_TAX_SCHEDULE, CostModel
from quantlab_shared.backtest.engine import run_backtest


def test_tax_at_schedule_boundaries():
    c = CostModel(tax_schedule=KOSPI_TAX_SCHEDULE)
    assert c.tax_at("2019-06-02") == 0.0030  # 인하 전날
    assert c.tax_at("2019-06-03") == 0.0025  # 시행일
    assert c.tax_at("2022-12-31") == 0.0023
    assert c.tax_at("2023-01-01") == 0.0020
    assert c.tax_at("2024-07-15") == 0.0018


def test_no_schedule_falls_back_to_constant():
    c = CostModel(transaction_tax=0.002)
    assert c.tax_at("2019-01-01") == 0.002
    assert c.sell_cost_at("2024-01-01") == c.sell_cost


def test_commission_added_both_sides():
    c = CostModel(transaction_tax=0.002, slippage=0.001, commission=0.00015)
    assert c.buy_cost == pytest.approx(0.00115)
    assert c.sell_cost == pytest.approx(0.00315)
    assert c.round_trip == pytest.approx(0.0043)


def test_engine_applies_exit_date_tax():
    # 평평한 가격 → 순손실 = 매수비용 + 매도일 세율 비용
    dates = pd.bdate_range("2024-01-01", periods=30)
    closes = pd.DataFrame({"A": np.full(30, 100.0)}, index=dates)
    sig = pd.DataFrame([{"ticker": "A", "signal_date": dates[5].date(), "direction": 1}])
    schedule = (("2020-01-01", 0.0030), ("2024-01-10", 0.0010))
    c = CostModel(slippage=0.0, commission=0.0, tax_schedule=schedule)

    res = run_backtest(sig, closes, holding_days=5, cost=c)  # 청산 = dates[10] (1/15) → 0.0010
    assert res.trades.iloc[0]["net_ret"] == pytest.approx(-0.0010)
    assert res.daily_net.sum() == pytest.approx(-0.0010)

    res2 = run_backtest(sig.assign(signal_date=dates[1].date()), closes, holding_days=3, cost=c)
    # 청산 = dates[4] (1/5) → 인하 전 0.0030
    assert res2.trades.iloc[0]["net_ret"] == pytest.approx(-0.0030)
