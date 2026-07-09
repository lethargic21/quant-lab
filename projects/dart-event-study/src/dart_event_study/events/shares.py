"""발행주식총수(보통주) 조회 — look-ahead 없는 정기보고서 선택 룰.

이벤트 시점에 '이미 공개된' 가장 최근 정기보고서의 발행주식총수를 쓴다.
공개 시점은 법정 제출기한으로 보수적으로 가정:
분기·반기보고서 = 기말 + 45일, 사업보고서 = 기말 + 90일.
(실제 접수일이 더 빠를 수 있지만, 이 방향의 오차는 look-ahead가 아니라 지연이므로 안전)
"""

from __future__ import annotations

import datetime as dt

from dart_event_study.dart.client import DartClient
from dart_event_study.events.parse import num

# (reprt_code, 기말 월/일, 공개지연일)
_REPORTS = [
    ("11013", (3, 31), 45),  # 1분기
    ("11012", (6, 30), 45),  # 반기
    ("11014", (9, 30), 45),  # 3분기
    ("11011", (12, 31), 90),  # 사업보고서
]


def total_shares_before(client: DartClient, corp_code: str, event_date: dt.date) -> float | None:
    """이벤트일 이전 공개된 최신 정기보고서의 보통주 발행총수. 없으면 None."""
    candidates: list[tuple[dt.date, str, str]] = []  # (기말, bsns_year, reprt_code)
    for year in (event_date.year, event_date.year - 1, event_date.year - 2):
        for code, (m, d), lag in _REPORTS:
            period_end = dt.date(year, m, d)
            if period_end + dt.timedelta(days=lag) <= event_date:
                candidates.append((period_end, str(year), code))
    candidates.sort(reverse=True)

    for _, bsns_year, code in candidates:
        rows = client.stock_totals(corp_code, bsns_year, code)
        for r in rows:
            if r.get("se", "").strip() == "보통주":
                shares = num(r.get("istc_totqy"))
                if shares:
                    return shares
    return None
