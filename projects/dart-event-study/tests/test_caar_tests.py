"""CAAR 검정 배터리 검증 — 합성 데이터 (네트워크 불필요).

핵심 회귀 테스트: (1) car_fast/event_inputs가 기존 market_model_ar과 같은 CAR을 내는가,
(2) 각 검정이 알려진 입력에서 기대대로 동작하는가, (3) placebo가 효과 없음/있음을 구분하는가.
"""

import datetime as dt

import numpy as np
import pandas as pd
import pytest
from dart_event_study.analysis.caar_tests import (
    Panel,
    bmp_test,
    build_panel,
    build_prefix,
    caar_bootstrap_ci,
    car_at_positions,
    car_fast,
    corrado_rank_test,
    day0_pos,
    event_inputs,
    placebo_test,
    run_caar_tests,
    sign_test,
)
from dart_event_study.analysis.event_study import car, market_model_ar

DATES = pd.bdate_range("2022-01-03", periods=400)
DAY0 = DATES[300].date()
rng = np.random.default_rng(7)
MKT = pd.Series(rng.normal(0.0004, 0.01, 400), index=DATES)


# ── 1. 기존 event_study와의 정합성 (핵심 회귀 가드) ──────────────────────────


def test_car_fast_matches_market_model_ar():
    stock = MKT + rng.normal(0, 0.008, 400)  # 잡음 있는 종목
    stock.iloc[300] += 0.05
    panel = build_panel(stock, MKT)
    for w in [(-1, 1), (0, 5), (0, 20)]:
        ar = market_model_ar(stock, MKT, DAY0, est_len=120, gap=10, rel_range=w)
        expected = car(ar, w)
        pos = day0_pos(panel, DAY0)
        got = car_fast(panel, pos, est_len=120, gap=10, window=w)
        assert got == pytest.approx(expected, abs=1e-9), f"window {w}"


def test_car_at_positions_matches_car_fast():
    # 벡터화 prefix-sum CAR가 스칼라 car_fast와 정확히 일치 (placebo 경로 가드)
    stock = MKT + rng.normal(0, 0.008, 400)
    panel = build_panel(stock, MKT)
    pfx = build_prefix(panel)
    positions = np.array([160, 200, 250, 300, 350])
    for w in [(-1, 1), (0, 5), (0, 20)]:
        vec = car_at_positions(pfx, positions, est_len=120, gap=10, window=w)
        for k, pos in enumerate(positions):
            scalar = car_fast(panel, int(pos), 120, 10, w)
            assert vec[k] == pytest.approx(scalar, abs=1e-9), f"window {w} pos {pos}"


def test_car_at_positions_out_of_range_nan():
    panel = build_panel(MKT.copy(), MKT)
    pfx = build_prefix(panel)
    # est_start 음수(위치 10) / 윈도우 초과(맨 끝) → nan
    vec = car_at_positions(pfx, np.array([10, 399]), est_len=120, gap=10, window=(0, 5))
    assert np.isnan(vec).all()


def test_event_inputs_car_matches():
    stock = MKT + rng.normal(0, 0.008, 400)
    panel = build_panel(stock, MKT)
    ei = event_inputs(panel, DAY0, est_len=120, gap=10, window=(0, 5))
    ar = market_model_ar(stock, MKT, DAY0, 120, 10, (0, 5))
    assert ei.car == pytest.approx(car(ar, (0, 5)), abs=1e-9)
    assert ei.win_ars.sum() == pytest.approx(ei.car, abs=1e-12)


def test_event_inputs_scar_finite_and_positive_on_jump():
    # 잡음 있는 종목(잔차분산>0) + 이벤트일 +10% 점프 → SCAR 유한·양수
    stock = MKT + rng.normal(0, 0.008, 400)
    stock.iloc[300] += 0.10
    panel = build_panel(stock, MKT)
    ei = event_inputs(panel, DAY0, 120, 10, (0, 5))
    assert np.isfinite(ei.scar) and ei.scar > 0


def test_event_inputs_zero_residual_scar_nan():
    # 종목 ≡ 시장 → 잔차분산 0 → 예측오차 SD 0 → SCAR = 0/0 = nan (bmp_test가 걸러냄)
    panel = build_panel(MKT.copy(), MKT)
    ei = event_inputs(panel, DAY0, 120, 10, (0, 5))
    assert abs(ei.car) < 1e-10
    assert np.isnan(ei.scar)


def test_event_inputs_boundary_none():
    panel = build_panel(MKT.copy(), MKT)
    assert event_inputs(panel, DATES[20].date(), 120, 10, (0, 5)) is None  # 추정 부족
    assert event_inputs(panel, dt.date(2022, 1, 1), 120, 10, (0, 5)) is None  # 비거래일


