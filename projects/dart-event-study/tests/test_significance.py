"""부트스트랩 Sharpe CI + Deflated Sharpe 검증 (v1.2 #5, 네트워크 불필요)."""

import numpy as np
import pandas as pd
from dart_event_study.analysis.significance import block_bootstrap_sharpe, deflated_sharpe

rng = np.random.default_rng(7)
DATES = pd.bdate_range("2019-01-01", periods=1500)


def make_series(mu, sigma=0.01):
    return pd.Series(rng.normal(mu, sigma, len(DATES)), index=DATES)


def test_bootstrap_ci_covers_strong_signal():
    # 뚜렷한 양의 SR → CI 하한 > 0, P(SR<=0) 매우 낮음
    # (mu=0.002 → 연 Sharpe ≈ 3.2; 표본 실현치가 시드에 따라 ±0.8 흔들려도 하한이 0 위)
    daily = make_series(mu=0.002)
    b = block_bootstrap_sharpe(daily, n_boot=500)
    assert b["ci_lo"] > 0
    assert b["p_le_0"] < 0.01
    assert b["ci_lo"] < b["sharpe"] < b["ci_hi"]


def test_bootstrap_zero_signal_ci_straddles_zero():
    daily = make_series(mu=0.0)
    b = block_bootstrap_sharpe(daily, n_boot=500)
    assert b["ci_lo"] < 0 < b["ci_hi"]
    assert 0.1 < b["p_le_0"] < 0.9


def test_bootstrap_deterministic_with_seed():
    daily = make_series(mu=0.0005)
    assert block_bootstrap_sharpe(daily, n_boot=200) == block_bootstrap_sharpe(daily, n_boot=200)


def test_deflated_sharpe_shrinks_with_more_trials():
    daily = make_series(mu=0.0004)  # 완만한 양의 SR
    few = deflated_sharpe(daily, trial_sharpes_ann=[0.6, 0.1])
    many = deflated_sharpe(daily, trial_sharpes_ann=list(rng.normal(0, 0.5, 50)) + [0.6])
    # 시도가 많고 분산이 크면 우연 기대 최대(sr0)가 커져 DSR이 내려간다
    assert many["sr0_ann"] > few["sr0_ann"]
    assert many["dsr"] < few["dsr"]


def test_deflated_sharpe_strong_signal_survives():
    daily = make_series(mu=0.002)  # 연 Sharpe ≈ 3+
    d = deflated_sharpe(daily, trial_sharpes_ann=list(rng.normal(0, 0.3, 20)))
    assert d["dsr"] > 0.99
