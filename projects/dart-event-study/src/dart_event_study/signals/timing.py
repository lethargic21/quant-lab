"""공시 접수 → 최초 체결가능 시점 매핑 (절대 원칙 1 — look-ahead 방지).

규칙 (종가 체결 가정):
- 접수 시각을 알고, 접수일이 거래일이며, 시각 ≤ 장마감(15:30) → 당일 종가
- 그 외 전부(장 마감 후, 비거래일, **시각 미상**) → 익영업일 종가

현재 데이터 현실: OpenDART는 접수 '일자'만 제공하고, DART 뷰어·KIND 페이지도
과거 공시의 접수시각을 주지 않음(실측, PLAN §7-4) → 전 건 시각 미상으로
익영업일 체결의 보수적 가정. 시각을 확보하면 인자만 채우면 된다.
"""

from __future__ import annotations

import datetime as dt

from quantlab_shared.data.calendar import TradingCalendar

MARKET_CLOSE = dt.time(15, 30)


def execution_date(
    rcept_date: dt.date,
    calendar: TradingCalendar,
    rcept_time: dt.time | None = None,
    cutoff: dt.time = MARKET_CLOSE,
) -> dt.date:
    """공시를 보고 처음으로 체결 가능한 거래일 (종가 체결 가정)."""
    if rcept_time is not None and rcept_time <= cutoff and calendar.is_trading_day(rcept_date):
        return rcept_date
    return calendar.next_trading_day(rcept_date)