# ── 2. BMP ───────────────────────────────────────────────────────────────────


def test_bmp_zero_mean_scar_insignificant():
    # 대칭 구성(평균 정확히 0) → t=0, p=1 (난수 꼬리로 인한 flaky 회피)
    half = np.random.default_rng(123).normal(0, 1, 100)
    scars = list(half) + list(-half)
    r = bmp_test(scars)
    assert r["bmp_t"] == pytest.approx(0.0, abs=1e-9) and r["bmp_p"] > 0.05


def test_bmp_shifted_scar_significant():
    scars = list(rng.normal(0.5, 1, 200))  # 양의 평균
    r = bmp_test(scars)
    assert r["bmp_t"] > 0 and r["bmp_p"] < 0.01
    assert r["n_bmp"] == 200


def test_bmp_ignores_nonfinite():
    r = bmp_test([1.0, 2.0, np.nan, 1.5])
    assert r["n_bmp"] == 3


def test_bmp_matches_manual_ttest():
    scars = list(rng.normal(0.3, 1, 50))
    from scipy import stats as sps

    t_manual, p_manual = sps.ttest_1samp(scars, 0)
    r = bmp_test(scars)
    assert r["bmp_t"] == pytest.approx(float(t_manual))
    assert r["bmp_p"] == pytest.approx(float(p_manual))


# ── 3. Corrado rank ──────────────────────────────────────────────────────────


def test_corrado_positive_when_window_ranks_top():
    # 윈도우 AR을 추정기간 어떤 값보다도 크게 → 윈도우 순위가 항상 최상위
    n, est_n, win_n = 40, 60, 3
    est = [rng.normal(0, 1, est_n) for _ in range(n)]
    win = [np.full(win_n, 100.0) for _ in range(n)]  # 극단적 양수
    r = corrado_rank_test(est, win)
    assert r["corrado_t"] > 3 and r["corrado_p"] < 0.01
    assert r["n_corrado"] == n


def test_corrado_null_insignificant():
    # 고정 시드 → 재현 가능한 대표 귀무 표본 (효과 없음 → 비유의)
    g = np.random.default_rng(101)
    n, est_n, win_n = 80, 100, 3
    est = [g.normal(0, 1, est_n) for _ in range(n)]
    win = [g.normal(0, 1, win_n) for _ in range(n)]
    r = corrado_rank_test(est, win)
    assert abs(r["corrado_t"]) < 1.96 and r["corrado_p"] > 0.05


def test_corrado_handles_variable_est_length():
    # 추정 길이가 섞여도 최빈 길이만 사용 (매트릭스 정렬)
    est = [rng.normal(0, 1, 60) for _ in range(30)] + [rng.normal(0, 1, 59) for _ in range(2)]
    win = [rng.normal(0, 1, 3) for _ in range(32)]
    r = corrado_rank_test(est, win)
    assert r["n_corrado"] == 30  # 59짜리 2개 제외


# ── 4. 일반화 부호검정 ───────────────────────────────────────────────────────


def test_sign_test_all_positive():
    r = sign_test([0.01] * 50, est_pos_fracs=[0.5] * 50)
    assert r["pos_ratio"] == 1.0 and r["sign_z"] > 0 and r["sign_p"] < 1e-6


def test_sign_test_balanced_insignificant():
    cars = [0.01] * 25 + [-0.01] * 25
    r = sign_test(cars, est_pos_fracs=[0.5] * 50)
    assert r["sign_z"] == pytest.approx(0.0, abs=1e-9)
    assert r["sign_p"] == pytest.approx(1.0)


def test_sign_test_uses_estimation_baseline():
    # 추정기간 양수비율 0.7 → 귀무 0.7 대비 검정 (0.5 대비보다 보수적)
    cars = [0.01] * 35 + [-0.01] * 15  # 70% 양수
    r = sign_test(cars, est_pos_fracs=[0.7] * 50)
    assert r["p_hat"] == pytest.approx(0.7)
    assert r["sign_z"] == pytest.approx(0.0, abs=1e-9)  # 관측 70% == 귀무 70%


# ── 5. 부트스트랩 CI ─────────────────────────────────────────────────────────


