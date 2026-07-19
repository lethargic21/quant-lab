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
    assert s2["thin_sample"] is False and s2["p_naive"] < 0.01


def test_clustered_t_singleton_clusters_matches_naive():
    from dart_event_study.analysis.event_study import clustered_t
    from scipy import stats as sps

    vals = list(rng.normal(0.02, 0.01, 80))
    t_naive, _ = sps.ttest_1samp(vals, 0)
    t_cl, _ = clustered_t(vals, clusters=range(80))  # 전부 싱글턴
    assert t_cl == pytest.approx(float(t_naive), rel=1e-9)


def test_clustered_t_shrinks_with_within_cluster_correlation():
    from dart_event_study.analysis.event_study import clustered_t
    from scipy import stats as sps

    # 10개 클러스터, 클러스터 안 10개 값이 완전 동일 (상관 1) → 유효표본 10
    base = rng.normal(0.02, 0.01, 10)
    vals = np.repeat(base, 10)
    clusters = np.repeat(range(10), 10)
    t_naive, _ = sps.ttest_1samp(vals, 0)
    t_cl, _ = clustered_t(vals, clusters)
    # naive는 n=100인 척 부풀고, 클러스터 t는 유효표본 10 수준으로 축소돼야 함
    assert abs(t_cl) < abs(float(t_naive)) / 2
    t_eff, _ = sps.ttest_1samp(base, 0)  # 진짜 표본 10개의 t와 같은 규모
    assert abs(t_cl) == pytest.approx(abs(float(t_eff)) * np.sqrt(10 / 9) / np.sqrt(10 / 9), rel=0.35)


def test_bh_fdr():
    from dart_event_study.analysis.event_study import bh_fdr

    # 교과서 예: p=[0.01, 0.02, 0.03, 0.04] m=4 → q=[0.04, 0.04, 0.04, 0.04]
    q = bh_fdr([0.01, 0.02, 0.03, 0.04])
    assert q == pytest.approx([0.04, 0.04, 0.04, 0.04])
    # 강한 신호는 살아남고 경계 신호는 커짐
    q2 = bh_fdr([0.0001, 0.04, 0.5, np.nan])
    assert q2[0] == pytest.approx(0.0003)
    assert q2[1] == pytest.approx(0.06)  # 0.04 * 3/2
    assert q2[2] == pytest.approx(0.5)
    assert np.isnan(q2[3])
    # 순서 불변성
    assert bh_fdr([0.5, 0.0001, 0.04])[1] == pytest.approx(0.0003)


def test_clustered_t_single_cluster_returns_nan():
    from dart_event_study.analysis.event_study import clustered_t

    t, p = clustered_t([0.01, 0.02, 0.03], clusters=["a", "a", "a"])
    assert np.isnan(t) and np.isnan(p)
