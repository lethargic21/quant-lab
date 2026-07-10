"""이벤트 스터디 핵심 로직 검증 — 합성 수익률 (네트워크 불필요)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from dart_event_study.analysis.event_study import car, market_model_ar, summarize_cars

# 250 거래일 (주말 제외), 이벤트일 = 200번째 날
DATES = pd.bdate_range("2023-01-02", periods=250)
DAY0 = DATES[200].date()

rng = np.random.default_rng(42)
MKT = pd.Series(rng.normal(0.0005, 0.01, 250), index=DATES)


def test_stock_equals_market_gives_zero_ar():
    # 종목 = 시장 그대로 (β=1, α=0) → AR ≈ 0
    ar = market_model_ar(MKT.copy(), MKT, DAY0, est_len=120, gap=10, rel_range=(-1, 5))
    assert ar is not None
    assert abs(ar).max() < 1e-10


def test_event_day_jump_shows_in_car():
    stock = MKT.copy()
    stock.iloc[200] += 0.10  # 이벤트일 +10% 점프
    ar = market_model_ar(stock, MKT, DAY0, est_len=120, gap=10, rel_range=(-1, 5))
    assert ar.loc[0] == pytest.approx(0.10, abs=1e-6)
    assert car(ar, (0, 5)) == pytest.approx(0.10, abs=1e-6)
    assert car(ar, (-1, -1)) == pytest.approx(0.0, abs=1e-6)


def test_beta_estimation():
    # 종목 = 2×시장 → β=2 반영되어 AR ≈ 0
    stock = MKT * 2
    ar = market_model_ar(stock, MKT, DAY0, est_len=120, gap=10, rel_range=(0, 5))
    assert abs(ar).max() < 1e-10


def test_gap_excludes_event_leakage():
    # 이벤트 직전(갭 구간)의 급등이 추정에 섞이지 않아야 함
    stock = MKT.copy()
    stock.iloc[195] += 0.50  # 갭 구간(day -5) 급등
    ar = market_model_ar(stock, MKT, DAY0, est_len=120, gap=10, rel_range=(0, 0))
    assert abs(ar.loc[0]) < 0.01  # 추정이 오염됐다면 α가 틀어져 AR이 크게 벗어남


def test_insufficient_estimation_returns_none():
    early_day = DATES[30].date()  # 앞쪽 이벤트 — 추정 표본 부족
    assert market_model_ar(MKT.copy(), MKT, early_day, 120, 10, (0, 5)) is None


def test_day0_not_trading_returns_none():
    assert market_model_ar(MKT.copy(), MKT, dt.date(2023, 1, 1), 120, 10, (0, 5)) is None


def test_summarize_flags_thin_sample():
    s = summarize_cars([0.01] * 5)
    assert s["N"] == 5 and s["thin_sample"] is True
    s2 = summarize_cars(list(rng.normal(0.02, 0.01, 100)))
    assert s2["thin_sample"] is False and s2["p"] < 0.01
