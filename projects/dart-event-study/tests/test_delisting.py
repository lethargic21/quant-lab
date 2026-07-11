"""상폐 처리 엔진 규칙 검증 (v1.2 #1) — 테스트 우선 작성.

시나리오: 가격이 기간 중간에 끊기는 종목(상폐)에 보유 중이면
마지막 유효 가격에서 청산 할인(delist_discount)을 먹고 강제 청산된다.
할인 없이 NaN을 조용히 0 취급하던 기존 경로(손실 누락)를 막는 것이 목적.
"""

import numpy as np
import pandas as pd
import pytest
from quantlab_shared.backtest.costs import CostModel
from quantlab_shared.backtest.engine import run_backtest

DATES = pd.bdate_range("2024-01-01", periods=30)
NO_COST = CostModel(transaction_tax=0.0, slippage=0.0)


def delisted_closes():
    """A: 100 고정이다가 15일째(index 14) 이후 상폐(NaN). B: 정상 100 고정."""
    a = np.full(30, 100.0)
    a[15:] = np.nan
    return pd.DataFrame({"A": a, "B": np.full(30, 100.0)}, index=DATES)


def sig(ticker="A", day=5, direction=1):
    return pd.DataFrame([{"ticker": ticker, "signal_date": DATES[day].date(), "direction": direction}])


def test_forced_exit_with_discount():
    # 진입 5일, H=20 → 자연 청산일(25)이 마지막 유효일(14)보다 뒤 → 14일에 강제 청산 + 30% 할인
    res = run_backtest(sig(), delisted_closes(), holding_days=20, cost=NO_COST, delist_discount=0.30)
    tr = res.trades.iloc[0]
    assert tr["delisted"] == True  # noqa: E712
    assert tr["exit"] == DATES[14].date()
    # 가격 변동 0이므로 손익 = 청산 할인 그대로
    assert tr["gross_ret"] == pytest.approx(-0.30)
    # 일별 시리즈에도 할인이 반영되어야 함 (NaN 조용한 0 처리 금지)
    assert res.daily_gross.sum() == pytest.approx(-0.30)
    # 상폐 후 구간은 포지션 없음
    assert res.daily_gross.iloc[16:].eq(0).all()


def test_natural_exit_before_delisting_no_discount():
    # H=5 → 10일 청산, 상폐(15일) 전 → 할인 없음
    res = run_backtest(sig(), delisted_closes(), holding_days=5, cost=NO_COST, delist_discount=0.30)
    tr = res.trades.iloc[0]
    assert tr["delisted"] == False  # noqa: E712
    assert tr["gross_ret"] == pytest.approx(0.0)
    assert res.daily_gross.sum() == pytest.approx(0.0)


def test_healthy_ticker_unaffected():
    res = run_backtest(sig("B"), delisted_closes(), holding_days=20, cost=NO_COST, delist_discount=0.30)
    assert res.trades.iloc[0]["delisted"] == False  # noqa: E712
    assert res.daily_gross.sum() == pytest.approx(0.0)


def test_discount_off_preserves_legacy_behavior():
    # delist_discount=None → 기존 동작 (할인·강제청산 없음, NaN 청산가는 트레이드 제외)
    res = run_backtest(sig(), delisted_closes(), holding_days=20, cost=NO_COST, delist_discount=None)
    assert "delisted" not in res.trades.columns or not res.trades["delisted"].any()
    assert res.daily_gross.sum() == pytest.approx(0.0)  # 기존: 손실 미반영 (이게 문제였음을 명시)


def test_merger_delisting_forced_exit_without_discount():
    # v1.2 [2]: 합병·완전자회사화 상폐 = 손실형 집합에 없음 → 강제 청산은 하되 할인 없음
    res = run_backtest(
        sig(), delisted_closes(), holding_days=20, cost=NO_COST,
        delist_discount=0.30, delist_tickers={"OTHER"},  # A는 손실형 아님
    )
    tr = res.trades.iloc[0]
    assert tr["delisted"] == True  # noqa: E712
    assert tr["exit"] == DATES[14].date()
    assert tr["gross_ret"] == pytest.approx(0.0)  # 마지막 가격 청산, 무할인
    assert res.daily_gross.sum() == pytest.approx(0.0)


def test_loss_set_membership_applies_discount():
    res = run_backtest(
        sig(), delisted_closes(), holding_days=20, cost=NO_COST,
        delist_discount=0.30, delist_tickers={"A"},
    )
    assert res.trades.iloc[0]["gross_ret"] == pytest.approx(-0.30)


def test_short_position_discount_gains():
    # 숏 보유 중 상폐 → 청산 할인은 숏에 이익 (방향 부호 반영)
    res = run_backtest(sig(direction=-1), delisted_closes(), holding_days=20, cost=NO_COST, delist_discount=0.30)
    assert res.trades.iloc[0]["gross_ret"] == pytest.approx(+0.30)
