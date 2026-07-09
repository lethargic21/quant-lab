"""수정주가 OHLCV 로더 (FinanceDataReader/네이버 기반, parquet 디스크 캐싱).

일별 시가총액은 무료 소스가 없어 제공하지 않는다 (KRX 정보데이터시스템 봇 차단).
시총 대비 비율이 필요한 곳은 공시에 포함된 주식수 기반 비율을 사용할 것.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterable
from pathlib import Path

import pandas as pd

OHLCV_COLS = {"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}


class PriceStore:
    """티커별 수정주가 OHLCV 제공.

    - 수정주가: FDR 네이버 소스 (액면분할·증자 반영 — 절대 원칙 2.
      카카오 2021-04 5:1 분할 구간으로 실측 검증함)
    - 캐시 키 = (종류, ticker, start, end). 기간이 바뀌면 재수집.
    """

    def __init__(self, cache_dir: Path | str, start: str, end: str):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.start = start
        self.end = end

    def _cached(self, kind: str, ticker: str, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        s, e = self.start.replace("-", ""), self.end.replace("-", "")
        path = self.cache_dir / f"{kind}_{ticker}_{s}_{e}.parquet"
        if path.exists():
            return pd.read_parquet(path)
        df = fetch()
        df.to_parquet(path)
        return df

    def ohlcv(self, ticker: str) -> pd.DataFrame:
        """수정주가 OHLCV. index=date, columns=open/high/low/close/volume."""

        def fetch() -> pd.DataFrame:
            import FinanceDataReader as fdr

            df = fdr.DataReader(ticker, self.start, self.end)
            df = df.rename(columns=OHLCV_COLS)[list(OHLCV_COLS.values())]
            df.index.name = "date"
            return df

        return self._cached("ohlcv", ticker, fetch)

    def status_flags(self, ticker: str, calendar_days: Iterable[dt.date]) -> dict:
        """거래정지/상폐 추정 플래그 (가격 데이터 기반 최소 처리 — 한계는 README 명시).

        - n_suspended: 데이터는 있으나 거래량 0인 날 (거래정지 추정)
        - n_missing:   캘린더 거래일인데 데이터가 없는 날
        - delisted_like: 마지막 데이터가 기간 끝보다 20거래일 이상 이르면 상폐 추정
        """
        df = self.ohlcv(ticker)
        cal = sorted(calendar_days)
        have = {d.date() for d in pd.to_datetime(df.index)}
        n_missing = sum(1 for d in cal if d not in have)
        delisted_like = False
        if len(df) and cal:
            last = pd.to_datetime(df.index.max()).date()
            delisted_like = sum(1 for d in cal if d > last) >= 20
        return {
            "n_days": len(df),
            "n_suspended": int((df["volume"] == 0).sum()),
            "n_missing": n_missing,
            "delisted_like": delisted_like,
        }
