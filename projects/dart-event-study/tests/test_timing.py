"""타이밍 로직 유닛테스트 (절대 원칙 1 — 반드시 검증).

캘린더: 2024-01-02(화)~05(금), 08(월). 1/1 휴일, 1/6~7 주말.
"""

import datetime as dt

from dart_event_study.signals.timing import execution_date
from quantlab_shared.data.calendar import TradingCalendar

CAL = TradingCalendar([dt.date(2024, 1, d) for d in (2, 3, 4, 5, 8)])

TUE = dt.date(2024, 1, 2)
FRI = dt.date(2024, 1, 5)
SAT = dt.date(2024, 1, 6)
MON = dt.date(2024, 1, 8)


def t(h, m):
    return dt.time(h, m)


# ── 시각 미상 (현재 데이터 현실) — 무조건 익영업일 ──────────────


def test_unknown_time_is_conservative_next_day():
    # 실제로는 오전 공시였더라도 시각 미상이면 당일 체결로 보지 않는다
    assert execution_date(TUE, CAL) == dt.date(2024, 1, 3)


def test_unknown_time_friday_to_monday():
    assert execution_date(FRI, CAL) == MON


def test_unknown_time_weekend_filing():
    assert execution_date(SAT, CAL) == MON


# ── 시각 있음 — 15:30 컷오프 경계 ──────────────────────────────


def test_intraday_before_cutoff_same_day():
    assert execution_date(TUE, CAL, rcept_time=t(15, 29)) == TUE


def test_exactly_at_cutoff_same_day():
    # 15:30 정각 = 컷오프 포함 (≤)
    assert execution_date(TUE, CAL, rcept_time=t(15, 30)) == TUE


def test_after_cutoff_next_day():
    assert execution_date(TUE, CAL, rcept_time=t(15, 31)) == dt.date(2024, 1, 3)


def test_friday_after_close_to_monday():
    assert execution_date(FRI, CAL, rcept_time=t(17, 0)) == MON


def test_intraday_time_on_non_trading_day_still_next_day():
    # 비거래일(토) 접수는 시각이 장중이어도 당일 체결 불가
    assert execution_date(SAT, CAL, rcept_time=t(10, 0)) == MON


def test_holiday_filing():
    # 1/1(휴일) 장중 시각 → 첫 거래일 1/2
    assert execution_date(dt.date(2024, 1, 1), CAL, rcept_time=t(9, 0)) == TUE
