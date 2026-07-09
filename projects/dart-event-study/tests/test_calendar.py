"""영업일 캘린더 로직 검증 (합성 날짜 — 네트워크 불필요)."""

import datetime as dt

import pytest
from quantlab_shared.data.calendar import TradingCalendar

# 2024-01-02(화)~05(금), 08(월) — 주말 1/6~7 휴장, 1/1 휴일
DAYS = [dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
CAL = TradingCalendar(DAYS)


def test_is_trading_day():
    assert CAL.is_trading_day(dt.date(2024, 1, 2))
    assert not CAL.is_trading_day(dt.date(2024, 1, 6))  # 토


def test_next_trading_day_from_holiday():
    # 토요일 → 월요일
    assert CAL.next_trading_day(dt.date(2024, 1, 6)) == dt.date(2024, 1, 8)


def test_next_trading_day_exclusive_vs_inclusive():
    d = dt.date(2024, 1, 5)  # 금요일(거래일)
    assert CAL.next_trading_day(d) == dt.date(2024, 1, 8)  # 기본: d 이후
    assert CAL.next_trading_day(d, inclusive=True) == d


def test_prev_trading_day():
    assert CAL.prev_trading_day(dt.date(2024, 1, 8)) == dt.date(2024, 1, 5)
    assert CAL.prev_trading_day(dt.date(2024, 1, 7)) == dt.date(2024, 1, 5)  # 일요일 기준


def test_shift():
    assert CAL.shift(dt.date(2024, 1, 2), 3) == dt.date(2024, 1, 5)
    assert CAL.shift(dt.date(2024, 1, 5), 1) == dt.date(2024, 1, 8)  # 금→월 (주말 건너뜀)
    assert CAL.shift(dt.date(2024, 1, 8), -1) == dt.date(2024, 1, 5)
    with pytest.raises(ValueError):
        CAL.shift(dt.date(2024, 1, 6), 1)  # 비거래일 기준 이동 금지


def test_range_exceeded():
    with pytest.raises(ValueError):
        CAL.next_trading_day(dt.date(2024, 1, 8))
