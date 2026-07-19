"""자사주매입 이벤트 추출 — 직접취득(tsstkAqDecsn) + 신탁계약(tsstkAqTrctrCnsDecsn).

방향 = +1 고정 (호재).
- 직접취득 강도 = 취득예정주식수(보통주) / 발행주식총수
- 신탁 강도   = 계약금액 / (접수 전일종가 × 발행총수) — 신탁 공시는 금액 기반(실측),
  전일 종가만 사용하므로 look-ahead 없음. event_type을 buyback_trust로 분리해
  직접취득과 섞지 않고 각각 리포트한다 (v1.2 결론과의 비교 가능성 유지).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from dart_event_study.dart.client import DartClient
from dart_event_study.events.parse import num
from dart_event_study.events.shares import total_shares_before


def extract_buybacks(
    client: DartClient, ticker: str, corp_code: str, bgn_de: str, end_de: str
) -> list[dict]:
    events = []
    for r in client.structured("tsstkAqDecsn.json", corp_code, bgn_de, end_de):
        rcept_no = r["rcept_no"]
        rcept_dt = rcept_no[:8]  # rcept_no 앞 8자리 = 접수일자 (실측 일치 확인)
        plan_shares = num(r.get("aqpln_stk_ostk"))
        rcept_date = dt.date.fromisoformat(f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}")
        total = total_shares_before(client, corp_code, rcept_date)
        strength = plan_shares / total if (plan_shares and total) else None
        events.append(
            {
                "ticker": ticker,
                "corp_code": corp_code,
                "rcept_no": rcept_no,
                "rcept_dt": rcept_dt,
                "event_type": "buyback",
                "direction": 1,
                "strength": strength,
                "plan_shares": plan_shares,
                "plan_amount": num(r.get("aqpln_prc_ostk")),
                "total_shares": total,
                "method": (r.get("aq_mth") or "").strip(),
                "purpose": (r.get("aq_pp") or "").strip(),
            }
        )
    return events


def extract_trust_buybacks(
    client: DartClient,
    ticker: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
    prev_close: Callable[[str, dt.date], float | None],
) -> list[dict]:
    """신탁계약 방식 자사주 — 계약금액 기반. prev_close(ticker, date) = 접수 전일 종가."""
    events = []
    for r in client.structured("tsstkAqTrctrCnsDecsn.json", corp_code, bgn_de, end_de):
        rcept_no = r["rcept_no"]
        rcept_dt = rcept_no[:8]
        d = dt.date(int(rcept_dt[:4]), int(rcept_dt[4:6]), int(rcept_dt[6:]))
        amount = num(r.get("ctr_prc"))
        total = total_shares_before(client, corp_code, d)
        px = prev_close(ticker, d)
        strength = amount / (px * total) if (amount and total and px) else None
        events.append(
            {
                "ticker": ticker,
                "corp_code": corp_code,
                "rcept_no": rcept_no,
                "rcept_dt": rcept_dt,
                "event_type": "buyback_trust",
                "direction": 1,
                "strength": strength,
                "plan_amount": amount,
                "total_shares": total,
                "prev_close": px,
                "purpose": (r.get("ctr_pp") or "").strip(),
            }
        )
    return events
