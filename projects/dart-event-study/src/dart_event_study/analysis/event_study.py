"""이벤트 스터디 — 시장모형 AAR/CAR + 윈도우별 유의성 (Phase 4a).

실행:  uv run python -m dart_event_study.analysis.event_study

방법:
- 정상수익률 = 시장모형 R_i = α + β·R_KOSPI (추정: 이벤트 전 gap 띄우고 est_len 거래일)
- AR = 실현수익률 − 정상수익률, day 0 = 접수일 이후 첫 거래일(접수일 포함)
- CAR = config의 모든 윈도우에 대해 계산 — 테스트한 윈도우 전부 리포트 (절대 원칙 5)
- 유의성 = 이벤트 단면 1표본 t-검정. 표본 얇은 그룹은 결론 유보 딱지.

AAR/CAR 통계는 프로젝트 내부에 둔다 (확정 결정 3 — YAGNI).
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from scipy import stats

MIN_EST_OBS = 60  # 추정 표본 최소치 — 미달 이벤트는 제외(카운트 리포트)
THIN_SAMPLE = 30  # 이보다 얇으면 "결론 유보" 딱지


def market_model_ar(
    stock_ret: pd.Series,
    mkt_ret: pd.Series,
    day0: dt.date,
    est_len: int,
    gap: int,
    rel_range: tuple[int, int],
) -> pd.Series | None:
    """단일 이벤트의 상대일별 AR. 추정 표본 부족 시 None.

    stock_ret/mkt_ret: date 인덱스 일별 수익률. day0: 이벤트일(거래일).
    rel_range: (시작, 끝) 상대 거래일 — 양끝 포함.
    """
    joined = pd.concat({"stock": stock_ret, "mkt": mkt_ret}, axis=1).dropna()
    dates = joined.index
    pos = dates.searchsorted(pd.Timestamp(day0))
    if pos >= len(dates) or dates[pos].date() != day0:
        return None  # day0에 해당 종목 데이터 없음 (거래정지 등)

    est = joined.iloc[max(0, pos - gap - est_len) : pos - gap]
    if len(est) < MIN_EST_OBS:
        return None
    beta, alpha = np.polyfit(est["mkt"], est["stock"], 1)

    lo, hi = rel_range
    idx_lo, idx_hi = pos + lo, pos + hi
    if idx_lo < 0 or idx_hi >= len(dates):
        return None  # 윈도우가 데이터 범위를 벗어남 (기간 경계)
    win = joined.iloc[idx_lo : idx_hi + 1]
    ar = win["stock"] - (alpha + beta * win["mkt"])
    ar.index = range(lo, hi + 1)
    return ar


def car(ar: pd.Series, window: tuple[int, int]) -> float:
    """상대일 AR 시리즈에서 윈도우 [a, b] 누적초과수익률."""
    a, b = window
    return float(ar.loc[a:b].sum())


def summarize_cars(cars: list[float]) -> dict:
    """이벤트 단면 CAR 통계: N, 평균, naive t/p, 양수비율.

    ⚠️ naive t는 이벤트 간 독립을 가정 — 실적공시처럼 같은 날짜에 몰리는
    이벤트는 잔차가 상관되어 과대추정된다. clustered_t와 병기할 것.
    """
    arr = np.array(cars)
    n = len(arr)
    out = {"N": n, "mean_car": arr.mean() if n else np.nan, "pos_ratio": (arr > 0).mean() if n else np.nan}
    if n >= 2:
        t, p = stats.ttest_1samp(arr, 0)
        out |= {"t_naive": t, "p_naive": p}
    else:
        out |= {"t_naive": np.nan, "p_naive": np.nan}
    out["thin_sample"] = n < THIN_SAMPLE
    return out


def clustered_t(values, clusters) -> tuple[float, float]:
    """평균=0 가설의 클러스터-로버스트 t/p (CR1 보정, df = G−1).

    상수항 회귀의 클러스터-로버스트 분산: Var(평균) = G/(G−1) × Σ_g(클러스터 잔차합)² / n².
    클러스터가 전부 싱글턴이면 naive 분산과 일치한다 (검증: 테스트).
    """
    v = np.asarray(values, dtype=float)
    n = len(v)
    g = pd.Series(list(clusters))
    n_g = g.nunique()
    if n < 2 or n_g < 2:
        return np.nan, np.nan
    mean = v.mean()
    cluster_sums = pd.Series(v - mean).groupby(g.values).sum()
    var_mean = (n_g / (n_g - 1)) * float((cluster_sums**2).sum()) / n**2
    if var_mean <= 0:
        return np.nan, np.nan
    t = mean / np.sqrt(var_mean)
    p = 2 * stats.t.sf(abs(t), df=n_g - 1)
    return float(t), float(p)


def run_event_study(
    signals: pd.DataFrame,
    returns: dict[str, pd.Series],
    mkt_ret: pd.Series,
    est_len: int,
    gap: int,
    car_windows: list[tuple[int, int]],
    aar_range: tuple[int, int] = (-10, 20),
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """전체 이벤트 스터디.

    반환: (CAR 요약 테이블, AAR 곡선 테이블, 제외 카운트)
    그룹 = event_type × direction — 방향 룰이 실제로 갈라내는지 raw CAR로 확인.
    """
    lo = min(aar_range[0], min(w[0] for w in car_windows))
    hi = max(aar_range[1], max(w[1] for w in car_windows))

    dropped = {"가격데이터_없음": 0, "추정표본_부족/경계": 0}
    per_event: list[dict] = []
    ar_curves: dict[tuple, list[pd.Series]] = {}

    for _, sig in signals.iterrows():
        ret = returns.get(sig["ticker"])
        if ret is None:
            dropped["가격데이터_없음"] += 1
            continue
        # 이벤트일 = 접수일 이후 첫 거래일(접수일 포함) — 시장 반응 측정 기준
        rcept = dt.datetime.strptime(sig["rcept_dt"], "%Y%m%d").date()
        day0_ts = ret.index[ret.index.searchsorted(pd.Timestamp(rcept)) :]
        if not len(day0_ts):
            dropped["추정표본_부족/경계"] += 1
            continue
        ar = market_model_ar(ret, mkt_ret, day0_ts[0].date(), est_len, gap, (lo, hi))
        if ar is None:
            dropped["추정표본_부족/경계"] += 1
            continue

        key = (sig["event_type"], int(sig["direction"]))
        ar_curves.setdefault(key, []).append(ar)
        d0 = day0_ts[0].date()
        rec = {
            "event_type": sig["event_type"],
            "direction": int(sig["direction"]),
            "day0": d0.isoformat(),
            "month": d0.strftime("%Y-%m"),  # 클러스터링 보정용
        }
        for w in car_windows:
            rec[f"car_{w[0]}_{w[1]}"] = car(ar, w)
        per_event.append(rec)

    ev_df = pd.DataFrame(per_event)
    summary_rows = []
    for (etype, direction), grp in ev_df.groupby(["event_type", "direction"]):
        for w in car_windows:
            cars = grp[f"car_{w[0]}_{w[1]}"]
            s = summarize_cars(cars.tolist())
            # 이벤트 날짜 뭉침(cross-sectional dependence) 보정 — naive와 병기
            t_day, p_day = clustered_t(cars, grp["day0"])
            t_mon, p_mon = clustered_t(cars, grp["month"])
            s |= {
                "t_cl_day": t_day, "p_cl_day": p_day,
                "n_cl_day": grp["day0"].nunique(),
                "t_cl_month": t_mon, "p_cl_month": p_mon,
                "n_cl_month": grp["month"].nunique(),
            }
            summary_rows.append({"event_type": etype, "direction": direction, "window": f"[{w[0]},{w[1]}]"} | s)
    summary = pd.DataFrame(summary_rows)

    aar_rows = []
    for (etype, direction), curves in ar_curves.items():
        aar = pd.concat(curves, axis=1).mean(axis=1)
        for rel_day, v in aar.loc[aar_range[0] : aar_range[1]].items():
            aar_rows.append({"event_type": etype, "direction": direction, "rel_day": rel_day, "aar": v})
    aar_df = pd.DataFrame(aar_rows)

    return summary, aar_df, dropped


def main() -> None:
    from quantlab_shared.data.prices import PriceStore

    from dart_event_study.config import DATA_DIR, load_settings, load_universe

    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]
    es_cfg = settings["event_study"]
    windows = [tuple(w) for w in es_cfg["car_windows"]]

    signals = pd.read_parquet(DATA_DIR / f"signals_{mode}.parquet")
    store = PriceStore(DATA_DIR / "prices", start, end)
    returns = {t: store.ohlcv(t)["close"].pct_change().dropna() for t in signals["ticker"].unique()}
    mkt_ret = store.ohlcv("KS11")["close"].pct_change().dropna()

    summary, aar_df, dropped = run_event_study(
        signals, returns, mkt_ret, es_cfg["estimation_window"], es_cfg["gap"], windows
    )

    out_dir = DATA_DIR / f"eventstudy_{mode}"
    out_dir.mkdir(exist_ok=True)
    summary.to_parquet(out_dir / "car_summary.parquet")
    aar_df.to_parquet(out_dir / "aar_curves.parquet")

    print(f"이벤트 스터디: 시그널 {len(signals)}건, 제외 {dropped}")
    print(f"저장: {out_dir}\n")
    pd.set_option("display.float_format", lambda v: f"{v:.4f}")
    print("CAR 요약 (전 윈도우 리포트 — 절대 원칙 5):")
    print(
        summary.assign(flag=summary["thin_sample"].map({True: "⚠️표본유보", False: ""}))
        .drop(columns="thin_sample")
        .to_string(index=False)
    )


if __name__ == "__main__":
    main()
