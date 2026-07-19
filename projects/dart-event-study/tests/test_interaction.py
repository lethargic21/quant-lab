"""상호작용 회귀 검증 — 합성 데이터 (네트워크 불필요).

핵심: (1) OLS 계수 복원, (2) 클러스터-로버스트 SE가 CR1 정의와 일치,
(3) 심어둔 상호작용을 실제로 잡아내는가, (4) 효과 없으면 비유의(위양성 안 냄).
"""

import numpy as np
import pandas as pd
import pytest
from dart_event_study.analysis.interaction import ols_cluster, run_spec


def test_ols_recovers_known_coefficients():
    g = np.random.default_rng(0)
    n = 500
    x1, x2 = g.normal(size=n), g.normal(size=n)
    y = 1.5 + 2.0 * x1 - 0.5 * x2 + g.normal(0, 0.01, n)
    X = np.column_stack([np.ones(n), x1, x2])
    res = ols_cluster(y, X, clusters=range(n), names=["const", "x1", "x2"])
    coef = dict(zip(res["term"], res["coef"], strict=True))
    assert coef["const"] == pytest.approx(1.5, abs=0.01)
    assert coef["x1"] == pytest.approx(2.0, abs=0.01)
    assert coef["x2"] == pytest.approx(-0.5, abs=0.01)
    assert res.attrs["r2"] > 0.99


def test_cluster_se_matches_manual_cr1():
    """CR1 공식을 독립적으로 계산해 일치 확인 (구현 가드)."""
    g = np.random.default_rng(1)
    n, k = 120, 2
    x = g.normal(size=n)
    y = 0.3 + 0.8 * x + g.normal(0, 0.5, n)
    X = np.column_stack([np.ones(n), x])
    clusters = np.repeat(np.arange(12), 10)

    res = ols_cluster(y, X, clusters, ["const", "x"])

    xtx_inv = np.linalg.inv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    u = y - X @ beta
    meat = np.zeros((k, k))
    for grp in np.unique(clusters):
        idx = clusters == grp
        s = X[idx].T @ u[idx]
        meat += np.outer(s, s)
    n_g = 12
    c = (n_g / (n_g - 1)) * ((n - 1) / (n - k))
    se_manual = np.sqrt(np.diag(xtx_inv @ meat @ xtx_inv * c))
    assert res["se"].to_numpy() == pytest.approx(se_manual, rel=1e-10)
    assert res.attrs["n_clusters"] == 12


def test_cluster_se_inflates_with_correlated_clusters():
    """클러스터 내 잔차가 완전 상관이면 SE가 순진한 OLS보다 커져야 한다."""
    g = np.random.default_rng(2)
    n_g, per = 15, 20
    n = n_g * per
    x = g.normal(size=n)
    shock = np.repeat(g.normal(0, 1.0, n_g), per)  # 클러스터 공통 충격
    y = 0.5 * x + shock
    X = np.column_stack([np.ones(n), x])
    clusters = np.repeat(np.arange(n_g), per)

    clustered = ols_cluster(y, X, clusters, ["const", "x"])
    singleton = ols_cluster(y, X, np.arange(n), ["const", "x"])
    # 상수항(공통 충격을 그대로 받는 항)의 SE가 클러스터 보정에서 크게 커짐
    assert clustered.loc[0, "se"] > singleton.loc[0, "se"] * 2


def _panel(n_per_type=80, interaction=0.0, main=0.0, seed=3):
    """합성 이벤트 패널: attn_post 효과(main) + 신탁에서의 추가 효과(interaction)."""
    g = np.random.default_rng(seed)
    rows = []
    for is_trust in (0.0, 1.0):
        for i in range(n_per_type):
            attn = g.normal()
            car = main * attn + interaction * attn * is_trust + g.normal(0, 0.05)
            rows.append({
                "is_trust": is_trust,
                "attn_post": attn,
                "car_primary": car,
                "car_nonoverlap": car,
                "log_mktcap": g.normal(28, 1),
                "est_vol": abs(g.normal(0.02, 0.005)),
                "month": f"20{20 + i % 4}-{1 + i % 12:02d}",
            })
    return pd.DataFrame(rows)


def test_detects_injected_interaction():
    df = _panel(interaction=0.08, main=0.02, seed=4)
    res = run_spec(df, "car_primary", with_controls=True)
    row = res.set_index("term").loc["attn_x_trust"]
    assert row["coef"] == pytest.approx(0.08, abs=0.02)
    assert row["p"] < 0.01


def test_null_interaction_not_significant():
    df = _panel(interaction=0.0, main=0.0, seed=5)
    res = run_spec(df, "car_primary", with_controls=True)
    row = res.set_index("term").loc["attn_x_trust"]
    assert row["p"] > 0.05  # 효과 없으면 위양성 안 냄


def test_run_spec_includes_controls_only_when_asked():
    df = _panel(seed=6)
    with_c = run_spec(df, "car_primary", with_controls=True)
    without_c = run_spec(df, "car_primary", with_controls=False)
    assert "log_mktcap" in set(with_c["term"]) and "est_vol" in set(with_c["term"])
    assert "log_mktcap" not in set(without_c["term"])
    # 상호작용 항은 두 스펙 모두에 존재
    assert "attn_x_trust" in set(with_c["term"]) and "attn_x_trust" in set(without_c["term"])


def test_run_spec_drops_rows_with_missing_controls():
    df = _panel(seed=7)
    df.loc[:19, "log_mktcap"] = np.nan
    full = run_spec(df, "car_primary", with_controls=False)
    ctrl = run_spec(df, "car_primary", with_controls=True)
    assert ctrl.attrs["n"] == full.attrs["n"] - 20
