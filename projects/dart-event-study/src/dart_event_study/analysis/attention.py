"""감성 레이어 분석 CLI — 뉴스 반응 크기별 자사주 드리프트 (PLAN §11 사전 등록).

실행:  uv run python -m dart_event_study.analysis.attention

사전 고정: 대상 = 자사주(직접+신탁), 윈도우 = [0,+20] 하나, 그룹 = 삼분위,
검정 = 그룹별 월클러스터 t + 상/하위 차이 Welch t(naive — 명시).
결과가 어느 방향이든 그대로 리포트.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from quantlab_shared.data.prices import PriceStore
from scipy import stats

from dart_event_study.analysis.event_study import car, clustered_t, market_model_ar
from dart_event_study.config import DATA_DIR, load_settings, load_universe

WINDOW = (0, 20)  # 사전 고정 — 기존 유의 윈도우


def event_cars(bb: pd.DataFrame, store: PriceStore, es_cfg: dict) -> pd.DataFrame:
    """자사주 이벤트별 CAR[0,+20] + day0 월 (클러스터용)."""
    mkt = store.ohlcv("KS11")["close"].pct_change().dropna()
    rows = []
    for _, ev in bb.iterrows():
        ret = store.ohlcv(ev["ticker"])["close"].pct_change().dropna()
        rcept = dt.datetime.strptime(ev["rcept_dt"], "%Y%m%d").date()
        pos = ret.index.searchsorted(pd.Timestamp(rcept))
        if pos >= len(ret.index):
            continue
        day0 = ret.index[pos].date()
        ar = market_model_ar(ret, mkt, day0, es_cfg["estimation_window"], es_cfg["gap"], WINDOW)
        if ar is None:
            continue
        rows.append({"rcept_no": ev["rcept_no"], "car": car(ar, WINDOW), "month": day0.strftime("%Y-%m")})
    return pd.DataFrame(rows)


def tercile_report(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """metric 삼분위별 CAR 통계 + 상/하위 차이 검정. 동점은 순위로 분리."""
    d = df.dropna(subset=[metric]).copy()
    d["tercile"] = pd.qcut(d[metric].rank(method="first"), 3, labels=["하위", "중위", "상위"])
    rows = []
    for g, grp in d.groupby("tercile", observed=True):
        t_cl, p_cl = clustered_t(grp["car"], grp["month"])
        rows.append({
            "그룹": str(g), "N": len(grp),
            f"{metric}_중앙값": grp[metric].median(),
            "mean_car": grp["car"].mean(), "t_cl_month": t_cl, "p_cl_month": p_cl,
        })
    top, bot = d[d["tercile"] == "상위"]["car"], d[d["tercile"] == "하위"]["car"]
    t_diff, p_diff = stats.ttest_ind(top, bot, equal_var=False)
    rows.append({
        "그룹": "상위-하위 차이 (Welch, naive)", "N": len(top) + len(bot),
        f"{metric}_중앙값": np.nan,
        "mean_car": top.mean() - bot.mean(), "t_cl_month": t_diff, "p_cl_month": p_diff,
    })
    return pd.DataFrame(rows)


def main() -> None:
    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]

    news = pd.read_parquet(DATA_DIR / "news_buyback.parquet")
    events = pd.read_parquet(DATA_DIR / f"events_{mode}.parquet")
    bb = events[events["event_type"].isin(["buyback", "buyback_trust"])]

    store = PriceStore(DATA_DIR / "prices", start, end)
    cars = event_cars(bb, store, settings["event_study"])
    df = news.merge(cars, on="rcept_no", how="inner")
    print(f"자사주 이벤트 {len(bb)}건 중 뉴스+CAR 매칭 {len(df)}건\n")

    # 교락 정직 공개: 기사 수는 회사 규모와 상관될 수 있음
    size = bb.set_index("rcept_no")["total_shares"]
    df["total_shares"] = df["rcept_no"].map(size)
    rho = df[["n_articles", "total_shares"]].dropna().corr(method="spearman").iloc[0, 1]
    print(f"교락 체크 — 기사 수 × 발행주식총수 Spearman ρ = {rho:.2f} (규모 효과 혼입 가능성)\n")

    pd.set_option("display.float_format", lambda v: f"{v:.4f}")
    print(f"[가설 검정 — 사전 고정 윈도우 {WINDOW}] 기사 수(attention) 삼분위:")
    att = tercile_report(df, "n_articles")
    print(att.to_string(index=False))

    print("\n제목 감성(sent_score) 삼분위 (기사 있는 이벤트만):")
    sent = tercile_report(df[df["n_articles"] > 0], "sent_score")
    print(sent.to_string(index=False))

    out = DATA_DIR / "attention_analysis.parquet"
    df.to_parquet(out)
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
