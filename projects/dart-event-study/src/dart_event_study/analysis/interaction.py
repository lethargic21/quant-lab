"""이벤트 × 어텐션 상호작용 회귀 (Phase A).

실행:  uv run python -m dart_event_study.analysis.interaction

핵심 질문: **공시 후 드리프트가 리테일/미디어 어텐션 수준에 따라 증폭되는가, 소멸되는가?**

기존 [analysis/attention.py](attention.py)는 어텐션 삼분위 portfolio sort(그룹 비교)까지만 한다.
이 모듈은 지시서 A-2의 단면 회귀를 채운다:

    CAR ~ event_type + attn + event_type:attn + controls

⚠️ **표본 제약 (정직 고지)**: 뉴스 어텐션은 **자사주 이벤트에만** 수집돼 있다
(실적 5,300 / 유증 139건은 미수집). 따라서 event_type 축은 전 이벤트 타입이 아니라
**자사주 직접취득(178) vs 신탁계약(106)** 두 종류로만 구성된다. "이벤트 타입별로 어텐션
효과가 다른가"는 이 두 그룹 범위 안에서만 답할 수 있다.

⚠️ **어텐션의 시점 (look-ahead / 해석 주의)**: 뉴스 수집 창은 접수일 [-1, +1]이라
이 어텐션은 **사전 관심이 아니라 이벤트에 대한 사후·동시 반응**이다. 변수명을
`attn_post`로 못박는다. 그래서 종속변수를 두 개로 나눠 **둘 다** 리포트한다:
  - `CAR[0,+20]`   — 기존 삼분위 분석과 같은 사전 등록 윈도우. 단 어텐션 창(0,+1)과 **겹침**.
  - `CAR[+2,+20]`  — 어텐션 창과 **겹치지 않는** 구간. "반응이 이후 드리프트를 예측하는가".
겹치는 쪽만 유의하고 안 겹치는 쪽이 무의미하면 그것은 동시성이지 예측력이 아니다.
결과가 어느 방향이든, null이든 그대로 기록한다 (윈도우를 바꿔가며 유의성 사냥 금지).

`abnormal_attention`의 한계: 지시서 원안은 "종목별 rolling baseline 대비 z-score"지만,
수집된 데이터는 이벤트당 기사 수 하나뿐이라 **일별 baseline 시계열이 없다.** 그래서
abnormal화를 (a) log1p 후 표본 z-score + (b) 회귀에 로그 시총·추정창 변동성을 통제변수로
넣어 규모 교락을 partial out 하는 방식으로 대체한다. 진짜 rolling baseline이 아님을 명시.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from scipy import stats

from dart_event_study.analysis.caar_tests import Panel, build_panel, day0_pos, event_inputs

PRIMARY_WINDOW = (0, 20)    # 사전 등록 (기존 삼분위 분석과 동일)
NONOVERLAP_WINDOW = (2, 20)  # 어텐션 창 [-1,+1]과 겹치지 않는 구간


def ols_cluster(y: np.ndarray, X: np.ndarray, clusters, names: list[str]) -> pd.DataFrame:
    """클러스터-로버스트(CR1) SE를 쓰는 OLS. statsmodels 없이 numpy로.

    V = (X'X)⁻¹ [Σ_g X_g'u_g u_g'X_g] (X'X)⁻¹ × c,  c = G/(G−1) · (n−1)/(n−k)
    (event_study.clustered_t와 같은 CR1 계열 보정. df = G−1.)
    같은 달 이벤트끼리 잔차가 상관되는 것(이벤트 클러스터링)을 흡수한다.
    """
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape
    xtx_inv = np.linalg.pinv(X.T @ X)
    beta = xtx_inv @ (X.T @ y)
    resid = y - X @ beta

    g = pd.Series(list(clusters)).to_numpy()
    groups = pd.unique(g)
    n_g = len(groups)
    meat = np.zeros((k, k))
    for grp in groups:
        idx = g == grp
        s = X[idx].T @ resid[idx]
        meat += np.outer(s, s)
    c = (n_g / (n_g - 1)) * ((n - 1) / (n - k)) if n_g > 1 and n > k else np.nan
    var = xtx_inv @ meat @ xtx_inv * c
    se = np.sqrt(np.diag(var))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = beta / se
    p = 2 * stats.t.sf(np.abs(t), df=n_g - 1)

    ss_res = float((resid**2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    out = pd.DataFrame({"term": names, "coef": beta, "se": se, "t": t, "p": p})
    out.attrs |= {
        "n": n, "k": k, "n_clusters": n_g,
        "r2": 1 - ss_res / ss_tot if ss_tot > 0 else np.nan,
    }
    return out


def build_event_panel(
    news: pd.DataFrame, events: pd.DataFrame, panels: dict[str, Panel],
    closes: dict[str, pd.Series], est_len: int, gap: int,
) -> pd.DataFrame:
    """이벤트별 회귀 패널: CAR(두 윈도우) + 어텐션 + 통제변수 + 클러스터 키."""
    meta = events.set_index("rcept_no")[["total_shares"]]
    rows = []
    for _, ev in news.iterrows():
        tk = ev["ticker"]
        panel = panels.get(tk)
        if panel is None:
            continue
        rcept = dt.datetime.strptime(str(ev["rcept_dt"]), "%Y%m%d").date()
        j = panel.dates.searchsorted(pd.Timestamp(rcept))
        if j >= len(panel.dates):
            continue
        day0 = panel.dates[j].date()
        prim = event_inputs(panel, day0, est_len, gap, PRIMARY_WINDOW)
        nonov = event_inputs(panel, day0, est_len, gap, NONOVERLAP_WINDOW)
        if prim is None or nonov is None:
            continue
        pos = day0_pos(panel, day0)
        px = closes[tk].iloc[closes[tk].index.searchsorted(pd.Timestamp(day0))] if pos is not None else np.nan
        shares = meta["total_shares"].get(ev["rcept_no"], np.nan)
        rows.append({
            "rcept_no": ev["rcept_no"],
            "ticker": tk,
            "event_type": ev["event_type"],
            "day0": day0,
            "month": day0.strftime("%Y-%m"),          # 클러스터 키
            "car_primary": prim.car,                  # CAR[0,+20]  (어텐션 창과 겹침)
            "car_nonoverlap": nonov.car,              # CAR[+2,+20] (겹치지 않음)
            "n_articles": float(ev["n_articles"]),
            "sent_score": ev["sent_score"],
            "est_vol": float(prim.est_ars.std(ddof=1)),  # 추정창 잔차 변동성 (통제)
            "mktcap": float(px) * float(shares) if np.isfinite(px) and np.isfinite(shares) else np.nan,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # 사후 반응 어텐션: log1p 후 표본 z-score (rolling baseline 부재 — 모듈 docstring 참조)
    la = np.log1p(df["n_articles"])
    df["attn_post"] = (la - la.mean()) / la.std(ddof=1)
    df["is_trust"] = (df["event_type"] == "buyback_trust").astype(float)
    df["log_mktcap"] = np.log(df["mktcap"])
    return df


def run_spec(df: pd.DataFrame, dep: str, with_controls: bool) -> pd.DataFrame:
    """CAR ~ is_trust + attn_post + is_trust:attn_post [+ log_mktcap + est_vol], 월클러스터 SE."""
    cols = ["is_trust", "attn_post", "attn_x_trust"]
    d = df.copy()
    d["attn_x_trust"] = d["attn_post"] * d["is_trust"]
    if with_controls:
        cols += ["log_mktcap", "est_vol"]
    d = d.dropna(subset=[dep, *cols])
    if len(d) < len(cols) + 2:
        return pd.DataFrame()
    X = np.column_stack([np.ones(len(d))] + [d[c].to_numpy() for c in cols])
    return ols_cluster(d[dep].to_numpy(), X, d["month"], ["const", *cols])


def _print_spec(title: str, res: pd.DataFrame) -> None:
    if res.empty:
        print(f"\n[{title}] 표본 부족 — 생략")
        return
    a = res.attrs
    print(f"\n[{title}]  N={a['n']}, 클러스터(월)={a['n_clusters']}, R²={a['r2']:.3f}")
    show = res.copy()
    show["coef"] = show["coef"].map(lambda v: f"{v:+.4f}")
    show["se"] = show["se"].map(lambda v: f"{v:.4f}")
    show["t"] = show["t"].map(lambda v: f"{v:+.2f}")
    show["p"] = show["p"].map(lambda v: f"{v:.3f}")
    print(show.to_string(index=False))


def main() -> None:
    import sys

    from quantlab_shared.data.prices import PriceStore

    from dart_event_study.config import DATA_DIR, load_settings, load_universe

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]
    es_cfg = settings["event_study"]

    news = pd.read_parquet(DATA_DIR / "news_buyback.parquet")
    events = pd.read_parquet(DATA_DIR / f"events_{mode}.parquet")
    store = PriceStore(DATA_DIR / "prices", start, end)
    mkt = store.ohlcv("KS11")["close"].pct_change().dropna()

    tickers = news["ticker"].unique()
    closes = {t: store.ohlcv(t)["close"] for t in tickers}
    panels = {t: build_panel(closes[t].pct_change().dropna(), mkt) for t in tickers}

    df = build_event_panel(news, events, panels, closes, es_cfg["estimation_window"], es_cfg["gap"])
    print(f"뉴스 어텐션 이벤트 {len(news)}건 → CAR 매칭 {len(df)}건 "
          f"(직접 {int((df.is_trust == 0).sum())} / 신탁 {int((df.is_trust == 1).sum())})")
    print(f"통제변수 결측(시총) 제외 시 {int(df['log_mktcap'].notna().sum())}건\n"
          "어텐션 = log1p(기사 수) z-score, **사후·동시 반응** (수집창 [-1,+1]) — 사전 관심 아님")

    for dep, label in [("car_primary", "CAR[0,+20] · 어텐션 창과 겹침"),
                       ("car_nonoverlap", "CAR[+2,+20] · 겹치지 않음")]:
        _print_spec(f"{label} | 통제 O", run_spec(df, dep, with_controls=True))
        _print_spec(f"{label} | 통제 X (전 표본)", run_spec(df, dep, with_controls=False))

    out = DATA_DIR / f"interaction_{mode}.parquet"
    df.to_parquet(out)
    print(f"\n저장: {out}")
    print("해석: attn_post 계수 = 직접취득의 어텐션 효과, attn_x_trust = 신탁에서의 추가 차이(상호작용).")


if __name__ == "__main__":
    main()
