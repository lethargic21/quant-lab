"""CAAR 유의성 검정 배터리 — 표준 t 이외의 엄밀성 (Phase B).

실행:  uv run python -m dart_event_study.analysis.caar_tests

기존 event_study.py는 naive t + 클러스터-로버스트 t + BH-FDR까지 채운다.
이 모듈은 그 위에 이벤트 스터디 표준 검정을 **추가**한다 (event_study.py는 안 건드림):

1. BMP (Boehmer-Musumeci-Poulsen 1991) — 각 CAR를 자기 추정기간 예측오차
   표준편차로 표준화(SCAR)한 뒤 단면 t. event-induced variance를 보정한다.
2. Corrado rank test (Corrado 1989 / Corrado-Zivney 1992 누적형) — 비모수 순위 검정.
3. 일반화 부호검정 (Cowan 1992) — 귀무 양수확률을 0.5가 아니라 추정기간
   실제 양수비율로 잡는다.
4. CAAR event-level 부트스트랩 CI — 이벤트를 복원추출해 평균 CAR의 표본 불확실성.
5. **Placebo / permutation** — 랜덤 가짜 이벤트일로 동일 파이프라인을 돌려 얻은
   CAAR 귀무분포와 실제 CAAR을 비교. 이 프로젝트에서 제일 중요한 sanity check.

BMP·placebo p값에는 표 전체(그룹×윈도우)에 BH-FDR을 걸어 다중검정을 보정한다.

look-ahead 없음: 정상수익률 추정은 전부 이벤트 이전(gap 띄운) 기간. placebo의
가짜 이벤트일도 각 종목의 실제 거래일에서만 뽑고, 실제 이벤트 근방(±buffer)은 제외한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

MIN_EST_OBS = 60  # event_study.py와 동일 — 추정 표본 최소치
THIN_SAMPLE = 30


# ─────────────────────────────────────────────────────────────────────────────
# 종목별 정렬 패널 — (수익률, 시장수익률)을 공통 거래일에 맞춰 numpy로 캐시.
# placebo가 수십만~수백만 번 시장모형을 적합하므로 polyfit 대신 닫힌형 OLS를 쓴다.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Panel:
    """한 종목의 정렬된 일별 수익률 패널 (numpy 배열 + 날짜→인덱스)."""

    stock: np.ndarray  # 종목 일별 수익률
    mkt: np.ndarray    # 시장 일별 수익률 (같은 날짜에 정렬)
    dates: pd.DatetimeIndex


def build_panel(stock_ret: pd.Series, mkt_ret: pd.Series) -> Panel:
    joined = pd.concat({"stock": stock_ret, "mkt": mkt_ret}, axis=1).dropna()
    return Panel(
        stock=joined["stock"].to_numpy(),
        mkt=joined["mkt"].to_numpy(),
        dates=joined.index,
    )


def _ols(x: np.ndarray, y: np.ndarray) -> tuple[float, float, np.ndarray]:
    """단순 OLS y = alpha + beta·x. (alpha, beta, 잔차) 반환. denom<=0이면 예외."""
    xm = x.mean()
    dx = x - xm
    denom = float((dx * dx).sum())
    if denom <= 0:
        raise ValueError("시장수익률 분산 0 — 회귀 불가")
    beta = float((dx * (y - y.mean())).sum() / denom)
    alpha = float(y.mean() - beta * xm)
    resid = y - (alpha + beta * x)
    return alpha, beta, resid


def day0_pos(panel: Panel, day0: dt.date) -> int | None:
    """이벤트일(거래일)의 패널 내 정수 인덱스. 해당일 데이터 없으면 None."""
    pos = int(panel.dates.searchsorted(pd.Timestamp(day0)))
    if pos >= len(panel.dates) or panel.dates[pos].date() != day0:
        return None
    return pos


# ─────────────────────────────────────────────────────────────────────────────
# 이벤트별 검정 입력 — CAR, 표준화 CAR(BMP), 추정/윈도우 AR 시계열.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EventInputs:
    car: float
    scar: float                # BMP 표준화 CAR (car / 예측오차 SD)
    est_ars: np.ndarray        # 추정기간 AR (부호검정·Corrado용)
    win_ars: np.ndarray        # 윈도우 AR (Corrado용, 합 = car)
    est_pos_frac: float        # 추정기간 양수 AR 비율 (일반화 부호검정)
    full_est: bool             # 추정 표본이 정확히 est_len인가 (Corrado 매트릭스 정렬용)


def event_inputs(
    panel: Panel, day0: dt.date, est_len: int, gap: int, window: tuple[int, int]
) -> EventInputs | None:
    """단일 이벤트의 검정 입력. 경계 로직은 event_study.market_model_ar과 일치.

    BMP 예측오차 분산 (Campbell-Lo-MacKinlay): 시장모형 CAR의 예측오차 분산은
        Var(CAR) = s²·[ L + L²/T + (Σ_win(R_m,t − R̄_m))² / SSE_m ]
    s² = 추정기간 잔차분산(자유도 T−2), T = 추정표본, L = 윈도우 길이,
    R̄_m·SSE_m = 추정기간 시장수익률 평균·편차제곱합. 뒤 두 항이 α·β 추정오차 보정.
    """
    pos = day0_pos(panel, day0)
    if pos is None:
        return None
    a, b = window
    est_start = max(0, pos - gap - est_len)
    est_stock = panel.stock[est_start : pos - gap]
    est_mkt = panel.mkt[est_start : pos - gap]
    t_est = len(est_stock)
    if t_est < MIN_EST_OBS:
        return None
    idx_lo, idx_hi = pos + a, pos + b
    if idx_lo < 0 or idx_hi >= len(panel.stock):
        return None

    try:
        alpha, beta, resid = _ols(est_mkt, est_stock)
    except ValueError:
        return None
    s2 = float((resid**2).sum() / (t_est - 2)) if t_est > 2 else np.nan

    win_mkt = panel.mkt[idx_lo : idx_hi + 1]
    win_stock = panel.stock[idx_lo : idx_hi + 1]
    win_ar = win_stock - (alpha + beta * win_mkt)
    car = float(win_ar.sum())

    length = b - a + 1
    mkt_bar = est_mkt.mean()
    sse_m = float(((est_mkt - mkt_bar) ** 2).sum())
    car_var = s2 * (length + length**2 / t_est + (win_mkt - mkt_bar).sum() ** 2 / sse_m)
    scar = car / np.sqrt(car_var) if car_var > 0 else np.nan

    return EventInputs(
        car=car,
        scar=float(scar),
        est_ars=resid,
        win_ars=win_ar,
        est_pos_frac=float((resid > 0).mean()),
        full_est=(t_est == est_len),
    )


def car_fast(
    panel: Panel, pos: int, est_len: int, gap: int, window: tuple[int, int]
) -> float | None:
    """정수 위치 pos를 이벤트일로 한 CAR만 빠르게 계산 (placebo 내부 루프용).

    placebo 후보는 추정 표본을 정확히 est_len 확보한 위치에서만 뽑으므로
    est_start 음수/윈도우 경계 초과면 None.
    """
    a, b = window
    est_start = pos - gap - est_len
    if est_start < 0 or pos + a < 0 or pos + b >= len(panel.stock):
        return None
    est_mkt = panel.mkt[est_start : pos - gap]
    est_stock = panel.stock[est_start : pos - gap]
    try:
        alpha, beta, _ = _ols(est_mkt, est_stock)
    except ValueError:
        return None
    win_mkt = panel.mkt[pos + a : pos + b + 1]
    win_stock = panel.stock[pos + a : pos + b + 1]
    return float((win_stock - (alpha + beta * win_mkt)).sum())


@dataclass
class PanelPrefix:
    """누적합 캐시 — 임의 위치의 시장모형 CAR을 O(1)로 계산 (placebo 벡터화).

    닫힌형 OLS를 누적합으로 표현: 구간 [s,e)의 Σx, Σy, Σx², Σxy를 prefix로 얻어
    beta=(nΣxy−ΣxΣy)/(nΣx²−(Σx)²), alpha=(Σy−βΣx)/n. car_fast와 대수적으로 동일.
    """

    cx: np.ndarray    # cumsum(mkt),   길이 n+1 (선행 0)
    cy: np.ndarray    # cumsum(stock)
    cxx: np.ndarray   # cumsum(mkt²)
    cxy: np.ndarray   # cumsum(mkt·stock)
    n: int


def build_prefix(panel: Panel) -> PanelPrefix:
    x, y = panel.mkt, panel.stock
    z = lambda a: np.concatenate([[0.0], np.cumsum(a)])  # noqa: E731 — 선행 0 누적합
    return PanelPrefix(cx=z(x), cy=z(y), cxx=z(x * x), cxy=z(x * y), n=len(x))


def car_at_positions(
    pfx: PanelPrefix, positions: np.ndarray, est_len: int, gap: int, window: tuple[int, int]
) -> np.ndarray:
    """여러 이벤트 위치의 CAR을 한 번에 (벡터화). 회귀 불능(분모 0)은 nan.

    positions의 각 원소는 est_start = pos−gap−est_len ≥ 0, pos+b < n 이어야 한다
    (placebo 후보 생성 단계에서 보장). 경계 밖 위치는 nan으로 반환.
    """
    a, b = window
    pos = np.asarray(positions, dtype=np.int64)
    est_start = pos - gap - est_len
    est_end = pos - gap                      # exclusive
    win_lo = pos + a
    win_hi = pos + b + 1                      # exclusive
    ok = (est_start >= 0) & (win_lo >= 0) & (win_hi <= pfx.n)
    out = np.full(len(pos), np.nan)
    if not ok.any():
        return out
    es, ee = est_start[ok], est_end[ok]
    wl, wh = win_lo[ok], win_hi[ok]
    n = est_len
    sx = pfx.cx[ee] - pfx.cx[es]
    sy = pfx.cy[ee] - pfx.cy[es]
    sxx = pfx.cxx[ee] - pfx.cxx[es]
    sxy = pfx.cxy[ee] - pfx.cxy[es]
    denom = n * sxx - sx * sx
    with np.errstate(divide="ignore", invalid="ignore"):
        beta = (n * sxy - sx * sy) / denom
        alpha = (sy - beta * sx) / n
    length = b - a + 1
    swx = pfx.cx[wh] - pfx.cx[wl]
    swy = pfx.cy[wh] - pfx.cy[wl]
    car = swy - (alpha * length + beta * swx)
    car[denom <= 0] = np.nan
    res = out.copy()
    res[ok] = car
    return res


# ─────────────────────────────────────────────────────────────────────────────
# 검정들
# ─────────────────────────────────────────────────────────────────────────────


def bmp_test(scars: list[float]) -> dict:
    """BMP 표준화 단면 t. SCAR의 단면 표준편차로 검정 → event-induced variance 보정."""
    s = np.asarray([x for x in scars if np.isfinite(x)], dtype=float)
    n = len(s)
    if n < 2:
        return {"bmp_t": np.nan, "bmp_p": np.nan, "n_bmp": n}
    sd = s.std(ddof=1)
    if sd <= 0:
        return {"bmp_t": np.nan, "bmp_p": np.nan, "n_bmp": n}
    t = s.mean() / (sd / np.sqrt(n))
    return {"bmp_t": float(t), "bmp_p": float(2 * stats.t.sf(abs(t), df=n - 1)), "n_bmp": n}


def corrado_rank_test(
    est_ars: list[np.ndarray], win_ars: list[np.ndarray]
) -> dict:
    """누적 Corrado 순위검정 (Corrado 1989 · Corrado-Zivney 1992).

    각 이벤트의 추정+윈도우 AR(M = est_len + L개)을 순위화하고, 각 상대시점의
    이벤트 평균순위편차를 만든다. 검정통계량은 윈도우 구간 순위편차합 /
    (√L · 전체기간 순위편차 표준편차). 추정 표본 길이가 같은 이벤트만 사용
    (매트릭스 정렬을 위해) — n_corrado로 리포트.
    """
    # 추정 길이가 최빈값과 같은 이벤트만 (매트릭스 정렬)
    if not est_ars:
        return {"corrado_t": np.nan, "corrado_p": np.nan, "n_corrado": 0}
    lengths = [len(e) for e in est_ars]
    target = max(set(lengths), key=lengths.count)
    rows = [
        np.concatenate([e, w])
        for e, w, ln in zip(est_ars, win_ars, lengths, strict=True)
        if ln == target
    ]
    n = len(rows)
    win_len = len(win_ars[0])
    if n < 2:
        return {"corrado_t": np.nan, "corrado_p": np.nan, "n_corrado": n}

    mat = np.vstack(rows)                       # (n, M)
    m = mat.shape[1]
    ranks = np.apply_along_axis(stats.rankdata, 1, mat)  # 행별 1..M, 동점 평균
    demean = ranks - (m + 1) / 2                # 평균순위 (M+1)/2 중심화
    abar = demean.mean(axis=0)                  # 각 시점 이벤트 평균순위편차 (M,)
    s_k = np.sqrt((abar**2).mean())             # 전체기간 순위편차 표준편차
    if s_k <= 0:
        return {"corrado_t": np.nan, "corrado_p": np.nan, "n_corrado": n}
    win_sum = abar[target : target + win_len].sum()  # 윈도우 구간(끝 L개)
    t = win_sum / (np.sqrt(win_len) * s_k)
    return {"corrado_t": float(t), "corrado_p": float(2 * stats.norm.sf(abs(t))), "n_corrado": n}


def sign_test(cars: list[float], est_pos_fracs: list[float]) -> dict:
    """일반화 부호검정 (Cowan 1992). 귀무 양수확률 = 추정기간 평균 양수비율."""
    c = np.asarray(cars, dtype=float)
    n = len(c)
    if n < 2:
        return {"sign_z": np.nan, "sign_p": np.nan, "pos_ratio": np.nan, "p_hat": np.nan}
    w = int((c > 0).sum())
    p_hat = float(np.mean(est_pos_fracs)) if len(est_pos_fracs) else 0.5
    if not 0 < p_hat < 1:
        p_hat = 0.5
    z = (w - n * p_hat) / np.sqrt(n * p_hat * (1 - p_hat))
    return {
        "sign_z": float(z),
        "sign_p": float(2 * stats.norm.sf(abs(z))),
        "pos_ratio": w / n,
        "p_hat": p_hat,
    }


def caar_bootstrap_ci(cars: list[float], n_boot: int = 2000, seed: int = 42) -> dict:
    """이벤트 복원추출로 평균 CAR(=CAAR)의 95% CI + 양측 부트스트랩 p."""
    c = np.asarray(cars, dtype=float)
    n = len(c)
    if n < 2:
        return {"caar": float(c.mean()) if n else np.nan, "boot_ci_lo": np.nan,
                "boot_ci_hi": np.nan, "boot_p": np.nan}
    rng = np.random.default_rng(seed)
    means = c[rng.integers(0, n, size=(n_boot, n))].mean(axis=1)
    p = 2 * min(float((means <= 0).mean()), float((means >= 0).mean()))
    return {
        "caar": float(c.mean()),
        "boot_ci_lo": float(np.percentile(means, 2.5)),
        "boot_ci_hi": float(np.percentile(means, 97.5)),
        "boot_p": min(p, 1.0),
    }


def placebo_test(
    panels: dict[str, Panel],
    events: list[tuple[str, dt.date]],
    est_len: int,
    gap: int,
    window: tuple[int, int],
    n_perm: int = 1000,
    buffer: int = 30,
    seed: int = 42,
) -> dict:
    """Placebo/permutation — 랜덤 가짜 이벤트일 CAAR 귀무분포 vs 실제 CAAR.

    각 종목의 실제 거래일에서만 가짜 이벤트일을 뽑되(추정+윈도우 확보 가능한
    내부 위치), 실제 이벤트 ±buffer 거래일은 제외한다. 종목 구성과 표본 수 N을
    실제와 동일하게 유지하고 타이밍만 무작위화 → "이 효과가 아무 날에나 나오나?".

    반환: 실제 CAAR, 귀무 평균/95% CI, 경험적 양측 p = (1+#{|null|>=|real|})/(n_perm+1).
    """
    a, b = window
    # 종목별 유효 후보 위치 (추정+윈도우 확보) — buffer로 실제 이벤트 근방 제외
    real_by_ticker: dict[str, list[int]] = {}
    for tk, d in events:
        p = panels.get(tk)
        if p is None:
            continue
        pos = day0_pos(p, d)
        if pos is not None:
            real_by_ticker.setdefault(tk, []).append(pos)

    candidates: dict[str, np.ndarray] = {}
    for tk, panel in panels.items():
        n_days = len(panel.stock)
        lo = gap + est_len
        hi = n_days - 1 - b
        if hi <= lo:
            continue
        valid = np.arange(lo, hi + 1)
        # 실제 이벤트 ±buffer 제외
        for pos in real_by_ticker.get(tk, []):
            valid = valid[(valid < pos - buffer) | (valid > pos + buffer)]
        if len(valid):
            candidates[tk] = valid

    # 실제 CAAR (동일 경계로 계산 — placebo와 같은 잣대)
    real_cars = []
    for tk, d in events:
        panel = panels.get(tk)
        if panel is None:
            continue
        pos = day0_pos(panel, d)
        if pos is None:
            continue
        v = car_fast(panel, pos, est_len, gap, window)
        if v is not None:
            real_cars.append(v)
    if len(real_cars) < 2:
        return {"placebo_p": np.nan, "placebo_null_mean": np.nan,
                "placebo_ci_lo": np.nan, "placebo_ci_hi": np.nan,
                "real_caar": np.nan, "n_perm": 0}
    real_caar = float(np.mean(real_cars))

    # 각 실제 이벤트를 같은 종목의 랜덤 후보일로 치환 → n_perm개 가짜 CAAR.
    # 종목별 prefix 누적합으로 CAR을 벡터화 (car_at_positions) — 순진한 이중 루프
    # 대비 수십 배 빠르다 (placebo는 이벤트×n_perm번 시장모형을 적합).
    rng = np.random.default_rng(seed)
    usable = [tk for tk, _ in events if tk in candidates]  # 이벤트당 티커(중복 허용)
    if len(usable) < 2:
        return {"placebo_p": np.nan, "placebo_null_mean": np.nan,
                "placebo_ci_lo": np.nan, "placebo_ci_hi": np.nan,
                "real_caar": real_caar, "n_perm": 0}

    prefixes = {tk: build_prefix(panels[tk]) for tk in set(usable)}
    per_event = np.empty((len(usable), n_perm))
    for j, tk in enumerate(usable):
        cand = candidates[tk]
        draws = cand[rng.integers(0, len(cand), size=n_perm)]
        per_event[j] = car_at_positions(prefixes[tk], draws, est_len, gap, window)
    null_caars = np.nanmean(per_event, axis=0)  # 각 순열의 가짜 CAAR
    null_caars = null_caars[np.isfinite(null_caars)]
    if len(null_caars) < 2:
        return {"placebo_p": np.nan, "placebo_null_mean": np.nan,
                "placebo_ci_lo": np.nan, "placebo_ci_hi": np.nan,
                "real_caar": real_caar, "n_perm": len(null_caars)}
    p_emp = (1 + int((np.abs(null_caars) >= abs(real_caar)).sum())) / (len(null_caars) + 1)
    return {
        "real_caar": real_caar,
        "placebo_null_mean": float(null_caars.mean()),
        "placebo_ci_lo": float(np.percentile(null_caars, 2.5)),
        "placebo_ci_hi": float(np.percentile(null_caars, 97.5)),
        "placebo_p": float(p_emp),
        "n_perm": len(null_caars),
    }


def bh_fdr(pvals: list[float]) -> list[float]:
    """Benjamini-Hochberg q값 (event_study.bh_fdr과 동일 구현 — 재사용)."""
    from dart_event_study.analysis.event_study import bh_fdr as _bh

    return _bh(pvals)


# ─────────────────────────────────────────────────────────────────────────────
# 오케스트레이션
# ─────────────────────────────────────────────────────────────────────────────


def run_caar_tests(
    signals: pd.DataFrame,
    panels: dict[str, Panel],
    est_len: int,
    gap: int,
    car_windows: list[tuple[int, int]],
    n_perm: int = 1000,
    seed: int = 42,
) -> pd.DataFrame:
    """그룹(event_type×direction) × 윈도우별 검정 배터리. BMP·placebo p에 BH-FDR."""
    # 이벤트별 day0 (접수일 이후 첫 거래일) 미리 계산
    ev_rows = []
    for _, sig in signals.iterrows():
        panel = panels.get(sig["ticker"])
        if panel is None:
            continue
        rcept = dt.datetime.strptime(sig["rcept_dt"], "%Y%m%d").date()
        j = panel.dates.searchsorted(pd.Timestamp(rcept))
        if j >= len(panel.dates):
            continue
        ev_rows.append({
            "ticker": sig["ticker"],
            "event_type": sig["event_type"],
            "direction": int(sig["direction"]),
            "day0": panel.dates[j].date(),
        })
    ev = pd.DataFrame(ev_rows)

    rows = []
    for (etype, direction), grp in ev.groupby(["event_type", "direction"]):
        events = list(zip(grp["ticker"], grp["day0"], strict=True))
        for w in car_windows:
            inp = [event_inputs(panels[tk], d, est_len, gap, w) for tk, d in events]
            inp = [x for x in inp if x is not None]
            n = len(inp)
            if n < 2:
                continue
            cars = [x.car for x in inp]
            row = {
                "event_type": etype, "direction": direction,
                "window": f"[{w[0]},{w[1]}]", "N": n,
                "thin_sample": n < THIN_SAMPLE,
            }
            row |= caar_bootstrap_ci(cars, seed=seed)
            row |= bmp_test([x.scar for x in inp])
            row |= corrado_rank_test([x.est_ars for x in inp], [x.win_ars for x in inp])
            row |= sign_test(cars, [x.est_pos_frac for x in inp])
            row |= placebo_test(panels, events, est_len, gap, w, n_perm=n_perm, seed=seed)
            rows.append(row)

    out = pd.DataFrame(rows)
    if not out.empty:
        out["q_bmp"] = bh_fdr(out["bmp_p"].tolist())
        out["q_placebo"] = bh_fdr(out["placebo_p"].tolist())
    return out


def main() -> None:
    import sys

    from quantlab_shared.data.prices import PriceStore

    from dart_event_study.config import DATA_DIR, load_settings, load_universe

    # Windows에서 stdout이 파이프로 리다이렉트되면 로케일 인코딩(cp949)이라
    # 출력의 em-dash 등이 깨진다 (CI/로그 파일 대비). UTF-8로 재설정.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    settings, universe = load_settings(), load_universe()
    mode = universe["mode"]
    start, end = settings["period"]["start"], settings["period"]["end"]
    es_cfg = settings["event_study"]
    windows = [tuple(w) for w in es_cfg["car_windows"]]
    seed = settings.get("random_seed", 42)
    n_perm = settings.get("caar_tests", {}).get("n_perm", 1000)

    signals = pd.read_parquet(DATA_DIR / f"signals_{mode}.parquet")
    store = PriceStore(DATA_DIR / "prices", start, end)
    mkt_ret = store.ohlcv("KS11")["close"].pct_change().dropna()
    panels = {
        t: build_panel(store.ohlcv(t)["close"].pct_change().dropna(), mkt_ret)
        for t in signals["ticker"].unique()
    }

    print(f"CAAR 검정 배터리: 시그널 {len(signals)}건, placebo {n_perm}회/그룹×윈도우\n"
          "(BMP·Corrado·부호·부트스트랩·placebo — event_study.py의 naive/클러스터 t에 추가)")
    out = run_caar_tests(signals, panels, es_cfg["estimation_window"], es_cfg["gap"],
                         windows, n_perm=n_perm, seed=seed)

    out_dir = DATA_DIR / f"eventstudy_{mode}"
    out_dir.mkdir(exist_ok=True)
    out.to_parquet(out_dir / "caar_tests.parquet")

    pd.set_option("display.float_format", lambda v: f"{v:.4f}")
    show = out.copy()
    show["caar"] = show["caar"].map(lambda v: f"{v:+.2%}")
    cols = ["event_type", "direction", "window", "N", "caar",
            "bmp_t", "bmp_p", "q_bmp", "corrado_t", "corrado_p",
            "sign_z", "sign_p", "boot_ci_lo", "boot_ci_hi", "boot_p",
            "placebo_null_mean", "placebo_p", "q_placebo"]
    print(show[cols].to_string(index=False))
    print(f"\n저장: {out_dir / 'caar_tests.parquet'}")
    print("해석: placebo_p가 작을수록(가짜 이벤트로는 재현 안 됨) 실제 효과의 증거. "
          "q_*는 표 전체 다중검정 보정 후 값.")


if __name__ == "__main__":
    main()
