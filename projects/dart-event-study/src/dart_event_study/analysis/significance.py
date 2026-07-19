"""백테스트 성과의 통계적 유의성 (v1.2 #5).

실행:  uv run python -m dart_event_study.analysis.significance

1) 블록 부트스트랩 Sharpe CI — 일별 수익률의 자기상관을 보존한 채
   Sharpe의 표본 불확실성을 추정 (자사주 H=5 등 관심 전략).
2) Deflated Sharpe Ratio (Bailey & López de Prado 2014) — 우리가 시도한
   전략 조합 수(N=20: 스코프×H×변형)를 반영해, 관측된 최고 Sharpe가
   "여러 번 시도한 것 중 우연히 좋았던 것"일 확률을 걷어낸 유의확률.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

TRADING_DAYS = 252


def block_bootstrap_sharpe(
    daily: pd.Series, n_boot: int = 2000, block: int = 20, seed: int = 42
) -> dict:
    """무빙 블록 부트스트랩으로 연환산 Sharpe의 분포 추정.

    블록 단위 복원추출로 자기상관 구조를 보존한다 (iid 재추출은 CI를 과소평가).
    """
    r = daily.to_numpy()
    n = len(r)
    if n < block * 2:
        return {"sharpe": np.nan, "ci_lo": np.nan, "ci_hi": np.nan, "p_le_0": np.nan}
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    sharpes = np.empty(n_boot)
    for b in range(n_boot):
        sample = np.concatenate([r[s : s + block] for s in starts[b]])[:n]
        sd = sample.std()
        sharpes[b] = (sample.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0
    point = daily.mean() / daily.std() * np.sqrt(TRADING_DAYS)
    return {
        "sharpe": float(point),
        "ci_lo": float(np.percentile(sharpes, 2.5)),
        "ci_hi": float(np.percentile(sharpes, 97.5)),
        "p_le_0": float((sharpes <= 0).mean()),  # 부트스트랩 하에서 Sharpe≤0 비율
        "n_boot": n_boot,
        "block": block,
    }


def deflated_sharpe(daily: pd.Series, trial_sharpes_ann: list[float]) -> dict:
    """Deflated Sharpe Ratio — 시도 조합 수를 반영한 Sharpe 유의확률.

    trial_sharpes_ann: 시도한 전략 전체의 연환산 Sharpe 목록 (자기 자신 포함).
    반환 dsr = P(진짜 SR > 0 | 관측 SR, 시도 횟수 보정) — 1에 가까울수록 견고.
    """
    r = daily.to_numpy()
    t_len = len(r)
    sr_daily = r.mean() / r.std()  # 일 단위 SR
    skew = float(stats.skew(r))
    kurt = float(stats.kurtosis(r, fisher=False))

    trials = np.asarray(trial_sharpes_ann, dtype=float) / np.sqrt(TRADING_DAYS)  # 일 단위
    n_trials = len(trials)
    var_trials = trials.var(ddof=1) if n_trials > 1 else 0.0
    gamma = 0.5772156649
    # 시도 N번 중 최대 SR의 기댓값 (Bailey & Prado 2014)
    sr0 = np.sqrt(var_trials) * (
        (1 - gamma) * stats.norm.ppf(1 - 1 / n_trials)
        + gamma * stats.norm.ppf(1 - 1 / (n_trials * np.e))
    ) if n_trials > 1 else 0.0

    denom = np.sqrt(1 - skew * sr_daily + (kurt - 1) / 4 * sr_daily**2)
    z = (sr_daily - sr0) * np.sqrt(t_len - 1) / denom
    return {
        "sr_ann": float(sr_daily * np.sqrt(TRADING_DAYS)),
        "sr0_ann": float(sr0 * np.sqrt(TRADING_DAYS)),  # 우연 기대 최대 Sharpe
        "n_trials": n_trials,
        "dsr": float(stats.norm.cdf(z)),  # P(SR>0), 시도 수 보정 후
        "skew": skew,
        "kurt": kurt,
    }


def main() -> None:
    from quantlab_shared.backtest.costs import KOSPI_TAX_SCHEDULE, CostModel
    from quantlab_shared.backtest.engine import run_backtest
    from quantlab_shared.data.prices import PriceStore

    from dart_event_study.config import DATA_DIR, load_settings, load_universe

    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]
    c = settings["costs"]
    cost = CostModel(
        transaction_tax=c["transaction_tax"],
        slippage=c["slippage"],
        commission=c.get("commission", 0.0),
        tax_schedule=KOSPI_TAX_SCHEDULE if c.get("use_tax_schedule") else None,
    )

    signals = pd.read_parquet(DATA_DIR / f"signals_{mode}.parquet")
    store = PriceStore(DATA_DIR / "prices", start, end)
    closes = pd.DataFrame({t: store.ohlcv(t)["close"] for t in signals["ticker"].unique()})
    volumes = (
        pd.DataFrame({t: store.ohlcv(t)["volume"] for t in signals["ticker"].unique()})
        if settings["backtest"].get("roll_suspended")
        else None
    )

    delist_tickers = None
    if mode == "full" and universe.get("selection") == "proxy_2019":
        from dart_event_study.config import resolve_universe_asof

        delist_tickers = set(resolve_universe_asof(universe)["delisted_loss"])

    # 관심 전략: 자사주 H=5 (유일한 비용 생존자)
    res = run_backtest(
        signals[signals.event_type == "buyback"], closes, 5, cost,
        delist_discount=settings["backtest"].get("delist_discount"),
        delist_tickers=delist_tickers,
        volumes=volumes,
    )
    boot = block_bootstrap_sharpe(res.daily_net)

    # 시도한 전략 전체의 Sharpe (다중 시도 보정용) — 백테스트 결과 테이블에서
    bt = pd.read_parquet(DATA_DIR / f"backtest_{mode}.parquet")
    trials = bt[~bt["scope"].str.contains("gross|KOSPI")]["sharpe"].dropna().tolist()
    dsr = deflated_sharpe(res.daily_net, trials)

    out = {"bootstrap_buyback_h5": boot, "deflated_sharpe_buyback_h5": dsr}
    pd.Series(out).to_json(DATA_DIR / f"significance_{mode}.json")

    print("자사주 H=5 (net) Sharpe 유의성:")
    print(f"  점추정 {boot['sharpe']:.3f}, 95% CI [{boot['ci_lo']:.3f}, {boot['ci_hi']:.3f}], "
          f"P(Sharpe<=0) = {boot['p_le_0']:.1%}  (블록 부트스트랩 {boot['n_boot']}회, 블록 {boot['block']}일)")
    print(f"  Deflated SR: 관측 {dsr['sr_ann']:.3f} vs 시도 {dsr['n_trials']}개 중 우연 기대 최대 {dsr['sr0_ann']:.3f}")
    print(f"  → DSR = {dsr['dsr']:.1%} (시도 수 보정 후 진짜 SR>0일 확률; 95% 미만이면 우연과 구분 불가)")


if __name__ == "__main__":
    main()
