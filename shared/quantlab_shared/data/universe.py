"""KOSPI 유니버스 스냅샷.

⚠️ 두 겹의 근사가 있다 — 사용하는 프로젝트 README에 한계로 명시할 것:
1. KRX 정보데이터시스템(지수 구성종목 API)이 봇 차단이라 실제 KOSPI200
   구성종목을 프로그램으로 조회할 수 없음 → KOSPI 시총 상위 n 보통주로 근사.
2. 조회 시점 스냅샷이라 과거 편입/편출 이력 미반영 (survivorship bias).
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


def get_kospi_top_n_asof(
    n: int = 200,
    asof: str = "2019-01-02",
    cache_dir: Path | str | None = None,
    loss_reason_keywords: tuple[str, ...] = ("감사의견",),
) -> dict:
    """기간 시작 시점(asof) 프록시 시총 상위 n KOSPI 보통주 — survivorship 보정 유니버스.

    프록시 시총 = 상장주식수(현재 상장분은 현재 주식수, 상폐분은 상폐 시점 주식수)
    × asof 수정종가. 수정종가가 분할을 소급 반영하므로 근사치로 성립하며,
    유상증자로 주식수가 늘어난 종목은 과대평가됨 (한계로 문서화).
    asof 이후 상장(IPO) 종목은 asof 가격이 없어 자연 제외 — point-in-time 정합.
    asof 이후 상폐된 종목(사유 불문)도 후보에 포함 — 당시엔 실재했던 종목이므로.

    반환: {"tickers": [...], "delisted": [...], "delisted_loss": [...],
           "fetched_at": ..., "asof": ...}
    delisted_loss = 손실형 상폐(감사의견 등) — 백테스트 청산 할인 대상.
    합병·완전자회사화 상폐는 보유자가 대가를 받으므로 할인 없이 마지막 가격 청산.
    """
    cache_path = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"kospi_top{n}_asof{asof.replace('-', '')}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text(encoding="utf-8"))

    import FinanceDataReader as fdr
    import pandas as pd

    # 후보 1: 현재 상장 보통주 (현재 주식수)
    lst = fdr.StockListing("KOSPI")
    cands = [
        {"ticker": r["Code"], "shares": float(r["Stocks"]), "delisted": False, "reason": ""}
        for _, r in lst.iterrows()
        if str(r["Code"]).endswith("0") and r["Stocks"]
    ]
    # 후보 2: asof 이후 상폐된 KOSPI 보통주 (상폐 시점 주식수)
    dl = fdr.StockListing("KRX-DELISTING")
    dl["DelistingDate"] = pd.to_datetime(dl["DelistingDate"])
    dl = dl[
        (dl["Market"] == "KOSPI")
        & (dl["SecuGroup"] == "주권")
        & (dl["DelistingDate"] > asof)
        & dl["Symbol"].str.endswith("0")
        & dl["ListingShares"].notna()
    ]
    for _, r in dl.iterrows():
        cands.append(
            {"ticker": r["Symbol"], "shares": float(r["ListingShares"]),
             "delisted": True, "reason": str(r["Reason"])}
        )

    # asof 수정종가 (2주 윈도우 첫 유효값) — 종목별 소요 커서 결과를 통째로 캐시
    window_end = (pd.Timestamp(asof) + pd.Timedelta(days=14)).strftime("%Y-%m-%d")
    for c in cands:
        try:
            px = fdr.DataReader(c["ticker"], asof, window_end)
            c["mcap"] = c["shares"] * float(px["Close"].iloc[0]) if len(px) else None
        except Exception:
            c["mcap"] = None

    ranked = sorted([c for c in cands if c.get("mcap")], key=lambda c: -c["mcap"])[:n]
    result = {
        "asof": asof,
        "fetched_at": dt.date.today().isoformat(),
        "tickers": [c["ticker"] for c in ranked],
        "delisted": [c["ticker"] for c in ranked if c["delisted"]],
        "delisted_loss": [
            c["ticker"] for c in ranked
            if c["delisted"] and any(k in c["reason"] for k in loss_reason_keywords)
        ],
    }
    if cache_path is not None:
        cache_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def get_kospi_top_n(n: int = 200, cache_dir: Path | str | None = None) -> list[str]:
    """KOSPI 시가총액 상위 n 보통주 티커 — KOSPI200 대용 스냅샷.

    보통주 필터: 티커 끝자리 '0' (우선주는 5/7/K 등으로 끝남).
    캐시 파일에 조회일(fetched_at)을 기록해 스냅샷 시점을 남긴다.
    """
    cache_path = None
    if cache_dir is not None:
        cache_dir = Path(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"kospi_top{n}.json"
        if cache_path.exists():
            return json.loads(cache_path.read_text())["tickers"]

    import FinanceDataReader as fdr

    lst = fdr.StockListing("KOSPI")
    common = lst[lst["Code"].str.endswith("0")]
    tickers = common.sort_values("Marcap", ascending=False)["Code"].head(n).tolist()
    if cache_path is not None:
        cache_path.write_text(
            json.dumps({"fetched_at": dt.date.today().isoformat(), "tickers": tickers})
        )
    return tickers