def test_bootstrap_ci_brackets_mean_and_deterministic():
    cars = list(rng.normal(0.03, 0.05, 200))
    r1 = caar_bootstrap_ci(cars, n_boot=1000, seed=1)
    r2 = caar_bootstrap_ci(cars, n_boot=1000, seed=1)
    assert r1 == r2  # 시드 고정 → 재현
    assert r1["boot_ci_lo"] < r1["caar"] < r1["boot_ci_hi"]
    assert r1["boot_p"] < 0.05  # 평균 0.03, 노이즈 대비 뚜렷


def test_bootstrap_ci_null_wide():
    # 평균을 정확히 0으로 센터링 → CAAR=0, CI가 0을 감싸고 p 큼 (flaky 회피)
    raw = np.random.default_rng(2).normal(0.0, 0.05, 200)
    cars = list(raw - raw.mean())
    r = caar_bootstrap_ci(cars, n_boot=1000, seed=2)
    assert r["boot_ci_lo"] < 0 < r["boot_ci_hi"]
    assert r["boot_p"] > 0.05


# ── 6. Placebo ───────────────────────────────────────────────────────────────


def _make_panels(n_tickers=8, n_days=400, seed=0):
    g = np.random.default_rng(seed)
    dates = pd.bdate_range("2022-01-03", periods=n_days)
    mkt = g.normal(0.0004, 0.01, n_days)
    panels = {}
    for i in range(n_tickers):
        stock = mkt + g.normal(0, 0.008, n_days)  # 진짜 초과수익 없음
        panels[f"T{i:03d}"] = Panel(stock=stock, mkt=mkt, dates=dates)
    return panels, dates


def test_placebo_no_effect_not_significant():
    panels, dates = _make_panels(seed=11)
    events = [(tk, dates[150 + i * 10].date()) for i, tk in enumerate(panels)]
    r = placebo_test(panels, events, 120, 10, (0, 5), n_perm=300, seed=3)
    # 실제 이벤트일에도 아무 효과 없음 → 실제 CAAR이 귀무분포 안 → p 크게
    assert r["placebo_p"] > 0.05
    assert abs(r["placebo_null_mean"]) < 0.01  # 귀무 중심 ≈ 0


def test_placebo_detects_injected_effect():
    panels, dates = _make_panels(seed=12)
    events = []
    for i, tk in enumerate(panels):
        d0_idx = 150 + i * 10
        p = panels[tk]
        # 실제 이벤트일에만 +6% 점프 주입 → placebo(랜덤일)로는 재현 불가
        p.stock[d0_idx] += 0.06
        events.append((tk, dates[d0_idx].date()))
    r = placebo_test(panels, events, 120, 10, (0, 5), n_perm=300, seed=4)
    assert r["real_caar"] > 0.04
    assert r["placebo_p"] < 0.05  # 가짜 이벤트로는 이 효과 안 나옴


def test_placebo_excludes_real_event_buffer():
    # buffer가 실제 이벤트 근방을 후보에서 빼는지 (주입 효과가 null에 새지 않음)
    panels, dates = _make_panels(n_tickers=6, seed=13)
    events = []
    for tk in panels:
        d0_idx = 200
        panels[tk].stock[d0_idx] += 0.06
        events.append((tk, dates[d0_idx].date()))
    r = placebo_test(panels, events, 120, 10, (0, 5), n_perm=200, buffer=30, seed=5)
    # 모든 종목 같은 위치에 효과 → null_mean은 0 근처여야 (효과일 제외됨)
    assert abs(r["placebo_null_mean"]) < 0.015


# ── 7. 오케스트레이션 스모크 테스트 ──────────────────────────────────────────


def test_run_caar_tests_end_to_end():
    panels, dates = _make_panels(n_tickers=10, seed=20)
    # 합성 시그널 테이블 (자사주 40건, 실적 40건)
    rows = []
    tickers = list(panels)
    for i in range(80):
        tk = tickers[i % len(tickers)]
        d0_idx = 150 + (i % 20) * 8
        rows.append({
            "ticker": tk,
            "rcept_dt": dates[d0_idx].strftime("%Y%m%d"),
            "event_type": "buyback" if i < 40 else "earnings",
            "direction": 1,
        })
    signals = pd.DataFrame(rows)
    out = run_caar_tests(signals, panels, 120, 10, [(-1, 1), (0, 5)], n_perm=100, seed=1)
    assert not out.empty
    # 각 그룹×윈도우 행에 모든 검정 컬럼이 존재
    for col in ["bmp_p", "corrado_p", "sign_p", "boot_p", "placebo_p", "q_bmp", "q_placebo"]:
        assert col in out.columns
        assert out[col].notna().any()
    # q값은 p값 이상 (BH-FDR 단조)
    assert (out["q_bmp"].dropna() >= out["bmp_p"].dropna() - 1e-9).all()
