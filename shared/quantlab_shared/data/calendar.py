"""KRX 영업일 캘린더."""

from __future__ import annotations

import datetime as dt
import json
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from pathlib import Path


class TradingCalendar:
    """거래일 시퀀스 기반 영업일 캘린더.

    KRX 실제 개장일(KOSPI 지수 데이터가 존재하는 날짜)을 사용하므로
    공휴일·임시휴장이 자동 반영된다.
    """

    def __init__(self, trading_days: Iterable[dt.date]):
        self.days: list[dt.date] = sorted(set(trading_days))
        if not self.days:
            raise ValueError("trading_days is empty")

    @classmethod
    def from_krx(cls, start: str, end: str, cache_dir: Path | str | None = None) -> TradingCalendar:
        """KOSPI 지수(KS11, FinanceDataReader) 일자로 거래일 캘린더 생성. 디스크 캐시 지원.

        (KRX 정보데이터시스템은 봇 차단으로 직접 조회 불가 → 네이버 소스인 FDR 사용)
        """
        s, e = start.replace("-", ""), end.replace("-", "")
        cache_path = None
        if cache_dir is not None:
            cache_dir = Path(cache_dir)
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache_path = cache_dir / f"trading_days_{s}_{e}.json"
            if cache_path.exists():
                days = json.loads(cache_path.read_text())
                return cls(dt.date.fromisoformat(d) for d in days)

        import FinanceDataReader as fdr

        df = fdr.DataReader("KS11", start, end)
        days = [d.date() for d in df.index]
        if cache_path is not None:
            cache_path.write_text(json.dumps([d.isoformat() for d in days]))
        return cls(days)

    def is_trading_day(self, d: dt.date) -> bool:
        i = bisect_left(self.days, d)
        return i < len(self.days) and self.days[i] == d

    def next_trading_day(self, d: dt.date, *, inclusive: bool = False) -> dt.date:
        """d 이후 첫 거래일. inclusive=True면 d 자신(거래일일 때)도 후보."""
        i = bisect_left(self.days, d) if inclusive else bisect_right(self.days, d)
        if i >= len(self.days):
            raise ValueError(f"calendar range exceeded: {d}")
        return self.days[i]

    def prev_trading_day(self, d: dt.date, *, inclusive: bool = False) -> dt.date:
        i = bisect_right(self.days, d) if inclusive else bisect_left(self.days, d)
        if i <= 0:
            raise ValueError(f"calendar range exceeded: {d}")
        return self.days[i - 1]

    def shift(self, d: dt.date, n: int) -> dt.date:
        """거래일 기준 n일 이동. d가 거래일이 아니면 에러."""
        i = bisect_left(self.days, d)
        if i >= len(self.days) or self.days[i] != d:
            raise ValueError(f"not a trading day: {d}")
        j = i + n
        if not 0 <= j < len(self.days):
            raise ValueError(f"calendar range exceeded: {d} + {n} trading days")
        return self.days[j]
